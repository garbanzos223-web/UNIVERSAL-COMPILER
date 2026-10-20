"""Search, filter, export and replay build history (items 30 and 40).

The GUI history dialog and the ``ece-autocompiler history`` command share these
helpers, so filtering behaves identically in both. Records are accepted either
as :class:`~ece_autocompiler.build.history.BuildRecord` objects or as the plain
dicts stored in ``history.jsonl``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..local_store import write_csv, write_json_atomic

#: Columns written to CSV exports, in a stable order.
CSV_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "project_path",
    "project_type",
    "adapter_id",
    "mode",
    "ok",
    "exit_code",
    "duration_seconds",
    "container_path",
    "artifact_count",
    "sha256",
    "message",
)


def _as_dict(record: Any) -> dict:
    if isinstance(record, dict):
        return dict(record)
    to_dict = getattr(record, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    fields = getattr(record, "__dataclass_fields__", None)
    if fields:
        return {name: getattr(record, name, "") for name in fields}
    return dict(getattr(record, "__dict__", {}))


def filter_records(
    records: list,
    *,
    query: str = "",
    ok: bool | None = None,
    adapter_id: str = "",
    project: str = "",
    limit: int | None = None,
) -> list:
    """Filter records by free text, result, adapter or project substring.

    *query* matches (case-insensitively) anywhere in the project path, project
    type, adapter id, message or output path. Results keep the input order.
    """
    needle = query.strip().lower()
    project_needle = project.strip().lower()
    adapter_needle = adapter_id.strip().lower()
    selected: list = []
    for record in records:
        data = _as_dict(record)
        if ok is not None and bool(data.get("ok")) is not ok:
            continue
        if adapter_needle and adapter_needle not in str(data.get("adapter_id", "")).lower():
            continue
        if project_needle and project_needle not in str(data.get("project_path", "")).lower():
            continue
        if needle:
            haystack = " ".join(
                str(data.get(key, ""))
                for key in ("project_path", "project_type", "adapter_id", "message",
                            "container_path", "mode")
            ).lower()
            if needle not in haystack:
                continue
        selected.append(record)
    if limit is not None and limit >= 0:
        selected = selected[-limit:] if limit else []
    return selected


def summarize(records: list) -> dict:
    """Totals for a set of records, honest about failures."""
    total = len(records)
    ok = sum(1 for r in records if _as_dict(r).get("ok"))
    last = _as_dict(records[-1]) if records else {}
    return {
        "total": total,
        "ok": ok,
        "failed": total - ok,
        "last": last.get("timestamp", ""),
        "last_ok": bool(last.get("ok")) if records else None,
    }


def export_json(records: list, path: Path, *, base: Path | None = None) -> Path:
    """Write records as a JSON array (atomic write)."""
    data = [_as_dict(r) for r in records]
    return write_json_atomic(Path(path), data)


def export_csv(records: list, path: Path) -> Path:
    """Write records as CSV with the stable :data:`CSV_COLUMNS` header."""
    rows = []
    for record in records:
        data = _as_dict(record)
        rows.append([_cell(data.get(column, "")) for column in CSV_COLUMNS])
    return write_csv(Path(path), list(CSV_COLUMNS), rows)


def _cell(value: Any) -> Any:
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def replay_command(record: Any, *, executable: str = "ece-autocompiler") -> str:
    """The copyable command that repeats a recorded build with the same inputs."""
    data = _as_dict(record)
    project = str(data.get("project_path", "") or ".")
    mode = str(data.get("mode", "ece") or "ece")
    verb = "exe" if mode == "exe" else "build"
    return f'{executable} {verb} "{project}"'


def describe(records: list) -> list[str]:
    """Short human summary lines for a filtered history view."""
    stats = summarize(records)
    if not stats["total"]:
        return ["No builds match the current filter."]
    lines = [
        f"{stats['total']} build(s): {stats['ok']} succeeded, {stats['failed']} failed."
    ]
    if stats["last"]:
        lines.append(f"Most recent: {stats['last']} ({'ok' if stats['last_ok'] else 'failed'}).")
    return lines


#: Public alias: the GUI and CLI may need the plain dict of a record.
as_dict = _as_dict
