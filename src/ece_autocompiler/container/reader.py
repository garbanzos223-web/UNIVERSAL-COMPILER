"""Container reader: parse, validate and stream the contents of an .ece file.

Reading a container is deliberately paranoid. ``open()`` performs structural
validation of the header, the manifest block, the index block and every entry
offset before any payload byte is touched, so a malformed file fails during
``open()`` rather than half way through an extraction.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..config import Limits
from ..errors import (
    ContainerError,
    ContainerFormatError,
    IntegrityError,
    LimitExceededError,
    UnsupportedVersionError,
)
from ..logging_config import get_logger
from .codec import decompress, decompress_stream
from .model import FileEntry, Index, Manifest
from .spec import (
    COMPRESSION_NAMES,
    FORMAT_MAJOR,
    FORMAT_MINOR,
    HASH_NAMES,
    HEADER_SIZE,
    HEADER_STRUCT,
    MAGIC,
    NO_VALUE,
)

log = get_logger("container.reader")


@dataclass
class Header:
    major: int
    minor: int
    flags: int
    header_size: int
    compression_id: int
    hash_algo_id: int
    entry_count: int
    manifest_offset: int
    manifest_length: int
    manifest_raw_length: int
    index_offset: int
    index_length: int
    index_raw_length: int
    payload_offset: int

    @property
    def compression_name(self) -> str:
        return COMPRESSION_NAMES.get(self.compression_id, f"unknown({self.compression_id})")

    @property
    def hash_name(self) -> str:
        return HASH_NAMES.get(self.hash_algo_id, f"unknown({self.hash_algo_id})")


class ContainerReader:
    """Reads and validates an .ece container."""

    def __init__(self, path: Path | str, *, limits: Limits | None = None) -> None:
        self.path = Path(path)
        self.limits = limits or Limits()
        self._handle = None
        self._size = 0
        self.header: Header | None = None
        self.manifest: Manifest | None = None
        self.index: Index | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def open(self) -> "ContainerReader":
        if self._handle is not None:
            return self

        if not self.path.exists():
            raise ContainerError("Container file does not exist", detail=str(self.path))
        if self.path.is_dir():
            raise ContainerError("Container path is a directory", detail=str(self.path))

        self._size = os.path.getsize(self.path)
        if self._size < HEADER_SIZE:
            raise ContainerFormatError(
                "File is too small to be an .ece container", detail=f"{self._size} bytes"
            )

        self._handle = open(self.path, "rb")
        try:
            self._parse()
        except BaseException:
            self.close()
            raise
        return self

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            finally:
                self._handle = None

    def __enter__(self) -> "ContainerReader":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # parsing
    # ------------------------------------------------------------------

    def _read_at(self, offset: int, length: int) -> bytes:
        assert self._handle is not None
        if offset < 0 or length < 0 or offset + length > self._size:
            raise ContainerFormatError(
                "Read outside the end of the file",
                detail=f"offset={offset} length={length} size={self._size}",
            )
        self._handle.seek(offset)
        data = self._handle.read(length)
        if len(data) != length:
            raise ContainerFormatError("Short read while parsing the container")
        return data

    def _parse(self) -> None:
        raw_header = self._read_at(0, HEADER_SIZE)
        fields = HEADER_STRUCT.unpack(raw_header)

        if fields[0] != MAGIC:
            raise ContainerFormatError(
                "Not an .ece container (bad magic bytes)",
                detail=fields[0][:8].hex(),
            )

        header = Header(
            major=fields[1],
            minor=fields[2],
            flags=fields[3],
            header_size=fields[4],
            compression_id=fields[5],
            hash_algo_id=fields[6],
            entry_count=fields[8],
            manifest_offset=fields[9],
            manifest_length=fields[10],
            manifest_raw_length=fields[11],
            index_offset=fields[12],
            index_length=fields[13],
            index_raw_length=fields[14],
            payload_offset=fields[15],
        )
        self.header = header

        # --- version gate: reject unknown majors, tolerate newer minors ---
        if header.major > FORMAT_MAJOR:
            raise UnsupportedVersionError(
                "Container format version is newer than this build understands",
                detail=f"{header.major}.{header.minor} > {FORMAT_MAJOR}.x",
            )
        if header.major < 1:
            raise ContainerFormatError("Invalid major version", detail=str(header.major))
        if header.minor > FORMAT_MINOR:
            log.debug(
                "Container minor version %s is newer than %s; reading known fields only",
                header.minor,
                FORMAT_MINOR,
            )

        if header.header_size < HEADER_SIZE:
            raise ContainerFormatError(
                "Header size is smaller than the minimum for this format version",
                detail=str(header.header_size),
            )
        if header.header_size > self._size:
            raise ContainerFormatError("Header size exceeds the file size")

        if header.compression_id not in COMPRESSION_NAMES:
            raise ContainerFormatError(
                "Unknown compression identifier", detail=str(header.compression_id)
            )
        if header.hash_algo_id not in HASH_NAMES:
            raise ContainerFormatError("Unknown hash algorithm", detail=str(header.hash_algo_id))

        self._validate_layout(header)

        manifest_raw = self._load_block(
            header.manifest_offset,
            header.manifest_length,
            header.manifest_raw_length,
            self.limits.max_manifest_bytes,
            "manifest",
        )
        try:
            manifest_obj = json.loads(manifest_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContainerFormatError("Manifest is not valid UTF-8 JSON", detail=str(exc)) from exc
        self.manifest = Manifest.from_dict(manifest_obj)

        index_raw = self._load_block(
            header.index_offset,
            header.index_length,
            header.index_raw_length,
            self.limits.max_index_bytes,
            "index",
        )
        try:
            index_obj = json.loads(index_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContainerFormatError("Index is not valid UTF-8 JSON", detail=str(exc)) from exc
        self.index = Index.from_dict(index_obj)

        if len(self.index.entries) != header.entry_count:
            raise ContainerFormatError(
                "Header entry count does not match the index",
                detail=f"{header.entry_count} != {len(self.index.entries)}",
            )
        if len(self.index.entries) > self.limits.max_entries:
            raise LimitExceededError(
                "Container declares too many entries",
                detail=str(len(self.index.entries)),
            )

        self._validate_entries(header)

        log.debug(
            "Opened %s version=%s.%s entries=%d",
            self.path,
            header.major,
            header.minor,
            len(self.index.entries),
        )

    def _validate_layout(self, header: Header) -> None:
        blocks = (
            ("manifest", header.manifest_offset, header.manifest_length),
            ("index", header.index_offset, header.index_length),
        )
        for name, offset, length in blocks:
            if offset < header.header_size:
                raise ContainerFormatError(f"{name} block starts inside the header")
            if offset + length > self._size:
                raise ContainerFormatError(
                    f"{name} block extends past the end of the file",
                    detail=f"offset={offset} length={length} size={self._size}",
                )

        if header.payload_offset < header.index_offset + header.index_length:
            raise ContainerFormatError("Payload starts before the end of the index block")
        if header.payload_offset > self._size:
            raise ContainerFormatError("Payload offset is past the end of the file")

        if header.manifest_length > self.limits.max_manifest_bytes:
            raise LimitExceededError("Manifest block exceeds the size limit")
        if header.index_length > self.limits.max_index_bytes:
            raise LimitExceededError("Index block exceeds the size limit")

    def _load_block(self, offset: int, length: int, raw_length: int, limit: int, name: str) -> bytes:
        data = self._read_at(offset, length)
        raw = decompress(data, self.header.compression_id, max_output=limit)
        if raw_length and len(raw) != raw_length:
            raise ContainerFormatError(
                f"Decoded {name} length does not match the header",
                detail=f"{len(raw)} != {raw_length}",
            )
        if len(raw) > limit:
            raise LimitExceededError(f"{name} block exceeds the size limit")
        return bytes(raw)

    def _validate_entries(self, header: Header) -> None:
        assert self.index is not None
        seen: set[str] = set()
        total_uncompressed = 0
        payload_bytes = 0

        for entry in self.index.entries:
            if entry.path in seen:
                raise ContainerFormatError("Duplicate path in index", detail=entry.path)
            seen.add(entry.path)

            if entry.hash_algo != "none" and entry.digest:
                if len(entry.digest) != 64:
                    raise ContainerFormatError(
                        "Malformed entry hash", detail=f"{entry.path}: {entry.digest!r}"
                    )
                int(entry.digest, 16)  # raises ValueError on non-hex

            if header.payload_offset + entry.offset + entry.length > self._size:
                raise ContainerFormatError(
                    "Entry payload extends past the end of the file", detail=entry.path
                )
            if entry.offset + entry.length > max(0, self._size - header.payload_offset):
                raise ContainerFormatError("Entry offset out of range", detail=entry.path)

            total_uncompressed += entry.size
            payload_bytes += entry.length

        if total_uncompressed > self.limits.max_total_uncompressed_bytes:
            raise LimitExceededError("Container payload exceeds the allowed total size")

        payload_region = self._size - header.payload_offset
        if payload_bytes > payload_region:
            raise ContainerFormatError(
                "Sum of entry lengths exceeds the payload region",
                detail=f"{payload_bytes} > {payload_region}",
            )

    # ------------------------------------------------------------------
    # access
    # ------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._handle is not None

    @property
    def file_size(self) -> int:
        return self._size

    def entries(self) -> list[FileEntry]:
        if self.index is None:
            raise ContainerError("Container is not open")
        return list(self.index.entries)

    def get_entry(self, path: str) -> FileEntry | None:
        if self.index is None:
            raise ContainerError("Container is not open")
        return self.index.by_path().get(path)

    def iter_entry_chunks(self, entry: FileEntry) -> Iterator[bytes]:
        """Yield an entry's uncompressed bytes one chunk at a time."""
        if self.header is None or self._handle is None:
            raise ContainerError("Container is not open")
        base = self.header.payload_offset + entry.offset

        def chunks() -> Iterator[bytes]:
            remaining = entry.length
            pos = base
            while remaining > 0:
                step = min(remaining, 1024 * 1024)
                block = self._read_at(pos, step)
                yield block
                pos += step
                remaining -= step

        yield from decompress_stream(
            _id_of(entry.compression),
            chunks(),
            entry.size,
        )

    def read_entry(self, entry: FileEntry, *, verify_hash: bool = True) -> bytes:
        """Return one entry's full contents in memory."""
        digest = hashlib.sha256()
        parts: list[bytes] = []
        for chunk in self.iter_entry_chunks(entry):
            digest.update(chunk)
            parts.append(chunk)
        data = b"".join(parts)

        if verify_hash and entry.digest:
            actual = digest.hexdigest()
            if actual != entry.digest:
                raise IntegrityError(
                    "Entry hash mismatch (the file has been altered or corrupted)",
                    detail=f"{entry.path}: expected {entry.digest}, got {actual}",
                )
        return data

    def verify_all(self) -> list[str]:
        """Hash every entry and return a list of paths that failed verification."""
        failures: list[str] = []
        for entry in self.entries():
            digest = hashlib.sha256()
            try:
                for chunk in self.iter_entry_chunks(entry):
                    digest.update(chunk)
            except (ContainerError, OSError):
                failures.append(entry.path)
                continue
            if entry.digest and digest.hexdigest() != entry.digest:
                failures.append(entry.path)
        return failures


def _id_of(compression_name: str) -> int:
    from .spec import COMPRESSION_BY_NAME

    if compression_name not in COMPRESSION_BY_NAME:
        raise ContainerFormatError("Unknown compression", detail=compression_name)
    return COMPRESSION_BY_NAME[compression_name]
