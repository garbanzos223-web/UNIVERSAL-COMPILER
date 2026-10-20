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
import os
import shutil
import subprocess
import sys
import time
import zipfile
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


def _run_cli(
    exe: Path,
    args: list[str],
    *,
    env: dict[str, str],
    timeout: int = 300,
    runner=None,
) -> tuple[int, str]:
    """Run the shipped CLI and return ``(exit_code, output)``."""
    run = runner or subprocess.run
    try:
        proc = run(
            [str(exe), *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"could not be run: {exc}"
    return proc.returncode, f"{proc.stdout or ''}{proc.stderr or ''}".strip()


def functional_probe(
    cli_exe: Path,
    *,
    work: Path,
    runner=None,
    timeout: int = 300,
) -> tuple[bool, list[str]]:
    """Build, validate and package a throwaway project with the shipped CLI.

    This is the check that proves the product works, not just that it starts:
    the console build has to produce a real container, validate it, and create a
    real distributable package.
    """
    lines: list[str] = []
    project = work / "demo"
    project.mkdir(parents=True, exist_ok=True)
    (project / "app.py").write_text("print('release probe')\n", encoding="utf-8")
    container = work / "demo.ece"
    packages = work / "packages"
    # The probe must be repeatable: the files of a previous run are removed
    # and the commands are allowed to overwrite their own output.
    if container.exists():
        container.unlink()
    if packages.is_dir():
        shutil.rmtree(packages, ignore_errors=True)

    env = os.environ.copy()
    env["ECE_AUTOCOMPILER_HOME"] = str(work / "home")

    code, output = _run_cli(
        cli_exe,
        ["build", str(project), "-o", str(container), "--consent", "--overwrite"],
        env=env,
        timeout=timeout,
        runner=runner,
    )
    if code != 0 or not container.is_file():
        lines.append(f"build failed (exit {code}): {output[-300:]}")
        return False, lines
    lines.append(f"built {container.name} ({container.stat().st_size} bytes)")

    code, output = _run_cli(
        cli_exe, ["validate", str(container), "--deep"], env=env, timeout=timeout, runner=runner
    )
    if code != 0:
        lines.append(f"validate failed (exit {code}): {output[-300:]}")
        return False, lines
    lines.append("validated the container (deep)")

    code, output = _run_cli(
        cli_exe,
        [
            "package", str(project), "--output-dir", str(packages),
            "--format", "zip", "--overwrite",
        ],
        env=env,
        timeout=timeout,
        runner=runner,
    )
    archives = sorted(packages.glob("*.zip"))
    if code != 0 or not archives:
        lines.append(f"package failed (exit {code}): {output[-300:]}")
        return False, lines
    with zipfile.ZipFile(archives[0]) as archive:
        names = archive.namelist()
    if "package-manifest.json" not in names:
        lines.append("the package has no manifest")
        return False, lines
    lines.append(f"packaged {archives[0].name} with its manifest")
    return True, lines


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
    functional: bool = False,
    functional_runner=None,
    work: Path | None = None,
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

    if functional and cli.path.is_file():
        if not _is_windows_executable(cli.path):
            cli.add(
                "builds, validates and packages a project",
                True,
                "skipped (not a Windows executable)",
            )
        else:
            probe_dir = work or (dist / "release-probe")
            try:
                probe_dir.mkdir(parents=True, exist_ok=True)
                ok, lines = functional_probe(
                    cli.path, work=probe_dir, runner=functional_runner
                )
            except OSError as exc:
                ok, lines = False, [f"the probe folder could not be used: {exc}"]
            cli.add("builds, validates and packages a project", ok, "; ".join(lines))

    return [gui, cli]


def _is_windows_executable(path: Path) -> bool:
    """True when the file starts with the MZ signature of a Windows program."""
    try:
        with open(path, "rb") as handle:
            return handle.read(2) == b"MZ"
    except OSError:
        return False


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
    parser.add_argument(
        "--skip-functional",
        action="store_true",
        help="do not build, validate and package a throwaway project with the CLI",
    )
    args = parser.parse_args(argv)

    dist = Path(args.dist)
    artifacts = check_artifacts(
        dist,
        skip_launch=args.skip_launch,
        wait_seconds=args.wait,
        functional=not args.skip_functional,
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
