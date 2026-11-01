"""Discovery of optional decompilation tools.

Nothing here installs software on its own. ``install_ilspy`` is an explicit,
user-consented action exposed by the UI/CLI; the command is printed first and
uses an argv list (never a shell string).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..local_store import app_data_dir

#: ILSpy command-line version verified against the .NET 8 runtime used here.
ILSPY_VERSION = "9.1.0.7988"


def tool_dir() -> Path:
    return app_data_dir() / "tools"


def _candidates(name: str, extra: list[Path]) -> list[Path]:
    found: list[Path] = []
    for item in extra:
        if item and item.is_file():
            found.append(item)
    on_path = shutil.which(name)
    if on_path:
        found.append(Path(on_path))
    return found


def find_ilspy(explicit: str = "") -> str:
    """Return the ilspycmd executable, or an empty string when unavailable."""
    env = os.environ.get("ECE_ILSPYCMD", "")
    extras = [
        Path(explicit) if explicit else Path(),
        Path(env) if env else Path(),
        tool_dir() / "ilspycmd" / "ilspycmd.exe",
        Path.home() / ".dotnet" / "tools" / "ilspycmd.exe",
    ]
    for candidate in _candidates("ilspycmd", extras):
        return str(candidate)
    return ""


def find_javap(explicit: str = "") -> str:
    java_home = os.environ.get("JAVA_HOME", "")
    extras = [
        Path(explicit) if explicit else Path(),
        Path(java_home) / "bin" / "javap.exe" if java_home else Path(),
        Path(java_home) / "bin" / "javap" if java_home else Path(),
    ]
    for candidate in _candidates("javap", extras):
        return str(candidate)
    return ""


def find_objdump() -> str:
    return shutil.which("objdump") or shutil.which("llvm-objdump") or ""


def capstone_available() -> bool:
    try:
        import capstone  # noqa: F401
    except Exception:
        return False
    return True


def install_ilspy(*, progress=None) -> tuple[bool, str, str]:
    """Install ILSpy into the app data tools folder. Returns (ok, path, log)."""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        return False, "", "The .NET SDK ('dotnet') is not installed; ILSpy cannot be installed."
    destination = tool_dir() / "ilspycmd"
    destination.mkdir(parents=True, exist_ok=True)
    command = [
        dotnet, "tool", "install", "ilspycmd",
        "--version", ILSPY_VERSION,
        "--tool-path", str(destination),
    ]
    if progress:
        progress("Installing ILSpy: " + " ".join(command))
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=900,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, "", f"ILSpy installation failed: {exc}"
    output = (completed.stdout or "") + (completed.stderr or "")
    exe = destination / "ilspycmd.exe"
    if completed.returncode == 0 and exe.is_file():
        return True, str(exe), output
    return False, str(exe) if exe.is_file() else "", output or f"dotnet exited with {completed.returncode}"
