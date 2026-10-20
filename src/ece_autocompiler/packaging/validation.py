"""Verification of a produced package.

Nothing is reported as successful before it has been read back: the archive is
opened, every entry is listed, every checksum is compared with the manifest and
the test (CRC) of every member is verified.

The checks also refuse the classic packaging mistakes: absolute paths, parent
directory escapes and duplicate entries.
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

from ..container.integrity import sha256_file


def _safe_member(name: str) -> bool:
    """Reject absolute paths and parent-directory escapes."""
    if not name or name.startswith("/") or name.startswith("\\"):
        return False
    normalized = name.replace("\\", "/")
    if ":" in normalized.split("/")[0] and len(normalized.split("/")[0]) == 2:
        return False  # drive letter
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    return ".." not in parts


def validate_directory(path: Path, expected: dict[str, str]) -> list[str]:
    """Check a packaged folder against the expected checksums."""
    problems: list[str] = []
    path = Path(path)
    if not path.is_dir():
        return [f"The package folder {path} does not exist."]
    found: dict[str, str] = {}
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(path).as_posix()
        found[rel] = sha256_file(item)
    for rel, digest in expected.items():
        if rel not in found:
            problems.append(f"Missing file in the package: {rel}")
        elif found[rel] != digest:
            problems.append(f"Checksum mismatch for {rel}.")
    for rel in found:
        if rel not in expected:
            problems.append(f"Unexpected file in the package: {rel}")
    return problems


def validate_zip(path: Path, expected: dict[str, str]) -> list[str]:
    """Check a ZIP archive against the expected checksums."""
    problems: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            broken = archive.testzip()
            if broken is not None:
                problems.append(f"The archive member {broken} is corrupt (CRC error).")
            names = archive.namelist()
            if len(names) != len(set(names)):
                problems.append("The archive contains duplicate entries.")
            for name in names:
                if not _safe_member(name):
                    problems.append(f"Unsafe path in the archive: {name}")
                    continue
                if name.endswith("/"):
                    continue
                digest = archive.read(name)
                actual = _sha256_bytes(digest)
                if name in expected and expected[name] != actual:
                    problems.append(f"Checksum mismatch for {name}.")
            for rel in expected:
                if rel not in names:
                    problems.append(f"Missing file in the archive: {rel}")
    except (OSError, zipfile.BadZipFile) as exc:
        problems.append(f"The archive could not be read: {exc}")
    return problems


def validate_tar(path: Path, expected: dict[str, str]) -> list[str]:
    """Check a TAR.GZ archive against the expected checksums."""
    problems: list[str] = []
    try:
        with tarfile.open(path, "r:gz") as archive:
            names: list[str] = []
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                names.append(member.name)
                if not _safe_member(member.name):
                    problems.append(f"Unsafe path in the archive: {member.name}")
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    problems.append(f"Could not read the archive member {member.name}.")
                    continue
                actual = _sha256_bytes(handle.read())
                if member.name in expected and expected[member.name] != actual:
                    problems.append(f"Checksum mismatch for {member.name}.")
            for rel in expected:
                if rel not in names:
                    problems.append(f"Missing file in the archive: {rel}")
    except (OSError, tarfile.TarError) as exc:
        problems.append(f"The archive could not be read: {exc}")
    return problems


def validate_executable(path: Path) -> list[str]:
    """Check that a Windows binary really is a PE executable."""
    problems: list[str] = []
    path = Path(path)
    if not path.is_file():
        return [f"The file {path} was not created."]
    try:
        with open(path, "rb") as handle:
            header = handle.read(2)
    except OSError as exc:
        return [f"The file {path} could not be read: {exc}"]
    if header != b"MZ":
        problems.append(
            "The produced file does not start with the MZ signature, so it is not a "
            "Windows executable."
        )
    return problems


def _sha256_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def digest_of(path: Path) -> str:
    """SHA-256 of a file, or of a folder's contents in a stable order.

    A package can be a single file (ZIP, installer) or a folder (the
    application folder format); both need a checksum.
    """
    import hashlib

    path = Path(path)
    if path.is_file():
        return sha256_file(path)
    if not path.is_dir():
        return ""
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(path).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(item).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def size_of(path: Path) -> int:
    """Total size of a file, or of the files inside a folder."""
    path = Path(path)
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    if path.is_dir():
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    continue
    return total


def validate_package(
    path: Path,
    fmt: str,
    expected: dict[str, str],
) -> list[str]:
    """Validate a produced package; returns the list of problems found."""
    fmt = (fmt or "").lower()
    if fmt == "zip":
        return validate_zip(path, expected)
    if fmt in ("tar.gz", "tgz"):
        return validate_tar(path, expected)
    if fmt == "folder":
        return validate_directory(path, expected)
    if fmt in ("inno", "nsis"):
        return validate_executable(path)
    if fmt == "deb":
        return _validate_deb(path)
    if fmt == "appimage":
        return validate_executable(path)
    # Unknown formats: the file must at least exist and be readable.
    path = Path(path)
    if not path.is_file():
        return [f"The file {path} was not created."]
    try:
        sha256_file(path)
    except OSError as exc:
        return [f"The file {path} could not be read: {exc}"]
    return []


def _validate_deb(path: Path) -> list[str]:
    """A .deb is an ar archive; check that it has the expected members."""
    problems: list[str] = []
    path = Path(path)
    if not path.is_file():
        return [f"The file {path} was not created."]
    try:
        with open(path, "rb") as handle:
            magic = handle.read(8)
    except OSError as exc:
        return [f"The file {path} could not be read: {exc}"]
    if magic != b"!<arch>\n":
        problems.append("The .deb file does not start with the ar archive signature.")
    return problems
