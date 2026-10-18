"""Build history persisted as JSON Lines.

One record per build attempt. The file is append-only and each line is an
independent JSON object, so a truncated final line (for example after a crash)
does not invalidate earlier entries.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..logging_config import get_logger

log = get_logger("build.history")

_lock = threading.Lock()


@dataclass
class BuildRecord:
    timestamp: str
    project_path: str
    project_type: str = ""
    adapter_id: str = ""
    ok: bool = False
    exit_code: int = -1
    duration_seconds: float = 0.0
    container_path: str = ""
    artifact_count: int = 0
    message: str = ""
    diagnostics: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict) -> "BuildRecord":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        data = {k: v for k, v in raw.items() if k in known}
        data.setdefault("timestamp", "")
        data.setdefault("project_path", "")
        return cls(**data)


def _history_path(base: Path) -> Path:
    return base / "history.jsonl"


def append(record: BuildRecord, *, base: Path) -> None:
    base.mkdir(parents=True, exist_ok=True)
    path = _history_path(base)
    line = json.dumps(asdict(record), ensure_ascii=False)
    with _lock:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def make_record(
    project_path: Path,
    *,
    ok: bool,
    exit_code: int = -1,
    duration_seconds: float = 0.0,
    adapter_id: str = "",
    project_type: str = "",
    container_path: Path | None = None,
    artifact_count: int = 0,
    message: str = "",
    diagnostics: list[str] | None = None,
) -> BuildRecord:
    return BuildRecord(
        timestamp=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        project_path=str(project_path),
        project_type=project_type,
        adapter_id=adapter_id,
        ok=ok,
        exit_code=exit_code,
        duration_seconds=duration_seconds,
        container_path=str(container_path) if container_path else "",
        artifact_count=artifact_count,
        message=message,
        diagnostics=diagnostics or [],
    )


def load(*, base: Path, limit: int = 100) -> list[BuildRecord]:
    path = _history_path(base)
    if not path.exists():
        return []
    records: list[BuildRecord] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(BuildRecord.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError):
                    # Skip a corrupt line rather than failing the whole history.
                    continue
    except OSError as exc:
        log.warning("Could not read build history: %s", exc)
        return []
    return records[-limit:]


def clear(*, base: Path) -> None:
    path = _history_path(base)
    with _lock:
        if path.exists():
            path.unlink()
