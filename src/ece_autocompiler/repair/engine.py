"""The repair engine: analyse, propose, apply, rebuild, verify, roll back.

The engine never claims a repair worked without rebuilding: after every change
the project is compiled again. A change that does not remove the error it
targeted is rolled back, and the original file is always kept as a backup.

Safety rules that are enforced here:

* only proposals with high confidence are applied automatically;
* the original file is copied to a backup folder before it is touched;
* a repair that does not fix the targeted error is undone;
* a repair that makes the test suite fail is undone;
* the number of attempts and the total time are limited;
* the same error twice in a row stops the loop.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..config import AppConfig
from ..local_store import append_jsonl, write_text_atomic
from ..logging_config import get_logger
from .analysis import FailureAnalysis, analyze
from .diagnostics import add_context, parse_diagnostics
from .rules import CONFIDENCE_HIGH, RepairProposal, propose_repairs

log = get_logger("repair")

#: Default limits.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_TIMEOUT_SECONDS = 300.0


@dataclass
class BuildOutcome:
    """What one build produced."""

    ok: bool = False
    output: str = ""
    exit_code: int = -1
    errors: list[str] = field(default_factory=list)
    log_path: Path | None = None


@dataclass
class RepairAttempt:
    """One attempt to fix one error."""

    index: int = 0
    rule_id: str = ""
    file: str = ""
    title: str = ""
    diff: str = ""
    applied: bool = False
    verification: str = ""       # resolved | not-resolved | rolled-back | tests-failed
    files_modified: list[str] = field(default_factory=list)
    error_before: str = ""
    error_after: str = ""
    timestamp: str = ""

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "rule_id": self.rule_id,
            "file": self.file,
            "title": self.title,
            "diff": self.diff,
            "applied": self.applied,
            "verification": self.verification,
            "files_modified": list(self.files_modified),
            "error_before": self.error_before,
            "error_after": self.error_after,
            "timestamp": self.timestamp,
        }


@dataclass
class RepairOutcome:
    """The result of a repair run."""

    ok: bool = False
    final_ok: bool = False
    attempts: list[RepairAttempt] = field(default_factory=list)
    message: str = ""
    analysis: FailureAnalysis | None = None
    proposals: list[RepairProposal] = field(default_factory=list)
    manual_actions: list[str] = field(default_factory=list)
    journal_path: Path | None = None
    reverted: bool = False

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "final_ok": self.final_ok,
            "message": self.message,
            "attempts": [a.as_dict() for a in self.attempts],
            "analysis": self.analysis.as_dict() if self.analysis else {},
            "proposals": [p.as_dict() for p in self.proposals],
            "manual_actions": list(self.manual_actions),
            "journal_path": str(self.journal_path) if self.journal_path else "",
            "reverted": self.reverted,
        }

    def text(self) -> str:
        lines = [self.message]
        for attempt in self.attempts:
            state = {
                "resolved": "fixed",
                "not-resolved": "did not fix the error, change reverted",
                "rolled-back": "rolled back",
                "tests-failed": "the tests failed, change reverted",
                "skipped": "not applied",
            }.get(attempt.verification, attempt.verification)
            lines.append(
                f"  {attempt.index}. {attempt.title} -> {state} ({attempt.file})"
            )
            if attempt.diff:
                lines.append(attempt.diff.rstrip())
        for action in self.manual_actions:
            lines.append(f"  -> {action}")
        return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RepairEngine:
    """Analyses build failures and repairs the deterministic ones."""

    def __init__(self, config: AppConfig | None = None, *, work_dir: Path | None = None) -> None:
        self.config = config or AppConfig()
        self.work_dir = Path(work_dir) if work_dir is not None else Path(self.config.work_dir)

    # ------------------------------------------------------------------
    def analyze_output(
        self,
        output: str,
        project_dir: Path,
        *,
        compiler: str = "",
        exit_code: int = -1,
        errors: list[str] | None = None,
    ) -> FailureAnalysis:
        """Parse *output* and group the problems into root causes."""
        diagnostics = parse_diagnostics(output, compiler)
        add_context(diagnostics, project_dir)
        result = analyze(diagnostics, exit_code=exit_code, errors=errors)
        return result

    def propose(
        self,
        analysis: FailureAnalysis,
        project_dir: Path,
        *,
        output: str = "",
    ) -> list[RepairProposal]:
        """Propose repairs for the analysed failures."""
        return propose_repairs(analysis.diagnostics, Path(project_dir), output=output)

    # ------------------------------------------------------------------
    def _backup_dir(self, run_id: str) -> Path:
        return self.work_dir / "repair-backups" / run_id

    def _backup(self, path: Path, run_dir: Path) -> Path:
        """Copy the original file into the backup folder; return the backup."""
        target = run_dir / "files" / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        return target

    def _restore(self, path: Path, backup: Path) -> bool:
        try:
            shutil.copy2(backup, path)
            return True
        except OSError as exc:
            log.warning("Could not restore %s: %s", path, exc)
            return False

    def _journal(self, attempt: RepairAttempt) -> Path:
        path = self.work_dir / "repair-journal.jsonl"
        append_jsonl(path, attempt.as_dict())
        return path

    def _run_tests(self, tests_command: list[str], project_dir: Path) -> tuple[bool, str]:
        """Run the user's test command; returns (ok, output)."""
        from ..build.process import run_single

        result = run_single(tests_command, Path(project_dir), timeout=900)
        return result.ok, "\n".join(result.errors[-20:] + result.step_results[-1].output_lines[-20:])

    # ------------------------------------------------------------------
    def repair(
        self,
        project_dir: Path,
        build_runner,
        *,
        adapter_id: str = "",
        compiler: str = "",
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        apply: bool = True,
        tests_command: list[str] | None = None,
        progress=None,
    ) -> RepairOutcome:
        """Find and fix the errors of a failed build.

        *build_runner* is called without arguments and must return a
        :class:`BuildOutcome`; it is called once per verification, so the
        caller decides exactly what a "build" means for this project.
        """
        project_dir = Path(project_dir)
        outcome = RepairOutcome()
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = self._backup_dir(run_id)

        def report(message: str) -> None:
            if progress:
                progress(message)

        started = time.monotonic()
        build = build_runner()
        if build.ok:
            outcome.ok = True
            outcome.final_ok = True
            outcome.message = "The build succeeds; there is nothing to repair."
            return outcome

        seen_signatures: set[str] = set()
        for index in range(1, max(1, int(max_attempts)) + 1):
            if time.monotonic() - started > timeout:
                outcome.message = (
                    "The repair run reached its time limit before the build succeeded."
                )
                break

            analysis = self.analyze_output(
                build.output,
                project_dir,
                compiler=compiler,
                exit_code=build.exit_code,
                errors=build.errors,
            )
            outcome.analysis = analysis
            outcome.manual_actions = analysis.manual_actions()
            proposals = self.propose(analysis, project_dir, output=build.output)
            outcome.proposals = proposals

            usable = [p for p in proposals if p.confidence == CONFIDENCE_HIGH]
            if not proposals:
                outcome.message = (
                    "No automatic repair is safe for this error. The analysis below "
                    "says what to fix."
                )
                break
            if not usable:
                outcome.message = (
                    "Only uncertain repairs were found. They are shown below but were "
                    "not applied."
                )
                break

            proposal = usable[0]
            signature = analysis.first_error.signature() if analysis.first_error else ""
            if signature and signature in seen_signatures:
                outcome.message = (
                    "The same error appeared again after a repair; the loop was "
                    "stopped so the project is not modified endlessly."
                )
                break
            if signature:
                seen_signatures.add(signature)

            attempt = RepairAttempt(
                index=index,
                rule_id=proposal.rule_id,
                file=str(proposal.file),
                title=proposal.title,
                diff=proposal.diff,
                error_before=analysis.first_error.text() if analysis.first_error else "",
                timestamp=_now(),
            )
            outcome.attempts.append(attempt)

            if not apply:
                attempt.verification = "skipped"
                outcome.message = "The repair was not applied (dry run)."
                break

            backup = self._backup(proposal.file, run_dir)
            try:
                write_text_atomic(proposal.file, proposal.full_text)
            except OSError as exc:
                attempt.verification = "not-resolved"
                outcome.message = f"The change could not be written: {exc}"
                break
            attempt.applied = True
            attempt.files_modified = [str(proposal.file)]

            report(f"Repair {index}: {proposal.title}")
            build = build_runner()

            if build.ok:
                attempt.verification = "resolved"
                attempt.error_after = ""
                outcome.ok = True
                outcome.final_ok = True
                outcome.message = (
                    f"The build succeeds after {index} repair(s). The change was kept."
                )
                self._journal(attempt)
                break

            new_analysis = self.analyze_output(
                build.output,
                project_dir,
                compiler=compiler,
                exit_code=build.exit_code,
                errors=build.errors,
            )
            still_there = _still_failing(new_analysis, analysis, proposal)
            if still_there:
                attempt.verification = "not-resolved"
                attempt.error_after = (
                    new_analysis.first_error.text() if new_analysis.first_error else ""
                )
                outcome.reverted = True
                self._restore(proposal.file, backup)
                outcome.message = (
                    "The change did not fix the error, so it was reverted. The original "
                    "file is untouched."
                )
                self._journal(attempt)
                break

            attempt.verification = "resolved"
            attempt.error_after = (
                new_analysis.first_error.text() if new_analysis.first_error else ""
            )
            self._journal(attempt)

            if tests_command:
                tests_ok, tests_output = self._run_tests(tests_command, project_dir)
                if not tests_ok:
                    attempt.verification = "tests-failed"
                    outcome.reverted = True
                    self._restore(proposal.file, backup)
                    outcome.message = (
                        "The repair compiled but the test suite failed, so the change "
                        "was reverted."
                    )
                    self._journal(attempt)
                    break
            # The targeted error is gone: keep the change and look for the next one.

        if not outcome.message:
            if outcome.final_ok:
                outcome.message = "The build succeeds."
            else:
                outcome.message = (
                    "The build still fails and no further safe repair was available."
                )
        if outcome.attempts and outcome.journal_path is None:
            outcome.journal_path = self.work_dir / "repair-journal.jsonl"
        return outcome


def _still_failing(
    new_analysis: FailureAnalysis,
    old_analysis: FailureAnalysis,
    proposal: RepairProposal,
) -> bool:
    """True when the error the proposal targeted is still reported.

    When the new output cannot be parsed at all, the repair cannot be
    verified; the safe answer is to undo the change.
    """
    old_error = old_analysis.first_error
    if old_error is None:
        return False
    new_error = new_analysis.first_error
    if new_error is None:
        return True
    if new_error.signature() == old_error.signature():
        return True
    # The same problem reported at another place is still the same problem.
    if (
        new_error.file == old_error.file
        and new_error.code == old_error.code
        and new_error.message == old_error.message
    ):
        return True
    return False
