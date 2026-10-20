"""Environment doctor (item 27).

Answers one question honestly: *can this machine build things with this tool?*
Each check reports a status (``ok`` / ``warn`` / ``error``), a plain-language
detail and a concrete fix. When a toolchain is missing the fix includes the
copyable install command from :mod:`ece_autocompiler.toolchain_hints`.

Nothing here installs software or changes the machine: every check is read-only
apart from writing a tiny probe file inside the tool's own data directory.
"""

from __future__ import annotations

import importlib.util
import os
import platform as platform_module
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig
from ..local_store import app_data_dir, config_dir
from ..toolchain_hints import hint_lines
from ..toolchain_versions import toolchain_versions as tool_version_rows

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_ERROR = "error"


@dataclass
class CheckResult:
    """One environment check."""

    key: str
    name: str
    status: str
    detail: str = ""
    fix: str = ""
    commands: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "fix": self.fix,
            "commands": list(self.commands),
        }

    def lines(self) -> list[str]:
        marker = {STATUS_OK: "[ok]  ", STATUS_WARN: "[warn]", STATUS_ERROR: "[FAIL]"}[self.status]
        out = [f"{marker} {self.name}: {self.detail}"]
        if self.fix and self.status != STATUS_OK:
            out.append(f"         fix: {self.fix}")
        out.extend(f"         $ {cmd}" for cmd in self.commands)
        return out


@dataclass
class DoctorReport:
    """The full result of a doctor run."""

    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.status != STATUS_ERROR for c in self.checks)

    def count(self, status: str) -> int:
        return sum(1 for c in self.checks if c.status == status)

    @property
    def summary(self) -> str:
        if self.ok:
            return (
                f"Environment looks usable: {self.count(STATUS_OK)} check(s) passed, "
                f"{self.count(STATUS_WARN)} warning(s)."
            )
        return (
            f"{self.count(STATUS_ERROR)} blocking problem(s), "
            f"{self.count(STATUS_WARN)} warning(s)."
        )

    def lines(self) -> list[str]:
        out = [self.summary]
        for check in self.checks:
            out.extend(check.lines())
        return out

    def text(self) -> str:
        return "\n".join(self.lines())

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "errors": self.count(STATUS_ERROR),
            "warnings": self.count(STATUS_WARN),
            "checks": [c.to_dict() for c in self.checks],
        }


def _writable(directory: Path) -> tuple[bool, str]:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".ece-write-probe"
        probe.write_text("probe", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True, str(directory)
    except OSError as exc:
        return False, f"{directory} ({exc})"


def _check_platform() -> CheckResult:
    machine = platform_module.machine().lower()
    is_windows = sys.platform.startswith("win")
    is_64 = machine in ("amd64", "x86_64")
    detail = f"{sys.platform} / {platform_module.release()} / {machine}"
    if not is_windows:
        return CheckResult(
            "platform", "Operating system", STATUS_ERROR, detail,
            fix="This tool builds Windows x64 binaries and only runs on Windows 10/11 x64.",
        )
    if not is_64:
        return CheckResult(
            "platform", "Operating system", STATUS_WARN, detail,
            fix="A 32-bit Python cannot produce win-x64 output; install 64-bit Python.",
        )
    return CheckResult("platform", "Operating system", STATUS_OK, detail)


def _check_python() -> CheckResult:
    version = platform_module.python_version()
    detail = f"Python {version} ({sys.executable})"
    if sys.version_info[:2] < (3, 10):
        return CheckResult(
            "python", "Python runtime", STATUS_ERROR, detail,
            fix="This tool needs Python 3.10 or newer.",
            commands=hint_lines("python"),
        )
    return CheckResult("python", "Python runtime", STATUS_OK, detail)


def _check_pyinstaller() -> CheckResult:
    found = importlib.util.find_spec("PyInstaller") is not None
    if found:
        return CheckResult(
            "pyinstaller", "PyInstaller (for .exe export)", STATUS_OK,
            "PyInstaller is importable with this Python",
        )
    return CheckResult(
        "pyinstaller", "PyInstaller (for .exe export)", STATUS_WARN,
        "not installed: .exe export is unavailable, .ece packaging still works",
        fix="Install PyInstaller with the same Python that runs this tool.",
        commands=hint_lines("pyinstaller"),
    )


def _check_writable(key: str, name: str, directory: Path, *, level: str = STATUS_ERROR) -> CheckResult:
    ok, detail = _writable(directory)
    if ok:
        return CheckResult(key, name, STATUS_OK, f"writable: {detail}")
    return CheckResult(
        key, name, level, f"not writable: {detail}",
        fix="Grant write access to this folder or point the setting somewhere writable.",
    )


def _check_long_paths() -> CheckResult:
    key, name = "long_paths", "Long path support"
    if not sys.platform.startswith("win"):
        return CheckResult(key, name, STATUS_WARN, "only meaningful on Windows")
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\FileSystem",
        ) as handle:
            value, _ = winreg.QueryValueEx(handle, "LongPathsEnabled")
        if int(value) == 1:
            return CheckResult(key, name, STATUS_OK, "LongPathsEnabled = 1 (paths beyond 260 chars allowed)")
        return CheckResult(
            key, name, STATUS_WARN, "LongPathsEnabled is 0; long paths are limited to 260 characters",
            fix="Keep your project in a short folder, or enable long paths in Windows.",
        )
    except FileNotFoundError:
        return CheckResult(
            key, name, STATUS_WARN, "LongPathsEnabled is not set (paths limited to 260 characters)",
            fix="Keep your project in a short folder, or enable long paths in Windows.",
        )
    except OSError as exc:
        return CheckResult(key, name, STATUS_WARN, f"could not read the setting ({exc})")


def _check_toolchains(tool_ids: list[str] | None) -> list[CheckResult]:
    results: list[CheckResult] = []
    for row in tool_version_rows(tool_ids):
        if row.status == "missing":
            results.append(
                CheckResult(
                    f"tool:{row.tool_id}", row.name, STATUS_WARN,
                    "not found on PATH",
                    fix="Install it if you intend to build this kind of project.",
                    commands=hint_lines(row.tool_id),
                )
            )
        elif row.status == "too-old":
            minimum = ".".join(str(p) for p in (row.minimum or ()))
            results.append(
                CheckResult(
                    f"tool:{row.tool_id}", row.name, STATUS_WARN,
                    f"found {row.version_text or 'an unknown version'}, need >= {minimum}",
                    fix=row.note or f"Update {row.name} to version {minimum} or newer.",
                )
            )
        else:
            results.append(
                CheckResult(
                    f"tool:{row.tool_id}", row.name, STATUS_OK,
                    row.version_text or "found",
                )
            )
    return results


def run_checks(*, config: AppConfig | None = None, tool_ids: list[str] | None = None) -> DoctorReport:
    """Run every environment check and return the report."""
    config = config or AppConfig()
    checks = [
        _check_platform(),
        _check_python(),
        _check_pyinstaller(),
        _check_writable("app_data", "Application data folder", app_data_dir()),
        _check_writable("config_dir", "Settings folder", config_dir(), level=STATUS_WARN),
        _check_writable("output_dir", "Build output folder", config.output_dir),
        _check_writable("work_dir", "Temporary work folder", config.work_dir),
        _check_writable("temp_dir", "System temporary folder", Path(os.environ.get("TEMP", ".")), level=STATUS_WARN),
        _check_long_paths(),
    ]
    checks.extend(_check_toolchains(tool_ids))
    return DoctorReport(checks=checks)
