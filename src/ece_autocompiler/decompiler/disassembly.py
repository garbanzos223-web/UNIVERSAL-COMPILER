"""Disassembly helpers.

Disassembly is *generated* output, never recovered source. Capstone is used
when installed, then an external ``objdump``; otherwise the caller is told
plainly that no disassembler is available.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import tools
from .pe import PEInfo
from .elf import ELFInfo

MAX_INSTRUCTIONS = 4000
MAX_BYTES = 256 * 1024


def _capstone_arch(machine: str, elf: bool):
    import capstone

    text = machine.lower()
    if "x86-64" in text or "amd64" in text:
        return capstone.CS_ARCH_X86, capstone.CS_MODE_64
    if "x86" in text or "i386" in text:
        return capstone.CS_ARCH_X86, capstone.CS_MODE_32
    if "arm64" in text or "aarch64" in text:
        return capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM
    if "arm" in text:
        return capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM
    _ = elf
    return None, None


def _render(instructions, base: int) -> str:
    lines = []
    for instruction in instructions:
        lines.append(f"0x{instruction.address:08x}  {instruction.mnemonic:<10} {instruction.op_str}")
    _ = base
    return "\n".join(lines)


def disassemble_bytes(blob: bytes, machine: str, base: int = 0) -> tuple[str, str]:
    """Return (text, note). Empty text means no disassembler is available."""
    if not tools.capstone_available():
        return "", (
            "Capstone is not installed. Install the 'decompile' extra "
            "(pip install capstone) or put objdump on PATH for real disassembly."
        )
    try:
        import capstone

        arch, mode = _capstone_arch(machine, elf=False)
        if arch is None:
            return "", f"Capstone has no configured mode for {machine}."
        engine = capstone.Cs(arch, mode)
        engine.detail = False
        instructions = []
        for instruction in engine.disasm(blob[:MAX_BYTES], base):
            instructions.append(instruction)
            if len(instructions) >= MAX_INSTRUCTIONS:
                break
        if not instructions:
            return "", "The selected bytes did not decode as instructions."
        return _render(instructions, base), f"{len(instructions)} instructions decoded with Capstone."
    except Exception as exc:  # pragma: no cover - optional dependency
        return "", f"Disassembly failed: {exc}"


def pe_disassembly(path: Path, info: PEInfo) -> tuple[str, str]:
    text_section = next((s for s in info.sections if s.name.startswith(".text")), None)
    if text_section is None:
        return "", "This PE has no .text section to disassemble."
    try:
        with open(path, "rb") as handle:
            handle.seek(text_section.raw_offset)
            blob = handle.read(min(text_section.raw_size, MAX_BYTES))
    except OSError as exc:
        return "", f"Could not read .text: {exc}"
    base = info.image_base + text_section.virtual_address
    code, note = disassemble_bytes(blob, info.machine, base)
    if not code:
        objdump = tools.find_objdump()
        if objdump:
            command = [objdump, "-d", "--start-address", str(base), "--stop-address", str(base + len(blob)), str(path)]
            try:
                completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
                if completed.stdout:
                    return completed.stdout[:4_000_000], "Disassembled with objdump."
            except (OSError, subprocess.TimeoutExpired):
                pass
    return code, note


def elf_disassembly(path: Path, info: ELFInfo) -> tuple[str, str]:
    text_section = next(
        (s for s in info.sections if s.name in (".text", ".init", ".plt") and s.size),
        None,
    )
    if text_section is None:
        return "", "This ELF has no .text section to disassemble."
    try:
        with open(path, "rb") as handle:
            handle.seek(text_section.offset)
            blob = handle.read(min(text_section.size, MAX_BYTES))
    except OSError as exc:
        return "", f"Could not read .text: {exc}"
    code, note = disassemble_bytes(blob, info.machine, text_section.address)
    if not code:
        objdump = tools.find_objdump()
        if objdump:
            command = [objdump, "-d", str(path)]
            try:
                completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
                if completed.stdout:
                    return completed.stdout[:4_000_000], "Disassembled with objdump."
            except (OSError, subprocess.TimeoutExpired):
                pass
    return code, note
