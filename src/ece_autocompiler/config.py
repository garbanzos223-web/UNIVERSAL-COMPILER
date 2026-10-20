"""Application configuration and hard safety limits.

The limits defined here are treated as untrusted-input boundaries: the
container reader, index parser and extractor all enforce them so that a hostile
or corrupt .ece file cannot exhaust memory or fill the disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .local_store import app_data_dir


def _default_output_dir() -> Path:
    """Writable default output directory; never depends on the current folder."""
    return app_data_dir() / "output"


def _default_work_dir() -> Path:
    """Writable default work directory; never depends on the current folder."""
    return app_data_dir() / "work"


@dataclass(frozen=True)
class Limits:
    """Resource limits enforced while reading or writing containers."""

    # Guard rails during packaging.
    max_entries: int = 100_000
    max_path_length: int = 1024
    max_total_uncompressed_bytes: int = 8 * 1024 * 1024 * 1024  # 8 GiB
    max_entry_uncompressed_bytes: int = 4 * 1024 * 1024 * 1024  # 4 GiB
    max_manifest_bytes: int = 8 * 1024 * 1024  # 8 MiB
    max_index_bytes: int = 64 * 1024 * 1024  # 64 MiB
    max_directory_depth: int = 64

    # Guard rails while scanning a project on disk.
    max_scan_files: int = 200_000
    max_scan_depth: int = 32

    # Build process.
    default_build_timeout_seconds: int = 1800
    max_build_timeout_seconds: int = 24 * 3600
    log_tail_lines: int = 4000


@dataclass
class AppConfig:
    """Runtime configuration, resolved once at start-up."""

    output_dir: Path = field(default_factory=_default_output_dir)
    work_dir: Path = field(default_factory=_default_work_dir)
    limits: Limits = field(default_factory=Limits)
    # When true, build commands that can run project-controlled code (install
    # steps, arbitrary build scripts) require explicit user confirmation.
    require_consent_for_scripts: bool = True
    # Locale for user-facing strings; technical strings stay in English.
    locale: str = "en"

    def ensure_directories(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
