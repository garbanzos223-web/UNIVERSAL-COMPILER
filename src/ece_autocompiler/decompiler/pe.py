"""Bounded Windows PE reader used by the analysis and native backends.

The base parser is pure standard library. ``pefile`` (an optional extra) is
used, when installed, for the richer directories (resources, version info and
authenticode location); without it those sections are reported as unavailable
instead of being invented.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_SECTIONS = 96
MAX_IMPORT_DESCRIPTORS = 4096
MAX_IMPORT_NAMES = 20000
MAX_STRINGS = 5000
MAX_STRING_SCAN_BYTES = 32 * 1024 * 1024
MAX_PE_BYTES = 512 * 1024 * 1024


@dataclass
class Section:
    name: str
    virtual_address: int
    virtual_size: int
    raw_offset: int
    raw_size: int
    characteristics: int
    entropy: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "virtual_address": self.virtual_address,
            "virtual_size": self.virtual_size,
            "raw_offset": self.raw_offset,
            "raw_size": self.raw_size,
            "characteristics": f"0x{self.characteristics:08x}",
            "entropy": round(self.entropy, 3),
        }


@dataclass
class PEInfo:
    path: Path
    size: int
    machine: str
    machine_id: int
    pe32_plus: bool
    timestamp: int
    characteristics: int
    subsystem: int
    entry_point_rva: int
    image_base: int
    sections: list[Section] = field(default_factory=list)
    data_directories: list[tuple[int, int]] = field(default_factory=list)
    clr_rva: int = 0
    clr_size: int = 0
    is_dll: bool = False
    imports: dict[str, list[str]] = field(default_factory=dict)
    exports: list[str] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    version_info: dict[str, str] = field(default_factory=dict)
    pdb_path: str = ""
    has_signature: bool = False
    strings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_dotnet(self) -> bool:
        return bool(self.clr_rva and self.clr_size)

    @property
    def architecture(self) -> str:
        return self.machine

    def rva_to_offset(self, rva: int) -> int | None:
        for section in self.sections:
            start = section.virtual_address
            span = max(section.virtual_size, section.raw_size)
            if start <= rva < start + span:
                delta = rva - start
                if delta < section.raw_size:
                    return section.raw_offset + delta
        if 0 <= rva < self.size:
            return rva
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "machine": self.machine,
            "pe32_plus": self.pe32_plus,
            "timestamp": self.timestamp,
            "subsystem": self.subsystem,
            "entry_point_rva": hex(self.entry_point_rva),
            "image_base": hex(self.image_base),
            "is_dll": self.is_dll,
            "is_dotnet": self.is_dotnet,
            "sections": [s.to_dict() for s in self.sections],
            "imports": {k: v[:200] for k, v in self.imports.items()},
            "imports_total": sum(len(v) for v in self.imports.values()),
            "exports": self.exports[:500],
            "exports_total": len(self.exports),
            "resources": self.resources[:200],
            "version_info": self.version_info,
            "pdb_path": self.pdb_path,
            "has_signature": self.has_signature,
            "warnings": self.warnings,
        }


def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = [0] * 256
    for byte in data:
        counts[byte] += 1
    total = len(data)
    value = 0.0
    for count in counts:
        if count:
            p = count / total
            value -= p * math.log2(p)
    return value


def _read_c_string(blob: bytes, offset: int, limit: int = 512) -> str:
    if offset < 0 or offset >= len(blob):
        return ""
    end = blob.find(b"\x00", offset, min(len(blob), offset + limit))
    if end < 0:
        end = min(len(blob), offset + limit)
    return blob[offset:end].decode("utf-8", errors="replace")


def parse_pe(path: Path) -> PEInfo:
    """Parse a PE file without loading it into memory as a whole."""
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_PE_BYTES:
        raise ValueError(
            f"the PE is {size:,} bytes; the bundled reader is limited to "
            f"{MAX_PE_BYTES:,} bytes"
        )
    data = path.read_bytes()
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise ValueError("not a PE file (missing MZ header)")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if e_lfanew + 24 > len(data) or data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        raise ValueError("not a PE file (missing PE signature)")

    (machine, count, timestamp, _sym_ptr, _num_sym, opt_size, characteristics) = struct.unpack_from(
        "<HHIIIHH", data, e_lfanew + 4
    )
    opt = e_lfanew + 24
    if opt + 2 > len(data):
        raise ValueError("truncated optional header")
    magic = struct.unpack_from("<H", data, opt)[0]
    pe32_plus = magic == 0x20B
    entry = struct.unpack_from("<I", data, opt + 16)[0]
    image_base = (
        struct.unpack_from("<Q", data, opt + 24)[0]
        if pe32_plus
        else struct.unpack_from("<I", data, opt + 28)[0]
    )
    subsystem = struct.unpack_from("<H", data, opt + 68)[0] if opt + 70 <= len(data) else 0
    dirs_at = opt + (108 if pe32_plus else 92)
    dir_count = struct.unpack_from("<I", data, dirs_at)[0] if dirs_at + 4 <= len(data) else 0
    dirs: list[tuple[int, int]] = []
    for index in range(min(int(dir_count), 16)):
        at = dirs_at + 4 + index * 8
        if at + 8 <= len(data):
            dirs.append(struct.unpack_from("<II", data, at))
        else:
            dirs.append((0, 0))

    sections: list[Section] = []
    section_at = opt + opt_size
    for index in range(min(count, MAX_SECTIONS)):
        at = section_at + index * 40
        if at + 40 > len(data):
            break
        raw_name = data[at : at + 8].rstrip(b"\x00")
        name = raw_name.decode("ascii", errors="replace")
        vsize, va, raw_size, raw_ptr = struct.unpack_from("<IIII", data, at + 8)
        chars = struct.unpack_from("<I", data, at + 36)[0]
        raw = data[raw_ptr : raw_ptr + raw_size] if raw_ptr and raw_size else b""
        sections.append(
            Section(
                name=name,
                virtual_address=va,
                virtual_size=vsize,
                raw_offset=raw_ptr,
                raw_size=raw_size,
                characteristics=chars,
                entropy=_entropy(raw),
            )
        )

    clr_rva, clr_size = dirs[14] if len(dirs) > 14 else (0, 0)
    info = PEInfo(
        path=path,
        size=size,
        machine=_machine_name(machine),
        machine_id=machine,
        pe32_plus=pe32_plus,
        timestamp=timestamp,
        characteristics=characteristics,
        subsystem=subsystem,
        entry_point_rva=entry,
        image_base=image_base,
        sections=sections,
        data_directories=dirs,
        clr_rva=clr_rva,
        clr_size=clr_size,
        is_dll=bool(characteristics & 0x2000),
        has_signature=bool(dirs[4][0] and dirs[4][1]) if len(dirs) > 4 else False,
    )
    info.imports = _parse_imports(data, info)
    info.exports = _parse_exports(data, info)
    info.pdb_path = _parse_pdb(data, info)
    info.strings = extract_strings(data)
    _enrich_with_pefile(info, path)
    return info


def _machine_name(machine: int) -> str:
    names = {
        0x014C: "x86 (32-bit)",
        0x8664: "x86-64",
        0xAA64: "ARM64",
        0x01C4: "ARMv7",
        0x01C0: "ARM",
        0x0200: "Itanium",
    }
    return names.get(machine, f"unknown (0x{machine:04x})")


def _parse_imports(data: bytes, info: PEInfo) -> dict[str, list[str]]:
    if len(info.data_directories) <= 1:
        return {}
    rva, size = info.data_directories[1]
    if not rva or not size:
        return {}
    base = info.rva_to_offset(rva)
    if base is None:
        return {}
    result: dict[str, list[str]] = {}
    thunk_size = 8 if info.pe32_plus else 4
    for index in range(MAX_IMPORT_DESCRIPTORS):
        at = base + index * 20
        if at + 20 > len(data):
            break
        original, _stamp, _chain, name_rva, first = struct.unpack_from("<IIIII", data, at)
        if not (original or name_rva or first):
            break
        name_at = info.rva_to_offset(name_rva)
        dll = _read_c_string(data, name_at) if name_at is not None else ""
        if not dll:
            continue
        names: list[str] = []
        thunk_rva = original or first
        thunk_at = info.rva_to_offset(thunk_rva)
        if thunk_at is not None:
            for slot in range(MAX_IMPORT_NAMES):
                pos = thunk_at + slot * thunk_size
                if pos + thunk_size > len(data):
                    break
                value = (
                    struct.unpack_from("<Q", data, pos)[0]
                    if thunk_size == 8
                    else struct.unpack_from("<I", data, pos)[0]
                )
                if not value:
                    break
                ordinal_flag = (1 << 63) if thunk_size == 8 else (1 << 31)
                if value & ordinal_flag:
                    names.append(f"ordinal #{value & 0xFFFF}")
                    continue
                hint_at = info.rva_to_offset(value)
                if hint_at is None or hint_at + 2 > len(data):
                    continue
                func = _read_c_string(data, hint_at + 2)
                if func:
                    names.append(func)
                if len(names) >= 2000:
                    names.append("... (truncated)")
                    break
        result[dll] = names
        if len(result) >= 512:
            break
    return result


def _parse_exports(data: bytes, info: PEInfo) -> list[str]:
    if not info.data_directories:
        return []
    rva, size = info.data_directories[0]
    if not rva or not size:
        return []
    base = info.rva_to_offset(rva)
    if base is None or base + 40 > len(data):
        return []
    (_flags, _stamp, _major, _minor, name_rva, _ord_base, count, funcs, names, ordinals) = struct.unpack_from(
        "<IIHHIIIIII", data, base
    )
    exported: list[str] = []
    names_at = info.rva_to_offset(names)
    ords_at = info.rva_to_offset(ordinals)
    funcs_at = info.rva_to_offset(funcs)
    if names_at is None:
        return []
    for index in range(min(count, 5000)):
        at = names_at + index * 4
        if at + 4 > len(data):
            break
        name_at = info.rva_to_offset(struct.unpack_from("<I", data, at)[0])
        name = _read_c_string(data, name_at) if name_at is not None else ""
        if not name:
            continue
        if ords_at is not None and funcs_at is not None:
            try:
                ordinal_index = struct.unpack_from("<H", data, ords_at + index * 2)[0]
                func_rva = struct.unpack_from("<I", data, funcs_at + ordinal_index * 4)[0]
                exported.append(f"{name} (RVA 0x{func_rva:08x})")
            except struct.error:
                exported.append(name)
        else:
            exported.append(name)
    return exported


def _parse_pdb(data: bytes, info: PEInfo) -> str:
    if len(info.data_directories) <= 6:
        return ""
    rva, size = info.data_directories[6]
    if not rva or not size:
        return ""
    at = info.rva_to_offset(rva)
    if at is None or at + 24 > len(data):
        return ""
    signature = data[at : at + 4]
    if signature != b"RSDS":
        return ""
    return _read_c_string(data, at + 24, limit=260)


def extract_strings(data: bytes, *, minimum: int = 5, limit: int = MAX_STRINGS) -> list[str]:
    """Extract printable ASCII and UTF-16LE strings, bounded in size."""
    blob = data[:MAX_STRING_SCAN_BYTES]
    found: list[str] = []
    seen: set[str] = set()
    pattern = re.compile(rb"[\x20-\x7e]{%d,}" % minimum)
    for match in pattern.finditer(blob):
        text = match.group().decode("ascii", errors="replace")
        if text not in seen:
            seen.add(text)
            found.append(text)
        if len(found) >= limit:
            return found
    wide = re.compile((r"(?:[\x20-\x7e]\x00){%d,}" % minimum).encode())
    for match in wide.finditer(blob):
        text = match.group().decode("utf-16-le", errors="replace")
        if text not in seen:
            seen.add(text)
            found.append(text)
        if len(found) >= limit:
            break
    return found


def _enrich_with_pefile(info: PEInfo, path: Path) -> None:
    """Optional richer directories; never required and never fatal."""
    try:
        import pefile  # type: ignore[import-not-found]
    except Exception:
        info.warnings.append(
            "pefile is not installed: resource entries and version information "
            "are not listed (install the 'decompile' extra for them)."
        )
        return
    try:
        pe = pefile.PE(str(path), fast_load=False)
    except Exception as exc:  # pragma: no cover - malformed file
        info.warnings.append(f"pefile could not read the optional directories: {exc}")
        return
    try:
        resources: list[dict[str, Any]] = []
        if hasattr(pe, "DIRECTORY_ENTRY_RESOURCE"):
            for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
                type_name = str(getattr(entry, "name", None) or entry.id)
                if not hasattr(entry, "directory"):
                    continue
                for res in entry.directory.entries:
                    name = str(getattr(res, "name", None) or res.id)
                    if not hasattr(res, "directory"):
                        continue
                    for lang in res.directory.entries:
                        data_entry = lang.data.struct
                        resources.append(
                            {
                                "type": type_name,
                                "name": name,
                                "language": lang.id,
                                "size": data_entry.Size,
                                "rva": hex(data_entry.OffsetToData),
                            }
                        )
        info.resources = resources
        version: dict[str, str] = {}
        if hasattr(pe, "FileInfo"):
            for file_info in pe.FileInfo:
                for entry in file_info:
                    if hasattr(entry, "StringTable"):
                        for table in entry.StringTable:
                            for key, value in table.entries.items():
                                version[key.decode(errors="replace")] = value.decode(errors="replace")
        info.version_info = version
    finally:
        try:
            pe.close()
        except Exception:
            pass


def extract_resource_bytes(path: Path) -> list[tuple[str, bytes]]:
    """Return (suggested name, bytes) for resources, using pefile when present."""
    try:
        import pefile  # type: ignore[import-not-found]
    except Exception:
        return []
    items: list[tuple[str, bytes]] = []
    try:
        pe = pefile.PE(str(path), fast_load=False)
    except Exception:
        return []
    try:
        if not hasattr(pe, "DIRECTORY_ENTRY_RESOURCE"):
            return []
        for entry in pe.DIRECTORY_ENTRY_RESOURCE.entries:
            type_name = str(getattr(entry, "name", None) or entry.id)
            if not hasattr(entry, "directory"):
                continue
            for res in entry.directory.entries:
                name = str(getattr(res, "name", None) or res.id)
                if not hasattr(res, "directory"):
                    continue
                for lang in res.directory.entries:
                    data_entry = lang.data.struct
                    try:
                        blob = pe.get_data(data_entry.OffsetToData, data_entry.Size)
                    except Exception:
                        continue
                    safe_type = re.sub(r"[^A-Za-z0-9_.-]", "_", type_name)
                    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
                    items.append((f"{safe_type}_{safe_name}_{lang.id}.bin", blob))
                    if len(items) >= 500:
                        return items
    finally:
        try:
            pe.close()
        except Exception:
            pass
    return items
