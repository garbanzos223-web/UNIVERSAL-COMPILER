"""Minimal ECMA-335 metadata reader.

This is enough to list the types and methods of a .NET assembly without any
third-party package, so an assembly is always analysable. Full C# source
recovery is delegated to ILSpy when it is available; this reader never claims
that an outline is the original code.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .pe import PEInfo, parse_pe

MAX_TYPES = 10000
MAX_METHODS = 40000

# Row schemas for the tables needed to reach TypeDef/MethodDef. Column types:
#   "u1"/"u2"/"u4"  fixed-width integers
#   "str"/"guid"/"blob"  heap indexes
#   "rid:<table>"  simple index
#   "coded:<group>"  coded index
_TABLES = {
    0x00: ("Module", ("u2", "str", "guid", "guid", "guid")),
    0x01: ("TypeRef", ("coded:ResolutionScope", "str", "str")),
    0x02: ("TypeDef", ("u4", "str", "str", "coded:TypeDefOrRef", "rid:Field", "rid:MethodDef")),
    0x03: ("FieldPtr", ("rid:Field",)),
    0x04: ("Field", ("u2", "str", "blob")),
    0x05: ("MethodPtr", ("rid:MethodDef",)),
    0x06: ("MethodDef", ("u4", "u2", "u2", "str", "blob", "rid:Param")),
    0x07: ("ParamPtr", ("rid:Param",)),
    0x08: ("Param", ("u2", "u2", "str")),
}
_CODED = {
    "ResolutionScope": (0x00, 0x1A, 0x23, 0x01),
    "TypeDefOrRef": (0x02, 0x01, 0x1B),
}
_CODED_BITS = {"ResolutionScope": 2, "TypeDefOrRef": 2}


@dataclass
class TypeRow:
    name: str
    namespace: str
    flags: int
    methods: list[str] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.namespace}.{self.name}" if self.namespace else self.name


@dataclass
class DotNetMetadata:
    runtime_version: str = ""
    stream_names: list[str] = field(default_factory=list)
    heap_sizes: int = 0
    rows: dict[int, int] = field(default_factory=dict)
    types: list[TypeRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def method_count(self) -> int:
        return sum(len(t.methods) for t in self.types)

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_version": self.runtime_version,
            "streams": self.stream_names,
            "heap_sizes": self.heap_sizes,
            "tables": {f"0x{k:02x}": v for k, v in sorted(self.rows.items())},
            "type_count": len(self.types),
            "method_count": self.method_count,
            "types": [
                {"name": t.name, "namespace": t.namespace, "methods": t.methods[:200]}
                for t in self.types[:500]
            ],
            "warnings": self.warnings,
        }


def _heap_index_size(heap_sizes: int, bit: int) -> int:
    return 4 if heap_sizes & bit else 2


def _coded_size(group: str, rows: dict[int, int]) -> int:
    bits = _CODED_BITS[group]
    largest = max((rows.get(table, 0) for table in _CODED[group]), default=0)
    return 2 if largest < (1 << (16 - bits)) else 4


def _column_size(column: str, rows: dict[int, int], heap_sizes: int) -> int:
    if column in ("u1",):
        return 1
    if column in ("u2",):
        return 2
    if column in ("u4",):
        return 4
    if column == "str":
        return _heap_index_size(heap_sizes, 0x01)
    if column == "guid":
        return _heap_index_size(heap_sizes, 0x02)
    if column == "blob":
        return _heap_index_size(heap_sizes, 0x04)
    if column.startswith("rid:"):
        table = int(column.split(":", 1)[1], 0) if column.split(":", 1)[1].startswith("0x") else _table_id(column.split(":", 1)[1])
        return 2 if rows.get(table, 0) < 0x10000 else 4
    if column.startswith("coded:"):
        return _coded_size(column.split(":", 1)[1], rows)
    return 2


def _table_id(name: str) -> int:
    for table_id, (table_name, _columns) in _TABLES.items():
        if table_name == name:
            return table_id
    return 0


def _row_size(table: int, rows: dict[int, int], heap_sizes: int) -> int:
    _name, columns = _TABLES[table]
    return sum(_column_size(column, rows, heap_sizes) for column in columns)


def _read_string(strings: bytes, offset: int) -> str:
    if offset <= 0 or offset >= len(strings):
        return ""
    end = strings.find(b"\x00", offset)
    if end < 0:
        end = len(strings)
    return strings[offset:end].decode("utf-8", errors="replace")


def parse_metadata(path: Path, pe: PEInfo | None = None) -> DotNetMetadata:
    """Parse the CLR metadata root and the TypeDef/MethodDef tables."""
    path = Path(path)
    info = pe or parse_pe(path)
    result = DotNetMetadata()
    if not info.clr_rva:
        result.warnings.append("The PE has no CLR data directory.")
        return result
    data = path.read_bytes()
    clr_at = info.rva_to_offset(info.clr_rva)
    if clr_at is None or clr_at + 16 > len(data):
        result.warnings.append("The CLR header is outside the file.")
        return result
    try:
        metadata_rva, metadata_size = struct.unpack_from("<II", data, clr_at + 8)
    except struct.error:
        result.warnings.append("Truncated CLR header.")
        return result
    root = info.rva_to_offset(metadata_rva)
    if root is None or root + 20 > len(data) or root + metadata_size > len(data):
        result.warnings.append("The metadata root is outside the file.")
        return result
    if data[root : root + 4] != b"BSJB":
        result.warnings.append("Metadata root signature is not BSJB.")
        return result
    try:
        version_length = struct.unpack_from("<I", data, root + 12)[0]
        if version_length > 512 or root + 16 + version_length + 4 > len(data):
            result.warnings.append("Invalid metadata version length.")
            return result
        result.runtime_version = data[root + 16 : root + 16 + version_length].split(b"\x00")[0].decode(
            "ascii", errors="replace"
        )
        at = root + 16 + version_length
        at += 2  # flags
        stream_count = struct.unpack_from("<H", data, at)[0]
        at += 2
        streams: dict[str, tuple[int, int]] = {}
        for _ in range(min(stream_count, 16)):
            if at + 8 > len(data):
                break
            offset, length = struct.unpack_from("<II", data, at)
            at += 8
            end = data.find(b"\x00", at, min(len(data), at + 64))
            if end < 0:
                break
            name = data[at:end].decode("ascii", errors="replace")
            at = (end + 4) & ~3
            streams[name] = (offset, length)
            result.stream_names.append(name)
    except (struct.error, IndexError, ValueError):
        result.warnings.append("The metadata stream table is malformed.")
        return result

    tables_entry = streams.get("#~") or streams.get("#-")
    strings_entry = streams.get("#Strings")
    if tables_entry is None or strings_entry is None:
        result.warnings.append("The assembly has no #~ or #Strings stream.")
        return result
    tables_at = root + tables_entry[0]
    strings_at = root + strings_entry[0]
    strings_end = strings_at + strings_entry[1]
    strings = data[strings_at : min(strings_end, len(data))]
    try:
        reserved, major, minor, heap_sizes, _reserved2 = struct.unpack_from("<IBBBB", data, tables_at)
        valid, _sorted = struct.unpack_from("<QQ", data, tables_at + 8)
    except struct.error:
        result.warnings.append("The #~ header is truncated.")
        return result
    result.heap_sizes = heap_sizes
    position = tables_at + 24
    rows: dict[int, int] = {}
    for table in range(64):
        if valid & (1 << table):
            if position + 4 > len(data):
                result.warnings.append("The #~ row counts are truncated.")
                return result
            rows[table] = struct.unpack_from("<I", data, position)[0]
            position += 4
    result.rows = rows
    data_start = position

    def table_offset(table: int) -> int:
        offset = data_start
        for index in range(table):
            if index in rows and index in _TABLES:
                offset += rows[index] * _row_size(index, rows, heap_sizes)
        return offset

    def read_index(offset: int, size: int) -> int:
        if size == 2:
            return struct.unpack_from("<H", data, offset)[0]
        return struct.unpack_from("<I", data, offset)[0]

    if 0x02 not in rows:
        result.warnings.append("The assembly has no TypeDef table.")
        return result
    typedef_at = table_offset(0x02)
    typedef_size = _row_size(0x02, rows, heap_sizes)
    method_at = table_offset(0x06) if 0x06 in rows else 0
    method_size = _row_size(0x06, rows, heap_sizes) if 0x06 in rows else 0
    str_size = _heap_index_size(heap_sizes, 0x01)

    types: list[TypeRow] = []
    type_count = min(rows[0x02], MAX_TYPES)
    for index in range(type_count):
        at = typedef_at + index * typedef_size
        if at + typedef_size > len(data):
            break
        cursor = at
        flags = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        name_index = read_index(cursor, str_size)
        cursor += str_size
        namespace_index = read_index(cursor, str_size)
        cursor += str_size
        cursor += _column_size("coded:TypeDefOrRef", rows, heap_sizes)
        field_start = read_index(cursor, _column_size("rid:Field", rows, heap_sizes))
        cursor += _column_size("rid:Field", rows, heap_sizes)
        method_start = read_index(cursor, _column_size("rid:MethodDef", rows, heap_sizes))
        # MethodList is 1-based; the end is the next row's MethodList.
        next_start = rows.get(0x06, 0) + 1
        if index + 1 < rows[0x02]:
            next_at = typedef_at + (index + 1) * typedef_size
            next_cursor = next_at + 4 + str_size * 2 + _column_size("coded:TypeDefOrRef", rows, heap_sizes)
            next_cursor += _column_size("rid:Field", rows, heap_sizes)
            next_start = read_index(next_cursor, _column_size("rid:MethodDef", rows, heap_sizes))
        methods: list[str] = []
        if method_at and method_size:
            for method_index in range(max(1, method_start), min(next_start, rows.get(0x06, 0) + 1)):
                m_at = method_at + (method_index - 1) * method_size
                if m_at + method_size > len(data):
                    break
                m_cursor = m_at + 4 + 2 + 2
                m_name_index = read_index(m_cursor, str_size)
                methods.append(_read_string(strings, m_name_index))
                if len(methods) >= 500:
                    break
        types.append(
            TypeRow(
                name=_read_string(strings, name_index),
                namespace=_read_string(strings, namespace_index),
                flags=flags,
                methods=[m for m in methods if m],
            )
        )
    result.types = types
    return result


def dotnet_summary(path: Path, pe: PEInfo | None = None) -> dict[str, Any]:
    meta = parse_metadata(path, pe)
    return meta.to_dict()
