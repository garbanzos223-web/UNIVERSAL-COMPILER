"""Small, reusable security helpers used by the CLI and the GUI.

Path safety for container *entries* lives in
:mod:`ece_autocompiler.container.security`; the helpers here are for
user-supplied file names and output locations.
"""

from __future__ import annotations

import re
from pathlib import Path

# Characters that are never valid in a Windows file name.
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Device names reserved by Windows, with or without an extension.
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *{f"com{i}" for i in range(1, 10)},
    *{f"lpt{i}" for i in range(1, 10)},
}


def sanitize_filename(name: str, *, default: str = "output") -> str:
    """Return a safe single-file name derived from *name*.

    Components that are absolute, relative or reserved are reduced to a plain
    name; anything that would produce an empty result falls back to *default*.
    """
    candidate = (name or "").strip().replace("\\", "/").split("/")[-1]
    candidate = _INVALID_FILENAME_CHARS.sub("_", candidate).strip(" .")
    if not candidate:
        return default
    stem = candidate.split(".")[0].lower()
    if stem in _RESERVED_NAMES:
        candidate = "_" + candidate
    return candidate


def ensure_within(root: Path, candidate: Path) -> bool:
    """Return True when *candidate* resolves to a path inside *root*."""
    try:
        root_resolved = Path(root).resolve()
        candidate_resolved = Path(candidate).resolve()
    except (OSError, RuntimeError):
        return False
    try:
        candidate_resolved.relative_to(root_resolved)
        return True
    except ValueError:
        return False


def is_relative_plain(rel: str) -> bool:
    """Return True for a simple relative path with no traversal or absoluteness."""
    if not rel or rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel):
        return False
    parts = re.split(r"[\\/]", rel)
    return all(p not in ("", ".", "..") for p in parts)
