"""The package manifest: what was packaged, and how to verify it.

The manifest is written inside the package (``package-manifest.json``) and is
also returned to the caller. It records the application metadata, every file
with its size and SHA-256 checksum, the toolchains that were used, the
dependency findings and the license notices.

With ``reproducible`` enabled the creation timestamp is pinned, so two runs
over the same inputs produce byte-identical archives.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from ..version import APP_NAME, APP_VERSION
from .models import DependencyIssue, PackageFile, PackageMetadata, PackageRequest

#: Schema identifier of the manifest format.
SCHEMA = "ece.package-manifest/1"

#: Timestamp used for reproducible builds (the ZIP epoch).
FIXED_TIMESTAMP = "1980-01-01T00:00:00Z"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def build_timestamp(reproducible: bool) -> str:
    """UTC timestamp for the manifest.

    Reproducible packages use a pinned timestamp so the archive does not
    change between two runs over the same inputs. ``SOURCE_DATE_EPOCH`` is
    honoured when it is set, which is the cross-platform convention.
    """
    if reproducible:
        epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
        if epoch:
            try:
                moment = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
                return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            except (ValueError, OverflowError, OSError):
                pass
        return FIXED_TIMESTAMP
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_manifest(
    *,
    metadata: PackageMetadata,
    files: list[PackageFile],
    request: PackageRequest,
    toolchains: dict[str, str] | None = None,
    entry_point: str = "",
    dependencies: list[DependencyIssue] | None = None,
    licenses: list[dict] | None = None,
    format_id: str = "",
) -> dict:
    """Build the manifest dictionary."""
    return {
        "schema": SCHEMA,
        "application": metadata.to_dict(),
        "package": {
            "format": format_id or request.fmt,
            "reproducible": bool(request.reproducible),
            "created_utc": build_timestamp(bool(request.reproducible)),
            "created_by": f"{APP_NAME} {APP_VERSION}",
            "source": str(request.project),
        },
        "content": {
            "file_count": len(files),
            "total_bytes": sum(f.size for f in files),
            "files": [f.to_dict() for f in files],
        },
        "entry_point": entry_point,
        "toolchains": dict(toolchains or {}),
        "dependencies": [d.to_dict() for d in (dependencies or [])],
        "licenses": list(licenses or []),
    }


def detected_toolchains() -> dict[str, str]:
    """Versions of the toolchains present on this machine (best effort)."""
    from ..detection import discover_all

    found: dict[str, str] = {}
    for tool_id, info in discover_all().items():
        if info.found:
            found[tool_id] = info.version or "installed"
    return found
