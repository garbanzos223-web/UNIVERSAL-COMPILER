"""One place that runs a build and returns everything about it.

The CLI, the GUI and the repair engine all need the same thing: run the real
build, keep the full output for the diagnostics, and report what came out of
it. This module does that once, so the three callers cannot drift apart.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters.base import Artifact
from ..config import AppConfig
from .orchestrator import BuildOrchestrator


@dataclass
class CompileOutcome:
    """The result of one compilation."""

    ok: bool = False
    output: str = ""
    exit_code: int = -1
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    log_path: Path | None = None
    duration_seconds: float = 0.0
    adapter_id: str = ""
    project_dir: Path | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    total_size: int = 0
    file_count: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return not self.ok


def _read_log(log_path: Path | None, limit: int = 200_000) -> str:
    if not log_path:
        return ""
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]


def compile_project(
    project: Path | str,
    *,
    config: AppConfig | None = None,
    config_overrides: dict | None = None,
    consent_granted: bool = False,
    prefer_adapter: str | None = None,
    optimization=None,
    progress=None,
    cancel=None,
    package_only: bool = False,
) -> CompileOutcome:
    """Run the real build of *project* and collect everything about it."""
    cfg = config or AppConfig()
    cfg.ensure_directories()
    orchestrator = BuildOrchestrator(config=cfg)
    started = time.monotonic()
    compiled = orchestrator.compile_project(
        project,
        config_overrides=config_overrides,
        consent_granted=consent_granted,
        prefer_adapter=prefer_adapter,
        package_only=package_only,
        progress=progress,
        cancel=cancel,
        optimization=optimization,
    )
    result = compiled.result
    duration = max(result.duration_seconds, round(time.monotonic() - started, 3))
    output = _read_log(result.log_path)
    if not output:
        output = "\n".join(result.errors)
    total_size = 0
    for artifact in compiled.artifacts:
        try:
            total_size += artifact.path.stat().st_size
        except OSError:
            continue
    return CompileOutcome(
        ok=result.ok,
        output=output,
        exit_code=result.exit_code,
        errors=list(result.errors),
        warnings=list(result.warnings),
        log_path=result.log_path,
        duration_seconds=duration,
        adapter_id=compiled.adapter_id,
        project_dir=compiled.project_dir,
        artifacts=list(compiled.artifacts),
        total_size=total_size,
        file_count=len(compiled.artifacts),
        notes=list(result.notes),
    )


def make_build_runner(
    project: Path | str,
    *,
    config: AppConfig | None = None,
    config_overrides: dict | None = None,
    consent_granted: bool = False,
    prefer_adapter: str | None = None,
    optimization=None,
    progress=None,
    cancel=None,
):
    """Return a callable that builds the project and yields a BuildOutcome.

    The repair engine calls it once per verification, so every call is a real
    build with the real toolchain.
    """
    from ..repair.engine import BuildOutcome

    def runner() -> BuildOutcome:
        outcome = compile_project(
            project,
            config=config,
            config_overrides=config_overrides,
            consent_granted=consent_granted,
            prefer_adapter=prefer_adapter,
            optimization=optimization,
            progress=progress,
            cancel=cancel,
        )
        return BuildOutcome(
            ok=outcome.ok,
            output=outcome.output,
            exit_code=outcome.exit_code,
            errors=list(outcome.errors),
            log_path=outcome.log_path,
        )

    return runner
