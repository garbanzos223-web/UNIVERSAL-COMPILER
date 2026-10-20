"""Measure before and after, and report only what was really measured.

A percentage is only produced when both numbers exist and are positive, and a
runtime claim is only produced when a benchmark actually ran. The module never
estimates a speed-up it did not measure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..local_store import append_jsonl


@dataclass
class Measurement:
    """What one build produced and how long it took."""

    profile: str = ""
    ok: bool = False
    compile_seconds: float = 0.0
    total_size: int = 0
    file_count: int = 0
    tests_ok: bool | None = None
    tests_output: str = ""
    benchmark: dict[str, float] = field(default_factory=dict)
    toolchains: dict[str, str] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    timestamp: str = ""

    def as_dict(self) -> dict:
        return {
            "profile": self.profile,
            "ok": self.ok,
            "compile_seconds": round(self.compile_seconds, 3),
            "total_size": self.total_size,
            "file_count": self.file_count,
            "tests_ok": self.tests_ok,
            "benchmark": dict(self.benchmark),
            "toolchains": dict(self.toolchains),
            "artifacts": list(self.artifacts),
            "timestamp": self.timestamp,
        }


@dataclass
class ComparisonReport:
    """The difference between a baseline build and an optimized build."""

    baseline: Measurement = field(default_factory=Measurement)
    optimized: Measurement = field(default_factory=Measurement)
    time_change_pct: float | None = None
    size_change_pct: float | None = None
    runtime_change_pct: float | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "baseline": self.baseline.as_dict(),
            "optimized": self.optimized.as_dict(),
            "time_change_pct": self.time_change_pct,
            "size_change_pct": self.size_change_pct,
            "runtime_change_pct": self.runtime_change_pct,
            "notes": list(self.notes),
        }

    def text(self) -> str:
        from ..usability.summary import human_size

        lines = ["Optimization comparison"]
        lines.append(
            f"  Baseline  : {self.baseline.profile or 'default'} "
            f"({self.baseline.compile_seconds:.2f}s, "
            f"{human_size(self.baseline.total_size)}, "
            f"{self.baseline.file_count} file(s))"
        )
        lines.append(
            f"  Optimized : {self.optimized.profile or 'default'} "
            f"({self.optimized.compile_seconds:.2f}s, "
            f"{human_size(self.optimized.total_size)}, "
            f"{self.optimized.file_count} file(s))"
        )
        if self.time_change_pct is None:
            lines.append("  Build time: not comparable (one of the builds did not run)")
        else:
            lines.append(f"  Build time: {self.time_change_pct:+.1f}%")
        if self.size_change_pct is None:
            lines.append("  Binary size: not comparable (one of the builds produced no file)")
        else:
            lines.append(f"  Binary size: {self.size_change_pct:+.1f}%")
        if self.runtime_change_pct is None:
            lines.append("  Runtime    : not measured (no benchmark command was run)")
        else:
            lines.append(f"  Runtime    : {self.runtime_change_pct:+.1f}%")
        for note in self.notes:
            lines.append(f"  - {note}")
        return "\n".join(lines)


def _percent(before: float, after: float) -> float | None:
    """Percentage change from *before* to *after*, or None when not measurable."""
    if before <= 0 or after <= 0:
        return None
    return round((after - before) / before * 100.0, 1)


def compare(baseline: Measurement, optimized: Measurement) -> ComparisonReport:
    """Compare two measurements without inventing numbers."""
    report = ComparisonReport(baseline=baseline, optimized=optimized)
    report.time_change_pct = _percent(baseline.compile_seconds, optimized.compile_seconds)
    report.size_change_pct = _percent(float(baseline.total_size), float(optimized.total_size))

    if baseline.tests_ok is False or optimized.tests_ok is False:
        report.notes.append(
            "The test suite failed in one of the builds; the comparison is not a "
            "regression signal."
        )
    if baseline.benchmark and optimized.benchmark:
        shared = sorted(set(baseline.benchmark) & set(optimized.benchmark))
        changes = [
            _percent(baseline.benchmark[key], optimized.benchmark[key]) for key in shared
        ]
        changes = [c for c in changes if c is not None]
        if changes:
            report.runtime_change_pct = round(sum(changes) / len(changes), 1)
    else:
        report.notes.append(
            "No benchmark command was run, so no runtime claim is made."
        )
    if not baseline.ok or not optimized.ok:
        report.notes.append(
            "One of the builds failed; only the successful measurements are meaningful."
        )
    return report


def record_optimization(base, report: ComparisonReport) -> None:
    """Append the comparison to the optimization log (JSON Lines)."""
    payload = report.as_dict()
    payload["timestamp"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    append_jsonl(base / "optimizations.jsonl", payload)
