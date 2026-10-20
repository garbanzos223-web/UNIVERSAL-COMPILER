"""The repair engine: parsing, proposals, application, verification, rollback.

The engine tests use a real build runner: it compiles the project file with
Python's own compiler, so a repair is only "successful" when the file really
compiles afterwards.
"""

from __future__ import annotations

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
from ece_autocompiler.repair import (  # noqa: E402
    BuildOutcome,
    RepairEngine,
    parse_diagnostics,
)
from ece_autocompiler.repair.diagnostics import (  # noqa: E402
    CATEGORY_LINKER,
    CATEGORY_MISSING_IMPORT,
    CATEGORY_SYNTAX,
    CATEGORY_TYPE,
)
from ece_autocompiler.repair.rules import propose_repairs  # noqa: E402


class DiagnosticParsingTests(unittest.TestCase):
    def test_gcc_diagnostics(self) -> None:
        output = (
            "main.cpp:12:5: error: 'uint32_t' does not name a type\n"
            "   12 |     uint32_t value;\n"
            "main.cpp:12:1: note: candidate: 'unsigned int'\n"
        )
        found = parse_diagnostics(output)
        errors = [d for d in found if d.severity == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].file, "main.cpp")
        self.assertEqual(errors[0].line, 12)
        self.assertEqual(errors[0].column, 5)
        self.assertEqual(errors[0].category, CATEGORY_MISSING_IMPORT)

    def test_msvc_diagnostics(self) -> None:
        output = "Program.cs(7,13): error CS0103: The name 'Console' does not exist in the current context\n"
        found = parse_diagnostics(output)
        errors = [d for d in found if d.severity == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "CS0103")
        self.assertEqual(errors[0].line, 7)

    def test_cargo_diagnostics(self) -> None:
        output = (
            "error[E0308]: mismatched types\n"
            " --> src/main.rs:4:9\n"
            "  |\n"
            "4 |     let x: i32 = \"hello\";\n"
        )
        found = parse_diagnostics(output)
        errors = [d for d in found if d.severity == "error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "E0308")
        self.assertEqual(errors[0].file, "src/main.rs")
        self.assertEqual(errors[0].line, 4)
        self.assertEqual(errors[0].category, CATEGORY_TYPE)

    def test_go_diagnostics(self) -> None:
        output = "main.go:10:2: undefined: fmt.Println\n"
        found = parse_diagnostics(output)
        errors = [d for d in found if d.severity == "error"]
        self.assertTrue(errors)
        self.assertEqual(errors[0].file, "main.go")
        self.assertEqual(errors[0].line, 10)

    def test_python_diagnostics(self) -> None:
        output = (
            '  File "C:\\work\\app.py", line 4\n'
            "    if True\n"
            "        ^\n"
            "SyntaxError: expected ':'\n"
        )
        found = parse_diagnostics(output)
        errors = [d for d in found if d.severity == "error"]
        located = [d for d in errors if d.file]
        self.assertTrue(located)
        self.assertEqual(located[0].line, 4)
        self.assertIn("expected ':'", located[0].message)

    def test_java_diagnostics(self) -> None:
        output = "src/Main.java:12: error: cannot find symbol\n"
        found = parse_diagnostics(output)
        errors = [d for d in found if d.severity == "error"]
        self.assertTrue(errors)
        self.assertEqual(errors[0].file, "src/Main.java")
        self.assertEqual(errors[0].line, 12)

    def test_typescript_diagnostics(self) -> None:
        output = "src/app.ts(3,5): error TS2322: Type 'string' is not assignable to type 'number'.\n"
        found = parse_diagnostics(output)
        errors = [d for d in found if d.severity == "error"]
        self.assertTrue(errors)
        self.assertEqual(errors[0].code, "TS2322")

    def test_linker_errors_are_categorised(self) -> None:
        output = "/usr/bin/ld: undefined reference to `main'\n"
        found = parse_diagnostics(output)
        self.assertTrue(any(d.category == CATEGORY_LINKER for d in found))

    def test_empty_output_produces_no_diagnostics(self) -> None:
        self.assertEqual(parse_diagnostics(""), [])


class RepairRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _proposals(self, name: str, content: str, output: str):
        path = self.tmp / name
        path.write_text(content, encoding="utf-8")
        diagnostics = parse_diagnostics(output)
        return propose_repairs(diagnostics, self.tmp, output=output)

    def test_python_missing_colon(self) -> None:
        output = (
            '  File "app.py", line 1\n'
            "    if True\n"
            "        ^\n"
            "SyntaxError: expected ':'\n"
        )
        proposals = self._proposals("app.py", "if True\n    print(1)\n", output)
        self.assertEqual(len(proposals), 1)
        proposal = proposals[0]
        self.assertEqual(proposal.rule_id, "python-missing-colon")
        self.assertEqual(proposal.confidence, "high")
        self.assertIn("if True:", proposal.full_text)
        self.assertIn("+if True:", proposal.diff)

    def test_cpp_missing_include(self) -> None:
        output = "main.cpp:3:5: error: 'uint32_t' does not name a type\n"
        proposals = self._proposals("main.cpp", "int main() {\n  return 0;\n}\n", output)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].rule_id, "cpp-missing-include")
        self.assertIn("#include <cstdint>", proposals[0].full_text)

    def test_cpp_include_is_not_added_twice(self) -> None:
        output = "main.cpp:3:5: error: 'uint32_t' does not name a type\n"
        proposals = self._proposals(
            "main.cpp", "#include <cstdint>\nint main() { return 0; }\n", output
        )
        self.assertEqual(proposals, [])

    def test_go_unused_import(self) -> None:
        output = 'main.go:3:2: "fmt" imported and not used\n'
        proposals = self._proposals(
            "main.go", 'package main\n\nimport "fmt"\n\nfunc main() {}\n', output
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].rule_id, "go-unused-import")
        self.assertNotIn('import "fmt"', proposals[0].full_text)

    def test_csharp_missing_using(self) -> None:
        output = "Program.cs(3,5): error CS0103: The name 'Console' does not exist in the current context\n"
        proposals = self._proposals(
            "Program.cs", "class Program\n{\n    static void Main() { }\n}\n", output
        )
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].rule_id, "csharp-missing-using")
        self.assertIn("using System;", proposals[0].full_text)

    def test_typescript_semicolon_is_only_a_suggestion(self) -> None:
        output = "app.ts(1,12): error TS1005: ';' expected.\n"
        proposals = self._proposals("app.ts", "const x = 1\nconst y = 2\n", output)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].confidence, "medium")
        self.assertIn("const x = 1;", proposals[0].full_text)

    def test_unknown_error_produces_no_proposal(self) -> None:
        output = "main.go:3:2: undefined: someFunction\n"
        proposals = self._proposals("main.go", "package main\n", output)
        self.assertEqual(proposals, [])


class RepairEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "proj"
        self.project.mkdir()
        self.source = self.project / "app.py"
        self.cfg = AppConfig(output_dir=self.tmp / "out", work_dir=self.tmp / "work")
        self.cfg.ensure_directories()
        self.engine = RepairEngine(config=self.cfg)

    def _runner(self, *, always_fail: bool = False):
        """A real build: it compiles the file with Python's own compiler."""

        def runner() -> BuildOutcome:
            text = self.source.read_text(encoding="utf-8")
            try:
                compile(text, str(self.source), "exec")
            except SyntaxError as exc:
                lines = text.splitlines()
                shown = lines[exc.lineno - 1] if exc.lineno and exc.lineno <= len(lines) else ""
                output = (
                    '  File "%s", line %s\n'
                    "    %s\n"
                    "    ^\n"
                    "SyntaxError: %s\n" % (self.source, exc.lineno, shown, exc.msg)
                )
                return BuildOutcome(
                    ok=False, output=output, exit_code=1, errors=[f"SyntaxError: {exc.msg}"]
                )
            if always_fail:
                return BuildOutcome(ok=False, output="still broken", exit_code=1)
            return BuildOutcome(ok=True, output="compiled")

        return runner

    def test_successful_repair_fixes_the_file(self) -> None:
        self.source.write_text("if True\n    print(1)\n", encoding="utf-8")
        outcome = self.engine.repair(self.project, self._runner())
        self.assertTrue(outcome.final_ok, msg=outcome.text())
        self.assertEqual(len(outcome.attempts), 1)
        self.assertEqual(outcome.attempts[0].verification, "resolved")
        self.assertIn("if True:", self.source.read_text(encoding="utf-8"))

    def test_dry_run_changes_nothing(self) -> None:
        original = "if True\n    print(1)\n"
        self.source.write_text(original, encoding="utf-8")
        outcome = self.engine.repair(self.project, self._runner(), apply=False)
        self.assertFalse(outcome.final_ok)
        self.assertEqual(self.source.read_text(encoding="utf-8"), original)
        self.assertTrue(outcome.proposals)

    def test_failed_repair_is_rolled_back(self) -> None:
        original = "if True\n    print(1)\n"
        self.source.write_text(original, encoding="utf-8")
        # The runner always fails, so the change cannot be verified.
        outcome = self.engine.repair(self.project, self._runner(always_fail=True))
        self.assertFalse(outcome.final_ok)
        self.assertTrue(outcome.reverted)
        self.assertEqual(self.source.read_text(encoding="utf-8"), original)

    def test_backup_of_the_original_file_is_kept(self) -> None:
        original = "if True\n    print(1)\n"
        self.source.write_text(original, encoding="utf-8")
        self.engine.repair(self.project, self._runner())
        backups = list((self.cfg.work_dir / "repair-backups").rglob("app.py"))
        self.assertTrue(backups, "the original file must be kept as a backup")
        self.assertEqual(backups[0].read_text(encoding="utf-8"), original)

    def test_journal_records_the_attempt(self) -> None:
        self.source.write_text("if True\n    print(1)\n", encoding="utf-8")
        outcome = self.engine.repair(self.project, self._runner())
        self.assertTrue(outcome.attempts)
        journal = self.cfg.work_dir / "repair-journal.jsonl"
        self.assertTrue(journal.is_file())
        content = journal.read_text(encoding="utf-8")
        self.assertIn("python-missing-colon", content)
        self.assertIn("resolved", content)

    def test_retry_limit_is_respected(self) -> None:
        self.source.write_text("if True\n    print(1)\n", encoding="utf-8")
        outcome = self.engine.repair(self.project, self._runner(), max_attempts=1)
        self.assertLessEqual(len(outcome.attempts), 1)

    def test_no_repairable_error_explains_what_to_do(self) -> None:
        self.source.write_text("print(1)\n", encoding="utf-8")
        outcome = self.engine.repair(self.project, self._runner(always_fail=True))
        self.assertFalse(outcome.final_ok)
        self.assertTrue(outcome.manual_actions or outcome.message)

    def test_successful_build_needs_no_repair(self) -> None:
        self.source.write_text("print(1)\n", encoding="utf-8")
        outcome = self.engine.repair(self.project, self._runner())
        self.assertTrue(outcome.final_ok)
        self.assertEqual(outcome.attempts, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
