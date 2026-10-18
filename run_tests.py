"""Run the test suite without installing the package.

Usage:
    python run_tests.py            # unit + integration + GUI smoke
    python run_tests.py unit       # unit only
    python run_tests.py integration
    python run_tests.py smoke      # GUI smoke only (skips without a display)
    python run_tests.py -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"


def main(argv: list[str]) -> int:
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))

    verbosity = 2 if "-v" in argv else 1
    args = [a for a in argv if a != "-v"]

    if args and args[0] in ("unit", "integration", "smoke"):
        start_dir = str(ROOT / "tests" / args[0])
    else:
        start_dir = str(ROOT / "tests")

    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=start_dir, top_level_dir=str(ROOT))
    runner = unittest.TextTestRunner(verbosity=verbosity)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
