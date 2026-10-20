"""The .exe export must not run project-controlled code without consent.

Some toolchains execute commands that come from the project: a CMake
configure runs the project's ``CMakeLists.txt``, Cargo runs ``build.rs`` and
MSBuild runs the project's targets. The exporter asks for consent before it
runs them, exactly like the ``.ece`` build does.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler.build import exe_exporter as EXE  # noqa: E402
from ece_autocompiler.detection.toolchains import ToolInfo  # noqa: E402


def _fake_tool(tool_id: str) -> ToolInfo:
    return ToolInfo(
        id=tool_id,
        name=tool_id,
        found=True,
        path=f"C:/tools/{tool_id}.exe",
        version="1.0",
    )


class ExeConsentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "cpp"
        self.project.mkdir()
        (self.project / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\nproject(demo)\n", encoding="utf-8"
        )
        (self.project / "main.cpp").write_text("int main() { return 0; }\n", encoding="utf-8")

    def _export(self, *, consent: bool):
        with mock.patch(
            "ece_autocompiler.build.exe_exporter.get_tool",
            side_effect=lambda tool_id: _fake_tool(tool_id),
        ), mock.patch(
            "ece_autocompiler.detection.toolchains.get_tool",
            side_effect=lambda tool_id: _fake_tool(tool_id),
        ):
            return EXE.compile_exe(
                self.project,
                output=self.tmp / "demo.exe",
                config=EXE.AppConfig(output_dir=self.tmp / "out", work_dir=self.tmp / "work"),
                consent_granted=consent,
            )

    def test_without_consent_nothing_runs_and_the_reason_is_clear(self) -> None:
        result = self._export(consent=False)
        self.assertFalse(result.ok)
        self.assertIn("consent", result.message.lower())
        self.assertTrue(any("Consent required" in error for error in result.errors))
        self.assertTrue(any("--consent" in action for action in result.actions))
        self.assertFalse((self.tmp / "demo.exe").exists())

    def test_the_failure_names_the_step_that_needs_consent(self) -> None:
        result = self._export(consent=False)
        joined = " ".join(result.errors)
        self.assertIn("cmake", joined.lower())
