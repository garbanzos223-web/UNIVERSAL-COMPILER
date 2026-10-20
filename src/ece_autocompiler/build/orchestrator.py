"""Build orchestrator: coordinates detection, adapter selection, build, packaging."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters.base import Artifact, BuildPlan, BuildResult, ProgressCallback
from ..adapters.registry import AdapterRegistry
from ..config import AppConfig, Limits
from ..container import ContainerWriter, Manifest, PayloadItem
from ..detection import scan
from ..errors import BuildError, ConfigurationError
from ..logging_config import get_logger

log = get_logger("build.orchestrator")

CancelToken = threading.Event


@dataclass
class PackageResult:
    ok: bool
    container_path: Path | None
    message: str = ""
    sha256: str = ""
    file_count: int = 0
    size: int = 0
    selected_root: str = ""


@dataclass
class CompileResult:
    """The outcome of detecting, planning and running a build.

    The packaging step is deliberately not part of it, so the same code can be
    reused by the repair engine and by the advanced package generator.
    """

    result: BuildResult = field(default_factory=BuildResult)
    artifacts: list[Artifact] = field(default_factory=list)
    adapter_id: str = ""
    project_type: str = ""
    project_dir: Path | None = None
    plan: BuildPlan | None = None
    selected_root: str = ""


def _artifact_to_payload(artifacts, root: Path):
    items: list[PayloadItem] = []
    seen = set()
    for a in artifacts:
        rel = getattr(a, "rel_path", None) or str(a.path.relative_to(root))
        # Normalize to forward slashes
        rel_posix = rel.replace("\\", "/")
        if rel_posix in seen:
            continue
        seen.add(rel_posix)
        items.append(PayloadItem(path=rel_posix, source=a.path))
    return items


class BuildOrchestrator:
    def __init__(self, config: AppConfig | None = None, limits: Limits | None = None) -> None:
        self.config = config or AppConfig()
        self.limits = limits or Limits()
        self.registry = AdapterRegistry()

    # ------------------------------------------------------------------
    def compile_project(
        self,
        project_path: Path | str,
        *,
        config_overrides: dict | None = None,
        consent_granted: bool = False,
        prefer_adapter: str | None = None,
        package_only: bool = False,
        selected_adapter: list[str] | None = None,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
        optimization=None,
        log_path: Path | None = None,
    ) -> CompileResult:
        """Detect, plan and run the build; return the result and the artifacts.

        *prefer_adapter* forces a specific adapter (monorepo choice).
        *package_only* skips the build and collects what is already on disk.
        *selected_adapter*, when given, receives the chosen adapter id.
        *optimization*, when given, is an
        :class:`ece_autocompiler.optimize.plan.OptimizationPlan` whose flags
        are applied to the build plan.
        """
        root = Path(project_path).resolve()
        if not root.exists():
            raise ConfigurationError("Project path does not exist", detail=str(root))

        if cancel is not None and cancel.is_set():
            raise BuildError("Cancelled")

        if progress:
            progress("Scanning project", 0, 100)
        sr = scan(root, limits=self.limits)

        if not sr.candidates or sr.best is None:
            raise ConfigurationError("No supported project detected in this directory.")

        # A candidate may point at a subdirectory (monorepos); build there.
        best = sr.best
        if prefer_adapter:
            for candidate in sr.candidates:
                if candidate.adapter_id == prefer_adapter:
                    best = candidate
                    break
        project_dir = (root / best.root).resolve() if best.root else root
        adapter = self.registry.for_detected(best)
        if adapter is None:
            raise ConfigurationError("No adapter for detected project", detail=best.adapter_id)
        if selected_adapter is not None:
            selected_adapter[:] = [adapter.adapter_id]

        if progress:
            progress("Creating build plan", 20, 100)
        plan = adapter.create_build_plan(project_dir, config_overrides)
        if optimization is not None:
            from ..optimize.plan import apply_optimization

            apply_optimization(plan, optimization)
        if plan.requires_consent and not consent_granted and not package_only:
            raise ConfigurationError(
                "This build requires explicit consent (runs project scripts).",
                detail="Grant consent and retry.",
            )

        if package_only:
            if progress:
                progress("Packaging only (build skipped)", 60, 100)
            res = BuildResult(ok=True, exit_code=0, notes=["Packaging only: no build was run."])
        else:
            if progress:
                progress("Running build", 30, 100)
            if log_path is None:
                log_dir = self.config.work_dir / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_path = log_dir / f"build_{int(time.time())}.log"
            res = adapter.execute_build(
                plan,
                progress=(
                    lambda msg, d, t: progress(
                        f"Build: {msg}", 30 + int(d * 40 / max(1, t)), 100
                    )
                )
                if progress
                else None,
                cancel=cancel,
                log_path=log_path,
                consent_granted=consent_granted,
            )
            if not res.ok:
                return CompileResult(
                    result=res,
                    artifacts=[],
                    adapter_id=adapter.adapter_id,
                    project_type=best.project_type,
                    project_dir=project_dir,
                    plan=plan,
                    selected_root=best.root,
                )

        if progress:
            progress("Collecting artifacts", 75, 100)
        artifacts = adapter.collect_artifacts(project_dir, plan)
        if not artifacts:
            # Fallback: package the source tree without VCS/cache noise. This
            # keeps a build that produced no explicit output usable, and stays
            # honest about not having produced a compiled artifact.
            for path in project_dir.rglob("*"):
                if not path.is_file():
                    continue
                rel = path.relative_to(project_dir).as_posix()
                if rel.startswith((".git/", "__pycache__/", "node_modules/")):
                    continue
                parts = rel.split("/")
                if any(p.startswith(".") and p not in (".env",) for p in parts):
                    continue
                artifacts.append(Artifact(path=path, kind="source", size=path.stat().st_size, rel_path=rel))

        return CompileResult(
            result=res,
            artifacts=artifacts,
            adapter_id=adapter.adapter_id,
            project_type=best.project_type,
            project_dir=project_dir,
            plan=plan,
            selected_root=best.root,
        )

    # ------------------------------------------------------------------
    def build_and_package(
        self,
        project_path: Path | str,
        output_path: Path | str,
        *,
        config_overrides: dict | None = None,
        consent_granted: bool = False,
        overwrite: bool = False,
        compression: str = "zlib",
        prefer_adapter: str | None = None,
        package_only: bool = False,
        selected_adapter: list[str] | None = None,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
        optimization=None,
    ) -> tuple[BuildResult, PackageResult]:
        """Detect, build (unless *package_only*) and package into an .ece file."""
        root = Path(project_path).resolve()
        if not root.exists():
            raise ConfigurationError("Project path does not exist", detail=str(root))
        out = Path(output_path)
        self.config.ensure_directories()
        started_at = time.monotonic()

        def _finish(result: BuildResult) -> BuildResult:
            """Stamp the real wall time of detect + build + package (item 8).

            The adapter measures its own compile step; packaging and scanning
            also take time, so the caller-facing duration is never less than
            the whole operation. Package-only runs therefore stop reporting
            a misleading 0.0 seconds.
            """
            result.duration_seconds = max(
                result.duration_seconds, round(time.monotonic() - started_at, 3)
            )
            return result

        compiled = self.compile_project(
            root,
            config_overrides=config_overrides,
            consent_granted=consent_granted,
            prefer_adapter=prefer_adapter,
            package_only=package_only,
            selected_adapter=selected_adapter,
            progress=progress,
            cancel=cancel,
            optimization=optimization,
        )
        res = compiled.result
        adapter_id = compiled.adapter_id
        project_dir = compiled.project_dir or root
        if not res.ok:
            return _finish(res), PackageResult(ok=False, container_path=None, message="Build failed")

        artifacts = compiled.artifacts

        if progress:
            progress("Packaging into .ece", 85, 100)
        manifest = Manifest(
            project_name=root.name,
            adapter_id=adapter_id,
            payload_type=compiled.project_type,
            platform="windows",
            architecture="x64",
            configuration="release",
            notes="Packaged by ECE Universal AutoCompiler",
        )
        writer = ContainerWriter(compression=compression, limits=self.limits)
        items = _artifact_to_payload(artifacts, project_dir)
        if not items:
            return _finish(res), PackageResult(ok=False, container_path=None, message="No artifacts to package")
        try:
            wrep = writer.write(out, items, manifest, overwrite=overwrite)
            digest = ""
            size = 0
            try:
                from ..container.integrity import sha256_file

                digest = sha256_file(wrep.destination)
                size = wrep.destination.stat().st_size
            except OSError:
                digest = ""
            prep = PackageResult(
                ok=True,
                container_path=wrep.destination,
                message="Package created",
                sha256=digest,
                file_count=len(items),
                size=size,
                selected_root=compiled.selected_root,
            )
        except Exception as e:
            return _finish(res), PackageResult(ok=False, container_path=None, message=str(e))

        if progress:
            progress("Done", 100, 100)
        return _finish(res), prep
