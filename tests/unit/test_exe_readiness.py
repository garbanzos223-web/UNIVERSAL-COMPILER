"""Honest .exe readiness: the tool answers "can this build here?" truthfully.

`exe_readiness` is the single source of truth behind the CLI's analyze report
and the GUI's Detect output. It never builds and never downloads: it reports
what was found, what is missing, and the install step - and it is allowed to
say "no". These tests pin that per adapter and through the CLI.
"""

from __future__ import annotations

import io
import json
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
    ExeReadiness,
    exe_readiness,
)

READINESS = "ece_autocompiler.build.exe_exporter"
KNOWN_ADAPTERS = [
    "python",
    "node",
    "unity",
    "unreal",
    "dotnet",
    "rust-cargo",
    "go",
    "cpp-cmake",
    "cpp-make",
]


class ExeReadinessTests(unittest.TestCase):
    def test_python_ready_reports_the_detected_version(self) -> None:
        with mock.patch(f"{READINESS}.pyinstaller_available",
                        return_value=(True, "6.22.3")):
            ready = exe_readiness("python")
        self.assertTrue(r.possible if False else ready.possible)
        self.assertIn("PyInstaller", ready.summary)
        self.assertIn("6.22.3", ready.summary)
        self.assertEqual(ready.missing, [])
        self.assertEqual(ready.actions, [])

    def test_python_without_pyinstaller_says_so_and_how_to_fix(self) -> None:
        with mock.patch(
            f"{READINESS}.pyinstaller_available",
            return_value=(False, "PyInstaller is not installed"),
        ):
            ready = exe_readiness("python")
        self.assertFalse(ready.possible)
        self.assertEqual(ready.missing, ["PyInstaller"])
        self.assertTrue(any("pip install pyinstaller" in a for a in ready.actions))

    def test_node_needs_a_bundler_and_offers_the_ece_route(self) -> None:
        ready = exe_readiness("node")
        self.assertFalse(ready.possible)
        self.assertTrue(any("npm install -g pkg" in a for a in ready.actions))
        self.assertTrue(any(".ece" in a for a in ready.actions))

    def test_unity_is_built_by_its_own_editor(self) -> None:
        ready = exe_readiness("unity")
        self.assertFalse(ready.possible)
        self.assertEqual(ready.missing, ["unity"])
        self.assertTrue(any("editor" in a for a in ready.actions))

    def test_native_toolchain_missing_reports_facts_and_install_step(self) -> None:
        with mock.patch(f"{READINESS}._missing_tools", return_value=["cargo"]):
            ready = exe_readiness("rust-cargo")
        self.assertFalse(ready.possible)
        self.assertEqual(ready.missing, ["cargo"])
        self.assertIn("cargo", ready.summary)
        self.assertTrue(any("rustup.rs" in a for a in ready.actions))

    def test_native_toolchain_present_is_ready(self) -> None:
        with mock.patch(f"{READINESS}._missing_tools", return_value=[]):
            ready = exe_readiness("go")
        self.assertTrue(ready.possible)
        self.assertEqual(ready.missing, [])
        self.assertIn("ready", ready.summary)

    def test_unknown_adapter_points_at_the_ece_path(self) -> None:
        ready = exe_readiness("cobol")
        self.assertFalse(ready.possible)
        self.assertIn(".ece", ready.summary)

    def test_as_dict_is_a_snapshot(self) -> None:
        ready = ExeReadiness("node", False, "no", missing=["pkg"], actions=["do"])
        payload = ready.as_dict()
        self.assertEqual(set(payload), {"possible", "summary", "missing", "actions"})
        payload["missing"].append("mutated")
        payload["actions"].append("mutated")
        self.assertEqual(ready.missing, ["pkg"])
        self.assertEqual(ready.actions, ["do"])

    def test_never_ready_without_saying_what_to_do(self) -> None:
        # Every known adapter that cannot build here must carry a next step.
        with mock.patch(f"{READINESS}._missing_tools", return_value=["some-tool"]), \
                mock.patch(
                    f"{READINESS}.pyinstaller_available",
                    return_value=(False, "not installed"),
                ):
            for adapter_id in KNOWN_ADAPTERS:
                with self.subTest(adapter=adapter_id):
                    ready = exe_readiness(adapter_id)
                    self.assertTrue(ready.summary, "a verdict is always given")
                    if not ready.possible:
                        self.assertTrue(
                            ready.actions,
                            f"{adapter_id}: a negative verdict must guide the user",
                        )


class AnalyzeReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece_ready_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "proj"
        self.project.mkdir()
        (self.project / "main.py").write_text("print('hola')\n", encoding="utf-8")

    def _run_cli(self, *argv: str):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_text_report_says_ready_when_pyinstaller_is_present(self) -> None:
        with mock.patch(
            f"{READINESS}.pyinstaller_available", return_value=(True, "6.22.3")
        ):
            code, out, _ = self._run_cli("analyze", str(self.project))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("Export readiness (.exe):", out)
        self.assertIn("PyInstaller 6.22.3 detected", out)
        self.assertIn(".ece always works", out)

    def test_text_report_gives_the_install_step_when_missing(self) -> None:
        with mock.patch(
            f"{READINESS}.pyinstaller_available",
            return_value=(False, "PyInstaller is not installed"),
        ):
            code, out, _ = self._run_cli("analyze", str(self.project))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("cannot build .exe yet", out)
        self.assertIn("pip install pyinstaller", out)

    def test_json_report_carries_readiness_per_candidate(self) -> None:
        with mock.patch(
            f"{READINESS}.pyinstaller_available", return_value=(True, "6.22.3")
        ):
            code, out, _ = self._run_cli("analyze", "--json", str(self.project))
        self.assertEqual(code, cli.EXIT_OK)
        payload = json.loads(out)
        top = payload["candidates"][0]
        self.assertEqual(top["adapter_id"], "python")
        self.assertTrue(top["exe_readiness"]["possible"])
        self.assertIn("PyInstaller", top["exe_readiness"]["summary"])

    def test_json_report_is_honest_when_the_tool_is_missing(self) -> None:
        with mock.patch(
            f"{READINESS}.pyinstaller_available",
            return_value=(False, "PyInstaller is not installed"),
        ):
            code, out, _ = self._run_cli("analyze", "--json", str(self.project))
        payload = json.loads(out)
        top = payload["candidates"][0]
        self.assertFalse(top["exe_readiness"]["possible"])
        self.assertEqual(top["exe_readiness"]["missing"], ["PyInstaller"])
        self.assertTrue(top["exe_readiness"]["actions"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
