"""The advanced package generator.

It produces real, verified distributable packages: ZIP and TAR.GZ archives
with the standard library, an application folder, a Windows installer (Inno
Setup or NSIS) and the Linux formats (DEB and AppImage) when their tooling is
installed.
"""

from __future__ import annotations

from .formats import describe_formats, format_ids, get_format
from .models import (
    DependencyIssue,
    PackageFile,
    PackageMetadata,
    PackageRequest,
    PackageResult,
)
from .packager import AdvancedPackager

__all__ = [
    "AdvancedPackager",
    "DependencyIssue",
    "PackageFile",
    "PackageMetadata",
    "PackageRequest",
    "PackageResult",
    "describe_formats",
    "format_ids",
    "get_format",
]
