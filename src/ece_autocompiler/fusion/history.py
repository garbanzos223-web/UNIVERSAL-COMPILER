"""Record fusion builds in the shared build history.

The GUI and the CLI both call :func:`record`, so a fused build appears in
``ece-autocompiler history`` next to ordinary builds. The record carries the
same verified facts as the fusion report: package hash, artifact count and
the per-module failures that made the build fail.
"""

from __future__ import annotations

from pathlib import Path

from ..build import history
from ..config import AppConfig


def build_record(result, *, base: Path | None = None) -> history.BuildRecord:
    """Build a :class:`BuildRecord` from a FusionResult (no side effects)."""
    report = getattr(result, "report", {}) or {}
    artifacts = report.get("artifacts") or []
    package = report.get("package_artifact") or {}
    failures = [
        f"{task.get('module_id', '?')}: {task.get('error', '')}".strip()
        for task in report.get("tasks", [])
        if task.get("status") != "built"
    ]
    if result.ok:
        message = "fusion build ok"
    elif failures:
        message = "fusion build failed: " + "; ".join(failures)
    else:
        message = "fusion build failed"
    languages = sorted(
        {module.language for module in result.graph.modules if module.language}
    )
    return history.make_record(
        Path(result.workspace.root),
        ok=bool(result.ok),
        exit_code=0 if result.ok else 1,
        duration_seconds=float(report.get("duration_seconds") or 0.0),
        adapter_id="fusion",
        project_type="fusion" + (":" + ",".join(languages) if languages else ""),
        mode="fusion",
        container_path=Path(result.package_path) if result.package_path else None,
        artifact_count=len(artifacts),
        sha256=str(package.get("sha256") or ""),
        message=message,
        diagnostics=(list(result.messages) + failures)[:20],
        format=result.package_kind,
    )


def record(result, *, base: Path | None = None) -> Path:
    """Append a fusion build to the history store; returns the store base."""
    store = Path(base) if base is not None else AppConfig().work_dir
    history.append(build_record(result), base=store)
    return store
