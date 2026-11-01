"""PyInstaller CArchive reader (bounded, read-only).

PyInstaller appends a ``CArchive`` to its bootloader. The archive is found by
its 8-byte cookie, then its table of contents is parsed and every entry is
extracted with explicit limits. Nothing is executed: the result is data
(modules, resources) that the Python backend turns into bytecode outlines.

Format reference: the cookie and TOC layout used by PyInstaller 4.x-6.x,
independently re-implemented here for analysis.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"
COOKIE_SIZE = 88
MAX_ENTRIES = 20000
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_ENTRY_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
_SAFE_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-+/@ ")


@dataclass
class ArchiveEntry:
    name: str
    typecode: str
    offset: int
    compressed_length: int
    uncompressed_length: int
    compressed: bool


@dataclass
class ArchiveInfo:
    path: Path
    start: int
    length: int
    python_version: str = ""
    entries: list[ArchiveEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _safe_relative(name: str) -> str:
    cleaned = "".join(ch if ch in _SAFE_NAME_CHARS else "_" for ch in name).replace("\\", "/")
    parts = [part for part in cleaned.split("/") if part not in ("", ".", "..")]
    if not parts:
        parts = ["unnamed"]
    return "/".join(parts)


def find_cookie(data: bytes) -> int:
    # The cookie is 88 bytes from the end of the archive (not necessarily of
    # the file: a signature may follow). Search the last 2 MiB.
    window_start = max(0, len(data) - 2 * 1024 * 1024)
    index = data.rfind(MAGIC, window_start)
    return index


def read_archive(path: Path) -> ArchiveInfo:
    path = Path(path)
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("the executable is too large for the bundled CArchive reader")
    data = path.read_bytes()
    cookie_at = find_cookie(data)
    if cookie_at < 0:
        raise ValueError("no PyInstaller CArchive cookie found")
    if cookie_at + COOKIE_SIZE > len(data):
        raise ValueError("truncated PyInstaller cookie")
    (_magic, package_length, toc_offset, toc_length, pyver, pylibname) = struct.unpack_from(
        ">8sIIII64s", data, cookie_at
    )
    start = cookie_at + COOKIE_SIZE - package_length
    if start < 0 or start + package_length > len(data):
        raise ValueError("the CArchive package length is outside the file")
    info = ArchiveInfo(path=path, start=start, length=package_length)
    info.python_version = str(pyver) if isinstance(pyver, int) else pyver.decode("ascii", errors="replace")
    _ = pylibname
    toc_start = start + toc_offset
    toc_end = toc_start + toc_length
    if toc_start < start or toc_end > start + package_length or toc_end > len(data):
        raise ValueError("the CArchive table of contents is outside the package")
    at = toc_start
    total = 0
    while at < toc_end and len(info.entries) < MAX_ENTRIES:
        if at + 18 > toc_end:
            info.warnings.append("The table of contents ends mid-entry.")
            break
        entry_length, entry_offset, compressed_length, uncompressed_length = struct.unpack_from(
            ">IIII", data, at
        )
        compressed = data[at + 16]
        typecode = data[at + 17]
        if entry_length < 18 or at + entry_length > toc_end:
            info.warnings.append("An entry length is invalid; stopping.")
            break
        name_length = entry_length - 18
        name = data[at + 18 : at + 18 + name_length].rstrip(b"\x00").decode("utf-8", errors="replace")
        at += entry_length
        if compressed_length > MAX_ENTRY_BYTES:
            info.warnings.append(f"Ignoring oversized entry {name!r}.")
            continue
        total += min(uncompressed_length, MAX_ENTRY_BYTES)
        if total > MAX_TOTAL_BYTES:
            info.warnings.append("The archive exceeds the extraction limit; stopping.")
            break
        info.entries.append(
            ArchiveEntry(
                name=name,
                typecode=chr(typecode),
                offset=entry_offset,
                compressed_length=compressed_length,
                uncompressed_length=uncompressed_length,
                compressed=bool(compressed),
            )
        )
    return info


def extract_entries(path: Path, info: ArchiveInfo | None = None) -> list[tuple[str, str, bytes]]:
    """Return (type, safe_name, bytes) for every archive entry."""
    info = info or read_archive(path)
    data = Path(path).read_bytes()
    output: list[tuple[str, str, bytes]] = []
    total = 0
    for entry in info.entries:
        absolute = info.start + entry.offset
        end = absolute + entry.compressed_length
        if absolute < 0 or end > len(data):
            info.warnings.append(f"Skipping {entry.name!r}: outside the file.")
            continue
        blob = data[absolute:end]
        if entry.compressed:
            try:
                blob = zlib.decompress(blob)
            except zlib.error:
                info.warnings.append(f"Skipping {entry.name!r}: bad compressed data.")
                continue
        if len(blob) > MAX_ENTRY_BYTES:
            info.warnings.append(f"Skipping {entry.name!r}: too large.")
            continue
        total += len(blob)
        if total > MAX_TOTAL_BYTES:
            info.warnings.append("Extraction limit reached.")
            break
        output.append((entry.typecode, _safe_relative(entry.name), blob))
    return output


def module_payload(name: str, blob: bytes) -> tuple[str, bytes]:
    """Give frozen modules a usable .pyc name and a normal pyc header."""
    safe = _safe_relative(name)
    if not safe.lower().endswith((".pyc", ".py", ".pyd", ".dll")):
        safe += ".pyc"
    if safe.lower().endswith(".pyc"):
        try:
            from .formats import PYC_MAGICS

            if not (blob[:2] in PYC_MAGICS and blob[2:4] == b"\r\n"):
                import importlib.util

                blob = importlib.util.MAGIC_NUMBER + b"\x00" * 12 + blob
        except Exception:  # pragma: no cover - defensive
            pass
    return safe, blob


_PYZ_ITEM_MODULE = 0
_PYZ_ITEM_PKG = 1
_PYZ_ITEM_NSPKG = 3


def read_pyz(blob: bytes) -> tuple[str, list[tuple[str, object]], list[str]]:
    """Read a PyInstaller PYZ (ZlibArchive) and return its code objects.

    Returns ``(python_magic_hex, [(module_name, code_object)], warnings)``.
    Each entry is zlib-compressed marshalled bytecode; it is decompressed with
    explicit bounds and never executed.
    """
    import marshal
    import types

    warnings: list[str] = []
    modules: list[tuple[str, object]] = []
    if not blob.startswith(b"PYZ\x00"):
        return "", modules, ["Not a PYZ archive (missing PYZ\\0 magic)."]
    if len(blob) < 12:
        return "", modules, ["The PYZ header is truncated."]
    pymagic = blob[4:8]
    toc_offset = struct.unpack(">i", blob[8:12])[0]
    if toc_offset < 12 or toc_offset >= len(blob):
        return pymagic.hex(), modules, ["The PYZ table of contents is outside the archive."]
    try:
        table = marshal.loads(blob[toc_offset:])
    except (EOFError, ValueError, TypeError) as exc:
        return pymagic.hex(), modules, [f"The PYZ table of contents is unreadable: {exc}"]
    if isinstance(table, list):
        table = dict(table)
    if not isinstance(table, dict):
        return pymagic.hex(), modules, ["The PYZ table of contents has an unexpected shape."]
    total = 0
    for name, value in list(table.items())[:MAX_ENTRIES]:
        try:
            typecode, offset, length = value
        except (TypeError, ValueError):
            warnings.append(f"{name}: invalid PYZ entry.")
            continue
        if typecode == _PYZ_ITEM_NSPKG:
            continue
        if offset < 0 or length < 0 or offset + length > len(blob):
            warnings.append(f"{name}: PYZ data is outside the archive.")
            continue
        if total + length > MAX_TOTAL_BYTES:
            warnings.append("PYZ extraction limit reached.")
            break
        try:
            raw = zlib.decompress(blob[offset : offset + length])
            code = marshal.loads(raw)
        except (zlib.error, EOFError, ValueError, TypeError) as exc:
            warnings.append(f"{name}: {exc}")
            continue
        total += length
        if isinstance(code, types.CodeType):
            modules.append((str(name), code))
    return pymagic.hex(), modules, warnings
