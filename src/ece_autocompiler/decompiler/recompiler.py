"""Recompilation engine.

Recompiling means building a *new* binary from the recovered source with a real
toolchain: it is not, and cannot be, a byte-identical restoration of the
original program. This module detects the project kind, verifies the required
tools and sources, runs the actual commands with the project's safe process
runner, and verifies that the expected artifact really appeared.

Every failure is reported with the command, exit code and captured output.
Nothing here fabricates a successful build.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..build.process import run_command
from ..config import AppConfig
from ..errors import BuildCancelledError, EceError
from ..logging_config import get_logger

log = get_logger("decompiler.recompile")

MAX_SOURCE_FILES = 20000
ARTIFACT_SUFFIXES = {
    ".exe", ".dll", ".jar", ".class", ".pyc", ".zip", ".whl", ".so", ".dylib",
    ".node", ".a", ".lib", ".out", ".app", ".apk",
}

ProgressCallback = Callable[[str, int, int], None]


@dataclass
class BuildStep:
    id: str
    title: str
    cmd_args: list[str]
    workdir: Path | None = None
    timeout: int | None = None
    requires_approval: bool = False


@dataclass
class RecompilePlan:
    kind: str
    label: str
    root: Path
    steps: list[BuildStep] = field(default_factory=list)
    tool_ids: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ready: bool = False
    output_kind: str = ""

    def summary_lines(self) -> list[str]:
        lines = [
            f"Project: {self.root}",
            f"Kind: {self.label} ({self.kind})",
            f"Ready: {'yes' if self.ready else 'no'}",
        ]
        if self.tool_ids:
            lines.append("Toolchains: " + ", ".join(self.tool_ids))
        if self.issues:
            lines.append("Issues:")
            lines.extend(f"  - {issue}" for issue in self.issues)
        if self.steps:
            lines.append("Commands (nothing has run yet):")
            for step in self.steps:
                lines.append(f"  [{step.title}] {' '.join(step.cmd_args)}")
        for note in self.notes:
            lines.append(f"note: {note}")
        return lines


@dataclass
class RecompileResult:
    ok: bool
    plan: RecompilePlan
    output_dir: Path
    artifacts: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    log_path: Path | None = None
    duration_seconds: float = 0.0
    cancelled: bool = False

    def summary_lines(self) -> list[str]:
        lines = [
            f"Recompilation {'succeeded' if self.ok else 'did not succeed'} "
            f"in {self.duration_seconds:.1f}s",
            f"Output folder: {self.output_dir}",
        ]
        if self.artifacts:
            lines.append("Artifacts (verified on disk):")
            lines.extend(f"  {path} ({path.stat().st_size:,} bytes)" for path in self.artifacts[:12])
        if self.errors:
            lines.append("Errors:")
            lines.extend(f"  - {error}" for error in self.errors[:20])
        if self.warnings:
            lines.append("Warnings:")
            lines.extend(f"  - {warning}" for warning in self.warnings[:20])
        for note in self.notes:
            lines.append(f"note: {note}")
        if self.log_path:
            lines.append(f"Log: {self.log_path}")
        return lines


def _scan(root: Path) -> list[Path]:
    found: list[Path] = []
    skip = {"__pycache__", ".git", ".venv", "venv", "node_modules", "bin", "obj", "target"}
    for path in root.rglob("*"):
        if len(found) >= MAX_SOURCE_FILES:
            break
        if not path.is_file():
            continue
        if any(part in skip for part in path.parts):
            continue
        found.append(path)
    return found


def _which(name: str) -> str:
    return shutil.which(name) or ""


def detect_project_kind(root: Path) -> tuple[str, str]:
    root = Path(root)
    if not root.is_dir():
        return "unknown", "Not a directory"
    files = _scan(root)
    names = {path.name.lower() for path in files}
    suffixes = {path.suffix.lower() for path in files}
    if "pom.xml" in names:
        return "java-maven", "Java (Maven)"
    if "build.gradle" in names or "build.gradle.kts" in names:
        return "java-gradle", "Java (Gradle)"
    if any(name.endswith((".csproj", ".sln")) for name in names):
        return "dotnet", ".NET / C#"
    if "cmakelists.txt" in names:
        return "cpp-cmake", "C/C++ (CMake)"
    if "meson.build" in names:
        return "cpp-meson", "C/C++ (Meson)"
    if "go.mod" in names:
        return "go", "Go"
    if "cargo.toml" in names:
        return "rust", "Rust"
    if "makefile" in names or "gnumakefile" in names:
        return "cpp-make", "C/C++ (Make)"
    if "package.json" in names:
        return "node", "Node.js"
    if ".java" in suffixes:
        return "java-javac", "Java (plain sources)"
    if ".py" in suffixes:
        return "python", "Python"
    if suffixes & {".c", ".cc", ".cpp", ".cxx"}:
        return "cpp-sources", "C/C++ (loose sources)"
    return "unknown", "Unknown project"


def _source_files(root: Path, suffix: str, limit: int = 5000) -> list[Path]:
    found: list[Path] = []
    for path in _scan(root):
        if path.suffix.lower() == suffix:
            found.append(path)
            if len(found) >= limit:
                break
    return found


def plan_recompile(root: Path, output_dir: Path, *, build_exe: bool = False) -> RecompilePlan:
    root = Path(root).resolve()
    output_dir = Path(output_dir).resolve()
    kind, label = detect_project_kind(root)
    plan = RecompilePlan(kind=kind, label=label, root=root)
    python = _which("python") or _which("python3")
    dotnet = _which("dotnet")
    mvn = _which("mvn") or _which("mvn.cmd")
    gradle = _which("gradle") or _which("gradle.bat")
    javac = _which("javac")
    cmake = _which("cmake")
    make = _which("make") or _which("mingw32-make")
    go = _which("go")
    cargo = _which("cargo")
    npm = _which("npm") or _which("npm.cmd")

    if kind == "python":
        if not python:
            plan.issues.append("Python is not installed on PATH.")
        sources = _source_files(root, ".py")
        if not sources:
            plan.issues.append("No .py source file was found.")
        plan.tool_ids = ["python"]
        if python and sources:
            plan.steps.append(
                BuildStep("pycompile", "Verify every Python source", [python, "-m", "compileall", "-q", str(root)], root)
            )
            if build_exe:
                entry = _python_entry(sources)
                plan.steps.append(
                    BuildStep(
                        "pyinstaller",
                        "Build a standalone Windows .exe from the recovered source",
                        [
                            python, "-m", "PyInstaller", "--noconfirm", "--clean",
                            "--onefile", "--distpath", str(output_dir),
                            "--workpath", str(output_dir / "_pyinstaller"),
                            "--specpath", str(output_dir),
                            str(entry),
                        ],
                        root,
                        requires_approval=True,
                    )
                )
                plan.output_kind = "exe"
            else:
                plan.output_kind = "pyc"
        plan.ready = not plan.issues
        plan.notes.append(
            "Python recompilation verifies the source and writes bytecode"
            + (" / a new PyInstaller executable" if build_exe else "")
            + " into the output folder."
        )
        return plan

    if kind == "dotnet":
        project = _first_with_suffix(root, (".csproj", ".sln", ".fsproj", ".vbproj"))
        if not dotnet:
            plan.issues.append(".NET SDK ('dotnet') is not installed on PATH.")
        if project is None:
            plan.issues.append("No .csproj/.sln file was found.")
        plan.tool_ids = ["dotnet"]
        if dotnet and project:
            plan.steps.append(
                BuildStep(
                    "dotnet-build",
                    "dotnet build (Release)",
                    [dotnet, "build", str(project), "-c", "Release", "-o", str(output_dir)],
                    root,
                )
            )
            plan.output_kind = "dll"
        plan.ready = not plan.issues
        plan.notes.append("The rebuilt assembly is a new build; it will not be byte-identical.")
        return plan

    if kind in ("java-maven", "java-gradle"):
        plan.tool_ids = ["java", "mvn" if kind == "java-maven" else "gradle"]
        if kind == "java-maven":
            if not mvn:
                plan.issues.append("Maven ('mvn') is not installed on PATH.")
            if mvn:
                plan.steps.append(
                    BuildStep("mvn", "mvn package", [mvn, "-q", "-DskipTests", "package"], root)
                )
                plan.output_kind = "jar"
        else:
            wrapper = root / ("gradlew.bat" if (root / "gradlew.bat").is_file() else "gradlew")
            if wrapper.is_file():
                plan.steps.append(
                    BuildStep("gradle", "Gradle build", [str(wrapper), "build", "-x", "test"], root)
                )
            elif gradle:
                plan.steps.append(BuildStep("gradle", "Gradle build", [gradle, "build", "-x", "test"], root))
            else:
                plan.issues.append("Gradle is not installed and no wrapper was found.")
            plan.output_kind = "jar"
        plan.ready = not plan.issues
        return plan

    if kind == "java-javac":
        sources = _source_files(root, ".java")
        plan.tool_ids = ["javac"]
        if not javac:
            plan.issues.append("javac is not installed (a JDK is required, not just a JRE).")
        if not sources:
            plan.issues.append("No .java source file was found.")
        if javac and sources:
            list_file = output_dir / "javac-sources.txt"
            plan.steps.append(
                BuildStep(
                    "javac",
                    f"Compile {len(sources)} Java source file(s)",
                    [javac, "-encoding", "UTF-8", "-d", str(output_dir / "classes"), f"@{list_file}"],
                    root,
                )
            )
            plan.notes.append(f"The compiler source list is written to {list_file} before the build.")
            plan.output_kind = "class"
        plan.ready = not plan.issues
        return plan

    if kind == "cpp-cmake":
        plan.tool_ids = ["cmake"]
        if not cmake:
            plan.issues.append("CMake is not installed on PATH.")
        if cmake:
            build_dir = output_dir / "cmake-build"
            plan.steps.append(
                BuildStep("cmake-config", "CMake configure", [cmake, "-S", str(root), "-B", str(build_dir), "-DCMAKE_BUILD_TYPE=Release"], root)
            )
            plan.steps.append(
                BuildStep("cmake-build", "CMake build", [cmake, "--build", str(build_dir), "--config", "Release"], root)
            )
            plan.output_kind = "exe"
        plan.ready = not plan.issues
        return plan

    if kind == "cpp-make":
        plan.tool_ids = ["make"]
        if not make:
            plan.issues.append("make is not installed on PATH.")
        if make:
            plan.steps.append(BuildStep("make", "make", [make], root))
            plan.output_kind = "exe"
        plan.ready = not plan.issues
        return plan

    if kind == "go":
        plan.tool_ids = ["go"]
        if not go:
            plan.issues.append("Go is not installed on PATH.")
        if go:
            plan.steps.append(
                BuildStep("go", "go build", [go, "build", "-o", str(output_dir / "app.exe"), "./..."], root)
            )
            plan.output_kind = "exe"
        plan.ready = not plan.issues
        return plan

    if kind == "rust":
        plan.tool_ids = ["cargo"]
        if not cargo:
            plan.issues.append("Cargo is not installed on PATH.")
        if cargo:
            plan.steps.append(BuildStep("cargo", "cargo build --release", [cargo, "build", "--release"], root))
            plan.output_kind = "exe"
        plan.ready = not plan.issues
        return plan

    if kind == "node":
        plan.tool_ids = ["npm"]
        if not npm:
            plan.issues.append("npm is not installed on PATH.")
        if npm:
            plan.steps.append(
                BuildStep(
                    "npm-install", "npm install", [npm, "install", "--no-audit", "--no-fund"],
                    root, requires_approval=True,
                )
            )
            plan.steps.append(
                BuildStep(
                    "npm-build", "npm run build", [npm, "run", "build"],
                    root, requires_approval=True,
                )
            )
            plan.output_kind = "dist"
        plan.ready = not plan.issues
        plan.notes.append("npm install and npm run build execute project scripts; consent is required.")
        return plan

    if kind in ("cpp-sources",):
        plan.issues.append(
            "Loose C/C++ sources have no build system in this folder. Add a "
            "CMakeLists.txt or Makefile so the compiler knows how to link them."
        )
        plan.ready = False
        return plan

    plan.issues.append(
        "This build has no recompilation plan for this project type. Supported "
        "plans: .NET, Java (Maven/Gradle/javac), Python, CMake/Make, Go, Rust and Node."
    )
    plan.ready = False
    return plan


def _first_with_suffix(root: Path, suffixes: tuple[str, ...]) -> Path | None:
    for path in _scan(root):
        if path.suffix.lower() in suffixes:
            return path
    return None


def _python_entry(sources: list[Path]) -> Path:
    preferred = ("main.py", "app.py", "__main__.py", "run.py")
    by_name = {path.name.lower(): path for path in sources}
    for name in preferred:
        if name in by_name:
            return by_name[name]
    for path in sources:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "__main__" in text:
            return path
    return sources[0]


def _write_javac_list(plan: RecompilePlan, output_dir: Path) -> None:
    list_file = output_dir / "javac-sources.txt"
    sources = _source_files(plan.root, ".java")
    list_file.parent.mkdir(parents=True, exist_ok=True)
    list_file.write_text("\n".join(str(path) for path in sources), encoding="utf-8")


def _collect_artifacts(root: Path, started: float) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        try:
            if not path.is_file() or path.suffix.lower() not in ARTIFACT_SUFFIXES:
                continue
            if path.stat().st_mtime + 0.5 < started:
                continue
        except OSError:
            continue
        found.append(path)
        if len(found) >= 200:
            break
    found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return found


def recompile(
    root: Path,
    output_dir: Path,
    *,
    build_exe: bool = False,
    consent_granted: bool = False,
    progress: ProgressCallback | None = None,
    cancel=None,
    config: AppConfig | None = None,
    require_recompile_ready: bool = True,
) -> RecompileResult:
    """Run the planned real build and verify its artifacts."""
    root = Path(root).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    plan = plan_recompile(root, output_dir, build_exe=build_exe)

    if require_recompile_ready:
        manifest = root / "recovered-project.json"
        if manifest.is_file():
            try:
                import json

                payload = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = None
            if isinstance(payload, dict) and payload.get("recompile_ready") is False:
                plan.ready = False
                plan.issues.append(
                    "This recovered project is marked as not recompilable: it contains "
                    "generated outlines without real method bodies. Only an honest "
                    "analysis/partial reconstruction is available for this format."
                )

    result = RecompileResult(ok=False, plan=plan, output_dir=output_dir)
    cfg = config or AppConfig()
    cfg.ensure_directories()
    result.log_path = cfg.work_dir / "logs" / f"recompile_{int(started)}.log"
    result.log_path.parent.mkdir(parents=True, exist_ok=True)

    if not plan.ready or not plan.steps:
        result.errors.extend(plan.issues or ["No recompilation step is available."])
        result.duration_seconds = time.time() - started
        result.notes.extend(plan.notes)
        return result

    if plan.kind == "java-javac":
        _write_javac_list(plan, output_dir)

    # Consent: a step that runs project-controlled scripts must be approved.
    needed = any(step.requires_approval for step in plan.steps)
    if needed and not consent_granted:
        result.errors.append(
            "This build runs project-defined scripts (npm install/build or PyInstaller). "
            "Run it again with consent granted after checking the commands above."
        )
        result.duration_seconds = time.time() - started
        return result

    try:
        run = run_command(
            plan.steps,
            root,
            progress=progress,
            cancel=cancel,
            log_path=result.log_path,
            consent_granted=consent_granted or not needed,
            config=cfg,
        )
    except BuildCancelledError:
        result.cancelled = True
        result.errors.append("Recompilation was cancelled by the user.")
        result.duration_seconds = time.time() - started
        return result
    except EceError as exc:
        result.errors.append(str(exc))
        result.duration_seconds = time.time() - started
        return result

    result.duration_seconds = time.time() - started
    result.warnings.extend(run.warnings)
    result.notes.extend(run.notes)
    if not run.ok:
        result.ok = False
        result.errors.extend(run.errors or [f"A build step failed with exit code {run.exit_code}."])
        for step in run.step_results:
            if not step.ok:
                result.errors.append(f"{step.title}: exit code {step.exit_code}")
                result.errors.extend(step.error_lines[-10:])
        return result

    artifacts = _collect_artifacts(output_dir, started)
    if not artifacts:
        artifacts = _collect_artifacts(root, started)[:50]
    # Python compileall writes .pyc next to the sources, not into output_dir.
    if plan.kind == "python" and not build_exe:
        artifacts = [p for p in _collect_artifacts(root, started) if p.suffix == ".pyc"][:50]
    result.artifacts = artifacts
    result.ok = bool(artifacts)
    if not result.ok:
        result.errors.append(
            "The commands succeeded but no new artifact was found. The build did "
            "not produce verifiable output."
        )
    for note in plan.notes:
        if note not in result.notes:
            result.notes.append(note)
    result.notes.append(
        "The produced binary is a new build from recovered source; original "
        "signatures, metadata and binary layout are not preserved."
    )
    return result
