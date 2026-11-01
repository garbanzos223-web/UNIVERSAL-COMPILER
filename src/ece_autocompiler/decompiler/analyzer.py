"""Safe, read-only analysis of one imported file.

The analyzer never runs the imported program. It reads headers, metadata and
containers with bounded readers, records a SHA-256 digest for provenance, and
reports everything it could not do instead of guessing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from ..logging_config import get_logger
from . import archive, dotnet_metadata, elf, formats, java_class, pe, pyinstaller, python_bytecode
from .models import FileAnalysis

log = get_logger("decompiler.analyze")

MAX_HASH_BYTES = 1024 * 1024 * 1024


def sha256_file(path: Path) -> str:
    path = Path(path)
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _text_details(path: Path) -> dict[str, Any]:
    details: dict[str, Any] = {}
    try:
        head = path.read_bytes()[:8192]
    except OSError:
        return details
    encoding = "utf-8"
    text = ""
    for candidate in ("utf-8", "utf-16", "latin-1"):
        try:
            text = head.decode(candidate)
            encoding = candidate
            break
        except UnicodeDecodeError:
            continue
    details["encoding"] = encoding
    details["line_count_prefix"] = text.count("\n") + (1 if text else 0)
    details["first_lines"] = [line for line in text.splitlines()[:8]]
    return details


def analyze_file(path: Path, *, compute_hash: bool = True) -> FileAnalysis:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"not a file: {path}")
    size = path.stat().st_size
    fmt = formats.detect_format(path)
    details: dict[str, Any] = {}
    notes: list[str] = []
    warnings: list[str] = []

    try:
        if fmt.kind in ("pe-native", "pe-dotnet"):
            info = pe.parse_pe(path)
            details = info.to_dict()
            warnings.extend(info.warnings)
            if fmt.kind == "pe-dotnet":
                meta = dotnet_metadata.parse_metadata(path, info)
                details["dotnet"] = meta.to_dict()
                warnings.extend(meta.warnings)
        elif fmt.kind == "elf":
            info = elf.parse_elf(path)
            details = info.to_dict()
            warnings.extend(info.warnings)
            details["strings"] = elf.extract_strings(path.read_bytes()[: 16 * 1024 * 1024])[:500]
        elif fmt.kind == "java-class":
            model = java_class.parse_class(path)
            details = model.to_dict()
        elif fmt.kind == "python-bytecode":
            module = python_bytecode.read_module(path)
            details = python_bytecode.summary(module)
            warnings.extend(module.warnings)
        elif fmt.kind == "python-packaged":
            info = pyinstaller.read_archive(path)
            details = {
                "archive_start": info.start,
                "archive_length": info.length,
                "python_version": info.python_version,
                "entry_count": len(info.entries),
                "entries": [
                    {
                        "name": e.name,
                        "type": e.typecode,
                        "size": e.uncompressed_length,
                        "compressed": e.compressed,
                    }
                    for e in info.entries[:300]
                ],
            }
            warnings.extend(info.warnings)
        elif fmt.kind in (
            "zip", "jar", "java-archive", "python-wheel", "zip-python",
            "tar", "gzip", "bzip2", "xz",
        ):
            listing = archive.inspect_archive(path)
            details = {
                "archive_kind": listing.kind,
                "entry_count": len(listing.names),
                "total_uncompressed": listing.total_uncompressed,
                "entries": listing.names[:500],
            }
            warnings.extend(listing.warnings)
        elif fmt.kind == "ece":
            from ..container.reader import ContainerReader

            with ContainerReader(path) as reader:
                manifest = reader.manifest
                details = {
                    "format_version": f"{reader.header.major}.{reader.header.minor}" if reader.header else "",
                    "entry_count": len(reader.entries()),
                    "compression": reader.header.compression_name if reader.header else "",
                    "manifest": manifest.to_dict() if manifest else {},
                    "entries": [
                        {"path": entry.path, "size": entry.size, "executable": entry.executable}
                        for entry in reader.entries()[:500]
                    ],
                }
        elif fmt.kind.startswith("source-") or fmt.kind in ("text", "markdown"):
            details = _text_details(path)
        else:
            notes.append("No format-specific reader ran; only file metadata is reported.")
    except Exception as exc:  # analysis must never crash the UI
        warnings.append(f"Analysis of this file was incomplete: {exc}")
        log.debug("analysis failed for %s: %s", path, exc)

    digest = ""
    if compute_hash and size <= MAX_HASH_BYTES:
        digest = sha256_file(path)
    elif compute_hash:
        warnings.append("File is larger than 1 GiB: SHA-256 was not computed.")

    for note in fmt.notes:
        if note:
            notes.append(note)

    return FileAnalysis(
        path=path,
        size=size,
        sha256=digest,
        format=fmt,
        details=details,
        notes=notes,
        warnings=warnings,
    )


def analyze_many(paths: Iterable[Path], *, progress=None) -> list[FileAnalysis]:
    items = [Path(p) for p in paths]
    results: list[FileAnalysis] = []
    for index, path in enumerate(items, start=1):
        if progress:
            progress(f"Analyzing {path.name}", index - 1, len(items))
        try:
            results.append(analyze_file(path))
        except Exception as exc:
            fmt = formats.FormatInfo(
                kind="unreadable",
                label="Unreadable file",
                confidence=0.0,
                operations=("analyze",),
                evidence=(str(exc),),
            )
            results.append(
                FileAnalysis(path=path, size=0, sha256="", format=fmt, warnings=[str(exc)])
            )
    return results
