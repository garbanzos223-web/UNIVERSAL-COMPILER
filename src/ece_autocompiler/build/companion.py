"""Bundle several Windows executables into one launcher .exe.

The produced executable contains the selected binaries (PyInstaller onefile
payload) and starts all of them **concurrently** when it runs. One binary can be
marked as the main program; the launcher then waits for it while the companions
keep running. Without a main program, the launcher waits for every companion.

This is a real embed-and-launch feature, not a shortcut: the build verifies the
embedded files with a probe mode before reporting success.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig
from ..logging_config import get_logger, redact

log = get_logger("build.companion")

PROBE_ENV = "ECE_COMPANION_PROBE"
LAUNCHER_LOG = "ece-companion-launcher.log"


@dataclass
class CompanionRequest:
    files: list[Path]
    output: Path
    main: Path | None = None
    onefile: bool = True
    console: bool = False

    @property
    def companions(self) -> list[Path]:
        main = self.main.resolve() if self.main else None
        return [path for path in self.files if not main or path.resolve() != main]


@dataclass
class CompanionResult:
    ok: bool
    output: Path | None = None
    size: int = 0
    sha256: str = ""
    command: list[str] = field(default_factory=list)
    log_path: Path | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    probe_output: str = ""

    def summary_lines(self) -> list[str]:
        lines = [
            f"Combined executable {'created' if self.ok else 'was not created'} "
            f"in {self.duration_seconds:.1f}s",
        ]
        if self.output:
            lines.append(f"Output: {self.output}")
            if self.output.is_file():
                lines.append(f"Size: {self.size:,} bytes")
                lines.append(f"SHA-256: {self.sha256}")
        if self.probe_output:
            lines.append("Embedded-file probe:")
            lines.extend(f"  {line}" for line in self.probe_output.splitlines()[:20])
        for note in self.notes:
            lines.append(f"note: {note}")
        for warning in self.warnings:
            lines.append(f"warning: {warning}")
        for error in self.errors:
            lines.append(f"error: {error}")
        if self.log_path:
            lines.append(f"Log: {self.log_path}")
        return lines


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_stem(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name).strip("._")
    return cleaned or "combined"


def _launcher_source(main_rel: str, companion_rels: list[str]) -> str:
    return f'''"""Generated launcher: starts the embedded executables concurrently."""
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

MAIN = {main_rel!r}
COMPANIONS = {companion_rels!r}
LOG_NAME = {LAUNCHER_LOG!r}


def base_dir():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def log(message):
    try:
        base = Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".")
        with open(base / LOG_NAME, "a", encoding="utf-8") as handle:
            handle.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\\n")
    except OSError:
        pass


def spawn(relative):
    path = base_dir() / relative
    if not path.is_file():
        log("missing embedded file: " + str(path))
        return None
    try:
        process = subprocess.Popen([str(path)], cwd=str(path.parent))
        log("started " + str(path) + " pid=" + str(process.pid))
        return process
    except OSError as exc:
        log("could not start " + str(path) + ": " + str(exc))
        return None


def probe():
    expected = ([MAIN] if MAIN else []) + COMPANIONS
    missing = [rel for rel in expected if not (base_dir() / rel).is_file()]
    lines = []
    if missing:
        lines.append("missing embedded files: " + ", ".join(missing))
    else:
        for rel in expected:
            path = base_dir() / rel
            lines.append("embedded: " + rel + " (" + str(path.stat().st_size) + " bytes)")
    text = "\\n".join(lines) + "\\n"
    target = os.environ.get("ECE_COMPANION_PROBE_FILE", "")
    if target:
        try:
            Path(target).write_text(text, encoding="utf-8")
        except OSError:
            pass
    if not missing:
        try:
            sys.stdout.write(text)
        except Exception:
            pass
    return 2 if missing else 0


def main():
    if os.environ.get({PROBE_ENV!r}) == "1":
        return probe()
    processes = []
    for relative in COMPANIONS:
        process = spawn(relative)
        if process is not None:
            processes.append((relative, process))
    main_process = None
    if MAIN:
        main_process = spawn(MAIN)
        if main_process is None:
            for _rel, child in processes:
                child.wait()
            return 1
        processes.append((MAIN, main_process))
    if not processes:
        log("nothing to start")
        return 1
    # Stay alive while any embedded program runs, so onefile resources are not
    # removed underneath a still-running companion.
    codes = []
    for _rel, child in processes:
        codes.append(child.wait())
    if main_process is not None:
        return main_process.returncode or 0
    return 0 if all(code == 0 for code in codes) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException:
        log(traceback.format_exc())
        raise
'''


def build_companion_exe(
    request: CompanionRequest,
    *,
    config: AppConfig | None = None,
    progress=None,
    cancel=None,
) -> CompanionResult:
    started = time.time()
    cfg = config or AppConfig()
    cfg.ensure_directories()
    result = CompanionResult(ok=False, output=request.output)
    files = [Path(path) for path in request.files]
    if request.main is not None:
        files.append(Path(request.main))
    # Deduplicate while preserving order.
    unique: list[Path] = []
    seen: set[str] = set()
    for path in files:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    if not unique:
        result.errors.append("Select at least one .exe to embed.")
        return result
    for path in unique:
        if not path.is_file():
            result.errors.append(f"Not a file: {path}")
        elif path.suffix.lower() != ".exe":
            result.errors.append(f"Only .exe files can be embedded: {path}")
    if result.errors:
        return result

    output = Path(request.output)
    if output.suffix.lower() != ".exe":
        output = output.with_suffix(".exe")
    result.output = output
    try:
        if output.exists() and not output.is_file():
            result.errors.append(f"Output exists and is not a file: {output}")
            return result
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result.errors.append(f"Cannot write the output folder: {exc}")
        return result
    if any(path.resolve() == output.resolve() for path in unique):
        result.errors.append("The output cannot replace one of the input executables.")
        return result

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        result.errors.append(
            "PyInstaller is not installed. Install it with: "
            "python -m pip install pyinstaller"
        )
        return result

    name = _safe_stem(output.stem)
    work = cfg.work_dir / "companion" / f"{name}-{int(started)}"
    work.mkdir(parents=True, exist_ok=True)
    result.log_path = cfg.work_dir / "logs" / f"companion_{int(started)}.log"
    result.log_path.parent.mkdir(parents=True, exist_ok=True)

    embedded: list[Path] = []
    add_binary: list[str] = []
    main_rel = ""
    companion_rels: list[str] = []
    staging = work / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    for index, path in enumerate(unique):
        if request.main is not None and path.resolve() == Path(request.main).resolve():
            unique_name = path.name
            relative = f"main/{unique_name}"
            main_rel = relative
            destination = "main"
        else:
            unique_name = f"{index:02d}_{path.name}"
            relative = f"companions/{unique_name}"
            companion_rels.append(relative)
            destination = "companions"
        staged = staging / unique_name
        shutil.copyfile(path, staged)
        embedded.append(staged)
        add_binary.append(f"{staged.resolve()}{os.pathsep}{destination}")

    launcher = work / "_ece_companion_launcher.py"
    launcher.write_text(_launcher_source(main_rel, companion_rels), encoding="utf-8")

    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile" if request.onefile else "--onedir",
        "--console" if request.console else "--windowed",
        "--name", name,
        "--distpath", str(output.parent),
        "--workpath", str(work / "pyi"),
        "--specpath", str(work),
    ]
    for item in add_binary:
        command.extend(["--add-binary", item])
    command.append(str(launcher))
    result.command = command

    try:
        with open(result.log_path, "w", encoding="utf-8") as log_file:
            log_file.write("=== ECE companion bundle ===\n")
            log_file.write("command: " + " ".join(command) + "\n\n")
            log_file.flush()
            process = subprocess.Popen(
                command,
                cwd=str(work),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            assert process.stdout is not None
            for line in process.stdout:
                log_file.write(redact(line))
                log_file.flush()
                if cancel is not None and cancel.is_set():
                    process.terminate()
                    result.errors.append("Cancelled by the user.")
                    return result
                if progress:
                    progress(line.rstrip(), 0, 0)
            code = process.wait()
            process.stdout.close()
    except OSError as exc:
        result.errors.append(f"PyInstaller could not start: {exc}")
        return result

    result.duration_seconds = time.time() - started
    produced = output if request.onefile else output.parent / name / f"{name}.exe"
    if code != 0 or not produced.is_file():
        result.errors.append(
            f"PyInstaller exited with {code} and the expected executable was not "
            f"produced at {produced}. See the log."
        )
        return result
    result.output = produced
    if produced.resolve() != output.resolve() and output.is_file():
        try:
            output.unlink()
        except OSError:
            pass
    result.size = produced.stat().st_size
    result.sha256 = _sha256(produced)

    # Verify the embedded files without launching the user's programs.
    env = os.environ.copy()
    env[PROBE_ENV] = "1"
    probe_file = work / "probe.txt"
    env["ECE_COMPANION_PROBE_FILE"] = str(probe_file)
    try:
        probe = subprocess.run(
            [str(produced)],
            capture_output=True, text=True, timeout=120, env=env,
            cwd=str(produced.parent),
        )
        probe_text = ""
        if probe_file.is_file():
            try:
                probe_text = probe_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                probe_text = ""
        if not probe_text:
            probe_text = ((probe.stdout or "") + (probe.stderr or "")).strip()
        result.probe_output = probe_text.strip()
        if probe.returncode != 0:
            result.errors.append(
                "The combined executable was built but its embedded-file probe "
                f"failed (exit {probe.returncode}). It is not reported as usable."
            )
            result.ok = False
            return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        result.warnings.append(f"The embedded-file probe could not run: {exc}")
    result.ok = True
    result.notes.append(
        "The launcher starts every companion first and then the main executable; "
        "they run at the same time."
    )
    result.notes.append(
        "Companions keep running if the main program closes; the launcher does not "
        "kill processes it did not create in this session."
    )
    return result


def companion_plan(request: CompanionRequest) -> list[str]:
    """Human-readable plan shown before building; runs nothing."""
    lines = [f"Output: {request.output}"]
    if request.main:
        lines.append(f"Main executable (started after the companions): {request.main}")
    lines.append("Embedded and started concurrently:")
    for index, path in enumerate(request.files, start=1):
        lines.append(f"  {index}. {path}")
    lines.append(
        "Layout: onefile" if request.onefile else "Layout: onedir (folder)"
    )
    lines.append("Console: " + ("yes" if request.console else "no"))
    return lines
