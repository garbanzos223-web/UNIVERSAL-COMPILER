"""Base class for package formats.

Every format is a small, isolated adapter: it knows how to turn a staged
directory of files into one distributable artifact, and how to report when it
cannot (missing external tool, unsupported platform).

Adding a format means writing one class and registering it in
:mod:`ece_autocompiler.packaging.formats`; nothing else changes.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from ..models import PackageRequest
from ..tools import ToolAvailability, availability

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class FormatResult:
    """What a format produced."""

    ok: bool = False
    path: Path | None = None
    message: str = ""
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def safe_name(text: str, fallback: str = "application") -> str:
    """Turn a title into a file-name-safe token."""
    cleaned = _SAFE_NAME.sub("-", (text or "").strip()).strip("-._")
    return cleaned or fallback


class PackageFormat(ABC):
    """One distributable format."""

    fmt_id: str = ""
    display_name: str = ""
    extension: str = ""
    #: External tool this format needs ("" when the stdlib is enough).
    tool_id: str = ""
    #: Formats that only work on a specific platform say so here.
    platform_note: str = ""

    # ------------------------------------------------------------------
    def target_name(self, request: PackageRequest) -> str:
        """File name (without directory) of the produced artifact."""
        metadata = request.metadata
        stem = f"{safe_name(metadata.name)}-{safe_name(metadata.version, '0.0.0')}"
        return stem + self.extension

    def availability(self) -> ToolAvailability:
        """Whether this format can run on this machine."""
        if not self.tool_id:
            return ToolAvailability("", self.display_name, True, path="(built-in)")
        return availability(self.tool_id)

    def availability_lines(self) -> list[str]:
        from ..tools import availability_text

        return availability_text(self.availability())

    # ------------------------------------------------------------------
    @abstractmethod
    def build(
        self,
        staging: Path,
        request: PackageRequest,
        manifest: dict,
        target: Path,
    ) -> FormatResult:
        """Create the artifact at *target* from the files in *staging*."""

    # ------------------------------------------------------------------
    def create(self, staging: Path, request: PackageRequest, manifest: dict) -> FormatResult:
        """Full flow: check the tool, check the target, then build."""
        avail = self.availability()
        if not avail.found:
            lines = self.availability_lines()
            return FormatResult(
                ok=False,
                message=f"The {self.display_name} format is not available on this PC.",
                errors=lines,
                notes=[self.platform_note] if self.platform_note else [],
            )

        target = Path(request.output_dir) / self.target_name(request)
        if target.exists() and not request.overwrite:
            return FormatResult(
                ok=False,
                message=f"{target.name} already exists in {request.output_dir}.",
                errors=[
                    "The existing file was kept. Re-run with the overwrite option to "
                    "replace it."
                ],
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return FormatResult(
                ok=False,
                message=f"The output folder could not be created: {exc}",
                errors=[f"Check that {target.parent} is writable."],
            )
        return self.build(staging, request, manifest, target)
