"""PyInstaller entry point for the desktop application.

This is a thin launcher so a frozen single-file executable has one obvious
start function. When running from the source tree it adds ``src`` to the import
path; when frozen, the package is already bundled inside the executable.

When frozen (windowed builds have no console) crashes are additionally recorded
to ``%TEMP%\\ece-autocompiler-crash.log`` so failures can be diagnosed instead of
vanishing silently.
"""

from __future__ import annotations

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):  # running from source, not from a .exe
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def _install_crash_log() -> None:
    """Record unhandled exceptions and fatal errors to a file (frozen only)."""
    import faulthandler
    import os
    import traceback

    base = os.environ.get("TEMP") or os.environ.get("TMP") or "."
    try:
        crash_file = open(Path(base) / "ece-autocompiler-crash.log", "a", encoding="utf-8")
    except OSError:
        return

    faulthandler.enable(file=crash_file, all_threads=True)

    def _hook(exc_type: type, exc: BaseException, tb) -> None:
        try:
            crash_file.write("".join(traceback.format_exception(exc_type, exc, tb)))
            crash_file.flush()
        except Exception:
            pass

    sys.excepthook = _hook


def main() -> int:
    if getattr(sys, "frozen", False):
        _install_crash_log()

    from ece_autocompiler.ui import run_app

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
