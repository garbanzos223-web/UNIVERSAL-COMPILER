"""Python project adapter.

Real, testable behavior:
  - Detects: pyproject.toml, setup.py, setup.cfg, requirements.txt, or *.py files.
  - Inspect requirements: checks that Python interpreter is available (it is).
  - Create build plan: if pyproject.toml suggests a build backend, emit a "pip install build && python -m build" step only when user approves? Or for a distribution package, but to "collect distributable artifacts" and package into .ece, we can produce a bytecode-aware collection: run `python -m compileall` (syntax check) and then package source files plus any existing dist/ or build/ artifacts that already exist.

The spec says "invoke the appropriate installed build tools". For many Python projects, the distributable is the source tree (or a wheel). Packaging source + compiled bytecode is a reasonable real behavior. We do NOT automatically run networked `pip install` or `pip build` without consent; those require `requires_approval=True`.

Also, we can offer a simple "package sources + bytecompile check" as the default safe plan.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..detection import ScanResult, get_tool
from .base import (
    Artifact,
    BuildAdapter,
    BuildPlan,
    BuildPlanStep,
    BuildResult,
    DetectionResult,
    Issue,
    ProgressCallback,
    CancelToken,
    RequirementsReport,
    Requirement,
)


class PythonAdapter(BuildAdapter):
    adapter_id = "python"
    display_name = "Python"

    def detect_project(self, root: Path, scan_result: ScanResult | None = None) -> list[DetectionResult]:
        results: list[DetectionResult] = []
        markers: list[str] = []
        # Explicit manifests.
        for name in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pipfile", "Pipfile.lock"):
            if (root / name).exists():
                markers.append(name)
        # General .py presence.
        py_count = 0
        if scan_result is not None:
            for rel in scan_result.files:
                if rel.endswith(".py"):
                    py_count += 1
        if py_count > 0 and not markers:
            markers.append(f"{py_count} Python file(s)")
        if markers:
            confidence = 0.95 if any(m in ("pyproject.toml", "setup.py") for m in markers) else 0.85 if "requirements.txt" in markers else 0.7
            results.append(
                DetectionResult(
                    adapter_id=self.adapter_id,
                    project_type="python",
                    root=root,
                    markers=markers,
                    confidence=confidence,
                )
            )
        return results

    def inspect_requirements(self, root: Path, config: dict | None = None) -> RequirementsReport:
        reqs: list[Requirement] = []
        py = get_tool("python")
        reqs.append(
            Requirement(
                id="python",
                name="Python interpreter",
                required=True,
                found=py.found if py else False,
                version=py.version if py else None,
                path=py.path if py else None,
                install_hint=py.install_hint if py else "https://www.python.org/downloads/",
                kind="tool",
            )
        )
        return RequirementsReport(requirements=reqs, ready=reqs[0].found, notes=[])

    def validate_configuration(self, root: Path, config: dict | None = None) -> list[Issue]:
        issues: list[Issue] = []
        rep = self.inspect_requirements(root, config)
        if not rep.ready:
            issues.append(
                Issue(
                    level="error",
                    code="ece.adapter.python.missing_python",
                    message="Python interpreter not found.",
                    fix="Install Python 3.8+.",
                )
            )
        return issues

    def create_build_plan(self, root: Path, config: dict | None = None) -> BuildPlan:
        cfg = config or {}
        out_dirs = [root / "dist", root / "build"]

        steps: list[BuildPlanStep] = []
        # Step 1: byte-compile to verify syntax and produce *.pyc-like artifacts where appropriate.
        # This is a real, safe action (no network, no system changes).
        py = get_tool("python")
        exe = py.path or "python"
        steps.append(
            BuildPlanStep(
                id="compileall",
                title="Verify syntax with compileall",
                cmd_args=[exe, "-m", "compileall", "-q", "."],
                workdir=root,
                timeout=180,
                requires_approval=False,
            )
        )

        # Optionally attempt PyInstaller if present and requested? Check if pyinstaller installed in environment.
        # Don't run pip install; only run if importable. But spec says don't download arbitrary things. Skip.
        if cfg.get("build_with_pyinstaller") is True:
            # lightweight check only
            steps.append(
                BuildPlanStep(
                    id="pyinstaller_check",
                    title="PyInstaller selected (requires PyInstaller to be preinstalled)",
                    cmd_args=[exe, "-m", "pip", "show", "pyinstaller"],
                    workdir=root,
                    requires_approval=True,
                    timeout=60,
                )
            )

        return BuildPlan(
            id="python-default",
            adapter_id=self.adapter_id,
            steps=steps,
            workdir=root,
            output_dirs=out_dirs,
            artifacts_expected=["source tree", "compiled artifacts"],
            notes=["Byte-compile is non-destructive and verifies basic syntax."],
        )

    def execute_build(
        self,
        plan: BuildPlan,
        *,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
        log_path: Path | None = None,
        consent_granted: bool = False,
    ) -> BuildResult:
        from ..build.process import run_command

        res = run_command(
            steps=plan.steps,
            workdir=plan.workdir,
            progress=progress,
            cancel=cancel,
            log_path=log_path,
            consent_granted=consent_granted,
        )
        return res

    def collect_artifacts(self, root: Path, plan: BuildPlan) -> list[Artifact]:
        artifacts: list[Artifact] = []
        # Collect sources.
        for pattern in ("**/*.py", "**/*.pyi", "**/pyproject.toml", "**/requirements.txt", "**/setup.py", "**/setup.cfg"):
            from glob import glob

            for p in glob(pattern, root_dir=root, recursive=True):
                full = root / p
                if full.is_dir():
                    continue
                try:
                    st = full.stat()
                except OSError:
                    continue
                artifacts.append(
                    Artifact(
                        path=full,
                        kind="source",
                        size=st.st_size,
                        rel_path=str(Path(p).as_posix()),
                    )
                )
        # Existing build outputs.
        for dname in ("dist", "build", ".dist", ".build"):
            d = root / dname
            if d.is_dir():
                for p in d.rglob("*"):
                    if p.is_dir():
                        continue
                    rel = p.relative_to(root).as_posix()
                    st = p.stat()
                    artifacts.append(
                        Artifact(
                            path=p,
                            kind="binary",
                            size=st.st_size,
                            rel_path=rel,
                        )
                    )
        # Deduplicate by rel_path
        seen: set[str] = set()
        deduped = []
        for a in artifacts:
            key = a.rel_path or a.path.as_posix()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(a)
        return deduped

    def classify_errors(self, output: str) -> list[str]:
        errs: list[str] = []
        for line in output.splitlines():
            l = line.strip()
            if not l:
                continue
            if "SyntaxError:" in l or "ImportError:" in l or "ModuleNotFoundError:" in l:
                errs.append(l)
            if re.search(r"^.*:[0-9]+:", l) and ("error:" in l.lower() or "exception:" in l.lower()):
                errs.append(l)
        return errs

    def describe_supported_targets(self) -> list[dict]:
        return [
            {"id": "source", "name": "Source bundle (bytecode-checked)"},
            {"id": "dist", "name": "Existing dist/ outputs"},
        ]
