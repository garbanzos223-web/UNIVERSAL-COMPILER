"""External packaging tools: detection and honest availability reporting.

Some package formats need a real third-party tool (Inno Setup, NSIS,
``dpkg-deb``, ``appimagetool``). This module finds them and, when one is
missing, produces the exact same "what is missing / where to install it"
message the rest of the application uses.

Nothing is installed and nothing is downloaded: the user gets a copyable
command and the official URL.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from ..toolchain_hints import hint_lines

#: tool id -> (display name, candidate executables)
TOOLS: dict[str, tuple[str, tuple[str, ...]]] = {
    "inno-setup": ("Inno Setup", ("ISCC", "iscc", "ISCC.exe")),
    "nsis": ("NSIS", ("makensis", "makensis.exe")),
    "dpkg-deb": ("dpkg-deb (Debian packaging)", ("dpkg-deb",)),
    "appimage": ("AppImageKit (appimagetool)", ("appimagetool", "appimagetool.AppImage")),
}


@dataclass(frozen=True)
class ToolAvailability:
    """Whether an external packaging tool can be used on this machine."""

    tool_id: str
    name: str
    found: bool
    path: str = ""
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "tool_id": self.tool_id,
            "name": self.name,
            "found": self.found,
            "path": self.path,
            "reason": self.reason,
        }


def tool_path(tool_id: str) -> str:
    """Absolute path of an external packaging tool, or an empty string."""
    entry = TOOLS.get(tool_id)
    if entry is None:
        return ""
    for candidate in entry[1]:
        found = shutil.which(candidate)
        if found:
            return found
    return ""


def availability(tool_id: str) -> ToolAvailability:
    """Report whether *tool_id* is usable, with an actionable reason."""
    entry = TOOLS.get(tool_id)
    if entry is None:
        return ToolAvailability(tool_id, tool_id, False, reason="Unknown packaging tool.")
    name, _ = entry
    path = tool_path(tool_id)
    if path:
        return ToolAvailability(tool_id, name, True, path=path)
    return ToolAvailability(
        tool_id,
        name,
        False,
        reason=f"{name} was not found on this PC.",
    )


def install_hint_lines(tool_id: str) -> list[str]:
    """Copyable install commands for a missing packaging tool."""
    return hint_lines(tool_id)


def availability_text(avail: ToolAvailability) -> list[str]:
    """Human lines for a missing tool: what is missing and how to get it."""
    if avail.found:
        return [f"{avail.name}: {avail.path}"]
    lines = [f"Missing: {avail.name}"]
    lines.extend(install_hint_lines(avail.tool_id))
    if not any(line.startswith(("winget", "choco", "scoop", "pip")) for line in lines):
        lines.append("  this tool is only available on Linux/macOS or from its official site")
    return lines


def run_tool(
    argv: list[str],
    *,
    cwd: str | None = None,
    timeout: int = 900,
    reproducible: bool = False,
) -> tuple[int, str]:
    """Run an external packaging tool and return ``(exit_code, output)``.

    The command is always an argv list (``shell=False``): paths that contain
    spaces cannot turn into an injected command.
    """
    env = os.environ.copy()
    if reproducible:
        # Honour the cross-platform convention for reproducible builds.
        env.setdefault("SOURCE_DATE_EPOCH", "315532800")  # 1980-01-01
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            shell=False,
            env=env,
        )
    except FileNotFoundError as exc:
        return 127, f"The tool was not found: {exc}"
    except subprocess.TimeoutExpired:
        return 124, "The packaging tool timed out."
    except OSError as exc:
        return 126, f"The packaging tool could not be started: {exc}"
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, output.strip()
