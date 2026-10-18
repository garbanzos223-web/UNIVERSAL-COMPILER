"""Edge cases for Windows path handling: unicode, spaces, reserved names,
trailing dots/spaces, deep nesting and paths beyond MAX_PATH.

These are the shapes that historically break Windows tooling: non-ASCII
project folders, segment names Win32 silently rewrites, and extraction depths
that exceed the classic 260-character limit.
"""

from __future__ import annotations

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

from ece_autocompiler.container import (  # noqa: E402
    ContainerWriter,
    Manifest,
    PayloadItem,
)
from ece_autocompiler.container.security import (  # noqa: E402
    normalize_entry_path,
    resolve_destination,
)
from ece_autocompiler.errors import UnsafePathError  # noqa: E402
from ece_autocompiler.extraction import Extractor  # noqa: E402


class NormalizeEntryPathTests(unittest.TestCase):
    """Full hostile-input matrix for normalize_entry_path."""

    def _rejects(self, raw: str, why: str) -> None:
        with self.assertRaises(UnsafePathError, msg=f"should reject {raw!r} ({why})"):
            normalize_entry_path(raw)

    def test_accepts_unicode_and_spaces(self) -> None:
        self.assertEqual(
            normalize_entry_path("carpeta ñandú/archivo ✨ final.txt"),
            "carpeta ñandú/archivo ✨ final.txt",
        )
        self.assertEqual(normalize_entry_path("spaced dir/inner file.py"), "spaced dir/inner file.py")

    def test_rejects_drive_letter(self) -> None:
        self._rejects("C:/windows/system32/evil.dll", "drive letter")
        self._rejects("c:x.txt", "lowercase drive letter")

    def test_rejects_unc(self) -> None:
        self._rejects("//server/share/x", "UNC path")
        self._rejects("\\\\server\\share\\x", "UNC with backslashes")

    def test_rejects_control_characters(self) -> None:
        self._rejects("bad\x00name.txt", "NUL byte")
        self._rejects("bad\x1fname.txt", "C0 control")

    def test_rejects_overlong_segment(self) -> None:
        self._rejects("a" * 256 + ".txt", "segment above 255 chars")

    def test_rejects_deep_nesting(self) -> None:
        deep = "/".join(f"d{i}" for i in range(70))
        self._rejects(deep, "more than 64 segments")

    def test_rejects_non_string(self) -> None:
        with self.assertRaises(UnsafePathError):
            normalize_entry_path(123)  # type: ignore[arg-type]

    def test_rejects_bad_utf8_bytes(self) -> None:
        with self.assertRaises(UnsafePathError):
            normalize_entry_path(b"\xff\xfe not utf8")

    def test_rejects_trailing_dot_segment(self) -> None:
        # Windows strips trailing dots: "a.txt." and "a.txt" are the same file.
        self._rejects("dir./a.txt", "directory ending in dot")
        self._rejects("file.txt.", "file ending in dot")
        self._rejects("weird...", "multiple trailing dots")

    def test_rejects_trailing_space_segment(self) -> None:
        self._rejects("file.txt ", "file ending in space")
        self._rejects("dir /a.txt", "directory ending in space")

    def test_rejects_reserved_device_names(self) -> None:
        for name in ("con", "CON", "nul.txt", "Lpt1.dat", "com9", "prn"):
            with self.assertRaises(UnsafePathError, msg=f"should reject {name!r}"):
                normalize_entry_path(name)
        # Names that merely start with a reserved word are fine.
        self.assertEqual(normalize_entry_path("console.log"), "console.log")
        self.assertEqual(normalize_entry_path("common/data.txt"), "common/data.txt")

    def test_internal_still_allowed(self) -> None:
        # Dots inside a name (not at the end) remain valid.
        self.assertEqual(normalize_entry_path("v1.2.3/readme.md"), "v1.2.3/readme.md")


class ResolveDestinationEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece_edge_"))
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unicode_space_destination(self) -> None:
        root = self.tmp / "proyecto ñandú ✨"
        root.mkdir()
        target = resolve_destination(root, "sub carpeta/ fichero final.txt")
        self.assertEqual(target.parent, root / "sub carpeta")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok", encoding="utf-8")
        self.assertTrue((root / "sub carpeta" / " fichero final.txt").exists())

    def test_long_path_above_260(self) -> None:
        # Build a destination whose full path exceeds MAX_PATH (260).
        base = self.tmp / "long"
        cursor = base
        cursor.mkdir()
        segment = "d" * 40
        while len(str(cursor)) <= 250:
            cursor = cursor / segment
            try:
                cursor.mkdir()
            except OSError:
                self.skipTest(f"filesystem refuses long paths: {cursor}")
        target = resolve_destination(cursor, "a" * 40 + "/leaf.txt")
        self.assertGreater(len(str(target)), 260)
        try:
            target.write_text("x", encoding="utf-8")
        except OSError as exc:
            self.skipTest(f"filesystem refuses writes above MAX_PATH ({exc})")
        self.assertTrue(target.exists())


class UnicodeRoundtripTests(unittest.TestCase):
    """Real containers with unicode/space paths must build and extract cleanly."""

    def setUp(self) -> None:
        import shutil

        self.tmp = Path(tempfile.mkdtemp(prefix="ece_uni_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_unicode_and_space_paths_roundtrip(self) -> None:
        files = {
            "carpeta ñandú/archivo ✨ final.txt": "héllo ✨".encode("utf-8"),
            " leading space/inner.py": b"print(1)\n",
            "v1.2.3/readme.md": b"# ok\n",
        }
        items = []
        for i, (rel, data) in enumerate(files.items()):
            src = self.tmp / f"src_{i}"
            src.write_bytes(data)
            items.append(PayloadItem(path=rel, source=src))
        manifest = Manifest(project_name="unicode", adapter_id="python", payload_type="python")
        out = self.tmp / "unicode.ece"
        ContainerWriter(compression="zlib").write(out, items, manifest, overwrite=True)

        dest = self.tmp / "destino salido ✨"
        report = Extractor().extract(out, dest)
        self.assertEqual(report.files_written, len(files))
        for rel, data in files.items():
            written = dest / rel
            self.assertTrue(written.is_file(), f"missing {rel}")
            self.assertEqual(written.read_bytes(), data)


class HostileEntryParseTests(unittest.TestCase):
    """A tampered index must fail at parse time, before anything touches disk."""

    def _raw_entry(self, path: str) -> dict:
        return {
            "path": path,
            "offset": 0,
            "length": 10,
            "size": 10,
            "compression": "store",
            "hash_algo": "sha256",
            "digest": "0" * 64,
            "executable": False,
        }

    def test_file_entry_rejects_reserved_name(self) -> None:
        from ece_autocompiler.container.model import FileEntry

        with self.assertRaises(UnsafePathError):
            FileEntry.from_dict(self._raw_entry("con"))
        with self.assertRaises(UnsafePathError):
            FileEntry.from_dict(self._raw_entry("sub/nul.txt"))
        with self.assertRaises(UnsafePathError):
            FileEntry.from_dict(self._raw_entry("file.txt."))
        with self.assertRaises(UnsafePathError):
            FileEntry.from_dict(self._raw_entry("../escape.txt"))

    def test_file_entry_accepts_unicode_name(self) -> None:
        from ece_autocompiler.container.model import FileEntry

        entry = FileEntry.from_dict(self._raw_entry("ñandú/datos ✨.bin"))
        self.assertEqual(entry.path, "ñandú/datos ✨.bin")

    def test_reserved_destination_is_rejected_before_any_write(self) -> None:
        import shutil

        dest = Path(tempfile.mkdtemp(prefix="ece_reserved_")) / "destino"
        self.addCleanup(shutil.rmtree, dest.parent, ignore_errors=True)
        with self.assertRaises(UnsafePathError):
            resolve_destination(dest, "con.txt")
        self.assertFalse(dest.exists())  # no directory was created


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
