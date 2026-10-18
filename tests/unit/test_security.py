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

from ece_autocompiler.container.security import (  # noqa: E402
    check_depth,
    normalize_entry_path,
    resolve_destination,
)
from ece_autocompiler.errors import UnsafePathError  # noqa: E402
from ece_autocompiler.security import (  # noqa: E402
    ensure_within,
    is_relative_plain,
    sanitize_filename,
)


class EntryPathTests(unittest.TestCase):
    def test_normalizes_separators(self) -> None:
        self.assertEqual(normalize_entry_path("./a/b.txt"), "a/b.txt")
        self.assertEqual(normalize_entry_path("a//b.txt"), "a/b.txt")
        self.assertEqual(normalize_entry_path("a/./b.txt"), "a/b.txt")

    def test_backslash_rejected(self) -> None:
        with self.assertRaises(UnsafePathError):
            normalize_entry_path("a\\b\\c.txt")

    def test_rejects_traversal(self) -> None:
        for bad in ("../evil", "a/../../evil", "..\\evil", "/etc/passwd", "C:/Windows/system32"):
            with self.subTest(bad=bad):
                with self.assertRaises(UnsafePathError):
                    normalize_entry_path(bad)

    def test_rejects_empty(self) -> None:
        with self.assertRaises(UnsafePathError):
            normalize_entry_path("")
        with self.assertRaises(UnsafePathError):
            normalize_entry_path(".")

    def test_resolve_destination_stays_inside(self) -> None:
        root = Path(tempfile.mkdtemp())
        target = resolve_destination(root, "a/b/c.txt")
        self.assertTrue(str(target).startswith(str(root)))

    def test_check_depth(self) -> None:
        check_depth("a/b/c", max_depth=5)
        with self.assertRaises(UnsafePathError):
            check_depth("/".join(["x"] * 10), max_depth=3)


class FilenameSanitizeTests(unittest.TestCase):
    def test_strips_dangerous_chars(self) -> None:
        self.assertEqual(sanitize_filename('a<b>c:d"e?g*h'), "a_b_c_d_e_g_h")

    def test_takes_last_component(self) -> None:
        self.assertEqual(sanitize_filename("C:/tmp/project"), "project")
        self.assertEqual(sanitize_filename("../../etc/passwd"), "passwd")

    def test_reserved_names_prefixed(self) -> None:
        self.assertEqual(sanitize_filename("CON"), "_CON")
        self.assertEqual(sanitize_filename("com1.txt"), "_com1.txt")

    def test_empty_falls_back(self) -> None:
        self.assertEqual(sanitize_filename(""), "output")
        self.assertEqual(sanitize_filename("..."), "output")


class WithinTests(unittest.TestCase):
    def test_ensure_within(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.assertTrue(ensure_within(root, root / "a" / "b"))
        self.assertFalse(ensure_within(root, root.parent / "elsewhere"))

    def test_is_relative_plain(self) -> None:
        self.assertTrue(is_relative_plain("a/b/c.txt"))
        self.assertFalse(is_relative_plain("../a"))
        self.assertFalse(is_relative_plain("/abs"))
        self.assertFalse(is_relative_plain("C:/abs"))
        self.assertFalse(is_relative_plain(""))


if __name__ == "__main__":
    unittest.main()
