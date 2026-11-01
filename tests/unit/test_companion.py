"""Companion EXE bundler: plan/source validation without building."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler.build.companion import (  # noqa: E402
    PROBE_ENV,
    CompanionRequest,
    _launcher_source,
    companion_plan,
)


class CompanionCliTests(unittest.TestCase):
    def test_bundle_plan_runs_nothing(self) -> None:
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from ece_autocompiler import cli

        tmp = Path(tempfile.mkdtemp(prefix="ece_bundle_cli_"))
        first = tmp / "one.exe"
        second = tmp / "two.exe"
        first.write_bytes(b"MZ")
        second.write_bytes(b"MZ")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([
                "bundle-exe", str(tmp / "combined.exe"),
                "--main", str(first), "--with", str(second), "--plan",
            ])
        self.assertEqual(code, 0, err.getvalue())
        text = out.getvalue()
        self.assertIn("combined.exe", text)
        self.assertIn("Plan only", text)
        self.assertFalse((tmp / "combined.exe").exists())
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


class CompanionPlanTests(unittest.TestCase):
    def test_plan_lists_main_and_companions(self) -> None:
        request = CompanionRequest(
            files=[Path("C:/apps/one.exe"), Path("C:/apps/two.exe")],
            output=Path("C:/apps/combined.exe"),
            main=Path("C:/apps/one.exe"),
            onefile=False,
            console=True,
        )
        text = "\n".join(companion_plan(request))
        self.assertIn("one.exe", text)
        self.assertIn("two.exe", text)
        self.assertIn("onedir", text)
        self.assertIn("Console: yes", text)

    def test_launcher_embeds_both_paths_and_probe(self) -> None:
        source = _launcher_source("main/app.exe", ["companions/00_a.exe", "companions/01_b.exe"])
        self.assertIn("main/app.exe", source)
        self.assertIn("companions/00_a.exe", source)
        self.assertIn("companions/01_b.exe", source)
        self.assertIn(PROBE_ENV, source)
        self.assertIn("subprocess.Popen", source)
        # The probe path must not launch the embedded programs.
        self.assertIn("return probe()", source)
        self.assertIn("_MEIPASS", source)

    def test_request_companions_excludes_main(self) -> None:
        main = Path(tempfile.gettempdir()) / "ece-main-test.exe"
        request = CompanionRequest(
            files=[main, Path(tempfile.gettempdir()) / "ece-other-test.exe"],
            output=Path(tempfile.gettempdir()) / "ece-combined-test.exe",
            main=main,
        )
        self.assertEqual(len(request.companions), 1)
        self.assertNotEqual(request.companions[0], main)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
