"""Subprocess execution with timeouts, cancellation, consent and safe logging.

Commands are always executed with argument lists (never ``shell=True``). Output
is read on a background thread into a queue so the main loop can enforce
timeouts and cancellation even when the child process is silent. Output is
redacted before it reaches the log file, and only a bounded tail is kept in
memory.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from ..config import AppConfig
from ..errors import BuildCancelledError, BuildTimeoutError
from ..logging_config import get_logger, redact

log = get_logger("build.process")

ProgressCallback = Callable[[str, int, int], None]


@dataclass
class RunStepResult:
    step_id: str
    title: str
    ok: bool = False
    exit_code: int = -1
    duration_seconds: float = 0.0
    output_lines: list[str] = field(default_factory=list)
    error_lines: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    ok: bool = False
    exit_code: int = -1
    duration_seconds: float = 0.0
    step_results: list[RunStepResult] = field(default_factory=list)
    log_path: Path | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _now() -> float:
    return time.perf_counter()


def parallel_jobs() -> str:
    """How many build jobs may run in parallel (the machine's CPU count).

    Build tools that support parallel jobs (``cmake --parallel``, ``ninja -j``,
    ``make -j``) use this value. It is capped so a huge machine does not
    exhaust its memory.
    """
    count = os.cpu_count() or 1
    return str(max(1, min(count, 64)))


def _kill_process_tree(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                proc.terminate()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _reader(stream, sink: "queue.Queue[str | None]") -> None:
    try:
        for line in iter(stream.readline, ""):
            sink.put(line)
    except Exception:  # pragma: no cover - stream closed while killing
        pass
    finally:
        sink.put(None)


def _stage_command(cmd: Sequence[str]) -> list[str]:
    """Return the argv used to start a command.

    On Windows, .cmd/.bat wrappers (npm, gradle) cannot be launched directly by
    CreateProcess, so they are routed through the command interpreter with an
    explicit argv list (still no shell string concatenation).
    """
    args = [str(c) for c in cmd]
    if os.name == "nt" and args and args[0].lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", *args]
    return args


def run_command(
    steps: Sequence,
    workdir: Path,
    *,
    progress: ProgressCallback | None = None,
    cancel=None,
    log_path: Path | None = None,
    env: dict[str, str] | None = None,
    consent_granted: bool = False,
    config: AppConfig | None = None,
) -> RunResult:
    """Execute one or more build steps in sequence."""
    cfg = config or AppConfig()
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    if log_path is None:
        cfg.ensure_directories()
        log_path = cfg.work_dir / "last_build.log"

    result = RunResult(log_path=log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    base_env = os.environ.copy()
    if env:
        base_env.update(env)

    tail_limit = cfg.limits.log_tail_lines
    start_total = _now()

    with open(log_path, "a", encoding="utf-8") as logf:
        logf.write("=== ECE Universal AutoCompiler build log ===\n")
        logf.write(f"Workdir: {workdir}\n")
        logf.write(f"Steps: {len(steps)}\n\n")

        for idx, step in enumerate(steps, start=1):
            if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                raise BuildCancelledError("Build was cancelled by the user")

            title = getattr(step, "title", "step")
            if getattr(step, "requires_approval", False) and not consent_granted:
                msg = f"Consent required for step '{title}' but was not granted"
                result.errors.append(msg)
                result.ok = False
                result.exit_code = -2
                return result

            cmd = list(getattr(step, "cmd_args", []))
            sdir = Path(getattr(step, "workdir", None) or workdir)
            sdir.mkdir(parents=True, exist_ok=True)
            timeout = (
                getattr(step, "timeout", None)
                or cfg.limits.default_build_timeout_seconds
            )
            timeout = min(int(timeout), cfg.limits.max_build_timeout_seconds)

            if progress:
                progress(f"[{idx}/{len(steps)}] {title}", idx - 1, len(steps))

            logf.write(f"\n--- STEP {idx}: {title}\n")
            logf.write(f"cmd: {cmd}\n")
            logf.write(f"workdir: {sdir}\n")
            logf.write(f"timeout: {timeout}s\n")

            step_res = RunStepResult(step_id=getattr(step, "id", str(idx)), title=title)
            sstart = _now()

            if not cmd:
                step_res.ok = True
                step_res.exit_code = 0
                result.step_results.append(step_res)
                continue

            argv = _stage_command(cmd)
            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=str(sdir),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                    text=True,
                    # Never let an undecodable byte kill the reader thread:
                    # a dead reader truncates the log while the build keeps
                    # running, which hides the real cause of a failure.
                    errors="replace",
                    env=base_env,
                    shell=False,
                )
            except FileNotFoundError as exc:
                msg = f"Executable not found: {argv[0]} - {exc}"
                log.error(msg)
                logf.write(f"ERROR: {msg}\n")
                step_res.ok = False
                step_res.exit_code = 127
                result.step_results.append(step_res)
                result.errors.append(msg)
                result.ok = False
                result.exit_code = 127
                return result
            except OSError as exc:
                msg = f"Failed to start process {argv}: {exc}"
                log.error(msg)
                logf.write(f"ERROR: {msg}\n")
                step_res.ok = False
                step_res.exit_code = 126
                result.step_results.append(step_res)
                result.errors.append(msg)
                result.ok = False
                result.exit_code = 126
                return result

            sink: "queue.Queue[str | None]" = queue.Queue()
            reader = threading.Thread(
                target=_reader, args=(proc.stdout, sink), daemon=True
            )
            reader.start()

            tail: deque[str] = deque(maxlen=tail_limit)
            cancelled = False
            timed_out = False

            while True:
                if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                    cancelled = True
                    break
                if _now() - sstart > timeout:
                    timed_out = True
                    break
                try:
                    item = sink.get(timeout=0.1)
                except queue.Empty:
                    if proc.poll() is not None and sink.empty():
                        break
                    continue
                if item is None:
                    break
                line_red = redact(item.rstrip("\r\n"))
                logf.write(line_red + "\n")
                tail.append(line_red)
                if progress and len(tail) % 20 == 0:
                    progress(line_red, idx - 1, len(steps))

            if cancelled or timed_out:
                _kill_process_tree(proc)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    proc.kill()

            reader.join(timeout=2)

            # Drain anything the reader produced after we stopped.
            while True:
                try:
                    item = sink.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    continue
                line_red = redact(item.rstrip("\r\n"))
                logf.write(line_red + "\n")
                tail.append(line_red)

            logf.flush()
            step_res.output_lines = list(tail)

            if cancelled:
                logf.write("CANCELLED\n")
                raise BuildCancelledError("Build was cancelled by the user")
            if timed_out:
                msg = f"Step timed out after {timeout}s: {title}"
                log.error(msg)
                logf.write(f"TIMEOUT after {timeout}s\n")
                raise BuildTimeoutError(msg)

            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover
                _kill_process_tree(proc)
                proc.wait(timeout=2)

            step_res.duration_seconds = round(_now() - sstart, 3)
            step_res.exit_code = proc.returncode if proc.returncode is not None else -1
            step_res.ok = step_res.exit_code == 0
            logf.write(
                f"exit_code={step_res.exit_code} duration={step_res.duration_seconds:.3f}s\n"
            )

            if not step_res.ok:
                result.errors.extend(step_res.output_lines[-50:])

            result.step_results.append(step_res)
            if not step_res.ok:
                result.ok = False
                result.exit_code = step_res.exit_code
                return result

    result.duration_seconds = round(_now() - start_total, 3)
    result.ok = True
    result.exit_code = 0
    return result


def run_single(cmd: list[str], workdir: Path, **kwargs) -> RunResult:
    from ..adapters.base import BuildPlanStep

    step = BuildPlanStep(
        id="single", title=" ".join(cmd) if cmd else "single", cmd_args=list(cmd)
    )
    return run_command([step], workdir, **kwargs)
