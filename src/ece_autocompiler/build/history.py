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
    mode: str = "ece"                 # "ece", "exe" or "package"
    ok: bool = False
    exit_code: int = -1
    duration_seconds: float = 0.0
    container_path: str = ""
    artifact_count: int = 0
    sha256: str = ""
    message: str = ""
    diagnostics: list[str] = field(default_factory=list)
    #: Package format used by a "package" run (empty for the other modes).
    format: str = ""
    #: Optimization profile used by the build ("" or "none" when not applied).
    profile: str = ""

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
    mode: str = "ece",
    container_path: Path | None = None,
    artifact_count: int = 0,
    sha256: str = "",
    message: str = "",
    diagnostics: list[str] | None = None,
    format: str = "",
    profile: str = "",
) -> BuildRecord:
    return BuildRecord(
        timestamp=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        project_path=str(project_path),
        project_type=project_type,
        adapter_id=adapter_id,
        mode=mode,
        ok=ok,
        exit_code=exit_code,
        duration_seconds=duration_seconds,
        container_path=str(container_path) if container_path else "",
        artifact_count=artifact_count,
        sha256=sha256,
        message=message,
        diagnostics=diagnostics or [],
        format=format,
        profile=profile,
    )


def load(*, base: Path, limit: int = 100) -> list[BuildRecord]:
    return load_report(base=base)[0][-limit:]


def load_all(*, base: Path) -> list[BuildRecord]:
    """Every stored record (used by exports)."""
    return load_report(base=base)[0]


def load_report(*, base: Path) -> tuple[list[BuildRecord], str]:
    """Load records plus a human note when a damaged file had to be repaired.

    Uses the atomic/quarantine reader so a half-written history file is repaired
    from its ``.bak`` instead of making the history unusable (item 24).
    """
    from ..local_store import read_jsonl

    result = read_jsonl(_history_path(base))
    records: list[BuildRecord] = []
    for raw in result.value:
        try:
            records.append(BuildRecord.from_dict(raw))
        except (TypeError, ValueError):
            continue
    return records, (result.detail if result.recovered else "")


def clear(*, base: Path) -> None:
    path = _history_path(base)
    with _lock:
        if path.exists():
            path.unlink()
