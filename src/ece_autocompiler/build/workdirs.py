"""Collision-proof work directories and bounded on-disk build hygiene.

Two real problems this module fixes:

* ``exe_<epoch-second>`` directory names collide when two exports start
  within the same second; both builds then share a workdir, a spec file and
  a log, and one of them silently loses its output. ``tempfile.mkdtemp``
  names are unique by construction, so that class of bug is gone.
* A failing build used to delete its workdir on the way out, destroying the
  only evidence of what happened, while directories abandoned by crashes
  accumulated forever. Failures now keep their workdir and
  :func:`sweep_work_dirs` bounds the total to the newest few.

Hygiene is best-effort by design: it may log what it did, but it must never
be able to fail a build.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..config import AppConfig
from ..logging_config import get_logger

log = get_logger("build.workdirs")

# Diagnosis budget: how many recent workdirs / logs stay on disk.
KEEP_WORKDIRS = 3
KEEP_LOGS = 20


def new_work_dir(config: AppConfig, prefix: str = "exe_") -> Path:
    """Create and return a uniquely named work directory.

    Two calls - even two in the same second, from the same process or from
    two - always return different, existing directories.
    """
    config.work_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(config.work_dir)))


def sweep_work_dirs(
    config: AppConfig,
    *,
    keep_workdirs: int = KEEP_WORKDIRS,
    keep_logs: int = KEEP_LOGS,
) -> list[str]:
    """Delete stale ``exe_*`` workdirs and build logs, keeping the newest few.

    Only ``exe_*`` directories and ``logs\\*.log`` files are touched: the
    history file and anything else in the work dir are left alone. Returns
    the names that were removed so the caller can log them. Never raises.
    """
    removed: list[str] = []
    work = config.work_dir
    if not work.is_dir():
        return removed

    try:
        dirs = [
            p for p in work.iterdir()
            if p.is_dir() and p.name.startswith("exe_")
        ]
        dirs.sort(key=_mtime_newest_first, reverse=True)
        for stale in dirs[max(int(keep_workdirs), 0):]:
            shutil.rmtree(stale, ignore_errors=True)
            removed.append(stale.name)
    except OSError as exc:  # pragma: no cover - best effort
        log.debug("workdir sweep skipped: %s", exc)

    try:
        log_dir = work / "logs"
        if log_dir.is_dir():
            logs = [
                p for p in log_dir.iterdir()
                if p.is_file() and p.name.endswith(".log")
            ]
            logs.sort(key=_mtime_newest_first, reverse=True)
            for stale in logs[max(int(keep_logs), 0):]:
                try:
                    stale.unlink()
                    removed.append(f"logs/{stale.name}")
                except OSError:  # pragma: no cover - best effort
                    pass
    except OSError as exc:  # pragma: no cover - best effort
        log.debug("log sweep skipped: %s", exc)

    if removed:
        log.info("swept %d stale build artifact(s)", len(removed))
    return removed


def _mtime_newest_first(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:  # pragma: no cover - vanished between list and stat
        return 0.0
