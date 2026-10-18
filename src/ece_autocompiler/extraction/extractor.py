"""Safe extraction of an .ece container.

Extraction is the highest-risk operation in the application: it writes
attacker-influenced paths to disk. Every entry is written through a temporary
file inside the destination and moved into place only after its hash has been
verified, so a truncated or tampered container never leaves a half-written file
that looks valid.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from ..config import Limits
from ..container.model import FileEntry
from ..container.reader import ContainerReader
from ..container.security import ensure_no_symlink_target, is_junction_or_symlink, resolve_destination
from ..errors import (
    ContainerError,
    ContainerFormatError,
    ExtractionConflictError,
    ExtractionError,
    IntegrityError,
    UnsafePathError,
)
from ..logging_config import get_logger

log = get_logger("extraction")

ProgressCallback = Callable[[str, int, int], None]
CancelCheck = Callable[[], bool]


@dataclass
class ExtractionReport:
    destination: Path
    files_written: int = 0
    bytes_written: int = 0
    overwritten: int = 0
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (
            f"{self.files_written} file(s), {self.bytes_written} bytes written"
            + (f", {self.overwritten} overwritten" if self.overwritten else "")
        )


class Extractor:
    def __init__(self, *, limits: Limits | None = None) -> None:
        self.limits = limits or Limits()

    def extract(
        self,
        container_path: Path | str,
        destination: Path | str,
        *,
        overwrite: bool = False,
        verify_hashes: bool = True,
        selected_paths: Iterable[str] | None = None,
        progress: ProgressCallback | None = None,
        cancel: CancelCheck | None = None,
    ) -> ExtractionReport:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        if not destination.is_dir():
            raise ExtractionError("Destination is not a directory", detail=str(destination))

        selection = set(selected_paths) if selected_paths is not None else None
        report = ExtractionReport(destination=destination)

        reader = ContainerReader(container_path, limits=self.limits)
        with reader:
            entries = reader.entries()
            total = sum(e.size for e in entries if selection is None or e.path in selection)
            done = 0

            for entry in entries:
                if cancel is not None and cancel():
                    raise ContainerError("Extraction cancelled")

                if selection is not None and entry.path not in selection:
                    continue

                target = resolve_destination(
                    destination, entry.path, max_length=self.limits.max_path_length
                )
                self._ensure_parent_dirs(target, destination)

                if target.exists():
                    if is_junction_or_symlink(target):
                        raise UnsafePathError(
                            "Destination entry is a symlink or junction", detail=entry.path
                        )
                    if target.is_dir():
                        raise ExtractionError(
                            "Destination entry is an existing directory", detail=entry.path
                        )
                    if not overwrite:
                        raise ExtractionConflictError(
                            "A file already exists at the destination",
                            detail=entry.path,
                        )
                    report.overwritten += 1

                ensure_no_symlink_target(target)

                written = self._write_entry(reader, entry, target, verify_hashes=verify_hashes)
                done += written
                report.files_written += 1
                report.bytes_written += written
                if done > self.limits.max_total_uncompressed_bytes:
                    raise ContainerError("Extraction produced more data than the configured limit")

                if progress:
                    progress(entry.path, done, total)

        return report

    # ------------------------------------------------------------------

    def _ensure_parent_dirs(self, target: Path, root: Path) -> None:
        # Re-check every ancestor is inside root in case a symlinked directory
        # appears part-way down the chain.
        relative_parent = target.parent
        chain: list[Path] = []
        cursor = relative_parent
        while cursor != root and cursor != cursor.parent:
            chain.append(cursor)
            cursor = cursor.parent
        for directory in reversed(chain):
            if directory.exists():
                if is_junction_or_symlink(directory):
                    raise UnsafePathError(
                        "Refusing to extract through a symlinked directory",
                        detail=str(directory),
                    )
                if not directory.is_dir():
                    raise ExtractionError(
                        "A parent path exists but is not a directory", detail=str(directory)
                    )
            else:
                directory.mkdir()

    def _write_entry(
        self,
        reader: ContainerReader,
        entry: FileEntry,
        target: Path,
        *,
        verify_hashes: bool,
    ) -> int:
        digest = hashlib.sha256()
        temp = target.with_name(target.name + ".ece-part")

        try:
            written = 0
            with open(temp, "wb") as out:
                for chunk in reader.iter_entry_chunks(entry):
                    out.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                out.flush()
                os.fsync(out.fileno())

            if verify_hashes and entry.digest:
                actual = digest.hexdigest()
                if actual != entry.digest:
                    raise IntegrityError(
                        "Entry hash mismatch; refusing to keep the extracted file",
                        detail=f"{entry.path}: expected {entry.digest}, got {actual}",
                    )

            if entry.executable and os.name != "nt":
                mode = os.stat(temp).st_mode
                os.chmod(temp, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

            os.replace(temp, target)
            return written
        except BaseException:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:  # pragma: no cover
                    pass
            raise

    # ------------------------------------------------------------------

    def inspect(self, container_path: Path | str) -> "ContainerReader":
        reader = ContainerReader(container_path, limits=self.limits)
        return reader.open()


def extract(
    container_path: Path | str,
    destination: Path | str,
    **kwargs,
) -> ExtractionReport:
    """Convenience wrapper around :class:`Extractor`."""
    return Extractor(limits=kwargs.pop("limits", None)).extract(
        container_path, destination, **kwargs
    )
