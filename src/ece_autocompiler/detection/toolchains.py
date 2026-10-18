"""Toolchain discovery.

Detects which compilers, interpreters and build tools are installed, where they
live and what version they report. Nothing here installs software: discovery is
read-only and only runs each tool's own ``--version``-style flag with a short
timeout. Automated installation, if ever added, must go through the explicit
consent workflow in :mod:`ece_autocompiler.build`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache

from ..logging_config import get_logger

log = get_logger("detection.toolchains")

VERSION_TIMEOUT = 8


@dataclass
class ToolInfo:
    id: str
    name: str
    found: bool = False
    path: str | None = None
    version: str | None = None
    error: str | None = None
    # Official documentation for installing the tool, when it is missing.
    install_hint: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "found": self.found,
            "path": self.path,
            "version": self.version,
            "error": self.error,
            "install_hint": self.install_hint,
        }


@dataclass(frozen=True)
class _Spec:
    id: str
    name: str
    # Candidate executable names; the first that resolves wins.
    executables: tuple[str, ...]
    version_args: tuple[str, ...] = ("--version",)
    # Some tools print the version to stderr (java, cl, gcc).
    errors_to_stdout: bool = False
    install_hint: str = ""
    # When true, require the tool to report an SDK, not just a runtime.
    sdk_flag: tuple[str, ...] = ()


SPECS: tuple[_Spec, ...] = (
    _Spec("python", "Python", ("python", "python3", "py"), install_hint="https://www.python.org/downloads/"),
    _Spec("node", "Node.js", ("node",), install_hint="https://nodejs.org/"),
    _Spec("npm", "npm", ("npm", "npm.cmd"), install_hint="https://nodejs.org/"),
    _Spec(
        "dotnet",
        ".NET SDK",
        ("dotnet",),
        sdk_flag=("--list-sdks",),
        errors_to_stdout=True,
        install_hint="https://dotnet.microsoft.com/download",
    ),
    _Spec("java", "Java runtime", ("java",), version_args=("-version",), install_hint="https://adoptium.net/"),
    _Spec("javac", "Java compiler", ("javac",), version_args=("-version",), install_hint="https://adoptium.net/"),
    _Spec("mvn", "Apache Maven", ("mvn", "mvn.cmd"), version_args=("-v",), install_hint="https://maven.apache.org/"),
    _Spec("gradle", "Gradle", ("gradle", "gradle.bat"), version_args=("-v",), install_hint="https://gradle.org/"),
    _Spec("cargo", "Cargo", ("cargo",), install_hint="https://rustup.rs/"),
    _Spec("rustc", "Rust compiler", ("rustc",), install_hint="https://rustup.rs/"),
    _Spec("go", "Go", ("go",), version_args=("version",), install_hint="https://go.dev/dl/"),
    _Spec("cmake", "CMake", ("cmake",), install_hint="https://cmake.org/download/"),
    _Spec("ninja", "Ninja", ("ninja",), install_hint="https://ninja-build.org/"),
    _Spec("gcc", "GCC (C)", ("gcc",), install_hint="https://www.mingw-w64.org/"),
    _Spec("gpp", "G++ (C++)", ("g++",), install_hint="https://www.mingw-w64.org/"),
    _Spec("clang", "Clang", ("clang",), install_hint="https://releases.llvm.org/"),
    _Spec("cl", "MSVC (cl.exe)", ("cl",), errors_to_stdout=True, install_hint="https://visualstudio.microsoft.com/"),
    _Spec("msbuild", "MSBuild", ("msbuild",), install_hint="https://visualstudio.microsoft.com/"),
    _Spec("make", "Make", ("make", "mingw32-make"), install_hint="https://www.mingw-w64.org/"),
    _Spec("php", "PHP", ("php",), install_hint="https://www.php.net/downloads"),
    _Spec("git", "Git", ("git",), install_hint="https://git-scm.com/"),
)


def _run_version(exe_path: str, args: tuple[str, ...], to_stdout: bool) -> str | None:
    # On Windows a .cmd/.bat wrapper (npm, gradle, mvn) cannot be launched
    # directly by CreateProcess; it must go through the command interpreter.
    command = [exe_path, *args]
    if os.name == "nt" and exe_path.lower().endswith((".cmd", ".bat")):
        command = ["cmd", "/c", exe_path, *args]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT,
            shell=False,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("Version probe failed for %s: %s", exe_path, exc)
        return None

    primary = completed.stdout if to_stdout else (completed.stdout or completed.stderr)
    text = (primary or completed.stderr or completed.stdout or "").strip()
    if not text:
        return None
    first_line = text.splitlines()[0].strip()
    return first_line[:200] if first_line else None


def _candidate_paths(name: str) -> list[str]:
    """Executable names to try, preferred first.

    On Windows, shutil.which may resolve an extensionless shim file ahead of
    the .cmd wrapper that CreateProcess can actually run, so the wrapper is
    preferred explicitly.
    """
    if os.name == "nt":
        return [name + ".cmd", name + ".bat", name + ".exe", name]
    return [name]


def _discover_one(spec: _Spec) -> ToolInfo:
    info = ToolInfo(id=spec.id, name=spec.name, install_hint=spec.install_hint)
    path = None
    for candidate in spec.executables:
        for name in _candidate_paths(candidate):
            resolved = shutil.which(name)
            if resolved:
                path = resolved
                break
        if path:
            break
    if not path:
        info.error = "Not found on PATH"
        return info

    info.path = path

    if spec.sdk_flag:
        version = _run_version(path, spec.sdk_flag, True)
        if version:
            info.found = True
            # --list-sdks prints "8.0.100 [C:\Program Files\dotnet\sdk]"
            info.version = version.split(" [")[0].strip()
        else:
            info.error = "Runtime present but no SDK found"
        return info

    version = _run_version(path, spec.version_args, spec.errors_to_stdout)
    info.found = True
    info.version = version
    return info


@lru_cache(maxsize=1)
def discover_all() -> dict[str, ToolInfo]:
    """Return every known tool keyed by id. Cached for the process lifetime."""
    result: dict[str, ToolInfo] = {}
    for spec in SPECS:
        result[spec.id] = _discover_one(spec)
    return result


def get_tool(tool_id: str) -> ToolInfo | None:
    return discover_all().get(tool_id)


def refresh() -> dict[str, ToolInfo]:
    discover_all.cache_clear()
    _discover_one.cache_clear() if hasattr(_discover_one, "cache_clear") else None
    return discover_all()


def missing_tools(tool_ids: list[str]) -> list[ToolInfo]:
    tools = discover_all()
    return [tools[t] for t in tool_ids if t in tools and not tools[t].found]


def available_tools() -> list[ToolInfo]:
    return [t for t in discover_all().values() if t.found]
