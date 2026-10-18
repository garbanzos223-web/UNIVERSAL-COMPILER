"""Generic external tool adapter (detection-only or tool-gated build).

Used for toolchains that exist on some systems but are not installed here
(e.g., .NET SDK, Cargo, Go, Maven, Gradle, CMake, Make, PHP, Unity, Unreal).
When the required tool is present, the build plan uses the tool's real command
argv; when it is missing, inspect_requirements returns ``ready=False`` with a
clear install_hint.

This avoids making "untested claims": we can still construct the command a real
integration would run, but we never fabricate success when the tool is absent.
"""

from __future__ import annotations

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
# run_command is imported lazily inside execute_build to avoid a circular
# import between the adapters and build packages.


def _tool_for_adapter(adapter_id: str) -> str | None:
    mapping = {
        "dotnet": "dotnet",
        "rust-cargo": "cargo",
        "go": "go",
        "java-maven": "mvn",
        "java-gradle": "gradle",
        "kotlin-gradle": "gradle",
        "cpp-cmake": "cmake",
        "cpp-meson": "ninja",
        "cpp-make": "make",
        "php": "php",
        "unity": None,
        "unreal": None,
    }
    return mapping.get(adapter_id)


def _default_steps(adapter_id: str, tool_id: str | None, root: Path) -> list[BuildPlanStep]:
    steps: list[BuildPlanStep] = []
    tool = get_tool(tool_id) if tool_id else None
    exe = tool.path if tool and tool.path else (tool_id or "unknown")
    if adapter_id == "dotnet" and tool_id == "dotnet":
        steps.append(
            BuildPlanStep(
                id="dotnet-publish",
                title="Build with dotnet publish (SDK required)",
                cmd_args=[exe, "publish", "-c", "Release"],
                workdir=root,
                requires_approval=True,
                timeout=1200,
            )
        )
    elif adapter_id in ("rust-cargo", "rust") and tool_id == "cargo":
        steps.append(
            BuildPlanStep(
                id="cargo-build",
                title="Build with cargo build --release",
                cmd_args=[exe, "build", "--release"],
                workdir=root,
                requires_approval=False,
                timeout=1200,
            )
        )
    elif adapter_id == "go" and tool_id == "go":
        steps.append(
            BuildPlanStep(
                id="go-build",
                title="Build with go build ./...",
                cmd_args=[exe, "build", "./..."],
                workdir=root,
                requires_approval=False,
                timeout=600,
            )
        )
    elif adapter_id == "java-maven" and tool_id == "mvn":
        steps.append(
            BuildPlanStep(
                id="mvn-package",
                title="Build with mvn package",
                cmd_args=[exe, "package", "-DskipTests"],
                workdir=root,
                requires_approval=True,
                timeout=1200,
            )
        )
    elif adapter_id in ("java-gradle", "kotlin-gradle") and tool_id == "gradle":
        steps.append(
            BuildPlanStep(
                id="gradle-build",
                title="Build with gradle build",
                cmd_args=[exe, "build", "-x", "test"],
                workdir=root,
                requires_approval=True,
                timeout=1200,
            )
        )
    elif adapter_id == "cpp-cmake" and tool_id == "cmake":
        steps.append(
            BuildPlanStep(
                id="cmake-configure",
                title="Configure with cmake",
                cmd_args=[exe, "-S", ".", "-B", "build"],
                workdir=root,
                requires_approval=False,
                timeout=300,
            )
        )
    elif adapter_id == "cpp-make" and tool_id == "make":
        steps.append(
            BuildPlanStep(
                id="make",
                title="Build with make",
                cmd_args=[exe],
                workdir=root,
                requires_approval=False,
                timeout=1200,
            )
        )
    elif adapter_id == "php" and tool_id == "php":
        php_files = sorted(
            p for p in root.rglob("*.php") if ".git" not in p.parts
        )
        if php_files:
            # One lint step per file; argv lists avoid shell glob expansion.
            for index, php_file in enumerate(php_files[:200]):
                steps.append(
                    BuildPlanStep(
                        id=f"php-lint-{index}",
                        title=f"Syntax check {php_file.name}",
                        cmd_args=[exe, "-l", str(php_file)],
                        workdir=root,
                        requires_approval=False,
                        timeout=60,
                    )
                )
        else:
            steps.append(
                BuildPlanStep(
                    id="php-none",
                    title="No PHP files to check",
                    cmd_args=[],
                    workdir=root,
                    requires_approval=False,
                )
            )
    else:
        # Engine or unsupported: no automatic build steps.
        steps.append(
            BuildPlanStep(
                id="noop",
                title="No automatic build steps for this adapter in this environment",
                cmd_args=[],
                workdir=root,
                requires_approval=False,
            )
        )
    return steps


class ExternalToolAdapter(BuildAdapter):
    def __init__(self, adapter_id: str, tool_id: str | None, display_name: str) -> None:
        self.adapter_id = adapter_id
        self.display_name = display_name
        self._tool_id = tool_id

    def detect_project(self, root: Path, scan_result=None) -> list[DetectionResult]:
        from ..detection import scan

        sr = scan_result or scan(root)
        return [c for c in sr.candidates if c.adapter_id == self.adapter_id]

    def inspect_requirements(self, root: Path, config: dict | None = None) -> RequirementsReport:
        reqs: list[Requirement] = []
        tool_id = self._tool_id
        if tool_id:
            t = get_tool(tool_id)
            reqs.append(
                Requirement(
                    id=tool_id,
                    name=t.name if t else tool_id,
                    required=True,
                    found=t.found if t else False,
                    version=t.version if t else None,
                    path=t.path if t else None,
                    install_hint=t.install_hint if t else "",
                    kind="tool",
                )
            )
        else:
            reqs.append(
                Requirement(
                    id=self.adapter_id,
                    name=self.display_name,
                    required=True,
                    found=False,
                    install_hint="This engine's build tools must be installed on the host.",
                    kind="sdk",
                )
            )
        ready = all(r.found for r in reqs)
        notes = ["Toolchain not found; end-to-end build is not verified in this environment."]
        return RequirementsReport(requirements=reqs, ready=ready, notes=notes)

    def validate_configuration(self, root: Path, config: dict | None = None) -> list[Issue]:
        rep = self.inspect_requirements(root, config)
        if not rep.ready:
            return [
                Issue(
                    level="warning",
                    code="ece.adapter.missing_toolchain",
                    message=f"Required toolchain for {self.display_name} is not installed.",
                    fix=rep.requirements[0].install_hint if rep.requirements else "",
                )
            ]
        return []

    def create_build_plan(self, root: Path, config: dict | None = None) -> BuildPlan:
        steps = _default_steps(self.adapter_id, self._tool_id, root)
        requires_consent = any(s.requires_approval for s in steps)
        return BuildPlan(
            id=f"{self.adapter_id}-plan",
            adapter_id=self.adapter_id,
            steps=steps,
            workdir=root,
            output_dirs=[root / "dist", root / "build", root / "bin"],
            requires_consent=requires_consent,
            notes=["Build plan is a best-effort command that will only run if the tool is present."],
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
        for pat in ("**/*.dll", "**/*.exe", "**/*.so", "**/*.dylib", "**/*.jar", "**/*.bin", "**/*.unitypackage"):
            from glob import glob

            for rel in glob(pat, root_dir=root, recursive=True):
                full = root / rel
                if full.is_dir():
                    continue
                rp = Path(rel).as_posix()
                if rp.startswith(("bin/", "obj/", "target/", "build/", "dist/", ".git/")) is False and full.exists():
                    pass
                try:
                    st = full.stat()
                except OSError:
                    continue
                artifacts.append(Artifact(path=full, kind="binary", size=st.st_size, rel_path=rp))
        return artifacts

    def classify_errors(self, output: str) -> list[str]:
        errs: list[str] = []
        for line in output.splitlines():
            l = line.strip()
            if not l:
                continue
            low = l.lower()
            if "error:" in low or "fatal:" in low or "failed" in low:
                errs.append(l)
        return errs

    def describe_supported_targets(self) -> list[dict]:
        return [{"id": "auto", "name": "Toolchain-defined output"}]
