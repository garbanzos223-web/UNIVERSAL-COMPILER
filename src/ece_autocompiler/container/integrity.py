"""Integrity sidecars for produced files (item 39).

Every produced artifact can get a ``<name>.sha256`` sidecar in the standard
``<hex> *<name>`` format, so a recipient can confirm the bytes were not altered.
For ``.ece`` containers the sidecar complements the container's own hashes; a
container is also validated structurally and, optionally, entry-by-entry.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

SIDECAR_SUFFIX = ".sha256"
_CHUNK = 1024 * 1024

STATUS_OK = "ok"
STATUS_MISMATCH = "mismatch"
STATUS_MISSING = "no-sidecar"
STATUS_ERROR = "error"


def sha256_file(path, *, chunk_size: int = _CHUNK) -> str:
    """Streaming sha256 of a file (never loads it whole into memory)."""
    digest = hashlib.sha256()
    with open(Path(path), "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sidecar_path(path) -> Path:
    path = Path(path)
    return path.with_name(path.name + SIDECAR_SUFFIX)


def manifest_line(path, digest: str | None = None) -> str:
    """One ``sha256sum``-compatible line."""
    path = Path(path)
    digest = digest or sha256_file(path)
    return f"{digest} *{path.name}"


def write_sidecar(path, *, digest: str | None = None) -> Path:
    """Write ``<name>.sha256`` next to *path* and return the sidecar path."""
    path = Path(path)
    line = manifest_line(path, digest)
    target = sidecar_path(path)
    tmp = target.with_name(target.name + ".tmp")
    try:
        with open(tmp, "w", encoding="ascii", newline="\n") as handle:
            handle.write(line + "\n")
        import os

        os.replace(tmp, path.with_name(path.name + SIDECAR_SUFFIX))
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return sidecar_path(path)


def read_sidecar(path) -> str | None:
    """Return the expected hex digest from the sidecar, or None when absent."""
    sidecar = sidecar_path(path)
    if not sidecar.is_file():
        return None
    try:
        first = sidecar.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if not first:
        return None
    token = first[0].strip().split()[0] if first[0].strip() else ""
    return token.lower() or None


@dataclass
class IntegrityCheck:
    """Result of verifying one file against its sidecar (and structure)."""

    path: Path
    status: str
    expected: str = ""
    actual: str = ""
    message: str = ""
    kind: str = "file"  # "file" or "container"

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def lines(self) -> list[str]:
        if self.ok:
            return [f"{self.path.name}: OK ({self.kind}), sha256 {self.actual[:16]}..."]
        return [f"{self.path.name}: {self.status.upper()} - {self.message}"]

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "status": self.status,
            "ok": self.ok,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
        }


def verify_file(path, *, expected: str | None = None) -> IntegrityCheck:
    """Verify a file's sha256 against *expected* or its sidecar."""
    path = Path(path)
    if not path.is_file():
        return IntegrityCheck(path, STATUS_ERROR, message="file does not exist")
    try:
        actual = sha256_file(path)
    except OSError as exc:
        return IntegrityCheck(path, STATUS_ERROR, message=f"cannot read the file: {exc}")

    wanted = (expected or read_sidecar(path) or "").lower()
    if not wanted:
        return IntegrityCheck(
            path, STATUS_MISSING, actual=actual,
            message="no .sha256 sidecar was found, so there is nothing to compare against",
        )
    if wanted == actual:
        return IntegrityCheck(path, STATUS_OK, expected=wanted, actual=actual)
    return IntegrityCheck(
        path, STATUS_MISMATCH, expected=wanted, actual=actual,
        message="the file's hash does not match its sidecar: it was changed or damaged",
    )


def verify_container(path, *, deep: bool = True) -> IntegrityCheck:
    """Structural + optional deep validation, plus the `.sha256` sidecar."""
    from .validator import validate_container

    path = Path(path)
    if not path.is_file():
        return IntegrityCheck(path, STATUS_ERROR, kind="container", message="file does not exist")

    report = validate_container(path, deep=deep)
    if not report.ok:
        first = report.errors[0] if report.errors else None
        return IntegrityCheck(
            path, STATUS_ERROR, kind="container",
            message=(first.message if first else "the container is not valid"),
        )
    if deep and report.failed_entries:
        return IntegrityCheck(
            path, STATUS_MISMATCH, kind="container",
            message=f"{len(report.failed_entries)} entr(y/ies) failed their hash check",
        )

    actual = sha256_file(path)
    wanted = read_sidecar(path)
    if not wanted:
        return IntegrityCheck(
            path, STATUS_OK, actual=actual, kind="container",
            message="the container is valid; no sidecar was found to compare",
        )
    if wanted == actual:
        return IntegrityCheck(path, STATUS_OK, expected=wanted, actual=actual, kind="container")
    return IntegrityCheck(
        path, STATUS_MISMATCH, expected=wanted, actual=actual, kind="container",
        message="the container is valid but its .sha256 sidecar does not match: it changed after packaging",
    )


def write_sidecars(paths) -> list[Path]:
    """Write a sidecar for each existing path; return the ones written."""
    written: list[Path] = []
    for path in paths:
        path = Path(path)
        if path.is_file():
            written.append(write_sidecar(path))
    return written
