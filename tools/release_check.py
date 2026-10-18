"""Verify the release artifacts built by ``scripts\\build_exe.ps1``.

PyInstaller can exit 0 while producing a bundle that dies on startup, and "the
file exists" says nothing about whether the product inside it works. This
script answers that honestly:

  * the console build must answer ``--version`` with this product and version;
  * the desktop build must start and stay alive (a window that crashed on
    startup is exactly the failure this catches);
  * both are reported with size and SHA256 for the release notes.

It writes ``<dist>\\release.json`` and exits non-zero when any check failed, so
a release is never announced on top of a broken build.

Usage:
    python tools\\release_check.py [--dist dist] [--json <file>] [--skip-launch]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from ece_autocompiler.version import APP_NAME, APP_VERSION  # noqa: E402

GUI_EXE_NAME = "ECE-AutoCompiler.exe"
CLI_EXE_NAME = "ECE-AutoCompiler-cli.exe"

CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    """Return the SHA256 of a file, read in chunks (never loads it whole)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024.0
    return f"{value:.2f} GB"  # pragma: no cover - unreachable


def check_version_output(text: str) -> tuple[bool, str]:
    """A ``--version`` run must name this product and this exact version."""
    if APP_NAME not in text:
        return False, f"output does not name {APP_NAME!r}"
    if APP_VERSION not in text:
        return False, f"output does not report version {APP_VERSION!r}"
    return True, f"{APP_NAME} {APP_VERSION}"


def verify_cli_exe(exe: Path, *, runner=None, timeout: int = 180) -> tuple[bool, str]:
    """Run ``<exe> --version`` and judge what it actually printed."""
    run = runner or subprocess.run
    try:
        proc = run(
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not be run: {exc}"

    output = f"{proc.stdout or ''}{proc.stderr or ''}".strip()
    ok, reason = check_version_output(output)
    if ok and proc.returncode != 0:
        return False, f"printed the right version but exited with {proc.returncode}"
    if not ok and output:
        reason = f"{reason}; output was {output[:200]!r}"
    return ok, reason


def launch_probe(exe: Path, *, popen=None, wait_seconds: float = 5.0) -> tuple[bool, str]:
    """Start the desktop build and confirm it is still alive after a moment.

    This proves the window came up and survived its imports; it does not
    pretend to test the UI (tests\\smoke does that against the real sources).
    The app is then shut down for real: a onefile build is a parent bootloader
    plus a child process, so killing only the parent would leave the window
    running and the .exe locked for the next build.
    """
    spawn = popen or subprocess.Popen
    external = popen is not None
    try:
        proc = spawn([str(exe)])
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not be started: {exc}"

    try:
        deadline = time.monotonic() + max(wait_seconds, 0.0)
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return False, f"exited early with code {proc.returncode}"
            time.sleep(0.1)
        return True, f"still running after {wait_seconds:g}s"
    finally:
        _stop_probe(proc, external=external)


def _stop_probe(proc, *, external: bool) -> None:
    """Shut down a probed process and everything it started."""
    if external:  # fakes used by the tests: no OS-level tree to kill
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError, AttributeError):
            pass
        return

    try:
        if proc.poll() is not None:
            return
    except OSError:  # pragma: no cover - already gone
        return

    try:
        # /T kills the whole tree (bootloader + window), /F forces it. Without
        # this the child keeps dist\ECE-AutoCompiler.exe locked.
        killed = subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
        if killed.returncode == 0:
            proc.wait(timeout=10)
            return
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - fall back below
        pass

    try:  # pragma: no cover - last resort, rarely reached on Windows
        proc.kill()
        proc.wait(timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


class ArtifactCheck:
    """One verified artifact: size, hash and the checks it had to pass."""

    def __init__(self, name: str, path: Path) -> None:
        self.name = name
        self.path = path
        self.size = 0
        self.sha256 = ""
        self.checks: list[tuple[str, bool, str]] = []

    def add(self, label: str, ok: bool, detail: str) -> None:
        self.checks.append((label, ok, detail))

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(ok for _, ok, _ in self.checks)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "size": self.size,
            "sha256": self.sha256,
            "passed": self.passed,
            "checks": [
                {"check": label, "passed": ok, "detail": detail}
                for label, ok, detail in self.checks
            ],
        }


def check_artifacts(
    dist: Path,
    *,
    skip_launch: bool = False,
    cli_runner=None,
    gui_popen=None,
    wait_seconds: float = 5.0,
) -> list[ArtifactCheck]:
    """Verify the console and desktop builds in *dist*; never raises."""
    gui = ArtifactCheck("desktop (GUI)", dist / GUI_EXE_NAME)
    cli = ArtifactCheck("console (CLI)", dist / CLI_EXE_NAME)

    for artifact, probe in ((gui, "launch"), (cli, "version")):
        if not artifact.path.is_file():
            artifact.add(
                "exists",
                False,
                f"not built: {artifact.path} is missing - run scripts\\build_exe.ps1",
            )
            continue
        artifact.size = artifact.path.stat().st_size
        artifact.sha256 = sha256_file(artifact.path)
        artifact.add("exists", True, human_size(artifact.size))

        if probe == "version":
            ok, detail = verify_cli_exe(artifact.path, runner=cli_runner)
            artifact.add("answers --version", ok, detail)
        elif skip_launch:
            artifact.add("starts", True, "skipped (--skip-launch)")
        else:
            ok, detail = launch_probe(
                artifact.path, popen=gui_popen, wait_seconds=wait_seconds
            )
            artifact.add("starts and stays alive", ok, detail)

    return [gui, cli]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release_check",
        description="Verify the built release artifacts really run.",
    )
    parser.add_argument("--dist", default=str(REPO / "dist"), help="artifact directory")
    parser.add_argument("--json", default="", help="where to write the summary JSON")
    parser.add_argument(
        "--skip-launch",
        action="store_true",
        help="do not start the desktop build (for headless machines)",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=5.0,
        help="seconds the desktop build must stay alive (default: 5)",
    )
    args = parser.parse_args(argv)

    dist = Path(args.dist)
    artifacts = check_artifacts(
        dist, skip_launch=args.skip_launch, wait_seconds=args.wait
    )

    print(f"{APP_NAME} {APP_VERSION} - release check")
    print(f"Artifacts in {dist}")
    for artifact in artifacts:
        print(f"  {artifact.name}: {artifact.path.name}")
        if artifact.size:
            print(f"    {human_size(artifact.size)}  sha256 {artifact.sha256}")
        for label, ok, detail in artifact.checks:
            mark = "OK  " if ok else "FAIL"
            print(f"    [{mark}] {label}: {detail}")

    all_ok = all(a.passed for a in artifacts)
    destination = Path(args.json) if args.json else dist / "release.json"
    payload = {
        "app": APP_NAME,
        "version": APP_VERSION,
        "dist": str(dist),
        "ok": all_ok,
        "artifacts": [a.as_dict() for a in artifacts],
    }
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Summary: {destination}")
    except OSError as exc:
        print(f"Could not write {destination}: {exc}")

    if not all_ok:
        print("What to do:")
        for artifact in artifacts:
            for label, ok, detail in artifact.checks:
                if not ok:
                    print(f"  - {artifact.name}: {label} failed ({detail})")
        print("  - rebuild with: powershell -ExecutionPolicy Bypass -File scripts\\build_exe.ps1")
        print("  - a desktop build that dies at startup logs to %TEMP%\\ece-autocompiler-crash.log")
        return 1

    print("All checks passed: both builds run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
