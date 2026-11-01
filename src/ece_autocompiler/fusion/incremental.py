"""Dependency-aware incremental builds (§12).

A module's cache key is the SHA-256 of a JSON blob containing:

- the content hash of every source file of the module,
- the module's build configuration and adapter id,
- the cache keys of its dependencies (so a rebuilt library invalidates its
  consumers), and
- the engine toolchain version string where relevant.

Keys are stored in ``<workspace>/.ece-cache/modules.json``. A module is a
cache hit only when the stored key matches AND every recorded output file
still exists with the SHA-256 recorded for it. Correctness beats speed: when
a key cannot be computed honestly the module is rebuilt.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CACHE_DIRNAME = ".ece-cache"
CACHE_FILENAME = "modules.json"


@dataclass
class CacheState:
    workspace_root: Path
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self.workspace_root / CACHE_DIRNAME / CACHE_FILENAME

    @classmethod
    def load(cls, workspace_root: Path) -> "CacheState":
        state = cls(workspace_root=workspace_root)
        if state.path.exists():
            try:
                raw = json.loads(state.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    state.entries = {
                        str(k): dict(v) for k, v in raw.get("modules", {}).items()
                    }
            except (json.JSONDecodeError, OSError):
                state.entries = {}
        return state

    def save(self) -> None:
        from ..local_store import write_json_atomic

        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(
            self.path,
            {"format": "ece-fusion-cache", "modules": self.entries},
        )

    # ------------------------------------------------------------------
    def record(
        self,
        module_id: str,
        key: str,
        outputs: list[str],
        digests: dict[str, str] | None = None,
    ) -> None:
        entry: dict[str, Any] = {"key": key, "outputs": list(outputs)}
        if digests:
            entry["digests"] = dict(digests)
        self.entries[module_id] = entry

    def lookup(self, module_id: str) -> dict[str, Any] | None:
        return self.entries.get(module_id)

    def clear(self) -> None:
        self.entries = {}


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    """Coerce a settings value into something the key payload can serialize."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def compute_key(
    module_id: str,
    source_files: list[Path],
    *,
    adapter_id: str,
    configuration: str,
    target_arch: str,
    dependency_keys: dict[str, str],
    settings: dict[str, Any] | None = None,
) -> str:
    payload = {
        "module": module_id,
        "adapter": adapter_id,
        "configuration": configuration,
        "architecture": target_arch,
        "files": ["", ""],  # replaced below with real hashes
        "dependencies": dict(sorted(dependency_keys.items())),
        # Toolchain identity and build settings: changing the compiler or an
        # optimization profile must invalidate the cache even when no source
        # file changed.
        "settings": _json_safe(settings or {}),
    }

    if not source_files:
        payload["files"] = ["__only_config__"]
    else:
        hashed: list[list[str]] = []
        for path in sorted(source_files):
            try:
                hashed.append([path.as_posix(), file_hash(path)])
            except OSError:
                # A file we cannot hash must not yield a false cache hit.
                hashed.append([path.as_posix(), "__unreadable__"])
        payload["files"] = hashed

    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def find_cache_hit(
    module_id: str,
    state: CacheState,
    key: str,
) -> bool:
    """A hit requires the key match, the outputs on disk and their content.

    Recorded digests are checked so a tampered, truncated or half-deleted
    artifact cannot be shipped as if the build were still valid.
    """
    entry = state.lookup(module_id)
    if not entry or entry.get("key") != key:
        return False
    outputs = entry.get("outputs") or []
    if not outputs:
        return False
    digests = entry.get("digests") or {}
    for out in outputs:
        path = Path(out)
        if not path.is_file():
            return False
        expected = digests.get(out)
        if expected:
            try:
                if file_hash(path) != expected:
                    return False
            except OSError:
                return False
    return True
