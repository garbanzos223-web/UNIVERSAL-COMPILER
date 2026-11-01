"""Data model shared by the decompiler, the reconstructed project and the UI.

The decompiler never guesses silently: every file it produces carries a
``status`` (recovered source, partial reconstruction, generated outline,
extracted resource or unsupported) and a ``provenance`` sentence explaining
where it came from. The UI and the CLI render those fields verbatim, so a
generated outline is never presented as the original source.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------- statuses
STATUS_RECOVERED = "recovered"      # complete source recovered by a backend
STATUS_PARTIAL = "partial"          # source-like, structurally incomplete
STATUS_GENERATED = "generated"      # pseudocode / outline generated from metadata
STATUS_RESOURCE = "resource"        # extracted binary or data resource
STATUS_UNSUPPORTED = "unsupported"  # known, but not recoverable by this build

# ---------------------------------------------------------------- categories
CATEGORY_SOURCE = "source"
CATEGORY_PSEUDOCODE = "pseudocode"
CATEGORY_DISASSEMBLY = "disassembly"
CATEGORY_RESOURCE = "resource"
CATEGORY_METADATA = "metadata"
CATEGORY_CONFIG = "config"

STATUS_LABELS = {
    STATUS_RECOVERED: "recovered source",
    STATUS_PARTIAL: "partial reconstruction",
    STATUS_GENERATED: "generated (not original source)",
    STATUS_RESOURCE: "extracted resource",
    STATUS_UNSUPPORTED: "unsupported",
}


@dataclass(frozen=True)
class FormatInfo:
    """What a file is, how sure the detector is and what can be done with it."""

    kind: str
    label: str
    language: str = "unknown"
    runtime: str = "unknown"
    architecture: str = "unknown"
    family: str = "unknown"
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def supports(self, operation: str) -> bool:
        return operation in self.operations

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence"] = list(self.evidence)
        data["operations"] = list(self.operations)
        data["notes"] = list(self.notes)
        return data


@dataclass
class FileAnalysis:
    """The safe, read-only analysis of one imported file."""

    path: Path
    size: int
    sha256: str
    format: FormatInfo
    details: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "name": self.name,
            "size": self.size,
            "sha256": self.sha256,
            "format": self.format.to_dict(),
            "details": self.details,
            "notes": self.notes,
            "warnings": self.warnings,
        }

    def summary_lines(self) -> list[str]:
        """Human-readable analysis, used by the CLI and the GUI analysis pane."""
        fmt = self.format
        lines = [
            f"File: {self.path}",
            f"Size: {self.size:,} bytes",
            f"SHA-256: {self.sha256 or '(not computed)'}",
            f"Format: {fmt.label} ({fmt.kind})",
            f"Language: {fmt.language}",
            f"Runtime: {fmt.runtime}",
            f"Architecture: {fmt.architecture}",
            f"Confidence: {int(fmt.confidence * 100)}%",
            f"Evidence: {', '.join(fmt.evidence) or '(none)'}",
            f"Supported operations: {', '.join(fmt.operations) or '(analysis only)'}",
        ]
        if self.details:
            lines.append("")
            lines.append("Details:")
            for key, value in self.details.items():
                if isinstance(value, (list, tuple)):
                    rendered = ", ".join(str(v) for v in value[:12])
                    if len(value) > 12:
                        rendered += f", ... ({len(value)} total)"
                elif isinstance(value, dict):
                    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
                else:
                    rendered = str(value)
                lines.append(f"  {key}: {rendered}")
        for note in self.notes:
            lines.append(f"note: {note}")
        for warning in self.warnings:
            lines.append(f"warning: {warning}")
        return lines


@dataclass
class RecoveredFile:
    """One file of a reconstructed project."""

    path: str
    category: str
    status: str
    provenance: str = ""
    language: str = ""
    disk_path: Path | None = None      # already-written file inside the project
    text: str | None = None            # when disk_path is None
    size: int = 0
    editable: bool = True

    @property
    def is_text(self) -> bool:
        if self.text is not None:
            return True
        if self.category == CATEGORY_RESOURCE:
            return False
        return self.disk_path is not None

    def read_text(self) -> str:
        if self.text is not None:
            return self.text
        if self.disk_path is not None:
            try:
                return self.disk_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return ""
        return ""

    def write_to(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.disk_path is not None:
            shutil.copyfile(self.disk_path, destination)
        else:
            destination.write_text(self.text or "", encoding="utf-8", newline="\n")
        return destination

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "category": self.category,
            "status": self.status,
            "provenance": self.provenance,
            "language": self.language,
            "size": self.size or (len(self.text.encode("utf-8")) if self.text else 0),
            "editable": self.editable,
        }


@dataclass
class BackendOutput:
    """What one backend produced for one input file."""

    backend: str
    ok: bool = True
    files: list[RecoveredFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    project_kind: str = ""
    recompile_ready: bool = False
    log: list[tuple[str, str]] = field(default_factory=list)

    def add(self, file: RecoveredFile) -> RecoveredFile:
        self.files.append(file)
        return file


@dataclass
class DecompileResult:
    """The complete outcome of a decompilation run."""

    ok: bool
    output_dir: Path
    files: list[RecoveredFile] = field(default_factory=list)
    analyses: list[FileAnalysis] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    log: list[tuple[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    project_kind: str = ""
    recompile_ready: bool = False
    manifest_path: Path | None = None
    cancelled: bool = False

    @property
    def sources(self) -> list[RecoveredFile]:
        return [f for f in self.files if f.category == CATEGORY_SOURCE]

    @property
    def resources(self) -> list[RecoveredFile]:
        return [f for f in self.files if f.category == CATEGORY_RESOURCE]

    def file_by_path(self, relative: str) -> RecoveredFile | None:
        for item in self.files:
            if item.path == relative:
                return item
        return None

    def export(self, destination: Path) -> list[Path]:
        """Write every reconstructed file under *destination*; return them."""
        destination = Path(destination)
        written: list[Path] = []
        for item in self.files:
            # Recovered paths are relative and normalised by the backend. Guard
            # again: a hostile archive must never escape the export directory.
            rel = Path(item.path)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"refusing to export an unsafe path: {item.path}")
            written.append(item.write_to(destination / rel))
        written.append(self.write_manifest(destination))
        return written

    def write_manifest(self, destination: Path | None = None) -> Path:
        base = Path(destination) if destination is not None else self.output_dir
        payload = {
            "tool": "ECE Universal AutoCompiler",
            "kind": "recovered-project",
            "project_kind": self.project_kind,
            "ok": self.ok,
            "recompile_ready": self.recompile_ready,
            "output_dir": str(base),
            "inputs": [a.to_dict() for a in self.analyses],
            "files": [f.to_dict() for f in self.files],
            "warnings": self.warnings,
            "errors": self.errors,
        }
        path = base / "recovered-project.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        if destination is None:
            self.manifest_path = path
        return path
