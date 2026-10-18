"""Application entry points.

``ece_autocompiler.app`` is the console entry point (same as the CLI);
``ece_autocompiler.ui`` hosts the desktop interface.
"""

from __future__ import annotations

import sys

from .cli import main


def run(argv: list[str] | None = None) -> int:
    return main(argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run(sys.argv[1:]))
