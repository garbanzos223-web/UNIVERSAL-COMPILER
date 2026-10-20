"""Local-only, atomic persistence for settings, profiles, history and cache.

Everything this module writes stays on this machine: no database, no service,
no network. Files are JSON, JSONL, CSV or plain text under
``%LOCALAPPDATA%\\ECE-Universal-AutoCompiler\\`` (override with the
``ECE_AUTOCOMPILER_HOME`` environment variable, which the tests use).

Writes are atomic (temporary file + ``os.replace``) and the previous content of
a JSON/JSONL file is kept as ``<name>.bak``. When a file cannot be parsed it is
quarantined as ``<name>.corrupt-<utc>`` and the ``.bak`` is restored, so a
half-written file never makes the application unusable.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

APP_DATA_DIRNAME = "ECE-Universal-AutoCompiler"
ENV_OVERRIDE = "ECE_AUTOCOMPILER_HOME"


def app_data_dir() -> Path:
    """Root of the local application data (per user, offline)."""
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or str(Path.home())
    return Path(base) / APP_DATA_DIRNAME


def config_dir() -> Path:
    """Where settings, profiles, templates and the cache live."""
    return app_data_dir() / "config"


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass
class LoadResult:
    """A value plus whether the stored file had to be repaired."""

    value: Any
    recovered: bool = False
    detail: str = ""


def _quarantine(path: Path) -> Path | None:
    try:
        target = path.with_name(f"{path.name}.corrupt-{_now_stamp()}")
        path.replace(target)
        return target
    except OSError:
        return None


def _restore_backup(path: Path) -> bool:
    backup = path.with_name(path.name + ".bak")
    if not backup.is_file():
        return False
    try:
        shutil.copyfile(backup, path)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def read_json(path: Path, default: Any) -> LoadResult:
    """Read JSON, repairing a damaged file instead of failing."""
    path = Path(path)
    if not path.is_file():
        return LoadResult(default)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return LoadResult(json.load(handle))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        quarantined = _quarantine(path)
        restored = _restore_backup(path)
        detail = f"'{path.name}' was unreadable ({exc.__class__.__name__}); "
        detail += "restored the previous .bak backup." if restored else "using defaults."
        if quarantined is not None:
            detail += f" The damaged file was kept as '{quarantined.name}'."
        if restored:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    return LoadResult(json.load(handle), recovered=True, detail=detail)
            except (OSError, ValueError):
                pass
        return LoadResult(default, recovered=True, detail=detail)
    except OSError as exc:
        return LoadResult(default, recovered=True, detail=f"could not read '{path.name}': {exc}")


def write_json_atomic(path: Path, data: Any) -> Path:
    """Write JSON through a temporary file; keep the previous file as ``.bak``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            shutil.copyfile(path, path.with_name(path.name + ".bak"))
        except OSError:
            pass
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return path


# ---------------------------------------------------------------------------
# JSONL
# ---------------------------------------------------------------------------


def append_jsonl(path: Path, record: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")


def read_jsonl(path: Path) -> LoadResult:
    """Read a JSONL file, skipping (and counting) lines that do not parse."""
    path = Path(path)
    if not path.is_file():
        return LoadResult([])
    rows: list[Any] = []
    skipped = 0
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    skipped += 1
    except OSError as exc:
        return LoadResult([], recovered=True, detail=f"could not read '{path.name}': {exc}")
    detail = f"skipped {skipped} unreadable line(s) in '{path.name}'" if skipped else ""
    return LoadResult(rows, recovered=bool(skipped), detail=detail)


# ---------------------------------------------------------------------------
# CSV / text
# ---------------------------------------------------------------------------


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        # utf-8-sig so Excel on Windows shows accents correctly.
        with open(tmp, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(list(header))
            for row in rows:
                writer.writerow(list(row))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return path


def read_text(path: Path, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return default


def write_text_atomic(path: Path, text: str) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return path


# ---------------------------------------------------------------------------
# Settings convenience
# ---------------------------------------------------------------------------


def settings_path(base: Path | None = None) -> Path:
    return (base or config_dir()) / "settings.json"


def load_settings(base: Path | None = None) -> LoadResult:
    return read_json(settings_path(base), {})


def save_settings(values: dict, base: Path | None = None) -> Path:
    return write_json_atomic(settings_path(base), values)
