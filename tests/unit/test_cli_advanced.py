"""CLI contract for the package, optimize and repair commands.

The three commands are checked through the real entry point, so the exit
codes, the JSON shapes and the human text are pinned here.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler import cli  # noqa: E402
from ece_autocompiler.config import AppConfig  # noqa: E402


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


@contextmanager
def temp_config(tmp: Path):
    """Give the CLI a configuration that writes inside the test folder."""
    cfg = AppConfig(output_dir=tmp / "out", work_dir=tmp / "work")
    cfg.ensure_directories()
    with mock.patch.object(cli, "AppConfig", return_value=cfg):
        yield cfg


class PackageCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "proj"
        self.project.mkdir()
        (self.project / "app.py").write_text("print('hi')\n", encoding="utf-8")
        self.out = self.tmp / "out"

    def test_package_creates_a_verified_archive(self) -> None:
        with temp_config(self.tmp):
            code, out, _ = run_cli(
                "package", str(self.project), "--output-dir", str(self.out),
                "--name", "Demo", "--version", "1.0.0",
            )
        self.assertEqual(code, cli.EXIT_OK, msg=out)
        self.assertIn("Validation : OK", out)
        packages = list(self.out.glob("*.zip"))
        self.assertEqual(len(packages), 1)

    def test_package_json_output_has_the_documented_fields(self) -> None:
        with temp_config(self.tmp):
            code, out, _ = run_cli(
                "package", str(self.project), "--output-dir", str(self.out), "--json",
            )
        self.assertEqual(code, cli.EXIT_OK, msg=out)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["format"], "zip")
        self.assertTrue(payload["sha256"])
        self.assertTrue(payload["validated"])
        self.assertIn("manifest", payload)

    def test_unknown_format_fails_with_the_supported_list(self) -> None:
        with temp_config(self.tmp):
            code, out, _ = run_cli(
                "package", str(self.project), "--output-dir", str(self.out), "--format", "rar",
            )
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("not a supported package format", out)

    def test_missing_project_is_an_error(self) -> None:
        with temp_config(self.tmp):
            code, out, _ = run_cli("package", str(self.tmp / "nope"))
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("does not exist", out)


class OptimizeCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "proj"
        self.project.mkdir()
        (self.project / "app.py").write_text("print('hi')\n", encoding="utf-8")

    def test_interpreter_projects_are_reported_as_unsupported(self) -> None:
        with temp_config(self.tmp):
            code, out, _ = run_cli("optimize", str(self.project), "--profile", "release")
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("cannot be optimized", out)
        self.assertIn("interpreter", out)

    def test_missing_project_is_an_error(self) -> None:
        with temp_config(self.tmp):
            code, out, _ = run_cli("optimize", str(self.tmp / "nope"))
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("does not exist", out)


class RepairCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "proj"
        self.project.mkdir()
        self.source = self.project / "app.py"
        self.source.write_text("if True\n    print(1)\n", encoding="utf-8")

    def test_dry_run_reports_the_proposal_without_changing_the_file(self) -> None:
        original = self.source.read_text(encoding="utf-8")
        with temp_config(self.tmp):
            code, out, _ = run_cli("repair", str(self.project))
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("missing ':'", out)
        self.assertIn("Nothing was changed", out)
        self.assertEqual(self.source.read_text(encoding="utf-8"), original)

    def test_apply_repairs_the_project(self) -> None:
        with temp_config(self.tmp):
            code, out, _ = run_cli("repair", str(self.project), "--apply")
        self.assertEqual(code, cli.EXIT_OK, msg=out)
        self.assertIn("build succeeds", out)
        self.assertIn("if True:", self.source.read_text(encoding="utf-8"))

    def test_json_output_carries_the_attempts(self) -> None:
        with temp_config(self.tmp):
            code, out, _ = run_cli("repair", str(self.project), "--apply", "--json")
        self.assertEqual(code, cli.EXIT_OK, msg=out)
        payload = json.loads(out)
        self.assertTrue(payload["final_ok"])
        self.assertTrue(payload["attempts"])
        self.assertEqual(payload["attempts"][0]["verification"], "resolved")

    def test_missing_project_is_an_error(self) -> None:
        with temp_config(self.tmp):
            code, out, _ = run_cli("repair", str(self.tmp / "nope"))
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("does not exist", out)


class BuildProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "proj"
        self.project.mkdir()
        (self.project / "app.py").write_text("print('hi')\n", encoding="utf-8")

    def test_unsupported_profile_stops_the_build_with_an_explanation(self) -> None:
        with temp_config(self.tmp):
            code, out, _ = run_cli(
                "build", str(self.project), "--profile", "release",
                "-o", str(self.tmp / "out.ece"),
            )
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("cannot be optimized", out)
        self.assertIn("interpreter", out)

    def test_default_profile_builds_as_before(self) -> None:
        with temp_config(self.tmp):
            code, out, _ = run_cli(
                "build", str(self.project), "--profile", "none",
                "-o", str(self.tmp / "out.ece"), "--consent",
            )
        self.assertEqual(code, cli.EXIT_OK, msg=out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
