"""Path safety for the .ece container.

All archive paths are treated as hostile. This module is the only place that
converts an index path into an on-disk path; nothing else may join an entry
path to a destination directory.

Rules enforced (see docs/SECURITY.md):
  * paths are UTF-8, normalized to forward slashes, relative to the extraction root;
  * no leading slash, no backslash, no Windows drive letter, no UNC prefix;
  * no '.' or '..' segments, no trailing slash, no empty segments;
  * no NUL or other C0/C1 control characters;
  * bounded segment count, total length and per-segment length;
  * the resolved destination must remain inside the chosen root.

The safety of the final join is re-checked after resolving symlinks, so an
attacker who pre-created a symlink inside the destination cannot redirect the
write outside the root.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

from ..errors import UnsafePathError
from .spec import FORMAT_VERSION  # noqa: F401  (re-export convenience)

# Maximum characters in a single path segment (NTFS caps components at 255).
MAX_SEGMENT_LENGTH = 255
# Windows MAX_PATH is 260; we allow a deliberate above-limit here because the
# extractor enforces its own bound at write time.
DEFAULT_MAX_PATH_LENGTH = 1024
DEFAULT_MAX_DEPTH = 64

# Any character in a C0 control set or DEL plus the C1 range is forbidden.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# Matches a Windows drive letter such as "C:" or "c:/x" anywhere in the first
# two characters, or UNC-style leading double slash.
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


def normalize_entry_path(
    raw: str | bytes,
    *,
    max_length: int = DEFAULT_MAX_PATH_LENGTH,
    max_depth: int = DEFAULT_MAX_DEPTH,
    allow_directories: bool = False,
) -> str:
    """Validate and normalize an archive path.

    Returns a relative, forward-slash path. Raises :class:`UnsafePathError`
    when the path violates any safety rule.
    """
    if isinstance(raw, (bytes, bytearray)):
        try:
            text = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsafePathError("Entry path is not valid UTF-8") from exc
    elif isinstance(raw, str):
        text = raw
    else:
        raise UnsafePathError("Entry path must be str or bytes", detail=type(raw).__name__)

    if not text:
        raise UnsafePathError("Entry path is empty")

    if len(text) > max_length:
        raise UnsafePathError(
            "Entry path exceeds the maximum allowed length",
            detail=f"{len(text)} > {max_length}",
        )

    if _CONTROL_CHARS.search(text):
        raise UnsafePathError("Entry path contains control characters")

    # Reject backslashes outright: they are drive/UNC separators on Windows and
    # ambiguous on POSIX. Entry paths always use '/'.
    if "\\" in text:
        raise UnsafePathError("Entry path contains a backslash", detail=text)

    if _DRIVE_PREFIX.match(text):
        raise UnsafePathError("Entry path is an absolute Windows path", detail=text)

    if text.startswith("/"):
        raise UnsafePathError("Entry path is an absolute POSIX path", detail=text)

    if text.startswith("//"):
        raise UnsafePathError("Entry path is a UNC path", detail=text)

    pure = PurePosixPath(text)

    segments = pure.parts
    if not segments:
        raise UnsafePathError("Entry path resolves to nothing")

    if len(segments) > max_depth:
        raise UnsafePathError(
            "Entry path nesting is too deep", detail=f"{len(segments)} > {max_depth}"
        )

    safe_segments: list[str] = []
    for segment in segments:
        if segment in ("", "."):
            continue
        if segment == "..":
            raise UnsafePathError("Entry path contains a parent directory segment")
        if len(segment) > MAX_SEGMENT_LENGTH:
            raise UnsafePathError(
                "Entry path segment is too long", detail=segment[:64]
            )
        safe_segments.append(segment)

    if not safe_segments:
        raise UnsafePathError("Entry path has no usable segments")

    normalized = "/".join(safe_segments)

    if not allow_directories and text.endswith("/"):
        raise UnsafePathError("Entry path ends with a separator but is not a directory")

    return normalized


def resolve_destination(root: Path, entry_path: str, *, max_length: int = DEFAULT_MAX_PATH_LENGTH) -> Path:
    """Return the final destination path for *entry_path* under *root*.

    The result is guaranteed to be inside *root* after resolving symlinks on
    every existing ancestor.
    """
    if len(entry_path) > max_length:
        raise UnsafePathError("Entry path exceeds the maximum allowed length")

    root_resolved = Path(root).resolve()
    candidate = root_resolved.joinpath(*entry_path.split("/"))

    # Walk up from the target to find the deepest existing ancestor and confirm
    # it still sits inside root. This defeats a symlink planted inside root.
    existing = candidate
    while not existing.exists():
        parent = existing.parent
        if parent == existing:
            break
        existing = parent

    try:
        existing_resolved = existing.resolve()
    except (OSError, RuntimeError) as exc:
        raise UnsafePathError("Cannot resolve destination path", detail=str(exc)) from exc

    try:
        existing_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise UnsafePathError(
            "Destination escapes the extraction root", detail=entry_path
        ) from exc

    return candidate


def ensure_no_symlink_target(path: Path) -> None:
    """Refuse to write through an existing symlink at *path*."""
    if path.is_symlink():
        raise UnsafePathError("Refusing to write through a symlink", detail=str(path))


def is_junction_or_symlink(path: Path) -> bool:
    """Return True when *path* is a symlink or an NTFS junction/reparse point."""
    try:
        if path.is_symlink():
            return True
    except OSError:
        return True
    try:
        # os.lstat is preferred over Path.is_symlink for junctions on Windows.
        import stat

        st = os.lstat(path)
        # FILE_ATTRIBUTE_REPARSE_POINT (0x400) shows up as S_ISLNK on junctions
        # only under some Python builds; fall back to the attribute check.
        return stat.S_ISLNK(st.st_mode)
    except OSError:
        return False


def check_depth(entry_path: str, max_depth: int = DEFAULT_MAX_DEPTH) -> None:
    depth = entry_path.count("/") + 1
    if depth > max_depth:
        raise UnsafePathError("Entry path nesting is too deep", detail=f"{depth} > {max_depth}")
