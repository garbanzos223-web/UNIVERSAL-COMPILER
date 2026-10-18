"""Standalone executable (``.exe``) export for Windows x64.

This is the "turn my code into an .exe" path. It scans the project, detects
its language and drives that language's own toolchain:

* Python  -> PyInstaller (``export_exe`` below), ``--onefile`` or ``--onedir``;
* C#/.NET -> ``dotnet publish -c Release -r win-x64 --self-contained``
  (``PublishSingleFile`` when single-file mode is chosen);
* Rust    -> ``cargo build --release``;
* Go      -> ``go build -o app.exe``;
* C/C++   -> CMake or Make, then the produced ``.exe`` is located.

External tools are always invoked as argv lists, never shell strings, and only
after an honest "is it installed" check with an install hint. Node.js needs a
bundler (pkg/nexe) and game engines build their own projects; both are reported
instead of faked.
"""

from __future__ import annotations

import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters.base import BuildPlanStep
from ..config import AppConfig
from ..detection import scan
from ..detection.toolchains import get_tool
from ..logging_config import get_logger
from .process import run_command, RunResult

log = get_logger("build.exe")

# Conventional Python entry-point file names, in priority order.
ENTRY_CANDIDATES: tuple[str, ...] = (
    "main.py",
    "app.py",
    "run.py",
    "__main__.py",
    "cli.py",
)


@dataclass
class ExeExportResult:
    ok: bool
    exe_path: Path | None
    message: str = ""
    entry: Path | None = None
    exit_code: int = -1
    duration_seconds: float = 0.0
    log_path: Path | None = None
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Language / adapter that produced (or would produce) the executable.
    language: str | None = None
    adapter_id: str | None = None


def pyinstaller_available() -> tuple[bool, str]:
    """Return (available, version-or-reason)."""
    import subprocess

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"PyInstaller could not be run: {exc}"
    if proc.returncode != 0:
        return False, "PyInstaller is not installed (pip install pyinstaller)"
    return True, (proc.stdout or "").strip() or "unknown"


def find_entry_point(project: Path) -> Path | None:
    """Best-effort discovery of the executable's entry-point file."""
    project = Path(project)
    if project.is_file() and project.suffix.lower() == ".py":
        return project
    if not project.is_dir():
        return None
    for name in ENTRY_CANDIDATES:
        p = project / name
        if p.is_file():
            return p
    # A package directory that matches the project name and has __main__.py.
    pkg = project / project.name / "__main__.py"
    if pkg.is_file():
        return pkg
    # A single top-level .py file, if that is all there is.
    top = [p for p in project.glob("*.py") if p.is_file()]
    if len(top) == 1:
        return top[0]
    return None


def export_exe(
    project: Path | str,
    *,
    output: Path | str | None = None,
    name: str | None = None,
    entry: Path | str | None = None,
    onefile: bool = True,
    console: bool = False,
    timeout: int = 1800,
    progress=None,
    cancel=None,
    config: AppConfig | None = None,
) -> ExeExportResult:
    """Bundle *project* into a Windows executable with PyInstaller."""
    cfg = config or AppConfig()
    cfg.ensure_directories()
    project = Path(project).resolve()
    if not project.exists():
        return ExeExportResult(
            ok=False, exe_path=None, message=f"Project path does not exist: {project}"
        )

    available, info = pyinstaller_available()
    if not available:
        return ExeExportResult(ok=False, exe_path=None, message=info)

    entry_path = Path(entry).resolve() if entry else find_entry_point(project)
    if entry_path is None or not entry_path.is_file():
        return ExeExportResult(
            ok=False,
            exe_path=None,
            message="Could not find a Python entry point (main.py, app.py, ...). "
            "Pass one explicitly.",
            entry=None,
        )

    exe_name = name or project.name or "application"
    out_path = Path(output) if output else (cfg.output_dir / f"{exe_name}.exe")

    work = cfg.work_dir / f"exe_{int(time.time())}"
    dist_dir = work / "dist"
    work.mkdir(parents=True, exist_ok=True)

    argv: list[str] = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile" if onefile else "--onedir",
        "--windowed" if not console else "--console",
        "--name",
        exe_name,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(work / "build"),
        "--specpath",
        str(work),
        str(entry_path),
    ]

    if progress:
        progress(f"Packaging {entry_path.name} into a standalone .exe", 0, 100)

    step = BuildPlanStep(
        id="pyinstaller",
        title=f"PyInstaller ({'onefile' if onefile else 'onedir'})",
        cmd_args=argv,
        workdir=project,
        timeout=timeout,
        requires_approval=False,
    )
    log_path = cfg.work_dir / "logs" / f"exe_{int(time.time())}.log"
    run = run_command(
        steps=[step],
        workdir=project,
        progress=progress,
        cancel=cancel,
        log_path=log_path,
        config=cfg,
    )

    # Locate the produced executable.
    produced = dist_dir / f"{exe_name}.exe"
    if not produced.is_file():
        nested = dist_dir / exe_name / f"{exe_name}.exe"
        if nested.is_file():
            produced = nested
    if run.ok and produced.is_file():
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if onefile:
            shutil.move(str(produced), str(out_path))
        else:
            # Folder mode: the executable needs its DLLs/helpers next to it,
            # so the whole bundle folder is moved, not just the .exe. The
            # executable inside takes the requested output name.
            bundle_dir = produced.parent
            target_dir = out_path.parent / out_path.stem
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            shutil.move(str(bundle_dir), str(target_dir))
            final_exe = target_dir / f"{out_path.stem}.exe"
            if produced.name != final_exe.name:
                (target_dir / produced.name).rename(final_exe)
            out_path = final_exe
        shutil.rmtree(work, ignore_errors=True)
        notes = [f"Entry point: {entry_path.relative_to(project).as_posix()}"]
        if not console:
            notes.append("Windowed app: no console is attached.")
        if not onefile:
            notes.append(
                f"Folder mode: keep '{out_path.parent.name}\\' together with the executable."
            )
        return ExeExportResult(
            ok=True,
            exe_path=out_path,
            message="Executable created",
            entry=entry_path,
            exit_code=run.exit_code,
            duration_seconds=run.duration_seconds,
            log_path=log_path,
            notes=notes,
            language="python",
            adapter_id="python",
        )

    errors = list(run.errors) or ["PyInstaller failed to produce an executable."]
    shutil.rmtree(work, ignore_errors=True)
    return ExeExportResult(
        ok=False,
        exe_path=None,
        message="Executable export failed",
        entry=entry_path,
        exit_code=run.exit_code,
        duration_seconds=run.duration_seconds,
        log_path=log_path,
        errors=errors,
    )


# --- Multi-language executable compilation ---------------------------------
#
# ``compile_exe`` scans a project, detects its language, and builds a native
# executable with that language's own toolchain. It never fakes success: before
# running anything it checks the required tools and, if one is missing, returns
# an honest failure with an install hint.

# Tools each compiled language needs to produce a Windows executable.
_NATIVE_TOOLS: dict[str, tuple[str, ...]] = {
    "dotnet": ("dotnet",),
    "rust-cargo": ("cargo",),
    "go": ("go",),
    "cpp-cmake": ("cmake",),
    "cpp-make": ("make",),
}

_INSTALL_HINTS: dict[str, str] = {
    "dotnet": "Install the .NET SDK (https://dotnet.microsoft.com/download).",
    "rust-cargo": "Install Rust (https://rustup.rs).",
    "go": "Install Go (https://go.dev/dl/).",
    "cpp-cmake": "Install CMake and a C++ compiler (Visual Studio Build Tools).",
    "cpp-make": "Install Make and a C/C++ compiler (MinGW or Visual Studio Build Tools).",
    "node": "Install Node.js (https://nodejs.org/).",
}


def _missing_tools(tool_ids: tuple[str, ...]) -> list[str]:
    missing = []
    for tid in tool_ids:
        tool = get_tool(tid)
        if tool is None or not tool.found:
            missing.append(tid)
    return missing


def _find_cpp_compiler() -> str | None:
    """Return a C/C++ compiler executable name, or None if none is installed."""
    for cand in ("cl", "g++", "clang++", "gcc", "clang", "cc"):
        tool = get_tool(cand)
        if tool is not None and tool.found:
            return tool.path or cand
    return None


def _first_exe(directory: Path, prefer: str | None = None) -> Path | None:
    """Find the most likely executable under *directory*."""
    if not directory or not directory.is_dir():
        return None
    exes = [p for p in directory.rglob("*.exe") if p.is_file()]
    if not exes:
        # Some toolchains emit an extension-less binary; skip those for a
        # Windows .exe export.
        return None
    if prefer:
        for p in exes:
            if p.stem.lower() == prefer.lower():
                return p
    # Prefer the largest executable (the main binary, not helpers).
    return max(exes, key=lambda p: p.stat().st_size)


def _run_steps(steps, workdir, *, timeout, progress, cancel, config) -> RunResult:
    log_path = config.work_dir / "logs" / f"exe_{int(time.time())}.log"
    return run_command(
        steps=steps, workdir=workdir, progress=progress, cancel=cancel,
        log_path=log_path, config=config,
    )


def compile_exe(
    project: Path | str,
    *,
    output: Path | str | None = None,
    name: str | None = None,
    entry: Path | str | None = None,
    onefile: bool = True,
    console: bool = False,
    timeout: int = 1800,
    progress=None,
    cancel=None,
    config: AppConfig | None = None,
) -> ExeExportResult:
    """Detect a project's language and compile it into a Windows executable."""
    cfg = config or AppConfig()
    cfg.ensure_directories()
    project = Path(project).resolve()
    if not project.exists():
        return ExeExportResult(
            ok=False, exe_path=None, message=f"Project path does not exist: {project}"
        )

    # 1. Detect the language.
    try:
        result = scan(project)
    except Exception as exc:  # noqa: BLE001 - surface as an honest failure
        return ExeExportResult(ok=False, exe_path=None, message=f"Detection failed: {exc}")
    best = result.best
    if best is None:
        return ExeExportResult(
            ok=False, exe_path=None,
            message="No supported project was detected in this directory.",
        )

    adapter_id = best.adapter_id
    label = best.project_type
    exe_name = name or project.name or "application"

    # 2. Route to the right builder.
    if adapter_id == "python":
        res = export_exe(
            project, output=output, name=name, entry=entry, onefile=onefile,
            console=console, timeout=timeout, progress=progress, cancel=cancel,
            config=cfg,
        )
        res.language = label
        return res

    if adapter_id == "node":
        return ExeExportResult(
            ok=False, exe_path=None, language=label, adapter_id=adapter_id,
            message="Node.js .exe export needs a bundler that is not installed "
                    "(pkg or nexe). Install one with: npm install -g pkg. "
                    "The .ece build still works.",
            notes=["Detected Node.js. Use 'build' to package it as .ece."],
        )

    if adapter_id in ("unity", "unreal"):
        return ExeExportResult(
            ok=False, exe_path=None, language=label, adapter_id=adapter_id,
            message=f"{label} projects are built inside their engine/editor, not here.",
            notes=["This tool detects the project and reports the missing engine; it "
                   "cannot produce an .exe by itself."],
        )

    if adapter_id not in _NATIVE_TOOLS:
        return ExeExportResult(
            ok=False, exe_path=None, language=label, adapter_id=adapter_id,
            message=f"No .exe build strategy for '{label}' yet.",
        )

    # 3. Check the required tools before running anything.
    required = _NATIVE_TOOLS[adapter_id]
    missing = _missing_tools(required)
    cpp_compiler = None
    if adapter_id in ("cpp-cmake", "cpp-make"):
        cpp_compiler = _find_cpp_compiler()
        if cpp_compiler is None:
            missing.append("c++ compiler")
    if missing:
        hints = [_INSTALL_HINTS.get(m, f"Install '{m}'.") for m in missing]
        return ExeExportResult(
            ok=False, exe_path=None, language=label, adapter_id=adapter_id,
            message=f"Cannot build a {label} .exe: missing {', '.join(missing)}.",
            errors=hints,
            notes=["Toolchain detected honestly; nothing was built."],
        )

    out_path = Path(output) if output else (cfg.output_dir / f"{exe_name}.exe")
    return _compile_native(
        adapter_id, label, project, out_path, exe_name, cpp_compiler,
        onefile=onefile, timeout=timeout, progress=progress, cancel=cancel,
        config=cfg,
    )


def _compile_native(
    adapter_id, label, project, out_path, exe_name, cpp_compiler,
    *, onefile=True, timeout, progress, cancel, config,
) -> ExeExportResult:
    """Build a native executable for a compiled language and locate the result."""
    import time as _time

    work = config.work_dir / f"exe_{int(_time.time())}"
    work.mkdir(parents=True, exist_ok=True)
    steps: list[BuildPlanStep] = []

    def _add(sid, title, argv, wd=None):
        steps.append(BuildPlanStep(
            id=sid, title=title, cmd_args=argv, workdir=wd or project,
            timeout=timeout, requires_approval=False,
        ))

    exe_dir = out_path.parent
    prefer = out_path.stem

    if adapter_id == "dotnet":
        publish = work / "publish"
        argv = [get_tool("dotnet").path or "dotnet", "publish", str(project),
                "-c", "Release", "-r", "win-x64", "--self-contained", "true"]
        if onefile:
            argv += ["-p:PublishSingleFile=true"]
            title = "dotnet publish (Release, win-x64, self-contained, single file)"
        else:
            title = "dotnet publish (Release, win-x64, self-contained)"
        _add("dotnet-publish", title, argv + ["-o", str(publish)])
        target_dir = publish
    elif adapter_id == "rust-cargo":
        _add("cargo-build", "cargo build --release",
             [get_tool("cargo").path or "cargo", "build", "--release",
              "--manifest-path", str(project / "Cargo.toml")])
        target_dir = project / "target" / "release"
    elif adapter_id == "go":
        _add("go-build", "go build",
             [get_tool("go").path or "go", "build", "-o", str(out_path), "."])
        target_dir = out_path.parent
    elif adapter_id == "cpp-cmake":
        build_dir = work / "cmake-build"
        _add("cmake-configure", "cmake configure",
             [get_tool("cmake").path or "cmake", "-S", str(project), "-B", str(build_dir)])
        _add("cmake-build", "cmake --build (Release)",
             [get_tool("cmake").path or "cmake", "--build", str(build_dir), "--config", "Release"])
        target_dir = build_dir
    elif adapter_id == "cpp-make":
        _add("make-build", "make (Release)",
             [get_tool("make").path or "make", "RELEASE=1"], wd=project)
        target_dir = project
    else:  # pragma: no cover - guarded by _NATIVE_TOOLS
        return ExeExportResult(ok=False, exe_path=None, language=label,
                               adapter_id=adapter_id, message="Unsupported strategy.")

    if progress:
        progress(f"Compiling {label} into an executable", 0, 100)
    run = _run_steps(steps, project, timeout=timeout, progress=progress,
                     cancel=cancel, config=config)

    produced = None
    if adapter_id == "go" and out_path.is_file():
        produced = out_path
    else:
        produced = _first_exe(target_dir, prefer)

    if run.ok and produced is not None and produced.is_file():
        exe_dir.mkdir(parents=True, exist_ok=True)
        if produced.resolve() != out_path.resolve():
            shutil.copy2(str(produced), str(out_path))
        shutil.rmtree(work, ignore_errors=True)
        notes = [f"Built with the native {label} toolchain."]
        if not onefile and adapter_id != "dotnet":
            notes.append(
                "The single-file/folder choice does not apply here: this "
                "toolchain already produces one .exe."
            )
        return ExeExportResult(
            ok=True, exe_path=out_path, message="Executable created",
            exit_code=run.exit_code, duration_seconds=run.duration_seconds,
            language=label, adapter_id=adapter_id,
            notes=notes,
        )

    shutil.rmtree(work, ignore_errors=True)
    return ExeExportResult(
        ok=False, exe_path=None, message=f"{label} .exe build failed.",
        exit_code=run.exit_code, duration_seconds=run.duration_seconds,
        language=label, adapter_id=adapter_id,
        errors=list(run.errors) or [f"The {label} toolchain did not produce an executable."],
    )
