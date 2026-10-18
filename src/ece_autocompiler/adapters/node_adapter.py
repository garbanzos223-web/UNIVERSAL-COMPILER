"""Node.js project adapter.

Real behavior:
  - Detects package.json (typescript if tsconfig.json present).
  - Inspect requirements: checks node and npm.
  - Build plan: by default, `npm install` (requires approval, runs project-controlled scripts) then `npm run build` if "build" script exists. If no build script, we still run a syntax check via `node --check` where possible, or just collect sources. Approval is required for `npm install` because package.json scripts execute code from node_modules and dependencies (as documented in security constraints).
  - Artifacts: collect package.json, src/, dist/, build/, public/, .next (if exist), lib/, plus any files referenced? We collect common output dirs and sources.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..detection import get_tool
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


class NodeAdapter(BuildAdapter):
    adapter_id = "node"
    display_name = "Node.js"

    def detect_project(self, root: Path, scan_result=None) -> list[DetectionResult]:
        markers: list[str] = []
        if (root / "package.json").exists():
            markers.append("package.json")
        if (root / "tsconfig.json").exists():
            markers.append("tsconfig.json")
        if not markers:
            return []
        project_type = "typescript" if "tsconfig.json" in markers else "node"
        confidence = 0.95 if markers == ["package.json", "tsconfig.json"] else 0.9
        return [
            DetectionResult(
                adapter_id=self.adapter_id,
                project_type=project_type,
                root=root,
                markers=markers,
                confidence=confidence,
            )
        ]

    def inspect_requirements(self, root: Path, config: dict | None = None) -> RequirementsReport:
        reqs: list[Requirement] = []
        node = get_tool("node")
        npm = get_tool("npm")
        for t, id_ in ((node, "node"), (npm, "npm")):
            reqs.append(
                Requirement(
                    id=id_,
                    name=t.name if t else id_,
                    required=True,
                    found=t.found if t else False,
                    version=t.version if t else None,
                    path=t.path if t else None,
                    install_hint=t.install_hint if t else "https://nodejs.org/",
                    kind="tool",
                )
            )
        ready = all(r.found for r in reqs)
        return RequirementsReport(requirements=reqs, ready=ready)

    def validate_configuration(self, root: Path, config: dict | None = None) -> list[Issue]:
        issues: list[Issue] = []
        rep = self.inspect_requirements(root, config)
        if not rep.ready:
            issues.append(
                Issue(
                    level="error",
                    code="ece.adapter.node.missing_tool",
                    message="Node.js or npm is not installed.",
                    fix="Install Node.js LTS.",
                )
            )
        return issues

    def create_build_plan(self, root: Path, config: dict | None = None) -> BuildPlan:
        cfg = config or {}
        node = get_tool("node")
        npm = get_tool("npm")
        node_exe = node.path or "node"
        npm_exe = npm.path or "npm"

        steps: list[BuildPlanStep] = []
        # npm install requires explicit approval (runs package scripts).
        if cfg.get("skip_npm_install") is not True:
            steps.append(
                BuildPlanStep(
                    id="npm-install",
                    title="Install dependencies (npm install)",
                    cmd_args=[npm_exe, "install"],
                    workdir=root,
                    requires_approval=True,
                    timeout=900,
                )
            )

        # Look for build script.
        has_build = False
        try:
            pkg = root / "package.json"
            if pkg.exists():
                data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
                scripts = data.get("scripts") or {}
                has_build = "build" in scripts
        except Exception:
            pass

        if has_build:
            steps.append(
                BuildPlanStep(
                    id="npm-build",
                    title="Build project (npm run build)",
                    cmd_args=[npm_exe, "run", "build"],
                    workdir=root,
                    requires_approval=True,
                    timeout=1200,
                )
            )
        else:
            # Syntax/type light check for obvious entry points.
            steps.append(
                BuildPlanStep(
                    id="syntax-check",
                    title="Syntax check (node --check common entry files)",
                    cmd_args=[node_exe, "--check", "index.js"],
                    workdir=root,
                    requires_approval=False,
                    timeout=120,
                )
            )

        return BuildPlan(
            id="node-default",
            adapter_id=self.adapter_id,
            steps=steps,
            workdir=root,
            output_dirs=[root / "dist", root / "build", root / ".next", root / "lib", root / "out"],
            artifacts_expected=["package.json", "dist/", "build/"],
            requires_consent=True,
            notes=["npm install runs project-controlled scripts and requires approval."],
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

        return run_command(
            steps=plan.steps,
            workdir=plan.workdir,
            progress=progress,
            cancel=cancel,
            log_path=log_path,
            consent_granted=consent_granted,
        )

    def collect_artifacts(self, root: Path, plan: BuildPlan) -> list[Artifact]:
        artifacts: list[Artifact] = []
        candidates = [
            "package.json",
            "package-lock.json",
            "pnpm-lock.yaml",
            "yarn.lock",
            "tsconfig.json",
            ".nvmrc",
            "README.md",
            "LICENSE",
            "LICENSE.md",
        ]
        for c in candidates:
            p = root / c
            if p.exists() and p.is_file():
                artifacts.append(
                    Artifact(path=p, kind="source", size=p.stat().st_size, rel_path=p.name)
                )
        for pat in ("**/*.js", "**/*.mjs", "**/*.cjs", "**/*.ts", "**/*.tsx", "**/*.json", "**/*.css", "**/*.scss", "**/*.html", "**/*.md"):
            from glob import glob

            for rel in glob(pat, root_dir=root, recursive=True):
                full = root / rel
                if full.is_dir():
                    continue
                rp = Path(rel).as_posix()
                if rp.startswith((".git/", "node_modules/")):
                    continue
                try:
                    st = full.stat()
                except OSError:
                    continue
                artifacts.append(Artifact(path=full, kind="source", size=st, rel_path=rp))
        for outd in ("dist", "build", ".next", "lib", "out", "public"):
            d = root / outd
            if d.is_dir():
                for p in d.rglob("*"):
                    if p.is_dir():
                        continue
                    rp = p.relative_to(root).as_posix()
                    if rp.startswith((".git/")):
                        continue
                    st = p.stat()
                    artifacts.append(Artifact(path=p, kind="binary", size=st.st_size, rel_path=rp))
        seen: set[str] = set()
        deduped: list[Artifact] = []
        for a in artifacts:
            k = a.rel_path
            if k in seen:
                continue
            seen.add(k)
            deduped.append(a)
        return deduped

    def classify_errors(self, output: str) -> list[str]:
        errs: list[str] = []
        for line in output.splitlines():
            l = line.strip()
            low = l.lower()
            if not l:
                continue
            if "syntaxerror" in low or "npm err!" in low or "error:" in low or "fatal:" in low:
                errs.append(l)
            m = re.search(r"^\s*(.+?):(\d+):(\d+):\s*(error|warning):", l)
            if m:
                errs.append(l)
        return errs

    def describe_supported_targets(self) -> list[dict]:
        return [
            {"id": "source", "name": "Source + deps manifest"},
            {"id": "dist", "name": "Built output (dist/build)"},
        ]
