"""Pure-standard-library ELF reader (32/64-bit, little/big endian).

Reports the real ELF header, program headers, sections, dynamic dependencies,
symbols, notes and debug-information sections. Disassembly is delegated to
Capstone when installed, or to ``objdump`` when found on PATH.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_SECTIONS = 4096
MAX_SYMBOLS = 50000
MAX_DYNAMIC = 4096
MAX_STRINGS = 2000
MAX_ELF_BYTES = 512 * 1024 * 1024

MACHINES = {
    3: "x86 (32-bit)",
    8: "MIPS",
    20: "PowerPC",
    21: "PowerPC64",
    40: "ARM",
    62: "x86-64",
    183: "ARM64",
    243: "RISC-V",
}
TYPES = {0: "none", 1: "relocatable", 2: "executable", 3: "shared object", 4: "core"}
SHT = {
    0: "NULL", 1: "PROGBITS", 2: "SYMTAB", 3: "STRTAB", 4: "RELA", 5: "HASH",
    6: "DYNAMIC", 7: "NOTE", 8: "NOBITS", 9: "REL", 10: "SHLIB", 11: "DYNSYM",
    14: "INIT_ARRAY", 15: "FINI_ARRAY", 16: "PREINIT_ARRAY", 17: "GROUP",
    18: "SYMTAB_SHNDX",
}
DT = {
    1: "NEEDED", 2: "PLTRELSZ", 3: "PLTGOT", 4: "HASH", 5: "STRTAB", 6: "SYMTAB",
    7: "RELA", 8: "RELASZ", 9: "RELAENT", 10: "STRSZ", 11: "SYMENT", 12: "INIT",
    13: "FINI", 14: "SONAME", 15: "RPATH", 16: "SYMBOLIC", 17: "REL", 18: "RELSZ",
    19: "RELENT", 20: "PLTREL", 21: "DEBUG", 22: "TEXTREL", 23: "JMPREL",
    24: "BIND_NOW", 25: "INIT_ARRAY", 26: "FINI_ARRAY", 27: "INIT_ARRAYSZ",
    28: "FINI_ARRAYSZ", 29: "RUNPATH", 30: "FLAGS",
}


@dataclass
class Section:
    index: int
    name: str
    type_id: int
    address: int
    offset: int
    size: int
    link: int
    info: int
    entsize: int
    flags: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "type": SHT.get(self.type_id, f"0x{self.type_id:x}"),
            "address": hex(self.address),
            "offset": self.offset,
            "size": self.size,
            "flags": hex(self.flags),
        }


@dataclass
class ELFInfo:
    path: Path
    size: int
    elf_class: int
    little_endian: bool
    type: str
    machine: str
    entry: int
    osabi: int
    interpreter: str = ""
    sections: list[Section] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    soname: str = ""
    rpath: str = ""
    runpath: str = ""
    symbols: list[str] = field(default_factory=list)
    undefined: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    debug_sections: list[str] = field(default_factory=list)
    strings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "class": f"{self.elf_class}-bit",
            "endianness": "little" if self.little_endian else "big",
            "type": self.type,
            "machine": self.machine,
            "entry_point": hex(self.entry),
            "interpreter": self.interpreter,
            "soname": self.soname,
            "needed": self.dependencies,
            "rpath": self.rpath,
            "runpath": self.runpath,
            "sections": [s.to_dict() for s in self.sections],
            "symbol_count": len(self.symbols),
            "symbols": self.symbols[:1000],
            "undefined": self.undefined[:1000],
            "debug_sections": self.debug_sections,
            "notes": self.notes,
            "warnings": self.warnings,
        }


def _c_string(data: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(data):
        return ""
    end = data.find(b"\x00", offset, min(len(data), offset + 4096))
    if end < 0:
        end = min(len(data), offset + 4096)
    return data[offset:end].decode("utf-8", errors="replace")


def parse_elf(path: Path) -> ELFInfo:
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_ELF_BYTES:
        raise ValueError(
            f"the ELF is {size:,} bytes; the bundled reader is limited to "
            f"{MAX_ELF_BYTES:,} bytes"
        )
    data = path.read_bytes()
    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    elf_class = 64 if data[4] == 2 else 32
    little = data[5] == 1
    end = "<" if little else ">"
    (e_type, e_machine) = struct.unpack_from(end + "HH", data, 16)
    (entry,) = struct.unpack_from(end + "Q" if elf_class == 64 else end + "I", data, 24)
    if elf_class == 64:
        (phoff, shoff) = struct.unpack_from(end + "QQ", data, 32)
        (phentsize, phnum, shentsize, shnum, shstrndx) = struct.unpack_from(end + "HHHHH", data, 54)
    else:
        (phoff, shoff) = struct.unpack_from(end + "II", data, 28)
        (phentsize, phnum, shentsize, shnum, shstrndx) = struct.unpack_from(end + "HHHHH", data, 42)
    info = ELFInfo(
        path=path,
        size=len(data),
        elf_class=elf_class,
        little_endian=little,
        type=TYPES.get(e_type, f"unknown ({e_type})"),
        machine=MACHINES.get(e_machine, f"unknown ({e_machine})"),
        entry=entry,
        osabi=data[7],
    )

    # Program headers: interpreter and PT_DYNAMIC location.
    dynamic_offset = 0
    dynamic_size = 0
    for index in range(min(phnum, 512)):
        at = phoff + index * phentsize
        if at + phentsize > len(data):
            break
        if elf_class == 64:
            p_type, p_flags, p_offset, p_vaddr, _paddr, p_filesz, _memsz, _align = struct.unpack_from(
                end + "IIQQQQQQ", data, at
            )
        else:
            p_type, p_offset, p_vaddr, _paddr, p_filesz, _memsz, p_flags, _align = struct.unpack_from(
                end + "IIIIIIII", data, at
            )
        if p_type == 3 and p_offset < len(data):  # PT_INTERP
            info.interpreter = _c_string(data, p_offset)
        elif p_type == 2:  # PT_DYNAMIC
            dynamic_offset, dynamic_size = p_offset, p_filesz
        elif p_type == 4 and p_offset + 12 <= len(data):  # PT_NOTE
            name_size, desc_size, note_type = struct.unpack_from(end + "III", data, p_offset)
            if name_size == 4 and data[p_offset + 12 : p_offset + 16] == b"GNU\x00":
                desc_at = p_offset + 12 + name_size
                if note_type == 3 and desc_at + desc_size <= len(data):
                    info.notes.append(f"GNU build-id: {data[desc_at:desc_at + desc_size].hex()}")

    # Section headers.
    section_headers: list[tuple[int, int, int, int, int, int, int]] = []
    for index in range(min(shnum, MAX_SECTIONS)):
        at = shoff + index * shentsize
        if at + shentsize > len(data):
            break
        if elf_class == 64:
            (sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info, _align, sh_entsize) = (
                struct.unpack_from(end + "IIQQQQIIQQ", data, at)
            )
        else:
            (sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info, _align, sh_entsize) = (
                struct.unpack_from(end + "IIIIIIIIII", data, at)
            )
        section_headers.append((sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info, sh_entsize))

    # Section-name string table.
    names_blob = b""
    if 0 <= shstrndx < len(section_headers):
        _n, _t, _f, _a, sh_offset, sh_size, _l, _i, _e = section_headers[shstrndx]
        names_blob = data[sh_offset : sh_offset + sh_size]

    sections: list[Section] = []
    for index, header in enumerate(section_headers):
        sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info, sh_entsize = header
        name = _c_string(names_blob, sh_name)
        sections.append(
            Section(index, name, sh_type, sh_addr, sh_offset, sh_size, sh_link, sh_info, sh_entsize, sh_flags)
        )
        if name.startswith(".debug_") or name in (".symtab", ".zdebug_info"):
            if name.startswith(".debug_"):
                info.debug_sections.append(name)
    info.sections = sections

    # Dynamic dependencies and symbol tables.
    strtab = b""
    strtab_size = 0
    dynsym: tuple[int, int, int] | None = None
    symtab: tuple[int, int, int] | None = None
    for section in sections:
        if section.name == ".dynstr":
            strtab = data[section.offset : section.offset + section.size]
            strtab_size = section.size
        elif section.name == ".dynsym":
            dynsym = (section.offset, section.size, section.entsize or (24 if elf_class == 64 else 16))
        elif section.name == ".symtab":
            symtab = (section.offset, section.size, section.entsize or (24 if elf_class == 64 else 16))
    _ = strtab_size

    if dynamic_offset and dynamic_size:
        entry_size = 16 if elf_class == 64 else 8
        for index in range(min(dynamic_size // entry_size, MAX_DYNAMIC)):
            at = dynamic_offset + index * entry_size
            if at + entry_size > len(data):
                break
            if elf_class == 64:
                tag, value = struct.unpack_from(end + "qQ", data, at)
            else:
                tag, value = struct.unpack_from(end + "iI", data, at)
            if tag == 0:
                break
            name = DT.get(tag, "")
            if name == "NEEDED":
                text = _c_string(strtab, value)
                if text:
                    info.dependencies.append(text)
            elif name == "SONAME":
                info.soname = _c_string(strtab, value)
            elif name == "RPATH":
                info.rpath = _c_string(strtab, value)
            elif name == "RUNPATH":
                info.runpath = _c_string(strtab, value)

    for table, label in ((dynsym, "dynamic"), (symtab, "static")):
        if table is None:
            continue
        offset, size, entry_size = table
        if entry_size <= 0:
            continue
        count = min(size // entry_size, MAX_SYMBOLS)
        for index in range(count):
            at = offset + index * entry_size
            if at + entry_size > len(data):
                break
            if elf_class == 64:
                st_name, st_info, st_other, st_shndx, _st_value, _st_size = struct.unpack_from(
                    end + "IBBHQQ", data, at
                )
            else:
                st_name, _st_value, _st_size, st_info, st_other, st_shndx = struct.unpack_from(
                    end + "IIIBBH", data, at
                )
            symbol = _c_string(strtab, st_name)
            if not symbol:
                continue
            if st_shndx == 0:
                info.undefined.append(symbol)
            elif label == "static":
                info.symbols.append(symbol)
    info.symbols = info.symbols[:MAX_SYMBOLS]
    info.undefined = info.undefined[:MAX_SYMBOLS]
    return info


def extract_strings(data: bytes, minimum: int = 5, limit: int = MAX_STRINGS) -> list[str]:
    from .pe import extract_strings as _extract

    return _extract(data, minimum=minimum, limit=limit)
