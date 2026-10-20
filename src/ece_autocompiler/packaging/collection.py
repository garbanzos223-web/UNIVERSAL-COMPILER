"""Collect the files that belong in a package.

This module answers three real questions:

* which files are the compiled artifacts (and the entry point that starts them);
* which files the user wants to include or exclude;
* which runtime dependencies are missing or must not be bundled.

Everything is decided from what is really on disk. When a dependency cannot be
resolved the result says so instead of pretending the package is complete.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

from ..adapters.base import Artifact
from .models import DependencyIssue, PackageFile, PackageRequest

#: Directories that never belong in a distributable package.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git/*",
    ".hg/*",
    ".svn/*",
    "__pycache__/*",
    "*/__pycache__/*",
    "node_modules/*",
    "*/node_modules/*",
    ".venv/*",
    "*/.venv/*",
    "venv/*",
    "*/venv/*",
    ".mypy_cache/*",
    ".pytest_cache/*",
    ".ruff_cache/*",
    ".idea/*",
    ".vs/*",
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.tmp",
    "*.bak",
    "Thumbs.db",
    "*.swp",
)

#: Windows and POSIX locations whose files are installed by the operating
#: system or by another application: bundling them breaks the target machine.
_SYSTEM_MARKERS = (
    "windows",
    "program files",
    "program files (x86)",
    "/usr/",
    "/bin/",
    "/sbin/",
    "/lib/",
    "/lib64/",
    "/opt/",
    "/etc/",
)

#: Executable suffixes on Windows and POSIX.
_EXECUTABLE_SUFFIXES = (".exe", ".com", ".bat", ".cmd", ".sh", ".py", ".jar", ".ps1")


def is_system_path(path: Path) -> bool:
    """True when *path* points at an operating-system or system-wide location."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    text = str(resolved).replace("\\", "/").lower()
    return any(marker in text for marker in _SYSTEM_MARKERS)


def matches_any(rel_path: str, patterns: list[str] | tuple[str, ...]) -> bool:
    """True when the POSIX *rel_path* matches one of the glob *patterns*.

    ``*`` crosses directory separators (like ``fnmatch`` does), so
    ``build/*`` matches ``build/app/app.exe`` as well.
    """
    if not patterns:
        return False
    for pattern in patterns:
        clean = pattern.strip().replace("\\", "/")
        if not clean:
            continue
        if fnmatch.fnmatch(rel_path, clean):
            return True
        # A bare name such as "*.log" should also match nested files.
        if "/" not in clean and fnmatch.fnmatch(Path(rel_path).name, clean):
            return True
        if fnmatch.fnmatch(rel_path, f"*/{clean.lstrip('/')}"):
            return True
    return False


def _is_executable(path: Path) -> bool:
    if path.suffix.lower() in _EXECUTABLE_SUFFIXES:
        return True
    if path.suffix.lower() == "" and path.name.lower() in ("makefile", "run", "start"):
        return True
    return False


def _rel_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return path.as_posix()


def _artifact_rel(artifact: Artifact, root: Path) -> str:
    rel = getattr(artifact, "rel_path", "") or ""
    if rel:
        return rel.replace("\\", "/")
    return _rel_path(artifact.path, root)


def collect_files(
    project_dir: Path,
    artifacts: list[Artifact],
    request: PackageRequest,
) -> tuple[list[PackageFile], list[DependencyIssue]]:
    """Pick the files that go into the package.

    Returns the selected files and the dependency issues found on the way
    (system files that were deliberately left out, missing runtime
    dependencies, ...).
    """
    from ..container.integrity import sha256_file

    project_dir = Path(project_dir)
    issues: list[DependencyIssue] = []
    seen: set[str] = set()
    files: list[PackageFile] = []

    excludes = list(DEFAULT_EXCLUDES) + list(request.exclude or [])
    includes = list(request.include or [])

    # (path, relative path, kind, forced) - forced files were named explicitly
    # by the user and are always kept.
    candidates: list[tuple[Path, str, str, bool]] = []
    for artifact in artifacts:
        path = Path(artifact.path)
        if not path.is_file():
            continue
        candidates.append((path, _artifact_rel(artifact, project_dir), artifact.kind, False))

    # The user's include list may point at files the adapter did not report.
    for pattern in includes:
        if any(ch in pattern for ch in "*?["):
            for path in sorted(project_dir.rglob(pattern)):
                if path.is_file():
                    candidates.append((path, _rel_path(path, project_dir), "asset", True))
        else:
            path = project_dir / pattern
            if path.is_file():
                candidates.append((path, _rel_path(path, project_dir), "asset", True))
            elif path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file():
                        candidates.append((child, _rel_path(child, project_dir), "asset", True))

    for path, rel, kind, forced in candidates:
        rel = rel.replace("\\", "/")
        if rel in seen:
            continue
        if matches_any(rel, excludes):
            continue
        if includes and not forced and not matches_any(rel, includes):
            # An include list restricts the package: only matching files stay.
            continue
        if is_system_path(path):
            issues.append(
                DependencyIssue(
                    name=rel,
                    kind="system",
                    severity="info",
                    detail="This file belongs to the system or to another installed "
                           "application and was not bundled.",
                    action="Ship the package with the runtime dependency documented "
                           "in the README instead.",
                )
            )
            continue
        try:
            size = path.stat().st_size
            digest = sha256_file(path)
        except OSError as exc:
            issues.append(
                DependencyIssue(
                    name=rel,
                    kind="missing",
                    severity="error",
                    detail=f"The file could not be read: {exc}",
                    action="Fix the file permissions and run the command again.",
                )
            )
            continue
        seen.add(rel)
        files.append(
            PackageFile(
                source=path,
                rel_path=rel,
                size=size,
                sha256=digest,
                kind=kind,
                executable=_is_executable(path),
            )
        )

    files.sort(key=lambda f: f.rel_path)
    return files, issues


def detect_entry_point(
    project_dir: Path,
    artifacts: list[Artifact],
    adapter_id: str,
) -> str:
    """Find the file that starts the application, when it can be determined."""
    project_dir = Path(project_dir)

    # A real executable always wins: it is what the user will run.
    for artifact in artifacts:
        path = Path(artifact.path)
        if path.suffix.lower() in (".exe", ".com", ".bat", ".cmd"):
            return _rel_path(path, project_dir)

    if adapter_id == "node":
        manifest = project_dir / "package.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            main = str(data.get("main") or "").strip()
            if main and (project_dir / main).is_file():
                return _rel_path(project_dir / main, project_dir)
            binaries = data.get("bin")
            if isinstance(binaries, dict) and binaries:
                first = sorted(binaries)[0]
                target = project_dir / str(binaries[first])
                if target.is_file():
                    return _rel_path(target, project_dir)

    if adapter_id in ("python",):
        from ..build.exe_exporter import find_entry_point

        found = find_entry_point(project_dir)
        if found is not None:
            return _rel_path(Path(found), project_dir)

    for artifact in artifacts:
        path = Path(artifact.path)
        if path.suffix.lower() in (".jar", ".sh", ".py"):
            return _rel_path(path, project_dir)
    return ""


def _python_requirements(project_dir: Path) -> list[str]:
    """Requirement names declared in requirements.txt (names only)."""
    requirements = project_dir / "requirements.txt"
    names: list[str] = []
    if not requirements.is_file():
        return names
    try:
        text = requirements.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return names
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name = line.split(";", 1)[0]
        for sep in ("==", ">=", "<=", "~=", "!=", ">", "<", "[", " "):
            name = name.split(sep, 1)[0]
        name = name.strip()
        if name:
            names.append(name)
    return names


def detect_dependencies(
    project_dir: Path,
    adapter_id: str,
    files: list[PackageFile],
) -> list[DependencyIssue]:
    """Report runtime dependencies that are missing or that are not bundled."""
    issues: list[DependencyIssue] = []
    project_dir = Path(project_dir)

    if adapter_id == "python":
        import importlib.util

        for name in _python_requirements(project_dir):
            module = name.replace("-", "_")
            try:
                found = importlib.util.find_spec(module) is not None
            except (ImportError, ValueError):
                found = False
            if not found:
                issues.append(
                    DependencyIssue(
                        name=name,
                        kind="missing",
                        severity="warning",
                        detail=f"The Python package '{name}' is declared in "
                               "requirements.txt but is not installed in the interpreter "
                               "that will run the application.",
                        action=f"Install it with: python -m pip install {name}",
                    )
                )
    elif adapter_id == "node":
        manifest = project_dir / "package.json"
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            declared: dict[str, str] = {}
            for section in ("dependencies", "optionalDependencies"):
                values = data.get(section)
                if isinstance(values, dict):
                    declared.update({str(k): str(v) for k, v in values.items()})
            modules = project_dir / "node_modules"
            for name in sorted(declared):
                if not (modules / name).exists():
                    issues.append(
                        DependencyIssue(
                            name=name,
                            kind="missing",
                            severity="warning",
                            detail=f"The dependency '{name}' ({declared[name]}) is declared "
                                   "in package.json but is not installed in node_modules.",
                            action=f"Install it with: npm install {name}",
                        )
                    )

    # A binary that ships without its runtime libraries is a real problem.
    binaries = [f for f in files if f.rel_path.lower().endswith((".exe", ".jar"))]
    for binary in binaries:
        if binary.rel_path.lower().endswith(".jar"):
            continue
        parent = Path(binary.rel_path).parent
        companions = [
            f for f in files
            if f.rel_path != binary.rel_path and Path(f.rel_path).parent == parent
        ]
        if not companions:
            issues.append(
                DependencyIssue(
                    name=binary.rel_path,
                    kind="unresolved",
                    severity="info",
                    detail="This executable ships alone; the runtime it needs is not "
                           "part of the package.",
                    action="Document the required runtime in the README, or bundle the "
                           "runtime libraries explicitly.",
                )
            )
    return issues
