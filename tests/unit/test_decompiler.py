"""Decompiler core: format detection, readers, backends and safe extraction.

No optional tool is required: every fixture is either built by the test (class
file, ELF header, CArchive, PYZ) or produced with the standard library
(py_compile, zipfile). Tests that need an external toolchain skip explicitly.
"""

from __future__ import annotations

import importlib.util
import json
import marshal
import py_compile
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler.container import ContainerWriter, Manifest, PayloadItem  # noqa: E402
from ece_autocompiler.decompiler import (  # noqa: E402
    DecompileSession,
    analyze_file,
    detect_format,
    supported_formats,
)
from ece_autocompiler.decompiler import pyinstaller, python_bytecode  # noqa: E402
from ece_autocompiler.decompiler.java_class import parse_class  # noqa: E402


def make_java_class() -> bytes:
    """Build a minimal but valid Java 8 class (Hello.greet)."""
    utf = [
        b"Hello", b"java/lang/Object", b"<init>", b"()V", b"greet",
        b"(Ljava/lang/String;)Ljava/lang/String;", b"Hello.java", b"Code", b"SourceFile",
    ]
    pool: list[bytes] = []
    index = 1
    ids: dict[bytes, int] = {}
    for value in utf:
        pool.append(b"\x01" + struct.pack(">H", len(value)) + value)
        ids[value] = index
        index += 1
    for value in (b"Hello", b"java/lang/Object"):
        pool.append(b"\x07" + struct.pack(">H", ids[value]))
        ids[b"class" + value] = index
        index += 1

    def code_attribute(body: bytes, max_locals: int = 1) -> bytes:
        payload = (
            struct.pack(">HH", 1, max_locals)
            + struct.pack(">I", len(body))
            + body
            + struct.pack(">HH", 0, 0)
        )
        return struct.pack(">HI", ids[b"Code"], len(payload)) + payload

    def method(access: int, name: bytes, descriptor: bytes, body: bytes, locals_: int = 1) -> bytes:
        return (
            struct.pack(">HHH", access, ids[name], ids[descriptor])
            + struct.pack(">H", 1)
            + code_attribute(body, locals_)
        )

    body = struct.pack(
        ">HHH", 0x21, ids[b"classHello"], ids[b"classjava/lang/Object"]
    ) + struct.pack(">H", 0)
    body += struct.pack(">H", 0) + struct.pack(">H", 2)
    body += method(1, b"<init>", b"()V", b"\xb1")
    body += method(1, b"greet", b"(Ljava/lang/String;)Ljava/lang/String;", b"\x01\xb0", 2)
    source = struct.pack(">HI", ids[b"SourceFile"], 2) + struct.pack(">H", ids[b"Hello.java"])
    body += struct.pack(">H", 1) + source
    return b"\xca\xfe\xba\xbe" + struct.pack(">HH", 0, 52) + struct.pack(">H", index) + b"".join(pool) + body


def make_elf() -> bytes:
    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4] = 2  # 64-bit
    header[5] = 1  # little endian
    header[6] = 1  # version
    struct.pack_into("<HHI", header, 16, 2, 62, 1)
    struct.pack_into("<QQQ", header, 24, 0x401000, 0, 0)
    struct.pack_into("<IHHHHHH", header, 48, 0, 64, 56, 0, 0, 0, 0)
    return bytes(header)


def make_carchive(modules: dict[str, bytes], pyver: int = 312) -> bytes:
    """Build a PyInstaller-compatible CArchive (one entry per module)."""
    body = bytearray()
    toc = bytearray()
    for name, blob in modules.items():
        offset = len(body)
        compressed = zlib.compress(blob)
        body += compressed
        padded = (name.encode() + b"\x00")
        padded += b"\x00" * ((16 - len(padded) % 16) % 16)
        toc += struct.pack(
            ">IIIIBc", 18 + len(padded), offset, len(compressed), len(blob), 1, b"m"
        ) + padded
    toc_offset = len(body)
    package_length = len(body) + len(toc) + 88
    cookie = pyinstaller.MAGIC + struct.pack(
        ">IIII64s", package_length, toc_offset, len(toc), pyver, b"python312.dll"
    )
    return bytes(body) + bytes(toc) + cookie


def make_pyz(modules: dict[str, object]) -> bytes:
    """Build a PyInstaller PYZ archive around real code objects."""
    blob = bytearray(b"PYZ\x00" + importlib.util.MAGIC_NUMBER + b"\x00\x00\x00\x00")
    table: list[tuple[str, tuple[int, int, int]]] = []
    for name, code in modules.items():
        payload = zlib.compress(marshal.dumps(code))
        offset = len(blob)
        blob += payload
        table.append((name, (0, offset, len(payload))))
    toc = marshal.dumps(table)
    toc_offset = len(blob)
    blob += toc
    struct.pack_into(">i", blob, 8, toc_offset)
    return bytes(blob)


class FormatDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece_decomp_fmt_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_python_bytecode_is_detected_and_reconstructed(self) -> None:
        source = self.tmp / "sample.py"
        source.write_text(
            "import os\n\nCONST = 42\n\nclass Greeter:\n"
            "    def hello(self, name):\n        return 'hi ' + name\n\n"
            "def main():\n    return Greeter().hello('x')\n",
            encoding="utf-8",
        )
        py_compile.compile(str(source), doraise=True)
        pyc = next((self.tmp / "__pycache__").glob("*.pyc"))
        self.assertEqual(detect_format(pyc).kind, "python-bytecode")
        analysis = analyze_file(pyc)
        self.assertIn("python_version", analysis.details)
        result = DecompileSession().decompile([pyc], self.tmp / "out")
        self.assertTrue(result.ok)
        outline = (self.tmp / "out" / "python" / "sample.py").read_text(encoding="utf-8")
        self.assertIn("import os", outline)
        self.assertIn("class Greeter", outline)
        self.assertIn("def hello(self, name)", outline)
        self.assertIn("NOT original source", outline)
        self.assertFalse(result.recompile_ready)
        disassembly = (self.tmp / "out" / "disassembly" / "sample.py.dis.txt").read_text(encoding="utf-8")
        self.assertIn("LOAD_CONST", disassembly)

    def test_java_class_model_and_outline(self) -> None:
        path = self.tmp / "Hello.class"
        path.write_bytes(make_java_class())
        self.assertEqual(detect_format(path).kind, "java-class")
        model = parse_class(path)
        self.assertEqual(model.this_class, "Hello")
        self.assertEqual([m.name for m in model.methods], ["<init>", "greet"])
        self.assertEqual(model.source_file, "Hello.java")
        result = DecompileSession().decompile([path], self.tmp / "out-java")
        self.assertTrue(result.ok)
        outline = (self.tmp / "out-java" / "java" / "Hello.java").read_text(encoding="utf-8")
        self.assertIn("public class Hello", outline)
        self.assertIn("java.lang.String greet(java.lang.String arg0)", outline)
        self.assertIn("NOT original source", outline)
        self.assertFalse(result.recompile_ready)

    def test_elf_header_is_read(self) -> None:
        path = self.tmp / "sample.elf"
        path.write_bytes(make_elf())
        fmt = detect_format(path)
        self.assertEqual(fmt.kind, "elf")
        self.assertEqual(fmt.architecture, "x86-64")
        analysis = analyze_file(path)
        self.assertIn("entry_point", analysis.details)
        result = DecompileSession().decompile([path], self.tmp / "out-elf")
        self.assertTrue(result.ok)
        self.assertTrue((self.tmp / "out-elf" / "analysis" / "elf.json").is_file())
        self.assertTrue(any(item.path.startswith("analysis/") for item in result.files))

    def test_zip_and_source_detection(self) -> None:
        archive = self.tmp / "bundle.zip"
        with zipfile.ZipFile(archive, "w") as handle:
            handle.writestr("app.py", "print('x')\n")
            handle.writestr("data/readme.txt", "hello")
        self.assertEqual(detect_format(archive).kind, "zip-python")
        result = DecompileSession().decompile([archive], self.tmp / "out-zip")
        self.assertTrue(result.ok)
        self.assertTrue((self.tmp / "out-zip" / "python" / "app.py").is_file())
        self.assertTrue(result.recompile_ready)
        text = self.tmp / "notes.md"
        text.write_text("# notes\n", encoding="utf-8")
        self.assertEqual(detect_format(text).kind, "source-markdown")

    def test_tar_and_gzip_detection(self) -> None:
        import tarfile

        tar = self.tmp / "items.tar"
        source = self.tmp / "one.txt"
        source.write_text("x" * 100, encoding="utf-8")
        with tarfile.open(tar, "w") as handle:
            handle.add(source, arcname="one.txt")
        self.assertEqual(detect_format(tar).kind, "tar")
        result = DecompileSession().decompile([tar], self.tmp / "out-tar")
        self.assertTrue(result.ok)
        self.assertTrue((self.tmp / "out-tar" / "resources" / "one.txt").is_file())

    @unittest.skipUnless(sys.platform.startswith("win"), "PE is a Windows format")
    def test_windows_pe_is_analysed(self) -> None:
        exe = Path(sys.executable)
        if not exe.is_file() or exe.read_bytes()[:2] != b"MZ":
            self.skipTest("the running interpreter is not a PE binary")
        fmt = detect_format(exe)
        self.assertIn(fmt.kind, ("pe-native", "pe-dotnet"))
        analysis = analyze_file(exe)
        self.assertIn("machine", analysis.details)
        self.assertTrue(analysis.details.get("sections"))

    def test_ece_container_is_extracted_and_recoverable(self) -> None:
        source = self.tmp / "app.py"
        source.write_text("print('recover me')\n", encoding="utf-8")
        manifest = Manifest(project_name="demo", adapter_id="python", payload_type="python")
        container = self.tmp / "demo.ece"
        ContainerWriter(compression="zlib").write(
            container, [PayloadItem(path="app.py", source=source)], manifest, overwrite=True
        )
        self.assertEqual(detect_format(container).kind, "ece")
        result = DecompileSession().decompile([container], self.tmp / "out-ece")
        self.assertTrue(result.ok)
        self.assertEqual(result.project_kind, "python")
        self.assertTrue(result.recompile_ready)
        recovered = self.tmp / "out-ece" / "source" / "app.py"
        self.assertEqual(recovered.read_text(encoding="utf-8"), "print('recover me')\n")
        manifest_file = self.tmp / "out-ece" / "recovered-project.json"
        self.assertTrue(manifest_file.is_file())
        self.assertTrue(json.loads(manifest_file.read_text(encoding="utf-8"))["files"])

    def test_multi_input_projects_are_namespaced(self) -> None:
        first = self.tmp / "one.pyc"
        second = self.tmp / "two.pyc"
        for name, path in (("one", first), ("two", second)):
            source = self.tmp / f"{name}.py"
            source.write_text("x = 1\n", encoding="utf-8")
            py_compile.compile(str(source), cfile=str(path), doraise=True)
        result = DecompileSession().decompile([first, second], self.tmp / "out-multi")
        self.assertTrue(result.ok)
        self.assertFalse(result.recompile_ready)
        self.assertTrue(any(item.path.startswith("one/") for item in result.files))
        self.assertTrue(any(item.path.startswith("two/") for item in result.files))

    def test_unsupported_format_reports_analysis_only(self) -> None:
        path = self.tmp / "mystery.bin"
        path.write_bytes(b"\x00\x01\x02\x03" * 100)
        result = DecompileSession().decompile([path], self.tmp / "out-bin")
        self.assertTrue(result.ok)
        self.assertFalse(result.recompile_ready)
        self.assertTrue(any("no decompilation backend" in warning for warning in result.warnings))

    def test_formats_catalog_is_not_empty(self) -> None:
        rows = supported_formats()
        self.assertTrue(any(row["format"] == "pe-dotnet" for row in rows))
        self.assertTrue(any(row["format"] == "ece" for row in rows))


class PyInstallerReaderTests(unittest.TestCase):
    def test_carchive_roundtrip(self) -> None:
        code = compile("value = 1\n", "<frozen>", "exec")
        payload = marshal.dumps(code)
        archive = make_carchive({"frozenmod": payload, "data.txt": b"hello"})
        path = Path(tempfile.mkdtemp(prefix="ece_carchive_")) / "bundle.exe"
        path.write_bytes(archive)
        info = pyinstaller.read_archive(path)
        self.assertEqual(len(info.entries), 2)
        entries = {name: blob for _type, name, blob in pyinstaller.extract_entries(path, info)}
        self.assertIn("frozenmod", entries)
        module = python_bytecode.module_from_bytes(
            pyinstaller.module_payload("frozenmod", entries["frozenmod"])[1], name="frozenmod"
        )
        self.assertIsNotNone(module.code)

    def test_pyz_roundtrip(self) -> None:
        code = compile("import os\n\ndef main():\n    return 1\n", "<frozen>", "exec")
        blob = make_pyz({"pkg.mod": code})
        magic, modules, warnings = pyinstaller.read_pyz(blob)
        self.assertEqual(len(modules), 1)
        self.assertEqual(modules[0][0], "pkg.mod")
        self.assertEqual(warnings, [])
        self.assertTrue(magic)
        outline = python_bytecode.recover_outline(
            python_bytecode.module_from_code(modules[0][1], name="pkg.mod")
        )
        self.assertIn("def main():", outline)


class SafeExtractionTests(unittest.TestCase):
    def test_zip_path_traversal_is_skipped(self) -> None:
        from ece_autocompiler.decompiler import archive

        tmp = Path(tempfile.mkdtemp(prefix="ece_zip_safety_"))
        archive_path = tmp / "evil.zip"
        with zipfile.ZipFile(archive_path, "w") as handle:
            handle.writestr("ok.txt", "good")
            handle.writestr("../escape.txt", "bad")
        destination = tmp / "out"
        report = archive.extract_archive(archive_path, destination)
        self.assertEqual(report.files_written, 1)
        self.assertTrue((destination / "ok.txt").is_file())
        self.assertFalse((tmp / "escape.txt").exists())
        self.assertTrue(any("unsafe" in item for item in report.skipped))
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
