import sys
import unittest
from pathlib import Path


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler.diagnostics import Category, classify, first_actionable, summarize  # noqa: E402


class ClassifierTests(unittest.TestCase):
    def _categories(self, output: str) -> set[Category]:
        return {d.category for d in classify(output)}

    def test_missing_tool_msvc(self) -> None:
        out = "'cl' is not recognized as an internal or external command"
        self.assertIn(Category.MISSING_TOOL, self._categories(out))

    def test_missing_dotnet_sdk(self) -> None:
        out = "error NETSDK1045: No .NET SDKs were found."
        self.assertIn(Category.MISSING_TOOL, self._categories(out))

    def test_linker(self) -> None:
        out = "undefined reference to `main'"
        self.assertIn(Category.LINKER, self._categories(out))

    def test_missing_python_module(self) -> None:
        out = "ModuleNotFoundError: No module named 'flask'"
        self.assertIn(Category.MISSING_DEPENDENCY, self._categories(out))

    def test_npm_404(self) -> None:
        out = "npm error 404 Not Found - GET https://registry.npmjs.org/nope"
        self.assertIn(Category.MISSING_DEPENDENCY, self._categories(out))

    def test_syntax_error(self) -> None:
        out = "app.py:3: syntax error near unexpected token"
        self.assertIn(Category.SYNTAX_TYPE, self._categories(out))

    def test_permission(self) -> None:
        out = "Error: EACCES: permission denied, open '/etc/x'"
        self.assertIn(Category.PERMISSION, self._categories(out))

    def test_network(self) -> None:
        out = "Could not resolve host: registry.npmjs.org"
        self.assertIn(Category.NETWORK, self._categories(out))

    def test_unknown_left_unclassified(self) -> None:
        out = "something entirely unrelated happened"
        self.assertEqual(classify(out), [])

    def test_deduplicates(self) -> None:
        out = "gcc: error: a.c\nc not found\ngcc: error: a.c"
        diags = classify(out)
        self.assertEqual(len(diags), len({(d.category, d.line) for d in diags}))

    def test_first_actionable_prefers_missing_tool(self) -> None:
        out = "app.py:1: syntax error\ncl is not recognized as an internal or external command"
        best = first_actionable(classify(out))
        self.assertIsNotNone(best)
        self.assertEqual(best.category, Category.MISSING_TOOL)

    def test_summarize(self) -> None:
        out = "cl is not recognized as an internal or external command"
        self.assertIn("Missing compiler", summarize(classify(out)))
        self.assertEqual(summarize([]), "No known error patterns were found in the output.")


if __name__ == "__main__":
    unittest.main()
