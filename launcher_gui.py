"""PyInstaller entry point for the desktop application.

This is a thin launcher so a frozen single-file executable has one obvious
start function. When running from the source tree it adds ``src`` to the import
path; when frozen, the package is already bundled inside the executable.
"""

from __future__ import annotations

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):  # running from source, not from a .exe
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main() -> int:
    from ece_autocompiler.ui import run_app

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
