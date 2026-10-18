"""Container validation: produce a structured report instead of raising.

The reader is strict and raises on the first problem, which is right for
extraction but unhelpful for an inspection command. The validator wraps the
reader, collects issues, and optionally verifies every entry hash.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Limits
from ..errors import EceError
from ..logging_config import get_logger
from .model import compute_content_hash
from .reader import ContainerReader

log = get_logger("container.validator")

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"


@dataclass
class ValidationIssue:
    severity: str
    code: str
    message: str
    detail: str = ""

    def __str__(self) -> str:
        suffix = f" [{self.detail}]" if self.detail else ""
        return f"{self.severity.upper()}: {self.message}{suffix}"


@dataclass
class ValidationReport:
    path: Path
    ok: bool = False
    issues: list[ValidationIssue] = field(default_factory=list)
    format_version: str = ""
    compression: str = ""
    hash_algo: str = ""
    entry_count: int = 0
    total_uncompressed: int = 0
    total_compressed: int = 0
    file_size: int = 0
    manifest: dict | None = None
    hashes_verified: bool = False
    failed_entries: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == SEVERITY_WARNING]


def validate_container(
    path: Path | str,
    *,
    limits: Limits | None = None,
    deep: bool = False,
) -> ValidationReport:
    """Validate an .ece file and return a report.

    ``deep=True`` additionally re-hashes every entry and recomputes the package
    content hash.
    """
    path = Path(path)
    report = ValidationReport(path=path)

    if not path.exists():
        report.issues.append(
            ValidationIssue(SEVERITY_ERROR, "ece.container.missing", "File does not exist", str(path))
        )
        return report
    if path.is_dir():
        report.issues.append(
            ValidationIssue(SEVERITY_ERROR, "ece.container.not_a_file", "Path is a directory", str(path))
        )
        return report

    reader = ContainerReader(path, limits=limits)
    try:
        reader.open()
    except EceError as exc:
        report.issues.append(ValidationIssue(SEVERITY_ERROR, exc.code, exc.message, exc.detail or ""))
        return report
    except OSError as exc:
        report.issues.append(
            ValidationIssue(SEVERITY_ERROR, "ece.container.io", "Cannot read the file", str(exc))
        )
        return report

    try:
        header = reader.header
        manifest = reader.manifest
        index = reader.index
        assert header is not None and manifest is not None and index is not None

        report.format_version = f"{header.major}.{header.minor}"
        report.compression = header.compression_name
        report.hash_algo = header.hash_name
        report.entry_count = len(index.entries)
        report.total_uncompressed = index.total_uncompressed
        report.total_compressed = sum(e.length for e in index.entries)
        report.file_size = reader.file_size
        report.manifest = manifest.to_dict()

        # Header flag / capability checks.
        if header.flags & 0x01 == 0:
            report.issues.append(
                ValidationIssue(
                    SEVERITY_WARNING, "ece.container.no_manifest_flag", "Manifest flag is not set"
                )
            )

        # Manifest vs. index consistency.
        if manifest.file_count and manifest.file_count != report.entry_count:
            report.issues.append(
                ValidationIssue(
                    SEVERITY_ERROR,
                    "ece.container.file_count_mismatch",
                    "Manifest file count does not match the index",
                    f"{manifest.file_count} != {report.entry_count}",
                )
            )
        if manifest.total_uncompressed and manifest.total_uncompressed != report.total_uncompressed:
            report.issues.append(
                ValidationIssue(
                    SEVERITY_ERROR,
                    "ece.container.size_mismatch",
                    "Manifest total size does not match the index",
                    f"{manifest.total_uncompressed} != {report.total_uncompressed}",
                )
            )

        if report.total_uncompressed > (limits or Limits()).max_total_uncompressed_bytes:
            report.issues.append(
                ValidationIssue(
                    SEVERITY_ERROR,
                    "ece.container.limit_exceeded",
                    "Uncompressed payload exceeds the configured limit",
                )
            )

        # Content hash over the index (does not require reading payload bytes).
        if manifest.content_hash:
            actual = compute_content_hash(index.entries, manifest.content_hash_algo)
            if actual != manifest.content_hash:
                report.issues.append(
                    ValidationIssue(
                        SEVERITY_ERROR,
                        "ece.container.content_hash_mismatch",
                        "Package content hash does not match the index",
                        f"expected {manifest.content_hash}, got {actual}",
                    )
                )
        else:
            report.issues.append(
                ValidationIssue(
                    SEVERITY_WARNING,
                    "ece.container.no_content_hash",
                    "Manifest has no content hash; integrity cannot be checked",
                )
            )

        if deep:
            failed = _deep_verify(reader, report)
            report.hashes_verified = True
            report.failed_entries = failed

        report.ok = not report.errors
    finally:
        reader.close()

    return report


def _deep_verify(reader: ContainerReader, report: ValidationReport) -> list[str]:
    failed: list[str] = []
    index = reader.index
    assert index is not None
    for entry in index.entries:
        digest = hashlib.sha256()
        try:
            for chunk in reader.iter_entry_chunks(entry):
                digest.update(chunk)
        except EceError as exc:
            failed.append(entry.path)
            report.issues.append(
                ValidationIssue(SEVERITY_ERROR, exc.code, f"Cannot read entry {entry.path}", exc.detail or "")
            )
            continue
        if entry.digest and digest.hexdigest() != entry.digest:
            failed.append(entry.path)
            report.issues.append(
                ValidationIssue(
                    SEVERITY_ERROR,
                    "ece.container.integrity",
                    f"Entry hash mismatch: {entry.path}",
                )
            )
    return failed
