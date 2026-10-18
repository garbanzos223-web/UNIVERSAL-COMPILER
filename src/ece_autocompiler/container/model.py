"""Data model for the manifest and file index stored inside an .ece container.

The model is deliberately plain: JSON-friendly dicts produced by ``to_dict`` and
parsed back by ``from_dict`` with strict validation of types and ranges. Unknown
keys are preserved on read and re-emitted on write so that a minor-version file
written by a newer tool is not silently stripped by an older one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..errors import ContainerFormatError
from ..version import APP_NAME, APP_VERSION, CONTAINER_FORMAT_VERSION
from .security import normalize_entry_path
from .spec import COMPRESSION_NAMES, HASH_NAMES


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContainerFormatError(message)


@dataclass
class FileEntry:
    """One file inside the container payload."""

    path: str
    offset: int
    length: int
    size: int
    compression: str = "store"
    hash_algo: str = "sha256"
    digest: str = ""
    executable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "offset": self.offset,
            "length": self.length,
            "size": self.size,
            "compression": self.compression,
            "hash_algo": self.hash_algo,
            "hash": self.digest,
            "executable": self.executable,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FileEntry":
        _require(isinstance(raw, dict), "Index entry must be an object")
        for key in ("path", "offset", "length", "size"):
            _require(key in raw, f"Index entry is missing '{key}'")
        path = normalize_entry_path(raw["path"])
        entry = cls(
            path=path,
            offset=int(raw["offset"]),
            length=int(raw["length"]),
            size=int(raw["size"]),
            compression=str(raw.get("compression", "store")),
            hash_algo=str(raw.get("hash_algo", "none")),
            digest=str(raw.get("hash", "")),
            executable=bool(raw.get("executable", False)),
        )
        if entry.compression not in COMPRESSION_NAMES.values():
            raise ContainerFormatError(
                "Unknown compression identifier", detail=entry.compression
            )
        if entry.hash_algo not in HASH_NAMES.values():
            raise ContainerFormatError("Unknown hash algorithm", detail=entry.hash_algo)
        if min(entry.offset, entry.length, entry.size) < 0:
            raise ContainerFormatError("Negative size or offset in index", detail=entry.path)
        return entry


@dataclass
class Index:
    entries: list[FileEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [e.to_dict() for e in self.entries]}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Index":
        _require(isinstance(raw, dict), "Index must be an object")
        raw_entries = raw.get("entries")
        _require(isinstance(raw_entries, list), "Index 'entries' must be a list")
        return cls(entries=[FileEntry.from_dict(e) for e in raw_entries])

    def by_path(self) -> dict[str, FileEntry]:
        return {e.path: e for e in self.entries}

    @property
    def total_uncompressed(self) -> int:
        return sum(e.size for e in self.entries)


@dataclass
class Manifest:
    """Project-level metadata describing what the payload is and how to run it."""

    project_name: str
    adapter_id: str = ""
    payload_type: str = "unknown"
    platform: str = "unknown"
    architecture: str = "unknown"
    configuration: str = "unknown"
    entry_points: list[dict[str, Any]] = field(default_factory=list)
    runtime_requirements: list[dict[str, Any]] = field(default_factory=list)
    created_utc: str = ""
    tool_version: str = APP_VERSION
    notes: str = ""
    file_count: int = 0
    total_uncompressed: int = 0
    content_hash: str = ""
    content_hash_algo: str = "sha256"
    unknown_fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "format_version": CONTAINER_FORMAT_VERSION,
            "created_by": {"tool": APP_NAME, "tool_version": self.tool_version},
            "created_utc": self.created_utc or _now_utc(),
            "project": {
                "name": self.project_name,
                "adapter_id": self.adapter_id,
                "detected_type": self.payload_type,
            },
            "package": {
                "payload_type": self.payload_type,
                "entry_points": self.entry_points,
                "runtime_requirements": self.runtime_requirements,
            },
            "target": {
                "platform": self.platform,
                "architecture": self.architecture,
                "configuration": self.configuration,
            },
            "content": {
                "file_count": self.file_count,
                "total_uncompressed": self.total_uncompressed,
                "hash_algo": self.content_hash_algo,
                "content_hash": self.content_hash,
            },
            "notes": self.notes,
        }
        # Unknown keys from a newer minor version survive a rewrite.
        for key, value in self.unknown_fields.items():
            if key not in base:
                base[key] = value
        return base

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Manifest":
        _require(isinstance(raw, dict), "Manifest must be an object")
        project = raw.get("project") or {}
        package = raw.get("package") or {}
        target = raw.get("target") or {}
        content = raw.get("content") or {}
        _require(isinstance(project, dict), "Manifest 'project' must be an object")
        _require(isinstance(package, dict), "Manifest 'package' must be an object")
        _require(isinstance(target, dict), "Manifest 'target' must be an object")
        _require(isinstance(content, dict), "Manifest 'content' must be an object")

        entry_points = package.get("entry_points", [])
        runtime_requirements = package.get("runtime_requirements", [])
        _require(isinstance(entry_points, list), "'entry_points' must be a list")
        _require(isinstance(runtime_requirements, list), "'runtime_requirements' must be a list")

        # Version handling: unknown majors are rejected by the reader before it
        # reaches here, so a newer minor simply leaves extra top-level keys.
        known = {
            "format_version",
            "created_by",
            "created_utc",
            "project",
            "package",
            "target",
            "content",
            "notes",
        }
        unknown = {k: v for k, v in raw.items() if k not in known}

        created_by = raw.get("created_by") or {}
        tool_version = str(created_by.get("tool_version", APP_VERSION)) if isinstance(created_by, dict) else APP_VERSION

        return cls(
            project_name=str(project.get("name", "unnamed")),
            adapter_id=str(project.get("adapter_id", "")),
            payload_type=str(package.get("payload_type", "unknown")),
            platform=str(target.get("platform", "unknown")),
            architecture=str(target.get("architecture", "unknown")),
            configuration=str(target.get("configuration", "unknown")),
            entry_points=list(entry_points),
            runtime_requirements=list(runtime_requirements),
            created_utc=str(raw.get("created_utc", "")),
            tool_version=tool_version,
            notes=str(raw.get("notes", "")),
            file_count=int(content.get("file_count", 0)),
            total_uncompressed=int(content.get("total_uncompressed", 0)),
            content_hash=str(content.get("content_hash", "")),
            content_hash_algo=str(content.get("hash_algo", "sha256")),
            unknown_fields=unknown,
        )


def compute_content_hash(entries: list[FileEntry], algo: str = "sha256") -> str:
    """Deterministic package-level hash.

    Digests over sorted ``path + NUL + hexdigest + LF`` lines, so the hash is
    independent of entry order while still covering every file's identity,
    content and size.
    """
    if algo != "sha256":
        raise ContainerFormatError("Unsupported content hash algorithm", detail=algo)
    hasher = hashlib.sha256()
    for entry in sorted(entries, key=lambda e: e.path):
        hasher.update(entry.path.encode("utf-8"))
        hasher.update(b"\x00")
        hasher.update(entry.digest.encode("ascii"))
        hasher.update(b"\x00")
        hasher.update(str(entry.size).encode("ascii"))
        hasher.update(b"\n")
    return hasher.hexdigest()


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
