"""Corrupt and hostile .ece containers must fail honestly at every layer.

Covers the failure matrix beyond happy-path round trips: zero-byte files,
truncation, tampered manifest/index/payload bytes, header inconsistencies and
the guarantee that a failed extraction leaves no partial artifacts behind.
"""

from __future__ import annotations

import struct
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
    ContainerReader,
    ContainerWriter,
    Manifest,
    PayloadItem,
    validate_container,
)
from ece_autocompiler.container.spec import HEADER_SIZE, HEADER_STRUCT  # noqa: E402
from ece_autocompiler.errors import (  # noqa: E402
    ContainerError,
    ContainerFormatError,
    IntegrityError,
    UnsafePathError,
)
from ece_autocompiler.extraction import Extractor  # noqa: E402


def _write_store_container(tmp: Path) -> Path:
    """Build a deterministic container with store (no) compression."""
    files = {
        "a.txt": b"hello world\n" * 16,
        "dir/b.bin": bytes(range(256)) * 4,
    }
    items = []
    for i, (rel, data) in enumerate(files.items()):
        src = tmp / f"payload_{i}"
        src.write_bytes(data)
        items.append(PayloadItem(path=rel, source=src))
    manifest = Manifest(project_name="corrupt", adapter_id="python", payload_type="python")
    out = tmp / "corrupt.ece"
    ContainerWriter(compression="store").write(out, items, manifest, overwrite=True)
    return out


class _PatchMixin:
    """Byte-level patching helpers for a store-compressed container."""

    tmp: Path
    container: Path

    def _read(self) -> bytes:
        return self.container.read_bytes()

    def _write(self, data: bytes) -> None:
        self.container.write_bytes(data)

    def _header_field(self, name: str):
        fields = dict(zip(
            ("magic", "major", "minor", "flags", "header_size", "compression_id",
             "hash_algo_id", "reserved_a", "entry_count", "manifest_offset",
             "manifest_length", "manifest_raw_length", "index_offset",
             "index_length", "index_raw_length", "payload_offset",
             "reserved_b", "reserved_c"),
            HEADER_STRUCT.unpack(self._read()[:HEADER_SIZE]),
        ))
        return fields[name]

    def _set_entry_count(self, value: int) -> None:
        data = bytearray(self._read())
        struct.pack_into("<I", data, 24, value)  # entry_count is uint32 at byte 24
        self._write(bytes(data))

    def _patch_region(self, offset: int, length: int, blob: bytes) -> None:
        assert len(blob) == length, "patch must keep the region length"
        data = bytearray(self._read())
        data[offset:offset + length] = blob
        self._write(bytes(data))


class CorruptStructureTests(unittest.TestCase, _PatchMixin):
    def setUp(self) -> None:
        import shutil

        self.tmp = Path(tempfile.mkdtemp(prefix="ece_corrupt_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.container = _write_store_container(self.tmp)

    # ------------------------------------------------------------- structure

    def test_zero_byte_file(self) -> None:
        empty = self.tmp / "empty.ece"
        empty.write_bytes(b"")
        with self.assertRaises(ContainerFormatError):
            ContainerReader(empty).open()
        report = validate_container(empty)
        self.assertFalse(report.ok)
        self.assertEqual(report.errors[0].code, "ece.container.format")

    def test_directory_instead_of_file(self) -> None:
        directory = self.tmp / "folder.ece"
        directory.mkdir()
        with self.assertRaises(ContainerError):
            ContainerReader(directory).open()
        report = validate_container(directory)
        self.assertFalse(report.ok)
        self.assertEqual(report.errors[0].code, "ece.container.not_a_file")

    def test_missing_file_validates_invalid(self) -> None:
        report = validate_container(self.tmp / "nope.ece")
        self.assertFalse(report.ok)
        self.assertEqual(report.errors[0].code, "ece.container.missing")

    def test_truncated_container(self) -> None:
        original = self._read()
        broken = self.tmp / "truncated.ece"
        broken.write_bytes(original[: len(original) // 2])
        with self.assertRaises(ContainerError):
            ContainerReader(broken).open()
        report = validate_container(broken)
        self.assertFalse(report.ok)

    def test_truncated_extraction_writes_nothing(self) -> None:
        original = self._read()
        broken = self.tmp / "truncated2.ece"
        broken.write_bytes(original[: 200])  # header + part of manifest
        dest = self.tmp / "dest_trunc"
        with self.assertRaises(ContainerError):
            Extractor().extract(broken, dest)
        leftovers = list(dest.rglob("*")) if dest.exists() else []
        self.assertEqual(leftovers, [], f"extraction left files behind: {leftovers}")

    def test_entry_count_mismatch_in_header(self) -> None:
        self._set_entry_count(self._header_field("entry_count") + 5)
        with self.assertRaises(ContainerFormatError) as caught:
            ContainerReader(self.container).open()
        self.assertIn("entry count", str(caught.exception).lower())
        report = validate_container(self.container)
        self.assertFalse(report.ok)

    # ------------------------------------------------------- manifest / index

    def test_manifest_bytes_are_garbage(self) -> None:
        offset = self._header_field("manifest_offset")
        length = self._header_field("manifest_length")
        self._patch_region(offset, length, b"\x00" * length)
        with self.assertRaises(ContainerFormatError):
            ContainerReader(self.container).open()
        report = validate_container(self.container)
        self.assertFalse(report.ok)

    def test_manifest_is_valid_json_but_wrong_shape(self) -> None:
        offset = self._header_field("manifest_offset")
        length = self._header_field("manifest_length")
        # Parses as JSON but "project" is a string instead of an object; pad
        # with whitespace so the on-disk region length stays identical.
        blob = b'{"project":"not an object"}'
        assert len(blob) <= length
        blob = blob + b" " * (length - len(blob))
        self._patch_region(offset, length, blob)
        with self.assertRaises(ContainerFormatError):
            ContainerReader(self.container).open()
        report = validate_container(self.container)
        self.assertFalse(report.ok)

    def test_index_bytes_are_garbage(self) -> None:
        offset = self._header_field("index_offset")
        length = self._header_field("index_length")
        self._patch_region(offset, length, b"\xff" * length)
        with self.assertRaises(ContainerFormatError):
            ContainerReader(self.container).open()
        report = validate_container(self.container)
        self.assertFalse(report.ok)

    def test_hostile_path_inside_index(self) -> None:
        # Swap the legal path "a.txt" for the reserved device name "con",
        # keeping the JSON region the same byte length by padding whitespace.
        offset = self._header_field("index_offset")
        length = self._header_field("index_length")
        region = self._read()[offset:offset + length]
        needle = b'"a.txt"'
        self.assertEqual(region.count(needle), 1, "expected exactly one path value")
        self.assertNotIn(needle, self._read()[:offset])  # not in header/manifest
        replacement = b'"con"' + b" " * (len(needle) - len(b'"con"'))
        self._patch_region(offset, length, region.replace(needle, replacement))
        with self.assertRaises(UnsafePathError):
            ContainerReader(self.container).open()
        report = validate_container(self.container)
        self.assertFalse(report.ok)
        self.assertEqual(report.errors[0].code, "ece.container.unsafe_path")

    # ----------------------------------------------------------- extraction

    def test_tampered_payload_fails_deep_validation_and_cleans_up(self) -> None:
        with ContainerReader(self.container) as reader:
            header = reader.header
            assert header is not None
            entry = reader.get_entry("dir/b.bin")
            assert entry is not None
            payload_offset = header.payload_offset
            target = payload_offset + entry.offset

        data = bytearray(self._read())
        data[target] ^= 0xFF  # flip a payload byte; layout stays identical
        self._write(bytes(data))

        report = validate_container(self.container, deep=True)
        self.assertFalse(report.ok)
        self.assertIn("dir/b.bin", report.failed_entries)

        dest = self.tmp / "dest_tampered"
        with self.assertRaises(IntegrityError):
            Extractor().extract(self.container, dest)

        parts = list(dest.rglob("*.ece-part"))
        self.assertEqual(parts, [], f"temporary files left behind: {parts}")
        self.assertFalse((dest / "dir" / "b.bin").exists(), "tampered file was kept")
        # The untampered first entry may legitimately have been written already.
        self.assertTrue((dest / "a.txt").exists())


class CorruptCliBehaviourTests(unittest.TestCase):
    """CLI must return non-zero and print a clean error, never a traceback."""

    def setUp(self) -> None:
        import shutil

        self.tmp = Path(tempfile.mkdtemp(prefix="ece_corrupt_cli_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _run_cli(self, *argv: str) -> tuple[int, str]:
        import io
        from contextlib import redirect_stdout, redirect_stderr

        from ece_autocompiler import cli

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_validate_zero_byte_returns_error_code(self) -> None:
        empty = self.tmp / "zero.ece"
        empty.write_bytes(b"")
        code, output = self._run_cli("validate", str(empty))
        self.assertEqual(code, 1)
        self.assertIn("INVALID", output)
        self.assertNotIn("Traceback", output)

    def test_validate_zero_byte_json_reports_failure(self) -> None:
        import json

        empty = self.tmp / "zero.ece"
        empty.write_bytes(b"")
        code, output = self._run_cli("validate", "--json", str(empty))
        self.assertEqual(code, 1)
        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["issues"])

    def test_extract_corrupt_returns_error_code(self) -> None:
        broken = self.tmp / "broken.ece"
        broken.write_bytes(b"\x89ECE\r\n\x1a\n" + b"\x00" * 90)  # header-ish garbage
        dest = self.tmp / "out"
        code, output = self._run_cli("extract", str(broken), str(dest))
        self.assertEqual(code, 1)
        self.assertIn("error", output.lower())
        self.assertNotIn("Traceback", output)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
