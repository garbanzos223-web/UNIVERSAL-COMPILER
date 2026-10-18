"""Error classification for build output.

Turns raw compiler/tool output into categorized, actionable diagnostics. This
is pattern based and deliberately conservative: when a line does not clearly
match a known class it is left unclassified rather than mislabeled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Category(str, Enum):
    MISSING_TOOL = "missing_tool"
    MISSING_DEPENDENCY = "missing_dependency"
    INVALID_CONFIG = "invalid_config"
    SYNTAX_TYPE = "syntax_type"
    LINKER = "linker"
    MISSING_ASSET = "missing_asset"
    UNSUPPORTED_TARGET = "unsupported_target"
    PERMISSION = "permission"
    NETWORK = "network"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


CATEGORY_LABELS = {
    Category.MISSING_TOOL: "Missing compiler or SDK",
    Category.MISSING_DEPENDENCY: "Missing dependency",
    Category.INVALID_CONFIG: "Invalid build configuration",
    Category.SYNTAX_TYPE: "Syntax or type error",
    Category.LINKER: "Linker error",
    Category.MISSING_ASSET: "Missing asset",
    Category.UNSUPPORTED_TARGET: "Unsupported target",
    Category.PERMISSION: "Permission or filesystem error",
    Category.NETWORK: "Network or package-manager failure",
    Category.TIMEOUT: "Timed out",
    Category.UNKNOWN: "Unclassified",
}


@dataclass
class Diagnostic:
    category: Category
    message: str
    line: str = ""
    hint: str = ""
    line_number: int = 0

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "label": CATEGORY_LABELS[self.category],
            "message": self.message,
            "line": self.line,
            "hint": self.hint,
            "line_number": self.line_number,
        }


# (regex, category, hint). First match wins.
_RULES: tuple[tuple[re.Pattern[str], Category, str], ...] = (
    (re.compile(r"(?i)command not found|is not recognized as an internal or external command|no such file or directory: '?[a-z0-9_\-]+'?\s*$"), Category.MISSING_TOOL, "Install the tool or fix PATH."),
    (re.compile(r"(?i)not found: (gcc|g\+\+|clang|cmake|ninja|make|cargo|go|mvn|gradle|dotnet|javac|php)"), Category.MISSING_TOOL, "Install the missing toolchain."),
    (re.compile(r"(?i)SDK.*not found|no .NET SDKs were found|MSB\d+.*not (found|installed)"), Category.MISSING_TOOL, "Install the required SDK."),
    (re.compile(r"(?i)cannot find -l\w+|undefined reference to|unresolved external symbol|LNK\d+"), Category.LINKER, "A library or object is missing or misnamed."),
    (re.compile(r"(?i)\bfatal error\b.*\.h: no such file|file not found: .*\.h|C1083"), Category.MISSING_DEPENDENCY, "Install the headers/dev package for the dependency."),
    (re.compile(r"(?i)ModuleNotFoundError|ImportError: no module named|cannot find module|npm error.*404|No matching distribution found|Could not resolve dependencies"), Category.MISSING_DEPENDENCY, "Install the missing dependency."),
    (re.compile(r"(?i)\berror\b[^\n]*:\s*\d+:\d+|syntax error|SyntaxError|TypeError|CS\d{4}|error TS\d+"), Category.SYNTAX_TYPE, "Fix the reported source location."),
    (re.compile(r"(?i)invalid (configuration|argument|option)|unknown option|unrecognized command|CMake Error|error MSB\d+"), Category.INVALID_CONFIG, "Check the build configuration."),
    (re.compile(r"(?i)permission denied|access is denied|EACCES|EPERM|read-only file system"), Category.PERMISSION, "Close files using them or run from a writable directory."),
    (re.compile(r"(?i)ETIMEDOUT|ECONNREFUSED|Could not resolve host|network|proxy|getaddrinfo|TLS|502 Bad Gateway|503 Service"), Category.NETWORK, "Check the network connection or proxy settings."),
    (re.compile(r"(?i)not supported target|unsupported (platform|architecture)|target .* not found"), Category.UNSUPPORTED_TARGET, "Choose a supported target."),
    (re.compile(r"(?i)no such file or directory|cannot open (source )?file"), Category.MISSING_ASSET, "The referenced file is missing."),
    (re.compile(r"(?i)timed out|timeout"), Category.TIMEOUT, "Increase the timeout or investigate a hang."),
)

_MAX_DIAGNOSTICS = 200


def classify(output: str) -> list[Diagnostic]:
    """Return categorized diagnostics found in *output*."""
    diagnostics: list[Diagnostic] = []
    seen: set[str] = set()

    for number, raw in enumerate(output.splitlines(), start=1):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if len(diagnostics) >= _MAX_DIAGNOSTICS:
            break
        for pattern, category, hint in _RULES:
            if pattern.search(line):
                key = f"{category.value}:{stripped[:160]}"
                if key in seen:
                    break
                seen.add(key)
                diagnostics.append(
                    Diagnostic(
                        category=category,
                        message=CATEGORY_LABELS[category],
                        line=stripped,
                        hint=hint,
                        line_number=number,
                    )
                )
                break
    return diagnostics


def summarize(diagnostics: list[Diagnostic]) -> str:
    if not diagnostics:
        return "No known error patterns were found in the output."
    counts: dict[Category, int] = {}
    for d in diagnostics:
        counts[d.category] = counts.get(d.category, 0) + 1
    parts = [f"{CATEGORY_LABELS[c]} x{n}" for c, n in counts.items()]
    return "; ".join(parts)


def first_actionable(diagnostics: list[Diagnostic]) -> Diagnostic | None:
    """Return the most useful single diagnostic (missing tools first)."""
    priority = [
        Category.MISSING_TOOL,
        Category.MISSING_DEPENDENCY,
        Category.INVALID_CONFIG,
        Category.NETWORK,
        Category.PERMISSION,
        Category.LINKER,
        Category.UNSUPPORTED_TARGET,
        Category.SYNTAX_TYPE,
        Category.MISSING_ASSET,
        Category.TIMEOUT,
        Category.UNKNOWN,
    ]
    for category in priority:
        for d in diagnostics:
            if d.category == category:
                return d
    return None
