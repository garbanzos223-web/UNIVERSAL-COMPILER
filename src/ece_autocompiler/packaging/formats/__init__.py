"""Registry of the available package formats.

Adding a format: write the class, then add it to :data:`FORMATS`. The CLI, the
GUI and the tests all read the registry, so a new format shows up everywhere
without further changes.
"""

from __future__ import annotations

from .archive import TarGzFormat, ZipFormat
from .base import FormatResult, PackageFormat
from .folder import FolderFormat
from .linux import AppImageFormat, DebFormat
from .windows import InnoSetupFormat, NsisFormat

#: Every format this application can produce, keyed by its id.
FORMATS: dict[str, PackageFormat] = {}


def _register(formats: list[PackageFormat]) -> None:
    for fmt in formats:
        FORMATS[fmt.fmt_id] = fmt


_register([ZipFormat(), TarGzFormat(), FolderFormat(), InnoSetupFormat(), NsisFormat(), DebFormat(), AppImageFormat()])


def get_format(fmt_id: str) -> PackageFormat | None:
    """The format with this id, or None when the id is unknown."""
    return FORMATS.get((fmt_id or "").strip().lower())


def format_ids() -> list[str]:
    """Every supported format id, in a stable order."""
    return list(FORMATS.keys())


def describe_formats() -> list[dict]:
    """A machine-readable description of every format and its requirements."""
    described: list[dict] = []
    for fmt_id, fmt in FORMATS.items():
        avail = fmt.availability()
        described.append(
            {
                "id": fmt_id,
                "name": fmt.display_name,
                "extension": fmt.extension,
                "tool": fmt.tool_id,
                "available": avail.found,
                "tool_path": avail.path,
                "note": fmt.platform_note,
            }
        )
    return described


__all__ = [
    "FORMATS",
    "FormatResult",
    "PackageFormat",
    "describe_formats",
    "format_ids",
    "get_format",
]
