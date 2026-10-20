"""Structural diff between two ``.ece`` containers (item 20).

The user asked to \"compare two builds\": which files were added, removed or
changed, and whether the project metadata moved. This reads two containers
(read-only, hash-checked by the reader) and returns a plain report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import Limits
from ..errors import ContainerError, EceError
from .reader import ContainerReader

ADDED = "added"
REMOVED = "removed"
MODIFIED = "modified"

#: Manifest fields worth comparing, with friendly labels.
_MANIFEST_FIELDS = (
    ("project_name", "Project name"),
    ("adapter_id", "Adapter"),
    ("payload_type", "Payload type"),
    ("platform", "Platform"),
    ("architecture", "Architecture"),
    ("configuration", "Configuration"),
    ("tool_version", "Tool version"),
    ("content_hash", "Content hash"),
)


@dataclass
class FileChange:
    path: str
    status: str
    old_size: int = 0
    new_size: int = 0
    old_hash: str = ""
    new_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "status": self.status,
            "old_size": self.old_size,
            "new_size": self.new_size,
            "old_hash": self.old_hash,
            "new_hash": self.new_hash,
        }

    def describe(self) -> str:
        if self.status == ADDED:
            return f"+ {self.path} ({self.new_size} B)"
        if self.status == REMOVED:
            return f"- {self.path} ({self.old_size} B)"
        return f"~ {self.path} ({self.old_size} B -> {self.new_size} B)"


@dataclass
class ContainerDiff:
    old_path: Path
    new_path: Path
    changes: list[FileChange] = field(default_factory=list)
    unchanged: int = 0
    old_file_count: int = 0
    new_file_count: int = 0
    old_total: int = 0
    new_total: int = 0
    manifest_changes: list[str] = field(default_factory=list)

    def _of(self, status: str) -> list[FileChange]:
        return [c for c in self.changes if c.status == status]

    @property
    def added(self) -> list[FileChange]:
        return self._of(ADDED)

    @property
    def removed(self) -> list[FileChange]:
        return self._of(REMOVED)

    @property
    def modified(self) -> list[FileChange]:
        return self._of(MODIFIED)

    @property
    def identical(self) -> bool:
        return not self.changes and not self.manifest_changes

    @property
    def summary(self) -> str:
        if self.identical:
            return "The two containers have identical contents and metadata."
        return (
            f"{len(self.added)} added, {len(self.removed)} removed, "
            f"{len(self.modified)} modified, {self.unchanged} unchanged."
        )

    def text(self) -> str:
        lines = [f"Diff: {self.old_path.name} -> {self.new_path.name}", self.summary]
        if self.manifest_changes:
            lines.append("Metadata changes:")
            lines.extend(f"  {item}" for item in self.manifest_changes)
        for change in self.changes:
            lines.append(f"  {change.describe()}")
        delta = self.new_total - self.old_total
        lines.append(f"Total size: {self.old_total} B -> {self.new_total} B ({delta:+d} B)")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "old": str(self.old_path),
            "new": str(self.new_path),
            "identical": self.identical,
            "summary": self.summary,
            "added": [c.to_dict() for c in self.added],
            "removed": [c.to_dict() for c in self.removed],
            "modified": [c.to_dict() for c in self.modified],
            "unchanged": self.unchanged,
            "old_total": self.old_total,
            "new_total": self.new_total,
            "manifest_changes": list(self.manifest_changes),
        }


def _open(path: Path, limits: Limits) -> ContainerReader:
    reader = ContainerReader(path, limits=limits)
    try:
        reader.open()
    except EceError as exc:
        raise ContainerError(
            f"Cannot read '{path.name}': {exc.message}", detail=exc.detail or ""
        ) from exc
    return reader


def diff_containers(old, new, *, limits: Limits | None = None) -> ContainerDiff:
    """Compare two containers; raises ContainerError if either cannot be read."""
    limits = limits or Limits()
    old_path, new_path = Path(old), Path(new)
    old_reader = _open(old_path, limits)
    try:
        new_reader = _open(new_path, limits)
        try:
            result = ContainerDiff(old_path=old_path, new_path=new_path)
            old_entries = {e.path: e for e in old_reader.entries()}
            new_entries = {e.path: e for e in new_reader.entries()}
            result.old_file_count = len(old_entries)
            result.new_file_count = len(new_entries)
            result.old_total = sum(e.size for e in old_entries.values())
            result.new_total = sum(e.size for e in new_entries.values())

            for path, entry in new_entries.items():
                previous = old_entries.get(path)
                if previous is None:
                    result.changes.append(
                        FileChange(path, ADDED, new_size=entry.size, new_hash=entry.digest)
                    )
                elif previous.digest != entry.digest or previous.size != entry.size:
                    result.changes.append(
                        FileChange(
                            path, MODIFIED,
                            old_size=previous.size, new_size=entry.size,
                            old_hash=previous.digest, new_hash=entry.digest,
                        )
                    )
                else:
                    result.unchanged += 1
            for path, entry in old_entries.items():
                if path not in new_entries:
                    result.changes.append(
                        FileChange(path, REMOVED, old_size=entry.size, old_hash=entry.digest)
                    )

            old_manifest = old_reader.manifest
            new_manifest = new_reader.manifest
            if old_manifest is not None and new_manifest is not None:
                for attribute, label in _MANIFEST_FIELDS:
                    before = getattr(old_manifest, attribute, "")
                    after = getattr(new_manifest, attribute, "")
                    if str(before) != str(after):
                        result.manifest_changes.append(f"{label}: {before} -> {after}")
            return result
        finally:
            new_reader.close()
    finally:
        old_reader.close()


def explain(diff: ContainerDiff) -> list[str]:
    """Plain-language lines for the diff (item 19 / item 20)."""
    lines = [diff.summary]
    if diff.added:
        lines.append(f"{len(diff.added)} file(s) exist only in the newer container.")
    if diff.removed:
        lines.append(f"{len(diff.removed)} file(s) were dropped.")
    if diff.modified:
        lines.append(f"{len(diff.modified)} file(s) changed content.")
    if diff.manifest_changes:
        lines.append("Metadata changed: " + "; ".join(diff.manifest_changes))
    return lines
