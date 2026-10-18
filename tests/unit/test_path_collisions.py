"""Windows path collisions inside a container (Bloque 2).

NTFS compares file names case-insensitively, so entries like "readme.txt" and
"README.TXT" - or the same path twice - resolve to one file. Such a container
cannot be extracted safely: without this guard the second entry would silently
replace the first (overwrite mode) or abort halfway with a misleading "file
already exists". These tests pin the three layers that now handle it: the
writer refuses to create it, the extractor refuses to unpack it before writing
anything, and the validator reports it as a warning while the container stays
readable/inspectable (format compatibility is never broken).
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

from ece_autocompiler.container import (  # noqa: E402
    ContainerReader,
    ContainerWriter,
    Manifest,
    PayloadItem,
    validate_container,
)
from ece_autocompiler.container.security import (  # noqa: E402
    describe_path_collisions,
    find_path_collisions,
    windows_collision_key,
)
from ece_autocompiler.errors import ContainerFormatError, IntegrityError  # noqa: E402
from ece_autocompiler.extraction import Extractor  # noqa: E402


class CollisionKeyTests(unittest.TestCase):
    def test_case_only_difference_is_the_same_key(self) -> None:
        self.assertEqual(
            windows_collision_key("Readme.txt"), windows_collision_key("README.TXT")
        )
        self.assertEqual(
            windows_collision_key("dir/A.txt"), windows_collision_key("DIR/a.TXT")
        )

    def test_distinct_names_keep_distinct_keys(self) -> None:
        self.assertNotEqual(
            windows_collision_key("a.txt"), windows_collision_key("a.txt.bak")
        )
        self.assertNotEqual(
            windows_collision_key("x/a.txt"), windows_collision_key("y/a.txt")
        )

    def test_find_groups_only_real_collisions(self) -> None:
        groups = find_path_collisions(["a.txt", "A.TXT", "b.txt"])
        self.assertEqual(len(groups), 1)
        self.assertEqual(list(groups.values()), [["a.txt", "A.TXT"]])
        self.assertEqual(
            find_path_collisions(["a.txt", "b.txt"]),
            {},
        )
        self.assertEqual(find_path_collisions([]), {})

    def test_repeated_path_is_reported_once_by_name(self) -> None:
        found = find_path_collisions(["a.txt", "a.txt"])
        self.assertEqual(list(found.values()), [["a.txt"]])
        self.assertIn("declared more than once", describe_path_collisions(found))

    def test_describe_lists_both_names(self) -> None:
        text = describe_path_collisions(find_path_collisions(["readme.txt", "README.TXT"]))
        self.assertIn("readme.txt", text)
        self.assertIn("README.TXT", text)


def _manifest() -> Manifest:
    return Manifest(project_name="collide", adapter_id="python", payload_type="python")


class WriterRefusesCollisionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece_collide_w_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_case_only_pair_is_refused(self) -> None:
        items = [
            PayloadItem(path="readme.txt", data=b"uno\n"),
            PayloadItem(path="README.TXT", data=b"dos\n"),
        ]
        out = self.tmp / "collide.ece"
        with self.assertRaises(ContainerFormatError) as caught:
            ContainerWriter().write(out, items, _manifest())
        message = str(caught.exception)
        self.assertIn("same file", message)
        self.assertIn("readme.txt", message)
        self.assertIn("README.TXT", message)
        self.assertFalse(out.exists(), "no container must be left behind")

    def test_exact_duplicate_is_still_refused(self) -> None:
        items = [
            PayloadItem(path="a.txt", data=b"uno\n"),
            PayloadItem(path="a.txt", data=b"dos\n"),
        ]
        with self.assertRaises(ContainerFormatError) as caught:
            ContainerWriter().write(self.tmp / "dup.ece", items, _manifest())
        self.assertIn("Duplicate path", str(caught.exception))

    def test_similar_but_distinct_names_still_write(self) -> None:
        items = [
            PayloadItem(path="a.txt", data=b"uno\n"),
            PayloadItem(path="A.txt.bak", data=b"dos\n"),
            PayloadItem(path="x/A.txt", data=b"tres\n"),
        ]
        out = self.tmp / "ok.ece"
        report = ContainerWriter().write(out, items, _manifest())
        self.assertEqual(report.file_count, 3)
        self.assertTrue(out.exists())


def _foreign_container(tmp: Path) -> Path:
    """Build a container with colliding entries the way another tool could.

    Our own writer refuses this payload, so the preflight check is bypassed on
    purpose: this is what an .ece produced elsewhere looks like.
    """
    items = [
        PayloadItem(path="readme.txt", data=b"primero\n"),
        PayloadItem(path="README.TXT", data=b"segundo\n"),
    ]
    out = tmp / "foreign.ece"
    with mock.patch.object(ContainerWriter, "_preflight", return_value=None):
        ContainerWriter().write(out, items, _manifest())
    return out


class ForeignContainerHandlingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece_collide_f_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.container = _foreign_container(self.tmp)

    def test_reader_still_opens_it(self) -> None:
        # Compatibility: reading/inspecting a foreign container must keep
        # working; only extraction has to refuse.
        with ContainerReader(self.container) as reader:
            paths = sorted(entry.path for entry in reader.entries())
        self.assertEqual(paths, ["README.TXT", "readme.txt"])

    def test_validation_warns_without_failing_integrity(self) -> None:
        report = validate_container(self.container, deep=True)
        self.assertTrue(report.ok, "the container itself is intact")
        codes = [issue.code for issue in report.warnings]
        self.assertIn("ece.container.path_collision", codes)
        warning = next(
            issue for issue in report.warnings if issue.code == "ece.container.path_collision"
        )
        self.assertIn("readme.txt", warning.detail)
        self.assertIn("README.TXT", warning.detail)
        self.assertEqual(report.failed_entries, [])

    def test_extraction_refuses_before_writing_anything(self) -> None:
        dest = self.tmp / "out"
        with self.assertRaises(IntegrityError) as caught:
            Extractor().extract(self.container, dest)
        self.assertIn("same file", str(caught.exception))
        leftovers = [p for p in dest.rglob("*") if p.is_file()] if dest.exists() else []
        self.assertEqual(leftovers, [], f"extraction left files behind: {leftovers}")

    def test_extraction_of_a_single_selected_path_still_works(self) -> None:
        # The escape hatch for a user who needs one of the colliding files.
        dest = self.tmp / "out_one"
        report = Extractor().extract(self.container, dest, selected_paths=["readme.txt"])
        self.assertEqual(report.files_written, 1)
        self.assertEqual((dest / "readme.txt").read_bytes(), b"primero\n")

    def test_cli_extract_reports_the_collision_cleanly(self) -> None:
        from ece_autocompiler import cli

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["extract", str(self.container), str(self.tmp / "cli_out")])
        combined = out.getvalue() + err.getvalue()
        self.assertEqual(code, cli.EXIT_ERROR)
        self.assertIn("same file", combined)
        self.assertNotIn("Traceback", combined)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
