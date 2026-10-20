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
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters.base import BuildPlanStep
from ..config import AppConfig
from ..detection import scan
from ..detection.toolchains import get_tool
from ..logging_config import get_logger
from .process import parallel_jobs, run_command, RunResult
from .workdirs import new_work_dir, sweep_work_dirs

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
    # "What to do next": concrete steps the user can act on. Every failure
    # path fills this in (see failure_lines), never blames the user, and
    # never leaves them at a dead end.
    actions: list[str] = field(default_factory=list)
    # Language / adapter that produced (or would produce) the executable.
    language: str | None = None
    adapter_id: str | None = None

    def failure_lines(self) -> list[str]:
        """Render the failure as what happened / why / what to do.

        Three labelled parts, always, in this order. The CLI prints these
        lines verbatim; the GUI renders the same fields as its own widgets.
        """
        lines = [f"What happened: {self.message or 'the export failed'}"]
        if self.errors:
            lines.append("Why:")
            lines.extend(f"  - {e}" for e in self.errors[:8])
        lines.append("What to do:")
        actions = self.actions or [
            "Open the build log for the exact error, fix it and try again."
        ]
        lines.extend(f"  - {a}" for a in actions[:6])
        if self.log_path:
            lines.append(f"Log: {self.log_path}")
        return lines


def pyinstaller_interpreter() -> str | None:
    """The Python interpreter that hosts PyInstaller, or None if there is none.

    Normally that is the interpreter running this code. In a standalone build
    ``sys.executable`` is the app's own .exe, which cannot host PyInstaller, so
    a Python installed on this PC (PATH or the ``py`` launcher) is used
    instead. If there is none, the honest answer is "cannot build here" - never
    a bare "PyInstaller is not installed" about a machine that never got asked.
    """
    if not getattr(sys, "frozen", False):
        return sys.executable
    for candidate in ("py", "python", "python3"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def pyinstaller_available() -> tuple[bool, str]:
    """Return (available, version-or-reason)."""
    import subprocess

    interpreter = pyinstaller_interpreter()
    if interpreter is None:
        return False, (
            "This is a standalone build and no Python interpreter was found on "
            "PATH to host PyInstaller"
        )
    try:
        proc = subprocess.run(
            [interpreter, "-m", "PyInstaller", "--version"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"PyInstaller could not be run: {exc}"
    if proc.returncode != 0:
        return False, "PyInstaller is not installed (pip install pyinstaller)"
    return True, (proc.stdout or "").strip() or "unknown"


def _pyinstaller_fix_actions() -> list[str]:
    """Concrete next steps when PyInstaller cannot be used on this PC."""
    if pyinstaller_interpreter() is None:
        return [
            "Install Python (https://www.python.org/downloads/windows/), then "
            "run the export again.",
            "Or package the project as .ece - that needs no interpreter.",
        ]
    return [
        "Install PyInstaller:  python -m pip install pyinstaller",
        "Then export the .exe again.",
    ]


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
            ok=False, exe_path=None, message=f"Project path does not exist: {project}",
            actions=[
                "Check that the folder still exists, then export again.",
                "If it was moved or renamed, select the project folder again.",
            ],
        )

    available, info = pyinstaller_available()
    if not available:
        return ExeExportResult(
            ok=False, exe_path=None, message=info,
            actions=_pyinstaller_fix_actions(),
        )

    entry_path = Path(entry).resolve() if entry else find_entry_point(project)
    if entry_path is None or not entry_path.is_file():
        return ExeExportResult(
            ok=False,
            exe_path=None,
            message="Could not find a Python entry point (main.py, app.py, ...). "
            "Pass one explicitly.",
            entry=None,
            actions=[
                "Put the program's entry file in the project folder "
                "(main.py, app.py, run.py, ...).",
                "Or point to it directly: --entry <file.py> on the CLI.",
            ],
        )

    exe_name = name or project.name or "application"
    out_path = Path(output) if output else (cfg.output_dir / f"{exe_name}.exe")

    # The interpreter that hosts PyInstaller: this one from source, or a real
    # Python on PATH when this is a standalone build (see
    # pyinstaller_interpreter). Without it there is nothing to run.
    interpreter = pyinstaller_interpreter()
    if interpreter is None:
        return ExeExportResult(
            ok=False, exe_path=None, language="python", adapter_id="python",
            message=(
                "This is a standalone build and no Python interpreter was found "
                "on PATH to host PyInstaller."
            ),
            actions=[
                "Install Python (https://www.python.org/downloads/windows/), "
                "then run the export again.",
                "Or package the project as .ece - that needs no interpreter.",
            ],
        )

    # Unique per build: same-second exports must not share a workdir, a
    # spec file or a log (the old ``exe_<epoch>`` naming did exactly that).
    sweep_work_dirs(cfg)
    work = new_work_dir(cfg)
    dist_dir = work / "dist"

    argv: list[str] = [
        interpreter,
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
    log_path = cfg.work_dir / "logs" / f"{work.name}.log"
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
    # The workdir is deliberately kept: after a failure it is the only
    # evidence of what happened. sweep_work_dirs bounds how many of these
    # diagnosis workdirs accumulate across builds.
    return ExeExportResult(
        ok=False,
        exe_path=None,
        message="Executable export failed",
        entry=entry_path,
        exit_code=run.exit_code,
        duration_seconds=run.duration_seconds,
        log_path=log_path,
        errors=errors,
        notes=[f"Build files kept for diagnosis: {work}"],
        actions=[
            "Open the build log above; its last error lines name the exact problem.",
            "Fix that issue in the project and export again.",
            "If an antivirus is installed, allow the "
            "%LOCALAPPDATA%\\ECE-Universal-AutoCompiler folder, then retry.",
        ],
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


@dataclass
class ExeReadiness:
    """Whether a detected project can be compiled to a Windows .exe on THIS PC.

    Detection only: nothing is built and nothing is downloaded. ``missing``
    are facts (tools not found); ``actions`` are the install steps. The answer
    is deliberately allowed to be "no" - claiming a build that cannot happen
    is the one thing this tool never does.
    """

    adapter_id: str
    possible: bool
    summary: str
    missing: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "possible": self.possible,
            "summary": self.summary,
            "missing": list(self.missing),
            "actions": list(self.actions),
        }


def exe_readiness(adapter_id: str) -> ExeReadiness:
    """Report .exe export readiness for one detected adapter."""
    if adapter_id == "python":
        available, info = pyinstaller_available()
        if available:
            return ExeReadiness(
                adapter_id, True, f"ready - PyInstaller {info} detected."
            )
        has_interpreter = pyinstaller_interpreter() is not None
        return ExeReadiness(
            adapter_id, False, f"cannot build .exe yet - {info}",
            missing=["PyInstaller"] if has_interpreter else ["Python interpreter"],
            actions=_pyinstaller_fix_actions(),
        )

    if adapter_id == "node":
        return ExeReadiness(
            adapter_id, False,
            "cannot build .exe here - it needs a bundler (pkg or nexe).",
            missing=["pkg or nexe"],
            actions=[
                "Install a bundler:  npm install -g pkg",
                "Or package the project as .ece - that needs no bundler.",
            ],
        )

    if adapter_id in ("unity", "unreal"):
        return ExeReadiness(
            adapter_id, False,
            "cannot build .exe here - engine projects are built by their own editor.",
            missing=[adapter_id],
            actions=[
                "Open the project in its editor and use its Windows x64 build menu.",
                "This tool detects the project and reports what is missing.",
            ],
        )

    tools = _NATIVE_TOOLS.get(adapter_id)
    if tools is not None:
        missing = _missing_tools(tools)
        hint = _INSTALL_HINTS.get(adapter_id, f"Install {', '.join(missing)}.")
        if not missing:
            return ExeReadiness(adapter_id, True, "ready - required toolchain found.")
        return ExeReadiness(
            adapter_id, False,
            f"cannot build .exe - missing {', '.join(missing)}.",
            missing=missing,
            actions=[hint],
        )

    return ExeReadiness(
        adapter_id, False,
        "no .exe export path for this project type yet - package it as .ece instead.",
    )


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


def _run_steps(steps, workdir, *, timeout, progress, cancel, config,
               log_name: str) -> RunResult:
    log_path = config.work_dir / "logs" / f"{log_name}.log"
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
            ok=False, exe_path=None, message=f"Project path does not exist: {project}",
            actions=[
                "Check that the folder still exists, then export again.",
                "If it was moved or renamed, select the project folder again.",
            ],
        )

    # 1. Detect the language.
    try:
        result = scan(project)
    except Exception as exc:  # noqa: BLE001 - surface as an honest failure
        return ExeExportResult(
            ok=False, exe_path=None, message=f"Detection failed: {exc}",
            actions=[
                "Check that the folder contains a recognized project "
                "(Python, Node.js, C#, Rust, Go, C/C++).",
                "'analyze <folder>' on the CLI lists everything it can see.",
            ],
        )
    best = result.best
    if best is None:
        return ExeExportResult(
            ok=False, exe_path=None,
            message="No supported project was detected in this directory.",
            actions=[
                "Select the folder that directly contains the project files "
                "(package.json, Cargo.toml, .csproj, main.py, CMakeLists.txt...).",
                "'analyze <folder>' shows what was found where.",
            ],
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
                    "(pkg or nexe).",
            actions=[
                "Install a bundler:  npm install -g pkg",
                "Then run the export again.",
                "Or use 'build' to package the project as .ece - no bundler needed.",
            ],
            notes=["Detected Node.js. Use 'build' to package it as .ece."],
        )

    if adapter_id in ("unity", "unreal"):
        return ExeExportResult(
            ok=False, exe_path=None, language=label, adapter_id=adapter_id,
            message=f"{label} projects are built inside their engine/editor, not here.",
            actions=[
                f"Open the project in the {label} editor and use its own "
                "build/export menu for Windows x64.",
                "This tool detects the project and reports the missing engine; "
                "it cannot produce the .exe itself.",
            ],
            notes=["This tool detects the project and reports the missing engine; it "
                   "cannot produce an .exe by itself."],
        )

    if adapter_id not in _NATIVE_TOOLS:
        return ExeExportResult(
            ok=False, exe_path=None, language=label, adapter_id=adapter_id,
            message=f"No .exe build strategy for '{label}' yet.",
            actions=[
                "'build' still packages this project as a .ece container.",
            ],
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
        # Hints are keyed by adapter (the tool name alone - "cargo" - would
        # miss the useful "Install Rust (https://rustup.rs)." line).
        hint = _INSTALL_HINTS.get(adapter_id, f"Install {', '.join(missing)}.")
        return ExeExportResult(
            ok=False, exe_path=None, language=label, adapter_id=adapter_id,
            message=f"Cannot build a {label} .exe: missing {', '.join(missing)}.",
            errors=[f"{m} was not found on this PC." for m in missing],
            actions=[hint, "Run the export again after installing."],
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
    # Unique per build; see the note in export_exe about same-second runs.
    sweep_work_dirs(config)
    work = new_work_dir(config)
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
        _add("cmake-build", "cmake --build (Release, parallel)",
             [get_tool("cmake").path or "cmake", "--build", str(build_dir),
              "--config", "Release", "--parallel", parallel_jobs()])
        target_dir = build_dir
    elif adapter_id == "cpp-make":
        _add("make-build", "make (Release, parallel)",
             [get_tool("make").path or "make", "-j", parallel_jobs(), "RELEASE=1"], wd=project)
        target_dir = project
    else:  # pragma: no cover - guarded by _NATIVE_TOOLS
        return ExeExportResult(ok=False, exe_path=None, language=label,
                               adapter_id=adapter_id, message="Unsupported strategy.")

    if progress:
        progress(f"Compiling {label} into an executable", 0, 100)
    run = _run_steps(steps, project, timeout=timeout, progress=progress,
                     cancel=cancel, config=config, log_name=work.name)

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

    # Kept for diagnosis, same as the Python path above.
    return ExeExportResult(
        ok=False, exe_path=None, message=f"{label} .exe build failed.",
        exit_code=run.exit_code, duration_seconds=run.duration_seconds,
        language=label, adapter_id=adapter_id,
        errors=list(run.errors) or [f"The {label} toolchain did not produce an executable."],
        notes=[f"Build files kept for diagnosis: {work}"],
        actions=[
            "Open the build log; its last error lines name the exact problem.",
            "Fix that issue in the project and export again.",
            "If an antivirus is installed, allow the "
            "%LOCALAPPDATA%\\ECE-Universal-AutoCompiler folder, then retry.",
        ],
    )
