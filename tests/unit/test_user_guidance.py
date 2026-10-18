"""User-facing failures follow the what / why / what-to-do contract.

Every export failure must tell the user (1) what happened, (2) why, and
(3) what to do next - in the CLI, in the GUI, and from the same result
object. These tests pin that contract on every real failure path.
"""

from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler import cli  # noqa: E402
from ece_autocompiler.build.exe_exporter import (  # noqa: E402
    ExeExportResult,
    compile_exe,
    export_exe,
)
from ece_autocompiler.build.process import RunResult  # noqa: E402


class _FailureContractMixin:
    """Shared Bloque-3 assertion: every failure follows the contract."""

    def _assert_contract(self, result: ExeExportResult) -> None:
        self.assertFalse(result.ok)
        self.assertTrue(result.message, "every failure explains what happened")
        self.assertTrue(result.actions, "every failure says what to do next")
        lines = result.failure_lines()
        self.assertTrue(lines[0].startswith("What happened:"))
        self.assertIn("What to do:", lines)
        # No step is empty and nothing blames the user.
        for action in result.actions:
            self.assertTrue(action.strip())
            self.assertFalse(action.lower().startswith("you "))


class ExportFailureGuidanceTests(_FailureContractMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "proj"
        self.project.mkdir()
        (self.project / "main.py").write_text(
            "if __name__ == '__main__':\n    print('hi')\n", encoding="utf-8"
        )

    def test_missing_pyinstaller_advises_install(self) -> None:
        with mock.patch(
            "ece_autocompiler.build.exe_exporter.pyinstaller_available",
            return_value=(False, "PyInstaller is not installed (pip install pyinstaller)"),
        ):
            result = export_exe(self.project)
        self._assert_contract(result)
        self.assertTrue(any("pyinstaller" in a.lower() for a in result.actions))

    def test_missing_entry_point_names_candidates_and_flag(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        with mock.patch(
            "ece_autocompiler.build.exe_exporter.pyinstaller_available",
            return_value=(True, "6.0"),
        ):
            result = export_exe(empty)
        self._assert_contract(result)
        self.assertTrue(any("main.py" in a for a in result.actions))
        self.assertTrue(any("--entry" in a for a in result.actions))

    def test_missing_project_path_advises(self) -> None:
        result = export_exe(self.tmp / "nowhere")
        self._assert_contract(result)

    def test_failed_build_keeps_why_and_next_steps(self) -> None:
        with mock.patch(
            "ece_autocompiler.build.exe_exporter.pyinstaller_available",
            return_value=(True, "6.0"),
        ), mock.patch(
            "ece_autocompiler.build.exe_exporter.run_command",
            return_value=RunResult(ok=False, exit_code=1,
                                   errors=["SyntaxError: bad.py line 3"]),
        ):
            result = export_exe(self.project)
        self._assert_contract(result)
        lines = result.failure_lines()
        self.assertIn("Why:", lines)
        self.assertIn("  - SyntaxError: bad.py line 3", lines)
        self.assertTrue(any(line.startswith("Log: ") for line in lines))
        self.assertTrue(any("log" in a.lower() for a in result.actions))


class DetectionFailureGuidanceTests(_FailureContractMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "proj"
        self.project.mkdir()

    def test_no_project_detected_advises_and_mentions_analyze(self) -> None:
        result = compile_exe(self.project)
        self.assertFalse(result.ok)
        self.assertTrue(result.actions)
        self.assertTrue(any("analyze" in a for a in result.actions))

    def test_missing_toolchain_lists_facts_and_install_steps(self) -> None:
        best = mock.MagicMock()
        best.adapter_id = "rust-cargo"
        best.project_type = "Rust"
        fake_scan = mock.MagicMock()
        fake_scan.best = best
        with mock.patch(
            "ece_autocompiler.build.exe_exporter.scan", return_value=fake_scan
        ), mock.patch(
            "ece_autocompiler.build.exe_exporter._missing_tools",
            return_value=["cargo"],
        ):
            result = compile_exe(self.project)
        self._assert_contract(result)
        # Why = facts; what to do = install steps.
        self.assertEqual(result.errors, ["cargo was not found on this PC."])
        self.assertTrue(result.actions[0].startswith("Install"))
        # The hint is keyed by adapter, so it names Rust and where to get it.
        self.assertTrue(any("rustup.rs" in a for a in result.actions))
        self.assertIn("Run the export again after installing.", result.actions)
        self.assertTrue(any("honestly" in n for n in result.notes))

    def test_node_project_explains_the_bundler_route(self) -> None:
        best = mock.MagicMock()
        best.adapter_id = "node"
        best.project_type = "Node.js"
        fake_scan = mock.MagicMock()
        fake_scan.best = best
        with mock.patch(
            "ece_autocompiler.build.exe_exporter.scan", return_value=fake_scan
        ):
            result = compile_exe(self.project)
        self._assert_contract(result)
        self.assertTrue(any("npm install -g pkg" in a for a in result.actions))
        self.assertTrue(any(".ece" in a for a in result.actions))


class CliFailurePresentationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _run_cli(self, *argv: str):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_exe_failure_prints_the_three_sections(self) -> None:
        failing = ExeExportResult(
            ok=False, exe_path=None, message="boom",
            errors=["why-one"], actions=["do-this"],
        )
        with mock.patch(
            "ece_autocompiler.build.exe_exporter.compile_exe",
            return_value=failing,
        ):
            code, out, _ = self._run_cli("exe", str(self.tmp))
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("What happened: boom", out)
        self.assertIn("Why:", out)
        self.assertIn("  - why-one", out)
        self.assertIn("What to do:", out)
        self.assertIn("  - do-this", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
