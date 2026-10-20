"""The copyable end-of-build summary (items 8, 34, 37, 38).

* item 8  - how long the build took and what it produced;
* item 34 - one block of text the user can copy and paste anywhere;
* item 37 - an explicit proof-of-work list: only what actually happened is
  claimed, and anything the tool did *not* do is stated as well;
* item 38 - every line that can contain tool output is passed through redaction
  before it is shown or saved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..logging_config import redact


def human_size(num_bytes: int) -> str:
    """Format a byte count for humans (decimal units, Windows convention)."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} TB"  # pragma: no cover - unreachable, kept for clarity


def human_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes} min {rest} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


@dataclass
class BuildSummary:
    """A stable, copyable account of one finished build."""

    ok: bool
    mode: str = "ece"
    project: str = ""
    adapter_id: str = ""
    label: str = ""
    output_path: str = ""
    output_size: int = 0
    artifact_count: int = 0
    duration_seconds: float = 0.0
    estimated_seconds: float | None = None
    estimate_note: str = ""
    started: str = ""
    finished: str = ""
    step_count: int = 0
    sha256: str = ""
    verified: str = ""            # "", "ok", "failed", "not-checked"
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    proof: list[str] = field(default_factory=list)
    log_path: str = ""

    @property
    def output_kind(self) -> str:
        return "exe" if self.mode == "exe" else "ece"

    def lines(self) -> list[str]:
        lines = [f"Build summary ({'OK' if self.ok else 'FAILED'})"]
        if self.project:
            lines.append(f"  Project : {self.project}")
        if self.adapter_id or self.label:
            lines.append(f"  Type    : {self.label or self.adapter_id}"
                         + (f" ({self.adapter_id})" if self.adapter_id and self.label else ""))
        if self.duration_seconds:
            lines.append(f"  Duration: {human_duration(self.duration_seconds)}")
        if self.estimated_seconds:
            lines.append(
                f"  Estimate: {human_duration(self.estimated_seconds)} "
                "(from your local history)"
            )
        if self.estimate_note:
            lines.append(f"  Timing: {redact(self.estimate_note)}")
        if self.ok:
            if self.output_path:
                size = f" ({human_size(self.output_size)})" if self.output_size else ""
                lines.append(f"  Output  : {self.output_path}{size}")
            if self.sha256:
                lines.append(f"  SHA-256 : {self.sha256}")
            if self.verified:
                lines.append(f"  Checked : {self.verified}")
        for item in self.proof:
            lines.append(f"  - {redact(item)}")
        for item in self.warnings:
            lines.append(f"  Warning : {redact(item)}")
        for item in self.notes:
            lines.append(f"  Note    : {redact(item)}")
        return lines

    def text(self) -> str:
        return "\n".join(self.lines())

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "project": self.project,
            "adapter_id": self.adapter_id,
            "label": self.label,
            "output_path": self.output_path,
            "output_size": self.output_size,
            "artifact_count": self.artifact_count,
            "duration_seconds": self.duration_seconds,
            "estimated_seconds": self.estimated_seconds,
            "estimate_note": self.estimate_note,
            # Item 34's stable JSON names. A finished summary has no time left,
            # so eta_s is null rather than a made-up zero.
            "estimate_s": self.estimated_seconds,
            "elapsed_s": self.duration_seconds,
            "eta_s": None,
            "started": self.started,
            "finished": self.finished,
            "step_count": self.step_count,
            "sha256": self.sha256,
            "verified": self.verified,
            "warnings": list(self.warnings),
            "notes": list(self.notes),
            "proof": list(self.proof),
        }


def build_summary(
    *,
    ok: bool,
    mode: str = "ece",
    project: Path | str = "",
    adapter_id: str = "",
    label: str = "",
    output_path: Path | str | None = None,
    output_size: int = 0,
    artifact_count: int = 0,
    duration_seconds: float = 0.0,
    estimated_seconds: float | None = None,
    estimate_note: str = "",
    started: str = "",
    finished: str = "",
    step_count: int = 0,
    sha256: str = "",
    verified: str = "",
    warnings: list[str] | None = None,
    notes: list[str] | None = None,
    skipped_steps: list[str] | None = None,
    installed_anything: bool = False,
    log_path: Path | str | None = None,
) -> BuildSummary:
    """Assemble a summary and its honest proof-of-work list."""
    proof: list[str] = []
    if ok:
        if adapter_id or label:
            proof.append(f"Built the project with {label or adapter_id}.")
        if step_count:
            proof.append(f"Ran {step_count} build step(s).")
        if artifact_count:
            proof.append(f"Packaged {artifact_count} file(s) into the output.")
        if verified == "ok":
            proof.append("Verified the output by re-reading and hashing it.")
        elif verified == "failed":
            proof.append("WARNING: the output could NOT be verified after writing.")
        else:
            proof.append("Wrote the output but did not re-verify it.")
    if skipped_steps:
        proof.append("Skipped (by your choice): " + ", ".join(skipped_steps) + ".")
    if not installed_anything:
        proof.append("Installed nothing; no system change was made.")
    if not proof:
        proof.append("No build step completed; see the message above.")
    return BuildSummary(
        ok=ok,
        mode=mode,
        project=str(project) if project else "",
        adapter_id=adapter_id,
        label=label,
        output_path=str(output_path) if output_path else "",
        output_size=output_size,
        artifact_count=artifact_count,
        duration_seconds=duration_seconds,
        estimated_seconds=estimated_seconds,
        estimate_note=estimate_note,
        started=started,
        finished=finished,
        step_count=step_count,
        sha256=sha256,
        verified=verified,
        warnings=list(warnings or []),
        notes=list(notes or []),
        proof=proof,
        log_path=str(log_path) if log_path else "",
    )
