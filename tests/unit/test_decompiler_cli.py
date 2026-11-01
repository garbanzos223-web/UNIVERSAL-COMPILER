"""CLI surface for the decompiler and recompiler."""

from __future__ import annotations

import io
import json
import os
import py_compile
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler import cli  # noqa: E402


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class DecompilerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece_decomp_cli_"))
        self.home = Path(tempfile.mkdtemp(prefix="ece_decomp_cli_home_"))
        self._old_home = os.environ.get("ECE_AUTOCOMPILER_HOME", "")
        os.environ["ECE_AUTOCOMPILER_HOME"] = str(self.home)
        source = self.tmp / "demo.py"
        source.write_text("import os\nVALUE = 3\n", encoding="utf-8")
        py_compile.compile(str(source), doraise=True)
        self.pyc = next((self.tmp / "__pycache__").glob("*.pyc"))

    def tearDown(self) -> None:
        if self._old_home:
            os.environ["ECE_AUTOCOMPILER_HOME"] = self._old_home
        else:
            os.environ.pop("ECE_AUTOCOMPILER_HOME", None)
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def test_formats_lists_real_backends(self) -> None:
        code, out, err = run_cli("formats")
        self.assertEqual(code, 0, err)
        self.assertIn("pe-dotnet", out)
        self.assertIn("python-bytecode", out)
        code, out, _ = run_cli("formats", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out))

    def test_analyze_only_does_not_write_a_project(self) -> None:
        target = self.tmp / "should-not-exist"
        code, out, err = run_cli("decompile", str(self.pyc), "--analyze-only", "-o", str(target))
        self.assertEqual(code, 0, err)
        self.assertIn("python-bytecode", out)
        self.assertFalse(target.exists())
        code, out, _ = run_cli("decompile", str(self.pyc), "--analyze-only", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload[0]["format"]["kind"], "python-bytecode")

    def test_decompile_writes_a_project_and_json(self) -> None:
        output = self.tmp / "out"
        code, out, err = run_cli("decompile", str(self.pyc), "-o", str(output), "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["project_kind"], "python")
        self.assertTrue(Path(payload["manifest"]).is_file())
        self.assertTrue((output / "python" / "demo.py").is_file())

    def test_missing_input_fails(self) -> None:
        code, out, err = run_cli("decompile", str(self.tmp / "nope.bin"))
        self.assertEqual(code, 1)
        self.assertIn("do not exist", out)

    def test_recompile_plan_and_real_python_build(self) -> None:
        project = self.tmp / "project"
        project.mkdir()
        (project / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        output = self.tmp / "rebuilt"
        code, out, err = run_cli("recompile", str(project), "-o", str(output), "--plan")
        self.assertEqual(code, 0, err)
        self.assertIn("compileall", out)
        code, out, err = run_cli("recompile", str(project), "-o", str(output), "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["artifacts"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
