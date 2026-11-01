"""Safe archive inspection and extraction shared by the backends.

Every entry is checked before a byte is written: absolute paths, ``..``
components, device files, symlinks and size limits are rejected or skipped.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

MAX_ENTRIES = 20000
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_ENTRY_BYTES = 512 * 1024 * 1024


@dataclass
class ArchiveListing:
    kind: str
    names: list[str] = field(default_factory=list)
    total_uncompressed: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class ArchiveExtractReport:
    files_written: int = 0
    bytes_written: int = 0
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    written: list[Path] = field(default_factory=list)


def _unsafe(name: str) -> bool:
    path = Path(name.replace("\\", "/"))
    return path.is_absolute() or ".." in path.parts or (len(path.parts) > 0 and path.parts[0] == "")


def inspect_archive(path: Path) -> ArchiveListing:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".zip", ".jar", ".war", ".whl") or zipfile.is_zipfile(path):
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()[:MAX_ENTRIES]
                total = sum(info.file_size for info in archive.infolist()[:MAX_ENTRIES])
            return ArchiveListing("zip", names, total)
        except (zipfile.BadZipFile, OSError) as exc:
            return ArchiveListing("zip", warnings=[str(exc)])
    if suffix in (".tar", ".tgz") or tarfile.is_tarfile(path):
        try:
            with tarfile.open(path, "r:*") as archive:
                members = archive.getmembers()[:MAX_ENTRIES]
                return ArchiveListing(
                    "tar", [m.name for m in members], sum(m.size for m in members if m.isfile())
                )
        except (tarfile.TarError, OSError) as exc:
            return ArchiveListing("tar", warnings=[str(exc)])
    if suffix == ".gz" or path.name.endswith(".tar.gz"):
        return ArchiveListing("gzip", [path.stem])
    return ArchiveListing("unknown", warnings=["This archive format has no bundled extractor."])


def extract_archive(path: Path, destination: Path) -> ArchiveExtractReport:
    path = Path(path)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    report = ArchiveExtractReport()
    suffix = path.suffix.lower()
    if suffix in (".zip", ".jar", ".war", ".whl") or zipfile.is_zipfile(path):
        return _extract_zip(path, destination, report)
    if suffix in (".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz") or tarfile.is_tarfile(path):
        return _extract_tar(path, destination, report)
    if suffix == ".gz":
        return _extract_single(path, destination, report, gzip.open)
    if suffix == ".bz2":
        return _extract_single(path, destination, report, bz2.open)
    if suffix == ".xz":
        return _extract_single(path, destination, report, lzma.open)
    report.warnings.append("This archive format has no bundled extractor.")
    return report


def _extract_zip(path: Path, destination: Path, report: ArchiveExtractReport) -> ArchiveExtractReport:
    total = 0
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist()[:MAX_ENTRIES]:
                name = info.filename
                if info.is_dir():
                    continue
                if _unsafe(name):
                    report.skipped.append(f"{name} (unsafe path)")
                    continue
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    report.skipped.append(f"{name} (symlink)")
                    continue
                if info.file_size > MAX_ENTRY_BYTES or total + info.file_size > MAX_TOTAL_BYTES:
                    report.skipped.append(f"{name} (size limit)")
                    continue
                target = destination / Path(name.replace("\\", "/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "wb") as sink:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        sink.write(chunk)
                        report.bytes_written += len(chunk)
                total += info.file_size
                report.files_written += 1
                report.written.append(target)
    except (zipfile.BadZipFile, OSError) as exc:
        report.warnings.append(str(exc))
    return report


def _extract_tar(path: Path, destination: Path, report: ArchiveExtractReport) -> ArchiveExtractReport:
    total = 0
    try:
        with tarfile.open(path, "r:*") as archive:
            for member in archive.getmembers()[:MAX_ENTRIES]:
                if not member.isfile():
                    if member.issym() or member.islnk():
                        report.skipped.append(f"{member.name} (link)")
                    continue
                if _unsafe(member.name):
                    report.skipped.append(f"{member.name} (unsafe path)")
                    continue
                if member.size > MAX_ENTRY_BYTES or total + member.size > MAX_TOTAL_BYTES:
                    report.skipped.append(f"{member.name} (size limit)")
                    continue
                target = destination / Path(member.name.replace("\\", "/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    continue
                with source, open(target, "wb") as sink:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        sink.write(chunk)
                        report.bytes_written += len(chunk)
                total += member.size
                report.files_written += 1
                report.written.append(target)
    except (tarfile.TarError, OSError) as exc:
        report.warnings.append(str(exc))
    return report


def _extract_single(path: Path, destination: Path, report: ArchiveExtractReport, opener) -> ArchiveExtractReport:
    name = path.stem or "extracted.bin"
    target = destination / name
    try:
        with opener(path, "rb") as source, open(target, "wb") as sink:
            total = 0
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_ENTRY_BYTES:
                    report.warnings.append("size limit reached")
                    break
                sink.write(chunk)
        report.files_written = 1
        report.bytes_written = target.stat().st_size
        report.written.append(target)
    except (OSError, EOFError, lzma.LZMAError) as exc:
        report.warnings.append(str(exc))
    return report
