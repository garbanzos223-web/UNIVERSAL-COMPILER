"""The .ece container format: specification constants, writer, reader, validator."""

from .model import FileEntry, Index, Manifest, compute_content_hash
from .reader import ContainerReader, Header
from .validator import ValidationIssue, ValidationReport, validate_container
from .writer import ContainerWriter, PayloadItem, WriteReport
from .diff import ContainerDiff, FileChange, diff_containers
from .integrity import (
    IntegrityCheck,
    sha256_file,
    verify_container,
    verify_file,
    write_sidecar,
)
from . import spec

__all__ = [
    "ContainerDiff",
    "ContainerReader",
    "ContainerWriter",
    "FileChange",
    "FileEntry",
    "Header",
    "Index",
    "IntegrityCheck",
    "Manifest",
    "PayloadItem",
    "ValidationIssue",
    "ValidationReport",
    "WriteReport",
    "compute_content_hash",
    "diff_containers",
    "sha256_file",
    "spec",
    "validate_container",
    "verify_container",
    "verify_file",
    "write_sidecar",
]
