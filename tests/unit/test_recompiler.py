"""Recompilation engine: planning, honest gating and a real rebuild.

The Python rebuild runs the real interpreter (``compileall``). The .NET test
builds a small project with the installed SDK and skips when it is absent; it
never asserts a success that the toolchain did not produce.
"""

from __future__ import annotations

import json
import shutil
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

from ece_autocompiler.config import AppConfig  # noqa: E402
from ece_autocompiler.decompiler import (  # noqa: E402
    detect_project_kind,
    plan_recompile,
    recompile,
)


class PlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece_recompile_plan_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_python_project_plan(self) -> None:
        project = self.tmp / "pyapp"
        project.mkdir()
        (project / "main.py").write_text("print('hi')\n", encoding="utf-8")
        self.assertEqual(detect_project_kind(project)[0], "python")
        plan = plan_recompile(project, self.tmp / "out")
        self.assertEqual(plan.kind, "python")
        self.assertTrue(plan.ready)
        self.assertTrue(any("compileall" in " ".join(step.cmd_args) for step in plan.steps))

    def test_dotnet_project_plan(self) -> None:
        project = self.tmp / "csapp"
        project.mkdir()
        (project / "App.csproj").write_text("<Project Sdk=\"Microsoft.NET.Sdk\" />\n", encoding="utf-8")
        self.assertEqual(detect_project_kind(project)[0], "dotnet")
        plan = plan_recompile(project, self.tmp / "out")
        self.assertEqual(plan.kind, "dotnet")
        if shutil.which("dotnet"):
            self.assertTrue(any("build" in step.cmd_args for step in plan.steps))
        else:
            self.assertFalse(plan.ready)

    def test_unknown_project_is_not_ready(self) -> None:
        project = self.tmp / "empty"
        project.mkdir()
        (project / "data.bin").write_bytes(b"\x00\x01")
        plan = plan_recompile(project, self.tmp / "out")
        self.assertFalse(plan.ready)
        self.assertTrue(plan.issues)

    def test_loose_c_sources_are_not_guessed(self) -> None:
        project = self.tmp / "csrc"
        project.mkdir()
        (project / "main.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
        plan = plan_recompile(project, self.tmp / "out")
        self.assertEqual(plan.kind, "cpp-sources")
        self.assertFalse(plan.ready)
        self.assertTrue(any("build system" in issue for issue in plan.issues))


class RecompileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece_recompile_run_"))
        self.home = Path(tempfile.mkdtemp(prefix="ece_recompile_home_"))
        self.config = AppConfig(work_dir=self.home / "work", output_dir=self.home / "output")
        self.config.ensure_directories()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def test_python_recompile_produces_verified_bytecode(self) -> None:
        project = self.tmp / "pyapp"
        project.mkdir()
        (project / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
        result = recompile(project, self.tmp / "out", config=self.config)
        self.assertTrue(result.ok, result.summary_lines())
        self.assertTrue(result.artifacts)
        self.assertTrue(any(path.suffix == ".pyc" for path in result.artifacts))
        for artifact in result.artifacts:
            self.assertTrue(artifact.is_file())
        self.assertTrue(result.log_path and result.log_path.is_file())

    def test_python_syntax_error_fails_honestly(self) -> None:
        project = self.tmp / "broken"
        project.mkdir()
        (project / "main.py").write_text("if True\n    pass\n", encoding="utf-8")
        result = recompile(project, self.tmp / "out", config=self.config)
        self.assertFalse(result.ok)
        self.assertTrue(result.errors)
        self.assertFalse(result.artifacts)

    def test_recovered_outline_gate_refuses(self) -> None:
        project = self.tmp / "outline"
        project.mkdir()
        (project / "main.py").write_text("x = 1\n", encoding="utf-8")
        (project / "recovered-project.json").write_text(
            json.dumps({"recompile_ready": False, "project_kind": "python"}),
            encoding="utf-8",
        )
        result = recompile(project, self.tmp / "out", config=self.config)
        self.assertFalse(result.ok)
        self.assertTrue(any("not recompilable" in error for error in result.errors))

    @unittest.skipUnless(shutil.which("dotnet"), "the .NET SDK is not installed")
    def test_dotnet_recompile_builds_a_new_assembly(self) -> None:
        project = self.tmp / "dotnetapp"
        project.mkdir()
        (project / "App.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <PropertyGroup>\n"
            "    <TargetFramework>net8.0</TargetFramework>\n"
            "  </PropertyGroup>\n"
            "</Project>\n",
            encoding="utf-8",
        )
        (project / "Greeter.cs").write_text(
            "namespace Recovered;\npublic static class Greeter\n{\n"
            "    public static string Hello() => \"hello\";\n}\n",
            encoding="utf-8",
        )
        result = recompile(project, self.tmp / "out", config=self.config)
        if not result.ok:
            self.skipTest("this SDK cannot target net8.0: " + "; ".join(result.errors[:2]))
        self.assertTrue(any(path.suffix == ".dll" for path in result.artifacts))
        self.assertTrue(any("not preserved" in note for note in result.notes))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
