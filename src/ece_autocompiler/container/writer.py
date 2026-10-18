"""Container writer: packages files into a versioned .ece container.

The payload is streamed into a temporary file first so arbitrarily large build
output never has to fit in memory at once. Entry offsets in the index are
relative to ``header.payload_offset``; keeping them payload-relative avoids a
circular dependency between the index contents and the index's own size.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from ..config import Limits
from ..errors import ContainerError, ContainerFormatError, LimitExceededError
from ..logging_config import get_logger
from .codec import compress, new_stream_encoder
from .model import FileEntry, Index, Manifest, compute_content_hash
from .security import MAX_SEGMENT_LENGTH, normalize_entry_path
from .spec import (
    COMPRESSION_BY_NAME,
    COMPRESSION_NONE,
    FLAG_HAS_MANIFEST,
    FORMAT_MAJOR,
    FORMAT_MINOR,
    HASH_SHA256,
    HEADER_SIZE,
    HEADER_STRUCT,
    MAGIC,
    ZLIB_LEVEL,
)

log = get_logger("container.writer")

ProgressCallback = Callable[[str, int, int], None]

READ_CHUNK = 1024 * 1024


@dataclass
class PayloadItem:
    """One file to place into the container.

    Exactly one of ``source`` or ``data`` must be provided.
    """

    path: str
    source: Path | None = None
    data: bytes | None = None
    executable: bool = False

    def __post_init__(self) -> None:
        if (self.source is None) == (self.data is None):
            raise ValueError("PayloadItem needs exactly one of source or data")


@dataclass
class WriteReport:
    destination: Path
    file_count: int
    total_uncompressed: int
    total_compressed: int
    content_hash: str
    entries: list[FileEntry] = field(default_factory=list)


class ContainerWriter:
    """Assembles an .ece container from a set of files."""

    def __init__(
        self,
        *,
        compression: str = "zlib",
        limits: Limits | None = None,
        zlib_level: int = ZLIB_LEVEL,
    ) -> None:
        if compression not in COMPRESSION_BY_NAME:
            raise ContainerFormatError("Unknown compression", detail=compression)
        self.compression = compression
        self.compression_id = COMPRESSION_BY_NAME[compression]
        self.limits = limits or Limits()
        self.zlib_level = zlib_level

    def write(
        self,
        destination: Path | str,
        items: Sequence[PayloadItem] | Iterable[PayloadItem],
        manifest: Manifest,
        *,
        overwrite: bool = False,
        progress: ProgressCallback | None = None,
    ) -> WriteReport:
        items_list = list(items)
        self._preflight(items_list)

        destination = Path(destination)
        if destination.exists() and not overwrite:
            raise ContainerError(
                "Output file already exists; choose another name or allow overwrite",
                detail=str(destination),
            )
        destination.parent.mkdir(parents=True, exist_ok=True)

        total = sum(_entry_size(i) for i in items_list)
        if total > self.limits.max_total_uncompressed_bytes:
            raise LimitExceededError(
                "Total payload exceeds the allowed size",
                detail=f"{total} > {self.limits.max_total_uncompressed_bytes}",
            )

        payload_tmp = destination.with_name(destination.name + ".payload.tmp")
        processed = 0
        entries: list[FileEntry] = []

        try:
            offset = 0
            with open(payload_tmp, "wb") as payload:
                for item in items_list:
                    if progress:
                        progress(f"Packaging {item.path}", processed, total)
                    raw_size, digest, written = self._write_item(payload, item)
                    if raw_size > self.limits.max_entry_uncompressed_bytes:
                        raise LimitExceededError(
                            "File exceeds the maximum single-entry size",
                            detail=item.path,
                        )
                    entries.append(
                        FileEntry(
                            path=item.path,
                            offset=offset,
                            length=written,
                            size=raw_size,
                            compression=self.compression,
                            hash_algo="sha256",
                            digest=digest,
                            executable=_is_executable(item),
                        )
                    )
                    offset += written
                    processed += raw_size

            if progress:
                progress("Finalizing container", processed, total)

            content_hash = compute_content_hash(entries)
            manifest.file_count = len(entries)
            manifest.total_uncompressed = total
            manifest.content_hash = content_hash
            manifest.content_hash_algo = "sha256"

            manifest_dict = manifest.to_dict()
            index_dict = Index(entries=entries).to_dict()

            manifest_block = self._encode_block(manifest_dict, self.limits.max_manifest_bytes)
            index_block = self._encode_block(index_dict, self.limits.max_index_bytes)

            manifest_offset = HEADER_SIZE
            index_offset = manifest_offset + len(manifest_block)
            payload_offset = index_offset + len(index_block)

            header = HEADER_STRUCT.pack(
                MAGIC,
                FORMAT_MAJOR,
                FORMAT_MINOR,
                FLAG_HAS_MANIFEST,
                HEADER_SIZE,
                self.compression_id,
                HASH_SHA256,
                0,
                len(entries),
                manifest_offset,
                len(manifest_block),
                _raw_len(manifest_dict),
                index_offset,
                len(index_block),
                _raw_len(index_dict),
                payload_offset,
                0,
                0,
            )
            if len(header) != HEADER_SIZE:
                raise ContainerFormatError("Header packing produced the wrong size")

            self._assemble(destination, header, manifest_block, index_block, payload_tmp)
            total_compressed = sum(e.length for e in entries)

            if progress:
                progress("Package written", total, total)

            log.info(
                "Wrote container %s entries=%d raw=%d compressed=%d",
                destination,
                len(entries),
                total,
                total_compressed,
            )
            return WriteReport(
                destination=destination,
                file_count=len(entries),
                total_uncompressed=total,
                total_compressed=total_compressed,
                content_hash=content_hash,
                entries=entries,
            )
        finally:
            if payload_tmp.exists():
                payload_tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _preflight(self, items: list[PayloadItem]) -> None:
        if not items:
            raise ContainerError("Cannot write a container with no files")
        if len(items) > self.limits.max_entries:
            raise LimitExceededError(
                "Too many files for one container",
                detail=f"{len(items)} > {self.limits.max_entries}",
            )

        seen: set[str] = set()
        for item in items:
            normalized = normalize_entry_path(
                item.path,
                max_length=self.limits.max_path_length,
                max_depth=self.limits.max_directory_depth,
            )
            if normalized in seen:
                raise ContainerFormatError("Duplicate path in payload", detail=normalized)
            if len(normalized.rsplit("/", 1)[-1]) > MAX_SEGMENT_LENGTH:
                raise LimitExceededError(
                    "File name component exceeds the maximum length", detail=normalized
                )
            item.path = normalized

            if item.source is not None:
                if not item.source.exists():
                    raise ContainerError(
                        "Payload source does not exist", detail=str(item.source)
                    )
                if item.source.is_dir():
                    raise ContainerError(
                        "Payload source is a directory", detail=str(item.source)
                    )
                size = item.source.stat().st_size
            else:
                assert item.data is not None
                size = len(item.data)

            if size > self.limits.max_entry_uncompressed_bytes:
                raise LimitExceededError(
                    "File exceeds the maximum single-entry size",
                    detail=f"{item.path}: {size}",
                )

    def _write_item(self, payload, item: PayloadItem) -> tuple[int, str, int]:
        """Append one item's compressed bytes and return (size, sha256, written)."""
        hasher = hashlib.sha256()
        encoder = new_stream_encoder(self.compression_id, self.zlib_level)
        written = 0
        raw_size = 0

        def emit(chunk: bytes) -> None:
            nonlocal written
            piece = encoder.feed(chunk)
            if piece:
                payload.write(piece)
                written += len(piece)

        if item.data is not None:
            hasher.update(item.data)
            raw_size = len(item.data)
            emit(item.data)
        else:
            assert item.source is not None
            with open(item.source, "rb") as src:
                while True:
                    chunk = src.read(READ_CHUNK)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    raw_size += len(chunk)
                    emit(chunk)

        tail = encoder.flush()
        if tail:
            payload.write(tail)
            written += len(tail)

        if self.compression_id == COMPRESSION_NONE and written != raw_size:
            raise ContainerFormatError("Stored entry length mismatch", detail=item.path)
        return raw_size, hasher.hexdigest(), written

    def _encode_block(self, obj: dict, max_bytes: int) -> bytes:
        raw = _raw_len_bytes(obj)
        if len(raw) > max_bytes:
            raise LimitExceededError(
                "Metadata block exceeds the size limit", detail=f"{len(raw)} > {max_bytes}"
            )
        return compress(raw, self.compression_id)

    @staticmethod
    def _assemble(
        destination: Path,
        header: bytes,
        manifest_block: bytes,
        index_block: bytes,
        payload_tmp: Path,
    ) -> None:
        final_tmp = destination.with_name(destination.name + ".part")
        try:
            with open(final_tmp, "wb") as out:
                out.write(header)
                out.write(manifest_block)
                out.write(index_block)
                with open(payload_tmp, "rb") as payload:
                    shutil.copyfileobj(payload, out, READ_CHUNK)
                out.flush()
                os.fsync(out.fileno())
            os.replace(final_tmp, destination)
        finally:
            if final_tmp.exists():
                final_tmp.unlink(missing_ok=True)


def _raw_len_bytes(obj: dict) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _raw_len(obj: dict) -> int:
    return len(_raw_len_bytes(obj))


def _entry_size(item: PayloadItem) -> int:
    if item.data is not None:
        return len(item.data)
    assert item.source is not None
    return item.source.stat().st_size


def _is_executable(item: PayloadItem) -> bool:
    if item.executable:
        return True
    if item.source is None:
        return False
    try:
        mode = item.source.stat().st_mode
        return bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    except OSError:
        return False
