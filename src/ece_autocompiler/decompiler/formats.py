"""Universal, read-only file format detection.

Detection uses magic bytes first, the PE/ELF/Java headers second and the file
extension only as a last hint. Nothing here executes the imported file: every
reader is bounded and works on bytes.

The returned :class:`FormatInfo` lists the operations this build can actually
perform. A format may be *identified* (and analysed safely) without a
decomplation backend being available; the UI shows the difference instead of
offering a button that cannot work.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from ..container.spec import MAGIC as ECE_MAGIC
from .models import FormatInfo

# CPython bytecode magic numbers (first two bytes, little-endian).
PYC_MAGICS: dict[bytes, str] = {
    b"\x03\xf3": "2.6",
    b"\x33\x0d": "3.6",
    b"\x42\x0d": "3.7",
    b"\x55\x0d": "3.8",
    b"\x61\x0d": "3.9",
    b"\x6f\x0d": "3.10",
    b"\xa7\x0d": "3.11",
    b"\xcb\x0d": "3.12",
    b"\xf3\x0d": "3.13",
}

_PYINSTALLER_MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"
_ELF_MACHINES = {
    3: "x86 (32-bit)",
    8: "MIPS",
    20: "PowerPC",
    21: "PowerPC64",
    40: "ARM",
    62: "x86-64",
    183: "ARM64",
    243: "RISC-V",
}
_PE_MACHINES = {
    0x014C: "x86 (32-bit)",
    0x0166: "MIPS",
    0x01C0: "ARM",
    0x01C4: "ARMv7",
    0x8664: "x86-64",
    0xAA64: "ARM64",
    0x0200: "Itanium",
}
_PE_MACHINE_NAMES = {
    0x014C: "i386",
    0x8664: "AMD64",
    0xAA64: "ARM64",
    0x01C4: "ARMv7",
}

SOURCE_EXTENSIONS = {
    ".py": ("python", "Python"),
    ".pyw": ("python", "Python"),
    ".java": ("java", "Java"),
    ".cs": ("csharp", "C#"),
    ".vb": ("vbnet", "Visual Basic .NET"),
    ".fs": ("fsharp", "F#"),
    ".c": ("c", "C"),
    ".h": ("c", "C header"),
    ".cc": ("cpp", "C++"),
    ".cpp": ("cpp", "C++"),
    ".cxx": ("cpp", "C++"),
    ".hpp": ("cpp", "C++ header"),
    ".js": ("javascript", "JavaScript"),
    ".mjs": ("javascript", "JavaScript"),
    ".ts": ("typescript", "TypeScript"),
    ".tsx": ("typescript", "TypeScript/TSX"),
    ".jsx": ("javascript", "JavaScript/JSX"),
    ".go": ("go", "Go"),
    ".rs": ("rust", "Rust"),
    ".php": ("php", "PHP"),
    ".rb": ("ruby", "Ruby"),
    ".kt": ("kotlin", "Kotlin"),
    ".swift": ("swift", "Swift"),
    ".lua": ("lua", "Lua"),
    ".sql": ("sql", "SQL"),
    ".html": ("html", "HTML"),
    ".css": ("css", "CSS"),
    ".json": ("json", "JSON"),
    ".xml": ("xml", "XML"),
    ".toml": ("toml", "TOML"),
    ".yaml": ("yaml", "YAML"),
    ".yml": ("yaml", "YAML"),
    ".md": ("markdown", "Markdown"),
    ".txt": ("text", "Plain text"),
}

_ARCHIVE_SUFFIXES = {".zip", ".jar", ".war", ".whl", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}


def _read_head(path: Path, count: int = 4096) -> bytes:
    try:
        with open(path, "rb") as handle:
            return handle.read(count)
    except OSError:
        return b""


def _read_tail(path: Path, count: int = 4096) -> bytes:
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            if size > count:
                handle.seek(size - count)
            return handle.read(count)
    except OSError:
        return b""


def _pe_header(head: bytes) -> dict:
    """Minimal PE header read (DOS -> NT -> COFF -> optional)."""
    info: dict[str, object] = {}
    if len(head) < 0x40 or head[:2] != b"MZ":
        return info
    import struct

    try:
        e_lfanew = struct.unpack_from("<I", head, 0x3C)[0]
        if e_lfanew + 24 > len(head) or head[e_lfanew : e_lfanew + 4] != b"PE\0\0":
            return info
        machine, sections, _stamp, _sym, _numsym, opt_size, characteristics = struct.unpack_from(
            "<HHIIIHH", head, e_lfanew + 4
        )
        info["machine_id"] = machine
        info["machine"] = _PE_MACHINES.get(machine, f"unknown (0x{machine:04x})")
        info["sections"] = sections
        info["is_dll"] = bool(characteristics & 0x2000)
        opt = e_lfanew + 24
        if opt + 2 <= len(head):
            magic = struct.unpack_from("<H", head, opt)[0]
            info["pe32_plus"] = magic == 0x20B
            if opt + 70 <= len(head):
                info["subsystem"] = struct.unpack_from("<H", head, opt + 68)[0]
            dirs_at = opt + (108 if magic == 0x20B else 92)
            if dirs_at + 4 <= len(head):
                count = struct.unpack_from("<I", head, dirs_at)[0]
                info["data_directories"] = min(count, 16)
                clr_at = dirs_at + 4 + 14 * 8
                if count > 14 and clr_at + 8 <= len(head):
                    clr_rva, clr_size = struct.unpack_from("<II", head, clr_at)
                    info["clr_rva"] = clr_rva
                    info["clr_size"] = clr_size
                    info["is_dotnet"] = bool(clr_rva and clr_size)
    except Exception:
        return info
    return info


def _elf_header(head: bytes) -> dict:
    info: dict[str, object] = {}
    if len(head) < 20 or head[:4] != b"\x7fELF":
        return info
    import struct

    ei_class = head[4]
    ei_data = head[5]
    end = "<" if ei_data == 1 else ">"
    try:
        e_type, e_machine = struct.unpack_from(end + "HH", head, 16)
        info["elf_class"] = 64 if ei_class == 2 else 32
        info["little_endian"] = ei_data == 1
        info["type_id"] = e_type
        info["type"] = {1: "relocatable", 2: "executable", 3: "shared object", 4: "core"}.get(e_type, "unknown")
        info["machine_id"] = e_machine
        info["machine"] = _ELF_MACHINES.get(e_machine, f"unknown ({e_machine})")
        info["architecture"] = info["machine"]
    except Exception:
        return info
    return info


def _zip_kind(path: Path) -> tuple[str, list[str], bool]:
    """Return (kind, first entries, has META-INF/MANIFEST.MF)."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()[:400]
    except (zipfile.BadZipFile, OSError, RuntimeError):
        return "zip", [], False
    has_manifest = any(n.upper() == "META-INF/MANIFEST.MF" for n in names)
    if any(n.endswith(".class") for n in names):
        return ("jar" if has_manifest or path.suffix.lower() in (".jar", ".war") else "java-archive", names, has_manifest)
    if any(".dist-info/" in n or n.endswith(".dist-info/METADATA") for n in names):
        return "python-wheel", names, has_manifest
    if any(n.endswith(".py") for n in names):
        return "zip-python", names, has_manifest
    return "zip", names, has_manifest


def detect_format(path: Path) -> FormatInfo:
    """Detect *path* by content, falling back to its extension."""
    path = Path(path)
    head = _read_head(path)
    tail = _read_tail(path)
    suffix = path.suffix.lower()

    if head.startswith(ECE_MAGIC):
        return FormatInfo(
            kind="ece",
            label="ECE container",
            language="mixed",
            runtime="ECE",
            family="container",
            confidence=1.0,
            evidence=("ECE magic header",),
            operations=("analyze", "extract", "decompile", "recompile"),
            notes=("Extracted with the project's own validated .ece reader.",),
        )

    if head[:4] == b"\xca\xfe\xba\xbe" and len(head) >= 8:
        import struct

        minor, major = struct.unpack_from(">HH", head, 4)
        return FormatInfo(
            kind="java-class",
            label=f"Java class (bytecode {major}.{minor})",
            language="Java",
            runtime="JVM",
            architecture="bytecode",
            family="java",
            confidence=1.0,
            evidence=(f"CAFEBABE header, class file version {major}.{minor}",),
            operations=("analyze", "decompile", "disassemble"),
            notes=("Source recovery uses javap when a JDK is installed; otherwise a "
                   "named class outline is generated from the constant pool.",),
        )

    if head[:2] in PYC_MAGICS and head[2:4] == b"\r\n":
        version = PYC_MAGICS[head[:2]]
        return FormatInfo(
            kind="python-bytecode",
            label=f"Python bytecode (CPython {version})",
            language="Python",
            runtime=f"CPython {version}",
            architecture="bytecode",
            family="python",
            confidence=0.98,
            evidence=(f"pyc magic {head[:4].hex()} for CPython {version}",),
            operations=("analyze", "decompile", "disassemble"),
            notes=("Code objects are read with the running interpreter's marshal "
                   "format; cross-version bytecode is reported, not guessed.",),
        )

    if head[:4] == b"PK\x03\x04" or (suffix in (".jar", ".war", ".whl", ".zip") and head[:2] == b"PK"):
        kind, names, _manifest = _zip_kind(path)
        labels = {
            "jar": "Java archive (JAR)",
            "java-archive": "ZIP archive with Java classes",
            "python-wheel": "Python wheel",
            "zip-python": "ZIP archive with Python sources",
            "zip": "ZIP archive",
        }
        family = "archive"
        operations = ["analyze", "extract"]
        if kind in ("jar", "java-archive"):
            family = "java"
            operations += ["decompile", "disassemble"]
        elif kind == "python-wheel":
            family = "python"
            operations += ["decompile"]
        elif kind == "zip-python":
            family = "python"
            operations += ["decompile", "recompile"]
        return FormatInfo(
            kind=kind,
            label=labels.get(kind, "ZIP archive"),
            language="Java" if family == "java" else ("Python" if family == "python" else "mixed"),
            runtime="JVM" if family == "java" else ("CPython" if family == "python" else "unknown"),
            family=family,
            confidence=0.9 if kind != "zip" else 0.8,
            evidence=(f"ZIP local header; {len(names)} entries inspected",),
            operations=tuple(operations),
            notes=(f"First entries: {', '.join(names[:5])}",) if names else (),
        )

    if _PYINSTALLER_MAGIC in tail:
        return FormatInfo(
            kind="python-packaged",
            label="Python application packaged with PyInstaller",
            language="Python",
            runtime="PyInstaller bootloader",
            architecture=_pe_header(head).get("machine", "unknown"),
            family="python",
            confidence=0.95,
            evidence=("PyInstaller CArchive cookie (MEI) found near the end of the file",),
            operations=("analyze", "decompile", "extract", "disassemble"),
            notes=("The embedded CArchive is extracted and its frozen modules are "
                   "reconstructed as Python bytecode outlines.",),
        )

    if head[:2] == b"MZ":
        pe = _pe_header(head)
        if pe.get("is_dotnet"):
            arch = pe.get("machine", "unknown")
            return FormatInfo(
                kind="pe-dotnet",
                label=".NET assembly (PE)",
                language="C# / .NET",
                runtime=".NET (CLR)",
                architecture=arch,
                family="dotnet",
                confidence=1.0,
                evidence=("MZ/PE header", "CLR runtime data directory is present"),
                operations=("analyze", "decompile", "extract", "disassemble"),
                notes=("Full C# recovery uses ILSpy (ilspycmd). Without it, metadata "
                       "and a namespace/class outline are produced.",),
            )
        return FormatInfo(
            kind="pe-native",
            label="Windows PE executable" if not pe.get("is_dll") else "Windows PE library",
            language="C/C++ (compiled)",
            runtime="native Windows",
            architecture=pe.get("machine", "unknown"),
            family="pe",
            confidence=1.0,
            evidence=("MZ/PE header", f"{pe.get('sections', '?')} sections"),
            operations=("analyze", "extract", "disassemble"),
            notes=("Original C/C++ source cannot be restored from a native binary. "
                   "Imports, exports, resources, strings and disassembly are real; "
                   "pseudocode is only offered when a disassembler is available.",),
        )

    if head[:4] == b"\x7fELF":
        elf = _elf_header(head)
        return FormatInfo(
            kind="elf",
            label=f"ELF {elf.get('type', 'binary')}",
            language="C/C++/Rust/Go (compiled)",
            runtime="native Unix",
            architecture=elf.get("architecture", "unknown"),
            family="elf",
            confidence=1.0,
            evidence=("ELF magic", f"class {elf.get('elf_class', '?')}-bit"),
            operations=("analyze", "extract", "disassemble"),
            notes=("Original source cannot be restored from an ELF binary. Headers, "
                   "sections, symbols, dependencies, strings and disassembly are real.",),
        )

    if head[:6] == b"\x37\x7a\xbc\xaf\x27\x1c":
        return FormatInfo(
            kind="7z",
            label="7-Zip archive",
            language="mixed",
            family="archive",
            confidence=1.0,
            evidence=("7z signature",),
            operations=("analyze",),
            notes=("Extraction needs 7-Zip or py7zr; neither is bundled.",),
        )
    if head[:4] == b"Rar!":
        return FormatInfo(
            kind="rar",
            label="RAR archive",
            language="mixed",
            family="archive",
            confidence=1.0,
            evidence=("RAR signature",),
            operations=("analyze",),
            notes=("Extraction needs unrar/7-Zip; neither is bundled.",),
        )
    if head[:2] == b"\x1f\x8b" or suffix in (".gz", ".tgz"):
        return FormatInfo(
            kind="gzip",
            label="GZip-compressed file",
            language="mixed",
            family="archive",
            confidence=0.95,
            evidence=("GZip signature",),
            operations=("analyze", "extract"),
        )
    if head[:3] == b"BZh" or suffix == ".bz2":
        return FormatInfo(
            kind="bzip2",
            label="BZip2-compressed file",
            language="mixed",
            family="archive",
            confidence=0.95,
            evidence=("BZip2 signature",),
            operations=("analyze", "extract"),
        )
    if head[:6] == b"\xfd7zXZ\x00" or suffix == ".xz":
        return FormatInfo(
            kind="xz",
            label="XZ-compressed file",
            language="mixed",
            family="archive",
            confidence=0.95,
            evidence=("XZ signature",),
            operations=("analyze", "extract"),
        )
    if len(head) > 262 and head[257:262] == b"ustar":
        return FormatInfo(
            kind="tar",
            label="TAR archive",
            language="mixed",
            family="archive",
            confidence=0.95,
            evidence=("ustar marker at offset 257",),
            operations=("analyze", "extract"),
        )

    if suffix in SOURCE_EXTENSIONS:
        language, label = SOURCE_EXTENSIONS[suffix]
        return FormatInfo(
            kind=f"source-{language}",
            label=f"{label} source",
            language=label,
            runtime="source",
            architecture="any",
            family="source",
            confidence=0.85,
            evidence=(f"file extension {suffix}",),
            operations=("analyze", "recompile") if language in (
                "python", "java", "csharp", "c", "cpp", "go", "rust", "javascript", "typescript"
            ) else ("analyze",),
        )

    # Plain text vs unknown binary: a NUL byte in the first 4 KiB is the
    # cheapest honest discriminator.
    if head and b"\x00" not in head:
        try:
            head.decode("utf-8")
            return FormatInfo(
                kind="text",
                label="Text file",
                language="text",
                family="text",
                confidence=0.6,
                evidence=("no NUL byte in the first 4 KiB", "valid UTF-8 prefix"),
                operations=("analyze",),
            )
        except UnicodeDecodeError:
            pass
    if suffix:
        return FormatInfo(
            kind="binary",
            label=f"Binary file ({suffix})",
            language="unknown",
            family="unknown",
            confidence=0.3,
            evidence=(f"unrecognised content; extension {suffix}",),
            operations=("analyze",),
            notes=("This build has no reader for this format.",),
        )
    return FormatInfo(
        kind="unknown",
        label="Unknown binary",
        language="unknown",
        family="unknown",
        confidence=0.1,
        evidence=("no known magic bytes or extension",),
        operations=("analyze",),
        notes=("This build has no reader for this format.",),
    )


def supported_formats() -> list[dict[str, str]]:
    """Catalog shown by ``ece-autocompiler formats`` and the About dialog."""
    rows = [
        ("pe-native", "Windows PE .exe/.dll", "analysis, imports, exports, resources, strings, disassembly"),
        ("pe-dotnet", ".NET assembly (PE)", "C# recovery with ILSpy, metadata, resources, project rebuild"),
        ("elf", "Linux ELF binary", "headers, sections, symbols, dependencies, strings, disassembly"),
        ("java-class", "Java .class", "class model, javap source/disassembly, outline fallback"),
        ("jar", "Java .jar/.war", "archive listing, class analysis, resource extraction, rebuild"),
        ("python-bytecode", "Python .pyc", "module outline, imports, disassembly"),
        ("python-packaged", "PyInstaller application", "CArchive extraction, frozen-module outlines"),
        ("python-wheel", "Python wheel", "archive listing, extraction"),
        ("ece", "ECE container", "manifest, verified extraction, recursive analysis"),
        ("zip/tar/gzip/xz/bzip2", "Archives", "safe listing and extraction"),
        ("source-*", "C, C++, C#, Java, Python, Go, Rust, JS/TS, PHP", "analysis and real recompilation"),
    ]
    return [{"format": kind, "label": label, "operations": ops} for kind, label, ops in rows]
