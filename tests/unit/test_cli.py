"""CLI contract: exit codes, argument handling and JSON output shapes.

These tests pin the documented contract that scripts and CI rely on:

    0  success
    1  operation failed (build, validation, missing path, ...)
    2  usage error (argparse)

Bloque 4 (CLI) extends the code table; the assertions here are written so new
codes slot in without rework.
"""

from __future__ import annotations

import io
import json
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
    """Run cli.main with captured stdout/stderr. Returns (code, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class ExitCodeContractTests(unittest.TestCase):
    def _exit_of(self, *argv: str) -> tuple[int, str, str]:
        """Run cli.main capturing streams; returns (exit_code, out, err)."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            cli.main(list(argv))
        return int(caught.exception.code), out.getvalue(), err.getvalue()

    def test_documented_code_values(self) -> None:
        self.assertEqual(cli.EXIT_OK, 0)
        self.assertEqual(cli.EXIT_ERROR, 1)
        self.assertEqual(cli.EXIT_USAGE, 2)

    def test_no_arguments_is_usage_error(self) -> None:
        code, _, err = self._exit_of()
        self.assertEqual(code, cli.EXIT_USAGE)
        self.assertIn("required", err)

    def test_unknown_command_is_usage_error(self) -> None:
        code, _, _ = self._exit_of("frobnicate")
        self.assertEqual(code, cli.EXIT_USAGE)

    def test_unknown_flag_is_usage_error(self) -> None:
        code, _, _ = self._exit_of("analyze", "--definitely-not-a-flag")
        self.assertEqual(code, cli.EXIT_USAGE)

    def test_help_exits_zero(self) -> None:
        code, out, _ = self._exit_of("--help")
        self.assertEqual(code, 0)
        self.assertIn("usage", out.lower())

    def test_version_exits_zero(self) -> None:
        code, out, _ = self._exit_of("--version")
        self.assertEqual(code, 0)
        self.assertIn("ECE", out)


class PathValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        import shutil

        self.tmp = Path(tempfile.mkdtemp(prefix="ece_cli_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_analyze_missing_path_returns_error(self) -> None:
        code, out, _ = run_cli("analyze", str(self.tmp / "nowhere"))
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("error", out.lower())

    def test_build_missing_path_returns_error(self) -> None:
        code, out, _ = run_cli("build", str(self.tmp / "nowhere"))
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("error", out.lower())

    def test_exe_missing_path_returns_error(self) -> None:
        code, out, _ = run_cli("exe", str(self.tmp / "nowhere"))
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("error", out.lower())

    def test_no_tracebacks_escape_the_cli(self) -> None:
        for argv in (
            ("analyze", str(self.tmp / "nowhere")),
            ("validate", str(self.tmp / "nope.ece")),
            ("inspect", str(self.tmp / "nope.ece")),
        ):
            code, out, err = run_cli(*argv)
            self.assertNotIn("Traceback", out + err, f"traceback from {argv}")
            self.assertEqual(code, cli.EXIT_ERROR)


class AnalyzeCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        import shutil

        self.tmp = Path(tempfile.mkdtemp(prefix="ece_cli_an_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_empty_directory_human_output(self) -> None:
        empty = self.tmp / "vacio"
        empty.mkdir()
        code, out, _ = run_cli("analyze", str(empty))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("No supported project type", out)

    def test_python_project_json_output(self) -> None:
        project = self.tmp / "mi_juego"
        project.mkdir()
        (project / "main.py").write_text("print('hola')\n", encoding="utf-8")
        code, out, _ = run_cli("analyze", "--json", str(project))
        self.assertEqual(code, cli.EXIT_OK)
        payload = json.loads(out)  # stdout only; logs go to stderr
        self.assertTrue(payload["candidates"])
        top = payload["candidates"][0]
        self.assertEqual(top["adapter_id"], "python")
        # A lone main.py is a partial match: detection stays conservative and
        # ranks it first rather than over-promising confidence.
        self.assertGreaterEqual(top["confidence"], 0.4)
        self.assertIn("label", top)

    def test_human_output_lists_candidates(self) -> None:
        project = self.tmp / "juego2"
        project.mkdir()
        (project / "main.py").write_text("print(1)\n", encoding="utf-8")
        code, out, _ = run_cli("analyze", str(project))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("python", out.lower())
        self.assertIn("%", out)  # confidence is shown


class ValidateInspectExtractTests(unittest.TestCase):
    def setUp(self) -> None:
        import shutil

        self.tmp = Path(tempfile.mkdtemp(prefix="ece_cli_val_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        from ece_autocompiler.container import ContainerWriter, Manifest, PayloadItem

        src = self.tmp / "src.txt"
        src.write_text("contenido de prueba\n", encoding="utf-8")
        manifest = Manifest(project_name="cli", adapter_id="python", payload_type="python")
        self.container = self.tmp / "cli.ece"
        ContainerWriter(compression="zlib").write(
            self.container, [PayloadItem(path="src.txt", source=src)], manifest, overwrite=True
        )

    def test_validate_ok_human(self) -> None:
        code, out, _ = run_cli("validate", str(self.container))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("VALID", out)

    def test_validate_ok_json_shape(self) -> None:
        code, out, _ = run_cli("validate", "--json", str(self.container))
        self.assertEqual(code, cli.EXIT_OK)
        payload = json.loads(out)
        for key in ("path", "ok", "format_version", "entry_count", "signature_state", "issues"):
            self.assertIn(key, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["signature_state"], "not-checked")

    def test_validate_missing_file_returns_error(self) -> None:
        code, out, _ = run_cli("validate", str(self.tmp / "no.ece"))
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("INVALID", out)

    def test_inspect_json_and_human(self) -> None:
        code, out, _ = run_cli("inspect", "--json", str(self.container))
        self.assertEqual(code, cli.EXIT_OK)
        payload = json.loads(out)
        self.assertIn("manifest", payload)
        self.assertIn("entries", payload)
        self.assertEqual(payload["entries"][0]["path"], "src.txt")

        code, out, _ = run_cli("inspect", str(self.container))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("src.txt", out)

    def test_extract_roundtrip(self) -> None:
        dest = self.tmp / "extraido"
        code, out, _ = run_cli("extract", str(self.container), str(dest))
        self.assertEqual(code, cli.EXIT_OK)
        self.assertTrue((dest / "src.txt").is_file())
        self.assertEqual((dest / "src.txt").read_text(encoding="utf-8"), "contenido de prueba\n")

    def test_extract_to_bad_destination_returns_error(self) -> None:
        # Destination path is an existing *file*: must fail with a clean error.
        blocker = self.tmp / "bloqueado"
        blocker.write_text("x", encoding="utf-8")
        code, out, err = run_cli("extract", str(self.container), str(blocker / "sub"))
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertNotIn("Traceback", out + err)


class ToolsCommandTests(unittest.TestCase):
    def test_tools_human_reports_python(self) -> None:
        code, out, _ = run_cli("tools")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("Python", out)

    def test_tools_json_shape(self) -> None:
        code, out, _ = run_cli("tools", "--json")
        self.assertEqual(code, cli.EXIT_OK)
        payload = json.loads(out)
        self.assertIn("python", payload)
        tool = payload["python"]
        for key in ("id", "name", "found", "path", "version", "install_hint"):
            self.assertIn(key, tool)
        self.assertTrue(tool["found"])

    def test_tools_json_lists_missing_with_hint(self) -> None:
        code, out, _ = run_cli("tools", "--json")
        self.assertEqual(code, cli.EXIT_OK)
        payload = json.loads(out)
        missing = [t for t in payload.values() if not t["found"]]
        for tool in missing:
            if tool["id"] in ("python", "git"):
                continue
            self.assertTrue(tool["install_hint"], f"{tool['id']} lacks an install hint")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
