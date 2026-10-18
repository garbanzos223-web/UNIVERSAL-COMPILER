"""Workdir hygiene: unique build dirs, bounded sweep, failure evidence.

The collision these guard against is real: the old ``exe_<epoch-second>``
naming meant two exports started in the same second shared one workdir,
spec file and log, and one build silently lost its output.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler.build.exe_exporter import export_exe  # noqa: E402
from ece_autocompiler.build.process import RunResult  # noqa: E402
from ece_autocompiler.build.workdirs import (  # noqa: E402
    new_work_dir,
    sweep_work_dirs,
)
from ece_autocompiler.config import AppConfig  # noqa: E402


def _touch_with_mtime(path: Path, mtime: float) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "marker.txt").write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


class NewWorkDirTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = AppConfig(
            output_dir=self.tmp / "out", work_dir=self.tmp / "work"
        )

    def test_two_calls_in_the_same_second_get_different_dirs(self) -> None:
        now = time.time()
        first = new_work_dir(self.cfg)
        second = new_work_dir(self.cfg)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir())
        self.assertTrue(second.is_dir())
        for d in (first, second):
            self.assertEqual(d.parent, self.cfg.work_dir)
            self.assertTrue(d.name.startswith("exe_"))
            self.assertEqual(list(d.iterdir()), [])  # fresh, not reused
        # Both started within the same wall-clock second: with epoch-based
        # names they would have been the very same directory.
        self.assertAlmostEqual(first.stat().st_ctime, now, delta=5)

    def test_real_appconfig_roundtrip(self) -> None:
        cfg = AppConfig(
            output_dir=self.tmp / "out2", work_dir=self.tmp / "work2"
        )
        cfg.ensure_directories()
        d = new_work_dir(cfg)
        self.assertTrue(d.is_dir())


class SweepWorkDirsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"
        self.work.mkdir(parents=True)
        self.cfg = SimpleNamespace(work_dir=self.work)

    def test_sweep_keeps_newest_workdirs_and_leaves_everything_else(self) -> None:
        now = time.time()
        for i in range(4):
            _touch_with_mtime(self.work / f"exe_{i}", now - 100 + i)
        history = self.work / "history.jsonl"
        history.write_text('{"ok": true}\n', encoding="utf-8")
        unrelated = self.work / "build_cache"
        unrelated.mkdir()

        removed = sweep_work_dirs(self.cfg, keep_workdirs=2)

        remaining = sorted(p.name for p in self.work.iterdir()
                           if p.is_dir() and p.name.startswith("exe_"))
        self.assertEqual(remaining, ["exe_2", "exe_3"])  # two newest kept
        self.assertEqual(sorted(removed), ["exe_0", "exe_1"])
        self.assertTrue(history.is_file())       # history is never touched
        self.assertTrue(unrelated.is_dir())      # non-exe_ dirs are ours only

    def test_sweep_keeps_newest_logs_and_ignores_other_files(self) -> None:
        now = time.time()
        log_dir = self.work / "logs"
        log_dir.mkdir()
        for i in range(8):
            f = log_dir / f"exe_{i}.log"
            f.write_text("log\n", encoding="utf-8")
            os.utime(f, (now - 50 + i, now - 50 + i))
        keepme = log_dir / "last_build.log"
        keepme.write_text("default log\n", encoding="utf-8")

        removed = sweep_work_dirs(self.cfg, keep_logs=3)

        # The budget counts every log, including the default last_build.log
        # (the newest here), so only the two newest exe logs survive the cut...
        remaining = sorted(p.name for p in log_dir.iterdir())
        self.assertEqual(remaining, ["exe_6.log", "exe_7.log", "last_build.log"])
        self.assertEqual(len(removed), 6)
        self.assertTrue(all(r.startswith("logs/") for r in removed))
        self.assertTrue(keepme.is_file())

    def test_sweep_on_missing_workdir_is_a_noop(self) -> None:
        cfg = SimpleNamespace(work_dir=self.tmp / "does-not-exist")
        self.assertEqual(sweep_work_dirs(cfg), [])

    def test_sweep_with_zero_keep_removes_all_workdirs(self) -> None:
        now = time.time()
        for i in range(3):
            _touch_with_mtime(self.work / f"exe_{i}", now - 10 + i)
        removed = sweep_work_dirs(self.cfg, keep_workdirs=0)
        self.assertEqual(sorted(removed), ["exe_0", "exe_1", "exe_2"])
        self.assertEqual(list(self.work.glob("exe_*")), [])


class FailedExportEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = AppConfig(
            output_dir=self.tmp / "out", work_dir=self.tmp / "work"
        )
        self.project = self.tmp / "proj"
        self.project.mkdir()
        (self.project / "main.py").write_text(
            "if __name__ == '__main__':\n    print('hi')\n", encoding="utf-8"
        )

    def _failing_export(self):
        with mock.patch(
            "ece_autocompiler.build.exe_exporter.pyinstaller_available",
            return_value=(True, "PyInstaller is available"),
        ), mock.patch(
            "ece_autocompiler.build.exe_exporter.run_command",
            return_value=RunResult(ok=False, exit_code=1,
                                   errors=["PyInstaller exploded"]),
        ):
            return export_exe(self.project, config=self.cfg, timeout=60)

    def test_failed_build_keeps_its_workdir_and_says_so(self) -> None:
        result = self._failing_export()
        self.assertFalse(result.ok)
        self.assertIn("PyInstaller exploded", result.errors)
        # The evidence stays on disk and the user is told where it is.
        leftovers = list(self.cfg.work_dir.glob("exe_*"))
        self.assertEqual(len(leftovers), 1)
        self.assertTrue(leftovers[0].is_dir())
        self.assertTrue(any(
            n.startswith("Build files kept for diagnosis")
            for n in result.notes
        ))

    def test_two_same_second_failures_do_not_share_a_workdir(self) -> None:
        first = self._failing_export()
        second = self._failing_export()
        self.assertFalse(first.ok)
        self.assertFalse(second.ok)
        leftovers = sorted(p.name for p in self.cfg.work_dir.glob("exe_*"))
        self.assertEqual(len(leftovers), 2)
        self.assertNotEqual(leftovers[0], leftovers[1])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
