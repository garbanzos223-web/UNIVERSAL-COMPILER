"""PyInstaller entry point for the console (CLI) build.

The desktop build is windowed, so it has no console attached and nothing it
prints would ever be visible. This second entry point produces
``ECE-AutoCompiler-cli.exe``: the same product, driven from a terminal
(``ECE-AutoCompiler-cli.exe exe path\\to\\project``), with the CLI guarantees
intact - real exit codes, structured errors, no GUI window.

Build both with ``scripts\\build_exe.ps1``; ``tools\\release_check.py`` then
runs this build and checks what it actually printed.
"""

from __future__ import annotations

import sys
from pathlib import Path

if not getattr(sys, "frozen", False):  # running from source, not from a .exe
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))


def main() -> int:
    from ece_autocompiler.cli import main as cli_main

    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
