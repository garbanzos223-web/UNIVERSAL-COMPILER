"""Decompilation service: inputs -> analysis -> backend -> reconstructed project.

The service is UI-agnostic (the CLI and the desktop panel use the same code),
runs nothing from the imported file, and reports every unsupported case instead
of producing a plausible-looking but fake result.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from ..logging_config import get_logger
from .analyzer import analyze_file
from .backends import BACKENDS, BackendContext, backend_for
from .backends.base import is_noise
from .models import (
    CATEGORY_METADATA,
    STATUS_GENERATED,
    BackendOutput,
    DecompileResult,
    FileAnalysis,
    RecoveredFile,
)
from .options import DecompileOptions

log = get_logger("decompiler.service")

ProgressCallback = Callable[[str, int, int], None]
LogCallback = Callable[[str, str], None]

MAX_DIRECTORY_FILES = 2000


def _safe_stem(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned or "input"


def expand_inputs(paths: Iterable[Path], *, max_files: int = MAX_DIRECTORY_FILES) -> list[Path]:
    """Expand folders into files (bounded, build-noise excluded)."""
    expanded: list[Path] = []
    for item in paths:
        item = Path(item)
        if item.is_file():
            expanded.append(item)
            continue
        if not item.is_dir():
            continue
        for path in sorted(item.rglob("*")):
            if len(expanded) >= max_files:
                return expanded
            if not path.is_file():
                continue
            relative = path.relative_to(item)
            if is_noise(relative.as_posix()):
                continue
            expanded.append(path)
    return expanded


@dataclass
class SessionOptions:
    """Small value object kept for callers that persist a session."""

    options: DecompileOptions
    output_dir: Path
    cancel: object | None = None


class DecompileSession:
    """Runs one decompilation session and aggregates the reconstructed project."""

    def __init__(self, options: DecompileOptions | None = None) -> None:
        self.options = options or DecompileOptions()

    def decompile(
        self,
        paths: Sequence[Path],
        output_dir: Path,
        *,
        progress: ProgressCallback | None = None,
        log_callback: LogCallback | None = None,
        cancel: threading.Event | None = None,
    ) -> DecompileResult:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        inputs = [Path(p) for p in paths]
        result = DecompileResult(ok=True, output_dir=output_dir)
        if not inputs:
            result.ok = False
            result.errors.append("No input file was selected.")
            return result

        def emit(message: str, tag: str = "info") -> None:
            result.log.append((message, tag))
            if log_callback:
                log_callback(message, tag)

        multiple = len(inputs) > 1
        used_names: set[str] = set()
        ready_flags: list[bool] = []
        kinds: list[str] = []
        total = len(inputs)

        for index, path in enumerate(inputs, start=1):
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                result.ok = False
                emit("Decompilation cancelled by the user.", "warn")
                break
            if progress:
                progress(f"Analyzing {path.name}", index - 1, total)
            emit(f"Analyzing {path}", "accent")
            try:
                analysis = analyze_file(path)
            except Exception as exc:
                result.ok = False
                result.errors.append(f"{path.name}: {exc}")
                emit(f"{path.name}: analysis failed: {exc}", "err")
                ready_flags.append(False)
                continue
            result.analyses.append(analysis)
            emit(
                f"{path.name}: {analysis.format.label}, {analysis.format.architecture}, "
                f"{int(analysis.format.confidence * 100)}% confidence",
                "info",
            )
            for warning in analysis.warnings:
                emit(f"  warning: {warning}", "warn")
            for note in analysis.notes:
                emit(f"  note: {note}", "muted")

            backend = backend_for(analysis.format.kind)
            stem = _safe_stem(path.stem)
            if multiple:
                base = stem
                counter = 2
                while base in used_names:
                    base = f"{stem}_{counter}"
                    counter += 1
                used_names.add(base)
                sub_dir = output_dir / base
            else:
                sub_dir = output_dir
            sub_dir.mkdir(parents=True, exist_ok=True)
            ctx = BackendContext(output_dir=sub_dir, options=self.options, log=emit)

            if backend is None:
                emit(f"  no decompilation backend for {analysis.format.kind}; analysis only.", "warn")
                output = BackendOutput(backend="analysis", project_kind="unknown")
                output.add(
                    ctx.write_text(
                        "analysis/summary.txt",
                        "\n".join(analysis.summary_lines()) + "\n",
                        category=CATEGORY_METADATA,
                        status=STATUS_GENERATED,
                        provenance="Analysis report; no backend can recover this format.",
                        language="text",
                    )
                )
                output.warnings.append(
                    f"{analysis.format.label} has no decompilation backend in this build; "
                    "only safe analysis is available."
                )
                rebuilt = _prefix(output.files, sub_dir.name if multiple else "")
                result.files.extend(rebuilt)
                result.warnings.extend(output.warnings)
                ready_flags.append(False)
                continue

            emit(f"  backend: {backend.label}", "muted")
            try:
                output = backend.decompile(path, analysis, ctx)
            except Exception as exc:
                result.ok = False
                result.errors.append(f"{path.name}: {backend.label} failed: {exc}")
                emit(f"  {backend.label} failed: {exc}", "err")
                ready_flags.append(False)
                continue
            if not output.ok:
                result.ok = False
            result.files.extend(_prefix(output.files, sub_dir.name if multiple else ""))
            result.warnings.extend(f"{path.name}: {w}" for w in output.warnings)
            result.errors.extend(f"{path.name}: {e}" for e in output.errors)
            for message, tag in output.log:
                emit(f"  {message}", tag)
            for warning in output.warnings:
                emit(f"  warning: {warning}", "warn")
            if output.project_kind:
                kinds.append(output.project_kind)
            ready_flags.append(output.recompile_ready)
            result.metadata[path.name] = output.metadata

        if kinds:
            unique = {k for k in kinds if k}
            result.project_kind = unique.pop() if len(unique) == 1 else "mixed"
        else:
            result.project_kind = "unknown"
        result.recompile_ready = (
            bool(ready_flags) and len(ready_flags) == len(inputs) and all(ready_flags)
        )
        if len(inputs) > 1 and not result.recompile_ready:
            result.warnings.append(
                "A merged multi-input project is only recompilable when every input "
                "produced a complete, compilable source project."
            )
        if not result.ok:
            emit("Decompilation finished with errors.", "err")
        else:
            emit(
                f"Recovered {len(result.files)} file(s) into {output_dir} "
                f"({len(result.sources)} source, {len(result.resources)} resources).",
                "ok",
            )
        result.write_manifest()
        return result

    def analyze(self, paths: Sequence[Path]) -> list[FileAnalysis]:
        return [analyze_file(Path(p)) for p in paths]


def _prefix(files: list[RecoveredFile], prefix: str) -> list[RecoveredFile]:
    if not prefix:
        return files
    for item in files:
        item.path = f"{prefix}/{item.path}"
    return files


def backend_catalog() -> list[dict[str, object]]:
    return [
        {
            "id": backend.id,
            "label": backend.label,
            "kinds": list(backend.kinds),
        }
        for backend in BACKENDS
    ]
