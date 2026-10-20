"""Folder format: an unpacked, ready-to-run directory.

This is the format to use when the package is copied by hand or shipped on a
network share: the files keep their layout, so the application can be started
directly from the folder.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..models import PackageRequest
from .base import FormatResult, PackageFormat


class FolderFormat(PackageFormat):
    """A directory that contains the application and its manifest."""

    fmt_id = "folder"
    display_name = "Application folder"
    extension = ""

    def target_name(self, request: PackageRequest) -> str:
        from .base import safe_name

        metadata = request.metadata
        return f"{safe_name(metadata.name)}-{safe_name(metadata.version, '0.0.0')}"

    def build(
        self, staging: Path, request: PackageRequest, manifest: dict, target: Path
    ) -> FormatResult:
        staging = Path(staging)
        try:
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(staging, target)
        except OSError as exc:
            return FormatResult(
                ok=False,
                message=f"The application folder could not be written: {exc}",
                errors=[f"Check that {target.parent} is writable."],
            )
        return FormatResult(
            ok=True,
            path=target,
            message=f"Application folder written to {target}",
        )
