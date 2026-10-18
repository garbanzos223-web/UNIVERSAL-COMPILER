"""Standalone executable (``.exe``) export for Python projects.

This is the "turn my code into an .exe" path. It drives **PyInstaller** as a
subprocess (argv list, never a shell string) to bundle a Python project — and
its interpreter — into a single Windows executable.

Only Python projects are supported here. Other languages require their own
toolchain; those are reported by their adapters rather than faked.
"""

from __future__ import annotations

import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters.base import BuildPlanStep
from ..config import AppConfig
from ..logging_config import get_logger
from .process import run_command

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
        shutil.move(str(produced), str(out_path))
        shutil.rmtree(work, ignore_errors=True)
        notes = [f"Entry point: {entry_path.relative_to(project).as_posix()}"]
        if not console:
            notes.append("Windowed app: no console is attached.")
        return ExeExportResult(
            ok=True,
            exe_path=out_path,
            message="Executable created",
            entry=entry_path,
            exit_code=run.exit_code,
            duration_seconds=run.duration_seconds,
            log_path=log_path,
            notes=notes,
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
