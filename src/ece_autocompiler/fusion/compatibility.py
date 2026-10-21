"""Compatibility validation for fused applications.

The checks here are based on real evidence only: PE/ELF headers reveal the
architecture of a prebuilt binary, Python declares its own version and
architecture, Unreal projects declare their engine association, manifests
declare their package managers. When a fact cannot be verified the result
says so instead of guessing.
"""

from __future__ import annotations

import logging
import platform
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# PE machine types (winnt.h)
PE_MACHINE = {
    0x014C: "x86",
    0x8664: "x64",
    0xAA64: "arm64",
    0x01C4: "arm",
}

ELF_MACHINE = {
    3: "x86",
    62: "x64",
    183: "arm64",
    40: "arm",
}


@dataclass
class CompatibilityReport:
    tools: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(c.get("level") == "error" for c in self.checks)

    def add(self, level: str, message: str) -> None:
        self.checks.append({"level": level, "message": message})


def binary_architecture(path: Path) -> str:
    """Best-effort architecture of a .exe/.dll/.lib/.so/.pyd file.

    Reads the real PE (or ELF) header. Returns "" when the format is not
    recognised (e.g. a .lib archive without a COFF symbol target we can read).
    """
    try:
        with path.open("rb") as handle:
            data = handle.read(4096)
    except OSError:
        return ""
    if not data:
        return ""
    # MZ PE
    if data[:2] == b"MZ":
        pe_offset = struct.unpack_from("<I", data, 0x3C)[0] if len(data) >= 0x40 else 0
        if pe_offset and pe_offset + 6 <= len(data):
            with path.open("rb") as handle:
                handle.seek(pe_offset)
                signature = handle.read(4)
            if signature == b"PE\x00\x00":
                with path.open("rb") as handle:
                    handle.seek(pe_offset + 4)
                    machine = struct.unpack("<H", handle.read(2))[0]
                return PE_MACHINE.get(machine, f"machine-0x{machine:04x}")
    # ELF
    if data[:4] == b"\x7fELF" and len(data) >= 20:
        machine = struct.unpack_from("<H", data, 18)[0]
        return ELF_MACHINE.get(machine, f"elf-machine-{machine}")
    # .lib archive with COFF x86/x64 member signatures (fallback: 0x8664 magic)
    if data[:8] == b"!<arch>\n" or data[:8] == b"!<arch>.":  # pragma: no cover
        for machine_value, name in ((0x8664, "x64"), (0x014C, "x86")):
            signature = struct.pack("<H", machine_value) + b"\x00\x00"
            if signature in data:
                return name
    return ""


def python_architecture() -> str:
    try:
        bits, _ = platform.architecture()
    except Exception:  # pragma: no cover
        return platform.machine() or ""
    if bits == "64bit":
        return "x64" if platform.machine().lower() in {"amd64", "x86_64", "arm64"} else "x64"
    if bits == "32bit":
        return "x86"
    return platform.machine() or ""


def prebuilt_architecture_conflicts(workspace_root: Path, artifacts: list[tuple[str, Path]]) -> list[str]:
    """Detect mixed-architecture native artifacts that cannot live in one process."""
    conflicts: list[str] = []
    arches: dict[str, str] = {}
    for label, path in artifacts:
        arch = binary_architecture(path)
        if not arch:
            continue
        arches[label] = arch
    if len(set(arches.values())) > 1:
        conflicts.append(
            "Mixed native architectures detected: "
            + ", ".join(f"{label}={arch}" for label, arch in arches.items())
            + ". 32-bit and 64-bit binaries cannot be linked or loaded in one process."
        )
    return conflicts


def check_python_extension_arch(pyd_path: Path, report: CompatibilityReport) -> None:
    arch = binary_architecture(pyd_path)
    py_arch = python_architecture()
    if not arch or not py_arch:
        report.add(
            "warning",
            f"Architecture of {pyd_path.name} could not be determined; "
            "the extension must be rebuilt for the running interpreter.",
        )
        return
    if arch == py_arch:
        report.add("info", f"Python extension architecture matches interpreter ({arch}).")
    else:
        report.add(
            "error",
            f"Python extension {pyd_path.name} is {arch} but the interpreter is "
            f"{py_arch}; it cannot be imported in-process.",
        )


def unreal_engine_installations() -> list[Path]:
    """Find installed Unreal Engine root folders (best effort, evidence-based)."""
    roots: list[Path] = []
    candidates = [
        Path(r"C:\Program Files\Epic Games"),
        Path(r"D:\Program Files\Epic Games"),
        Path(r"E:\Program Files\Epic Games"),
    ]
    for base in candidates:
        if base.exists():
            for item in base.iterdir():
                if item.is_dir() and item.name.upper().startswith("UE_"):
                    roots.append(item)
                if item.is_dir() and item.name.upper().startswith("UNREAL"):
                    roots.append(item)
    return roots


def check_unreal_engine(required_version: str, report: CompatibilityReport) -> None:
    """Verify that the requested engine association can be found on this host."""
    installs = unreal_engine_installations()
    if not installs:
        report.add(
            "warning",
            "No Unreal Engine installation found in the standard Epic Games "
            "folder; build and cook steps for the Unreal module cannot run here.",
        )
        return
    versions = [p.name.upper() for p in installs]
    wanted = ("UE_" + required_version).upper()
    if required_version and any(v.startswith(wanted) or f"UE_{required_version.split('.')[0]}" == v for v in versions):
        report.add("info", f"Unreal Engine {required_version} found among {versions}.")
    else:
        report.add(
            "warning",
            f"Project requires Unreal {required_version}; installed engines: "
            + ", ".join(versions)
            + ". Building requires the exact engine generation.",
        )


def check_python_runtime(required_hint: str, report: CompatibilityReport) -> None:
    """required_hint like 'python3.12'."""
    match = re.match(r"python(\d+)\.(\d+)", required_hint or "")
    if not match:
        return
    wanted = (int(match.group(1)), int(match.group(2)))
    actual = (platform.python_version_tuple()[0])

    used = tuple(int(x) for x in platform.python_version_tuple()[:2])

    if used != wanted:
        report.add(
            "warning",
            f"A module requires Python {wanted[0]}.{wanted[1]} but the "
            f"packaging interpreter is {platform.python_version()}. "
            "A locked runtime must be installed to ship that component.",
        )
    _ = actual


def check_dotted_runtime(hint: str, report: CompatibilityReport) -> None:
    """Dispatch a required-runtime hint to a concrete check."""
    if hint.startswith("python"):
        check_python_runtime(hint, report)
    elif hint.startswith("unreal-engine:"):
        check_unreal_engine(hint.split(":", 1)[1], report)
    # nuget/npm/cargo/python-package hints are installed per-module at build
    # time by the module's own package manager; nothing to pre-validate here.
