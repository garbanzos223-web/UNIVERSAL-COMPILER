import io
import sys
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
from ece_autocompiler.container import spec  # noqa: E402
from ece_autocompiler.extraction import Extractor  # noqa: E402
from ece_autocompiler.errors import ContainerError, ContainerFormatError  # noqa: E402


def _write_sample(tmp: Path, compression: str = "zlib") -> tuple[Path, dict[str, bytes]]:
    files = {
        "a.txt": b"hello world\n" * 20,
        "dir/b.bin": bytes(range(256)),
        "dir/deep/c.txt": b"deep\n",
        "empty.txt": b"",
    }
    items = []
    for i, (rel, data) in enumerate(files.items()):
        src = tmp / f"src_{i}"
        src.write_bytes(data)
        items.append(PayloadItem(path=rel, source=src))
    manifest = Manifest(project_name="sample", adapter_id="python", payload_type="python")
    out = tmp / f"sample_{compression}.ece"
    ContainerWriter(compression=compression).write(out, items, manifest, overwrite=True)
    return out, files


class ContainerRoundtripTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp = Path(tempfile.mkdtemp())

    def test_magic_and_header(self) -> None:
        out, _ = _write_sample(self.tmp)
        with open(out, "rb") as fh:
            head = fh.read(len(spec.MAGIC))
        self.assertEqual(head, spec.MAGIC)

    def test_roundtrip_all_compressions(self) -> None:
        for compression in ("store", "zlib", "lzma"):
            with self.subTest(compression=compression):
                out, files = _write_sample(self.tmp, compression)
                dest = self.tmp / f"out_{compression}"
                Extractor().extract(out, dest, overwrite=True)
                for rel, data in files.items():
                    written = (dest / rel).read_bytes()
                    self.assertEqual(written, data, f"{rel} differs for {compression}")

    def test_validate_deep(self) -> None:
        out, _ = _write_sample(self.tmp)
        report = validate_container(out, deep=True)
        self.assertTrue(report.ok, msg=str(report.issues))
        self.assertEqual(report.entry_count, 4)
        self.assertTrue(report.hashes_verified)

    def test_reader_manifest(self) -> None:
        out, _ = _write_sample(self.tmp)
        with ContainerReader(out) as reader:
            self.assertEqual(reader.manifest.project_name, "sample")
            self.assertEqual(reader.manifest.adapter_id, "python")
            self.assertEqual(len(reader.entries()), 4)

    def test_corruption_detected(self) -> None:
        out, _ = _write_sample(self.tmp)
        raw = bytearray(out.read_bytes())
        # Flip a byte inside the payload region (well past the header).
        raw[len(raw) // 2] ^= 0xFF
        out.write_bytes(raw)
        report = validate_container(out, deep=True)
        self.assertFalse(report.ok)

    def test_truncation_detected(self) -> None:
        out, _ = _write_sample(self.tmp)
        raw = out.read_bytes()
        out.write_bytes(raw[: len(raw) - 40])
        report = validate_container(out, deep=True)
        self.assertFalse(report.ok)

    def test_bad_magic_rejected(self) -> None:
        bad = self.tmp / "bad.ece"
        bad.write_bytes(b"NOTECE!!" + b"\x00" * 200)
        with self.assertRaises((ContainerError, ContainerFormatError)):
            with ContainerReader(bad):
                pass

    def test_empty_payload_rejected(self) -> None:
        manifest = Manifest(project_name="x")
        with self.assertRaises(ContainerError):
            ContainerWriter().write(self.tmp / "empty.ece", [], manifest, overwrite=True)


if __name__ == "__main__":
    unittest.main()
