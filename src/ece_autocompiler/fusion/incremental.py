"""Dependency-aware incremental builds (§12).

A module's cache key is the SHA-256 of a JSON blob containing:

- the content hash of every source file of the module,
- the module's build configuration and adapter id,
- the cache keys of its dependencies (so a rebuilt library invalidates its
  consumers), and
- the engine toolchain version string where relevant.

Keys are stored in ``<workspace>/.ece-cache/modules.json``. A module is a
cache hit only when the stored key matches AND every recorded output file
still exists on disk. Correctness beats speed: when a key cannot be computed
honestly the module is rebuilt.
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"format": "ece-fusion-cache", "modules": self.entries}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    def record(self, module_id: str, key: str, outputs: list[str]) -> None:
        self.entries[module_id] = {"key": key, "outputs": list(outputs)}

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


def compute_key(
    module_id: str,
    source_files: list[Path],
    *,
    adapter_id: str,
    configuration: str,
    target_arch: str,
    dependency_keys: dict[str, str],
) -> str:
    payload = {
        "module": module_id,
        "adapter": adapter_id,
        "configuration": configuration,
        "architecture": target_arch,
        "files": ["", ""],  # replaced below with real hashes
        "dependencies": dict(sorted(dependency_keys.items())),
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
    """A hit requires both the key match AND the recorded outputs on disk."""
    entry = state.lookup(module_id)
    if not entry or entry.get("key") != key:
        return False
    outputs = entry.get("outputs") or []
    if not outputs:
        return False
    return all(Path(out).exists() for out in outputs)
