"""On-disk cache for the toolchain probes.

Probing every tool means starting one process per tool, which costs about a
second on a typical machine - and every command that touches a toolchain
paid that price. The result of a probe only changes when the tool changes, so
the results are cached on disk for a short while.

The cache is invalidated when ``PATH`` changes (installing a tool changes it)
and it can always be dropped with :func:`ece_autocompiler.detection.refresh`.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

#: Name of the cache file inside the configuration folder.
CACHE_FILENAME = "toolchains.json"

#: How long a cached probe stays valid (15 minutes).
TTL_SECONDS = 900


def path_signature() -> str:
    """A fingerprint of the search path.

    Installing or removing a tool changes it, which is exactly when the
    cached probe becomes wrong.
    """
    raw = os.environ.get("PATH", "") + "\0" + os.environ.get("PATHEXT", "")
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def cache_path(base: Path | None = None) -> Path:
    """Where the cache file lives."""
    if base is not None:
        return Path(base) / CACHE_FILENAME
    from ..local_store import config_dir

    return config_dir() / CACHE_FILENAME


def load(base: Path | None = None, *, ttl: float = TTL_SECONDS) -> dict[str, dict] | None:
    """The cached probe results, or None when there is no usable cache."""
    path = cache_path(base)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("signature") != path_signature():
        return None
    try:
        timestamp = float(data.get("timestamp") or 0.0)
    except (TypeError, ValueError):
        return None
    if ttl <= 0 or (time.time() - timestamp) > ttl:
        return None
    tools = data.get("tools")
    if not isinstance(tools, dict):
        return None
    return {str(key): value for key, value in tools.items() if isinstance(value, dict)}


def save(tools: dict[str, dict], base: Path | None = None) -> bool:
    """Store the probe results; returns whether they were written."""
    payload = {
        "timestamp": time.time(),
        "signature": path_signature(),
        "tools": tools,
    }
    try:
        from ..local_store import write_json_atomic

        write_json_atomic(cache_path(base), payload)
        return True
    except OSError:
        return False


def invalidate(base: Path | None = None) -> bool:
    """Drop the cached probe results; returns whether a file was removed."""
    path = cache_path(base)
    try:
        if path.is_file():
            path.unlink()
            return True
    except OSError:
        pass
    return False
