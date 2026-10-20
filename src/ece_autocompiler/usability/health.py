"""Project health and toolchain version checks (items 15, 17, 36).

* item 15 - report the version of each detected toolchain and whether it meets
  the minimum this tool needs, without pretending a too-old tool will work;
* item 17 - warn *before* a build about long paths, non-ASCII names and names
  Windows cannot store, because those fail late and confusingly;
* item 36 - a short "is this project healthy enough to build?" report covering
  unreadable manifests, missing dependency folders and case-only name clashes.

Nothing here executes project code or writes anything.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Limits
from ..detection.markers import IGNORED_DIRECTORIES

# Windows refuses paths longer than this by default (MAX_PATH is 260,
# including the terminating NUL). We warn a little earlier than the wall.
_MAX_PATH_HARD = 259
_MAX_PATH_WARN = 220

_RESERVED_DEVICE_NAMES = frozenset(
    {
        "con", "prn", "aux", "nul",
        *{f"com{i}" for i in range(1, 10)},
        *{f"lpt{i}" for i in range(1, 10)},
    }
)

_UNSAFE_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Toolchain version checks live in a neutral module so the doctor command can
# use them too; they are re-exported here for convenience.
from ..toolchain_versions import (  # noqa: F401  (re-exported)
    MIN_TOOL_VERSIONS,
    ToolchainVersion,
    parse_version,
    toolchain_versions,
)


@dataclass
class HealthIssue:
    level: str  # "error" | "warning" | "info"
    code: str
    message: str
    detail: str = ""
    fix: str = ""

    def __str__(self) -> str:
        suffix = f" [{self.detail}]" if self.detail else ""
        return f"{self.level.upper()}: {self.message}{suffix}"

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "fix": self.fix,
        }


@dataclass
class ProjectHealth:
    root: Path
    issues: list[HealthIssue] = field(default_factory=list)
    file_count: int = 0

    @property
    def errors(self) -> list[HealthIssue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[HealthIssue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def summary(self) -> str:
        if not self.issues:
            return "Project health: no problems found."
        errors, warnings = len(self.errors), len(self.warnings)
        if errors:
            return f"Project health: {errors} problem(s), {warnings} warning(s)."
        return f"Project health: {warnings} warning(s), nothing blocking."

    def lines(self) -> list[str]:
        if not self.issues:
            return [self.summary]
        lines = [self.summary]
        for issue in self.issues:
            lines.append(f"  - {issue}")
            if issue.fix:
                lines.append(f"      fix: {issue.fix}")
        return lines

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "issues": [i.to_dict() for i in self.issues],
        }


def _walk_files(root: Path, limits: Limits) -> list[str]:
    files: list[str] = []
    for current, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [
            d for d in dirnames if d not in IGNORED_DIRECTORIES and not d.startswith(".")
        ]
        for name in filenames:
            files.append(name)
            if len(files) >= limits.max_scan_files:
                return files
        if len(files) >= limits.max_scan_files:
            break
    return files


def _check_names(root: Path, rel_files: list[str]) -> list[HealthIssue]:
    issues: list[HealthIssue] = []
    root_len = len(str(root.resolve()))
    collisions: dict[str, str] = {}
    long_paths = 0
    non_ascii = 0
    unsafe = 0

    for rel in rel_files:
        absolute_len = root_len + 1 + len(rel)
        if absolute_len > _MAX_PATH_HARD:
            long_paths += 1
        elif absolute_len > _MAX_PATH_WARN:
            long_paths += 1

        if any(ord(ch) > 127 for ch in rel):
            non_ascii += 1

        for segment in rel.split("/"):
            if _UNSAFE_NAME_CHARS.search(segment) or segment != segment.rstrip(". "):
                unsafe += 1
                break
            if segment.split(".", 1)[0].lower() in _RESERVED_DEVICE_NAMES:
                unsafe += 1
                break

        key = rel.casefold()
        previous = collisions.get(key)
        if previous is not None and previous != rel:
            issues.append(
                HealthIssue(
                    "warning",
                    "health.path_case_collision",
                    "Two files differ only by letter case, which Windows treats as one file",
                    f"{previous} | {rel}",
                    "Rename one of them before packaging.",
                )
            )
        else:
            collisions[key] = rel

    if long_paths:
        issues.append(
            HealthIssue(
                "warning",
                "health.long_paths",
                f"{long_paths} file path(s) are close to or past the Windows length limit",
                f"warn above {_MAX_PATH_WARN}, refuse above {_MAX_PATH_HARD} characters",
                "Move the project to a shorter folder (for example C:\\projects\\app).",
            )
        )
    if non_ascii:
        issues.append(
            HealthIssue(
                "warning",
                "health.non_ascii",
                f"{non_ascii} file name(s) contain non-ASCII characters",
                "some compilers misread accented or non-Latin names",
                "Rename the affected files to plain ASCII if a build fails on them.",
            )
        )
    if unsafe:
        issues.append(
            HealthIssue(
                "warning",
                "health.unsafe_names",
                f"{unsafe} file name(s) use characters or names Windows cannot store reliably",
                "this includes trailing dots/spaces and reserved names such as CON or NUL",
                "Rename those files before building or packaging.",
            )
        )
    return issues


def _check_manifests(root: Path) -> list[HealthIssue]:
    issues: list[HealthIssue] = []
    json_manifests = ("package.json", "composer.json", "tsconfig.json")
    for name in json_manifests:
        path = root / name
        if not path.is_file():
            continue
        try:
            json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(
                HealthIssue(
                    "error",
                    "health.invalid_manifest",
                    f"{name} is not valid JSON",
                    str(exc),
                    f"Fix the syntax in {name}; the build tools will refuse it too.",
                )
            )

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        if "[project]" not in text and "[build-system]" not in text and "[tool.poetry]" not in text:
            issues.append(
                HealthIssue(
                    "info",
                    "health.pyproject_thin",
                    "pyproject.toml has no [project], [build-system] or [tool.poetry] table",
                    "",
                    "This is fine for a plain script folder; ignore it if the project builds.",
                )
            )

    # A package.json without node_modules means dependencies are not installed.
    if (root / "package.json").is_file() and not (root / "node_modules").is_dir():
        issues.append(
            HealthIssue(
                "info",
                "health.node_modules_missing",
                "node_modules is missing; dependencies look uninstalled",
                "",
                "Either allow the install step in the build options, or run npm install first.",
            )
        )
    return issues


def check_project_health(
    root: Path | str,
    *,
    files: list[str] | None = None,
    limits: Limits | None = None,
    candidate_root: str = "",
) -> ProjectHealth:
    """Return a pre-build health report for *root* (item 36)."""
    limits = limits or Limits()
    root = Path(root)
    probe = (root / candidate_root).resolve() if candidate_root else root.resolve()
    health = ProjectHealth(root=root)

    if not probe.exists():
        health.issues.append(
            HealthIssue(
                "error",
                "health.missing_root",
                "The project folder does not exist",
                str(probe),
                "Select an existing folder again.",
            )
        )
        return health
    if not probe.is_dir():
        health.issues.append(
            HealthIssue(
                "error",
                "health.not_a_directory",
                "The selected path is not a folder",
                str(probe),
                "Select the project folder, not a file.",
            )
        )
        return health

    rel_files = files if files is not None else _walk_files(probe, limits)
    health.file_count = len(rel_files)
    if not rel_files:
        health.issues.append(
            HealthIssue(
                "warning",
                "health.empty",
                "No files were found in the project folder",
                str(probe),
                "Point at the folder that contains the project files.",
            )
        )

    health.issues.extend(_check_names(probe, rel_files))
    health.issues.extend(_check_manifests(probe))
    return health
