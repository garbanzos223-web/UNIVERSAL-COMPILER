"""Integration: a combined .exe really starts every embedded program together."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler.build.companion import CompanionRequest, build_companion_exe  # noqa: E402
from ece_autocompiler.build.exe_exporter import pyinstaller_available  # noqa: E402
from ece_autocompiler.config import AppConfig  # noqa: E402


@unittest.skipUnless(pyinstaller_available(), "PyInstaller is not installed")
class CompanionIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece_companion_it_"))
        self.home = Path(tempfile.mkdtemp(prefix="ece_companion_home_"))
        self.config = AppConfig(work_dir=self.home / "work", output_dir=self.home / "output")
        self.config.ensure_directories()

    def tearDown(self) -> None:
        for name in ("ece_flag_a.txt", "ece_flag_b.txt"):
            try:
                (Path(os.environ.get("TEMP", ".")) / name).unlink()
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def _build_probe_exe(self, name: str, flag: str) -> Path:
        script = self.tmp / f"{name}.py"
        script.write_text(
            "import os, pathlib, time\n"
            f"pathlib.Path(os.environ['TEMP'], {flag!r}).write_text('ran', encoding='utf-8')\n"
            "time.sleep(2)\n",
            encoding="utf-8",
        )
        dist = self.tmp / "dist"
        command = [
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--onefile", "--console", "--name", name,
            "--distpath", str(dist),
            "--workpath", str(self.tmp / f"work_{name}"),
            "--specpath", str(self.tmp / f"spec_{name}"),
            str(script),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=600)
        exe = dist / f"{name}.exe"
        self.assertTrue(exe.is_file(), completed.stdout[-2000:] + completed.stderr[-2000:])
        return exe

    def test_both_embedded_programs_run_simultaneously(self) -> None:
        first = self._build_probe_exe("probe_a", "ece_flag_a.txt")
        second = self._build_probe_exe("probe_b", "ece_flag_b.txt")
        output = self.tmp / "combined.exe"
        request = CompanionRequest(files=[first, second], output=output, main=None, console=True)
        result = build_companion_exe(request, config=self.config)
        self.assertTrue(result.ok, result.summary_lines())
        self.assertTrue(Path(result.output or output).is_file())
        self.assertTrue(result.probe_output)
        started = time.time()
        completed = subprocess.run(
            [str(result.output)], capture_output=True, text=True, timeout=120
        )
        duration = time.time() - started
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        a = Path(os.environ.get("TEMP", ".")) / "ece_flag_a.txt"
        b = Path(os.environ.get("TEMP", ".")) / "ece_flag_b.txt"
        self.assertTrue(a.is_file(), "the first embedded program never ran")
        self.assertTrue(b.is_file(), "the second embedded program never ran")
        # Both sleep 2 s and must run concurrently, not one after the other.
        self.assertLess(duration, 3.8, f"programs appear to have run sequentially ({duration:.1f}s)")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
