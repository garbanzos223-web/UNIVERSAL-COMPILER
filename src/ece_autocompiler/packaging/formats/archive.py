"""Archive formats: ZIP and TAR.GZ, both reproducible.

Reproducible means: the same input files always produce a byte-identical
archive. Entries are sorted, timestamps are pinned, ownership is normalised
and the gzip header carries no time or file name.

The standard library is enough for both formats, so they work on any machine
without installing anything.
"""

from __future__ import annotations

import gzip
import io
import tarfile
import zipfile
from pathlib import Path

from ..manifest import FIXED_ZIP_TIME
from ..models import PackageRequest
from .base import FormatResult, PackageFormat


def _staged_files(staging: Path) -> list[Path]:
    staging = Path(staging)
    return sorted((p for p in staging.rglob("*") if p.is_file()), key=lambda p: p.as_posix())


def _rel(staging: Path, path: Path) -> str:
    return path.relative_to(staging).as_posix()


class ZipFormat(PackageFormat):
    """A plain ZIP archive, the format that works everywhere."""

    fmt_id = "zip"
    display_name = "ZIP archive"
    extension = ".zip"

    def build(
        self, staging: Path, request: PackageRequest, manifest: dict, target: Path
    ) -> FormatResult:
        try:
            with zipfile.ZipFile(
                target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive:
                for path in _staged_files(staging):
                    info = zipfile.ZipInfo(_rel(staging, path), date_time=FIXED_ZIP_TIME)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.create_system = 3  # Unix, so the archive is platform neutral
                    mode = 0o755 if _executable(path) else 0o644
                    info.external_attr = mode << 16
                    archive.writestr(info, path.read_bytes())
        except OSError as exc:
            return FormatResult(
                ok=False,
                message=f"The ZIP archive could not be written: {exc}",
                errors=[f"Check that {target.parent} is writable and there is free space."],
            )
        return FormatResult(ok=True, path=target, message=f"ZIP archive written to {target}")


class TarGzFormat(PackageFormat):
    """A gzipped TAR archive, the format that keeps UNIX permissions."""

    fmt_id = "tar.gz"
    display_name = "TAR.GZ archive"
    extension = ".tar.gz"

    def build(
        self, staging: Path, request: PackageRequest, manifest: dict, target: Path
    ) -> FormatResult:
        buffer = io.BytesIO()
        try:
            with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as archive:
                for path in _staged_files(staging):
                    info = archive.gettarinfo(str(path), arcname=_rel(staging, path))
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mode = 0o755 if _executable(path) else 0o644
                    with open(path, "rb") as handle:
                        archive.addfile(info, handle)
            data = buffer.getvalue()
            with open(target, "wb") as raw:
                # filename="" and mtime=0 keep the gzip header reproducible.
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
                    gz.write(data)
        except OSError as exc:
            return FormatResult(
                ok=False,
                message=f"The TAR.GZ archive could not be written: {exc}",
                errors=[f"Check that {target.parent} is writable and there is free space."],
            )
        return FormatResult(ok=True, path=target, message=f"TAR.GZ archive written to {target}")


def _executable(path: Path) -> bool:
    return path.suffix.lower() in (".exe", ".com", ".bat", ".cmd", ".sh", ".jar")
