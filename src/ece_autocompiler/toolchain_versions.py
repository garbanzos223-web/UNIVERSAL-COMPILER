"""Minimum usable toolchain versions and a version report (item 15).

Neutral module (imports only detection) so both the ``doctor`` command and the
GUI's project-health report can use it without an import cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .detection.toolchains import get_tool

#: Minimum usable version for each tool this project drives, with the reason.
MIN_TOOL_VERSIONS: dict[str, tuple[tuple[int, ...], str]] = {
    "python": ((3, 9), "PyInstaller and modern syntax need Python 3.9+"),
    "node": ((18, 0), "the current LTS line starts at Node 18"),
    "npm": ((8, 0), "workspaces and lockfile v3 need npm 8+"),
    "dotnet": ((6, 0), "self-contained win-x64 publish is supported from .NET 6"),
    "cargo": ((1, 70), "recent dependency resolution needs a recent Cargo"),
    "rustc": ((1, 70), "recent dependency resolution needs a recent Rust"),
    "go": ((1, 20), "module builds assume Go 1.20+"),
    "cmake": ((3, 20), "presets and modern Windows support start at CMake 3.20"),
    "ninja": ((1, 10), "stable parallel builds need Ninja 1.10+"),
    "gcc": ((9, 0), "C++17/20 needs GCC 9+"),
    "javac": ((17, 0), "Temurin 17+ is the current LTS"),
    "java": ((17, 0), "Temurin 17+ is the current LTS"),
    "mvn": ((3, 8), "current plugin versions need Maven 3.8+"),
    "gradle": ((7, 5), "the current toolchain plugin needs Gradle 7.5+"),
    "pyinstaller": ((5, 0), "PyInstaller 5+ matches modern Python"),
}

_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+|\d+)")


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """Extract the first numeric version from *text*; None when there is none."""
    if not text:
        return None
    match = _VERSION_RE.search(text)
    if not match:
        return None
    parts: list[int] = []
    for piece in match.group(1).split("."):
        try:
            parts.append(int(piece))
        except ValueError:  # pragma: no cover - the regex only yields digits
            break
    return tuple(parts) or None


def version_satisfies(found: str | None, minimum: tuple[int, ...] | None) -> bool:
    """True when *found* is at least *minimum* (unreadable versions never fail)."""
    if minimum is None:
        return True
    parsed = parse_version(found)
    if parsed is None:
        return True
    length = max(len(parsed), len(minimum))
    left = parsed + (0,) * (length - len(parsed))
    right = minimum + (0,) * (length - len(minimum))
    return left >= right


@dataclass
class ToolchainVersion:
    """A detected tool, its version and whether it meets the minimum."""

    tool_id: str
    name: str
    found: bool
    version_text: str = ""
    minimum: tuple[int, ...] | None = None
    note: str = ""

    @property
    def satisfies(self) -> bool:
        if not self.found:
            return False
        return version_satisfies(self.version_text, self.minimum)

    @property
    def status(self) -> str:
        if not self.found:
            return "missing"
        if self.minimum is not None and not self.satisfies:
            return "too-old"
        return "ok"

    @property
    def minimum_text(self) -> str:
        return ".".join(str(p) for p in self.minimum) if self.minimum else ""

    def to_dict(self) -> dict:
        return {
            "id": self.tool_id,
            "name": self.name,
            "found": self.found,
            "version": self.version_text,
            "minimum": self.minimum_text,
            "satisfies": self.satisfies,
            "status": self.status,
            "note": self.note,
        }


def toolchain_versions(tool_ids: list[str] | None = None) -> list[ToolchainVersion]:
    """Version report for every known tool (or just *tool_ids*)."""
    ids = list(tool_ids) if tool_ids is not None else sorted(MIN_TOOL_VERSIONS)
    report: list[ToolchainVersion] = []
    for tool_id in ids:
        info = get_tool(tool_id)
        minimum, reason = MIN_TOOL_VERSIONS.get(tool_id, (None, ""))
        if info is None:
            report.append(
                ToolchainVersion(tool_id, tool_id, False, minimum=minimum, note=reason)
            )
            continue
        report.append(
            ToolchainVersion(
                tool_id=tool_id,
                name=info.name,
                found=info.found,
                version_text=info.version or "",
                minimum=minimum,
                note=reason,
            )
        )
    return report
