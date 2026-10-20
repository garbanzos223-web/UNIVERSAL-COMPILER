"""Local build-time estimates from builds that already happened (item 34).

Everything is computed from this machine's own history
(``%LOCALAPPDATA%\\ECE-Universal-AutoCompiler\\work\\timings.jsonl``): no service,
no account and no network. Estimates are always presented as estimates, and a
sample count below three (or a high spread) is labelled *rough* instead of
pretending to be precise.

Only successful builds feed the average. Cancelled and failed builds are still
recorded (so the history is honest) but are excluded from the mean, and the
exclusion is stated in the message.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..local_store import append_jsonl, read_jsonl

TIMINGS_FILENAME = "timings.jsonl"

#: Estimates below this many samples are labelled rough.
MINIMUM_SAMPLES = 3


def _path(base: Path) -> Path:
    return Path(base) / TIMINGS_FILENAME


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class TimingSample:
    timestamp: str
    adapter_id: str
    mode: str
    duration_seconds: float
    ok: bool
    file_count: int = 0
    input_bytes: int = 0

    @classmethod
    def from_dict(cls, raw: dict) -> "TimingSample":
        return cls(
            timestamp=str(raw.get("timestamp", "")),
            adapter_id=str(raw.get("adapter_id", "")),
            mode=str(raw.get("mode", "ece")),
            duration_seconds=float(raw.get("duration_seconds", 0.0) or 0.0),
            ok=bool(raw.get("ok", False)),
            file_count=int(raw.get("file_count", 0) or 0),
            input_bytes=int(raw.get("input_bytes", 0) or 0),
        )

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "adapter_id": self.adapter_id,
            "mode": self.mode,
            "duration_seconds": self.duration_seconds,
            "ok": self.ok,
            "file_count": self.file_count,
            "input_bytes": self.input_bytes,
        }


def load_samples(base: Path) -> list[TimingSample]:
    """Every recorded sample, oldest first. Damaged lines are skipped."""
    result = read_jsonl(_path(base))
    samples: list[TimingSample] = []
    for raw in result.value:
        try:
            samples.append(TimingSample.from_dict(raw))
        except (TypeError, ValueError):
            continue
    return samples


def record_sample(
    base: Path,
    *,
    adapter_id: str,
    mode: str,
    duration_seconds: float,
    ok: bool,
    file_count: int = 0,
    input_bytes: int = 0,
) -> TimingSample:
    """Append one sample to the local timing history."""
    sample = TimingSample(
        timestamp=_now_iso(),
        adapter_id=adapter_id or "unknown",
        mode=mode or "ece",
        duration_seconds=max(0.0, float(duration_seconds)),
        ok=bool(ok),
        file_count=max(0, int(file_count)),
        input_bytes=max(0, int(input_bytes)),
    )
    append_jsonl(_path(base), sample.to_dict())
    return sample


@dataclass
class Estimate:
    """What the local history says about this kind of build."""

    available: bool
    samples: int = 0
    average: float | None = None
    low: float | None = None
    high: float | None = None
    rough: bool = False
    excluded: int = 0
    message: str = ""

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "samples": self.samples,
            "average_s": round(self.average, 3) if self.average is not None else None,
            "low_s": round(self.low, 3) if self.low is not None else None,
            "high_s": round(self.high, 3) if self.high is not None else None,
            "rough": self.rough,
            "excluded": self.excluded,
            "message": self.message,
        }


def _format_range(low: float, high: float) -> str:
    if abs(high - low) < 1:
        return f"~{low:.0f} s"
    return f"~{low:.0f}-{high:.0f} s"


def estimate_build(
    base: Path,
    adapter_id: str,
    mode: str = "ece",
    *,
    file_count: int = 0,
    minimum_samples: int = MINIMUM_SAMPLES,
) -> Estimate:
    """Estimate a build duration for one adapter and mode from local history."""
    all_samples = load_samples(base)
    relevant = [
        s for s in all_samples
        if s.adapter_id == (adapter_id or "unknown") and s.mode == (mode or "ece")
    ]
    successful = [s for s in relevant if s.ok and s.duration_seconds > 0]
    excluded = len(relevant) - len(successful)

    if not successful:
        message = (
            "No successful timing history yet for this project type - this "
            "estimate will become available after a first successful build."
        )
        if excluded:
            message += f" ({excluded} failed/cancelled build(s) are excluded.)"
        return Estimate(False, samples=0, excluded=excluded, message=message)

    durations = [s.duration_seconds for s in successful]
    average = statistics.fmean(durations)
    low, high = min(durations), max(durations)
    spread = statistics.pstdev(durations) / average if len(durations) > 1 and average else 0.0
    rough = len(durations) < minimum_samples or spread > 0.5
    label = "rough estimate" if rough else "estimate"
    message = (
        f"Estimated: {_format_range(low, high)} ({len(durations)} similar build(s)). "
        f"This is a {label} from your local history."
    )
    if excluded:
        message += f" {excluded} failed/cancelled build(s) are excluded."
    return Estimate(
        True,
        samples=len(durations),
        average=average,
        low=low,
        high=high,
        rough=rough,
        excluded=excluded,
        message=message,
    )


def compare_to_estimate(estimate: Estimate, actual_seconds: float) -> str:
    """A one-line, honest comparison of the real duration with the estimate."""
    if not estimate.available or estimate.average is None or estimate.average <= 0:
        return ""
    delta = (actual_seconds - estimate.average) / estimate.average * 100.0
    if abs(delta) < 5:
        return f"Actual time {actual_seconds:.1f}s - about the same as estimated."
    if delta < 0:
        return f"Actual time {actual_seconds:.1f}s - {abs(delta):.0f}% faster than estimated."
    return f"Actual time {actual_seconds:.1f}s - {delta:.0f}% slower than estimated."


class Ticker:
    """Elapsed time and ETA for a build whose real fraction is known."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._start = clock()

    def elapsed(self) -> float:
        return max(0.0, self._clock() - self._start)

    def eta(self, fraction: float | None) -> float | None:
        """Remaining seconds, or None when there is no honest fraction yet."""
        if fraction is None or fraction <= 0 or fraction >= 1:
            return None
        elapsed = self.elapsed()
        if elapsed <= 0:
            return None
        return max(0.0, elapsed / fraction - elapsed)

    def summary(self, fraction: float | None) -> str:
        """A compact "elapsed / eta" fragment for a status line."""
        elapsed = self.elapsed()
        eta = self.eta(fraction)
        if eta is None:
            return f"elapsed {elapsed:.0f}s"
        return f"elapsed {elapsed:.0f}s, ~{eta:.0f}s left"
