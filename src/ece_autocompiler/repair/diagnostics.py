"""Parse compiler output into structured diagnostics.

Several compilers are supported, each with its own output format:

* GCC / Clang      ``file:line:col: error: message``
* MSVC             ``file(line,col): error C1234: message``
* Cargo / Rust     ``error[E0308]: message`` with a ``--> file:line:col`` line
* Go               ``file:line:col: message``
* Python           ``File "x.py", line 12`` plus the exception type
* .NET / C#        same shape as MSVC, with ``CS`` codes
* Java / javac     ``file.java:12: error: message``
* TypeScript       ``file.ts(3,5): error TS2322: message``

Nothing is invented: a diagnostic always carries the raw line it came from,
and a parser only reports what it actually matched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Error categories used by the analysis and the repair rules.
CATEGORY_SYNTAX = "syntax"
CATEGORY_MISSING_IMPORT = "missing-import"
CATEGORY_UNDEFINED = "undefined-identifier"
CATEGORY_TYPE = "type-mismatch"
CATEGORY_SIGNATURE = "invalid-signature"
CATEGORY_LINKER = "linker"
CATEGORY_DEPENDENCY = "missing-dependency"
CATEGORY_FLAGS = "incorrect-flags"
CATEGORY_TOOLCHAIN = "incompatible-toolchain"
CATEGORY_UNKNOWN = "unknown"

#: (category, needles) - the first match wins.
_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (CATEGORY_LINKER, (
        "undefined reference",
        "unresolved external symbol",
        "cannot find -l",
        "linker command failed",
        "ld returned",
        "duplicate symbol",
        "unresolved externals",
    )),
    (CATEGORY_MISSING_IMPORT, (
        "no module named",
        "cannot find module",
        "module not found",
        "cannot open include file",
        "unable to find package",
        "no such module",
        "package was not found",
        "failed to resolve",
        "does not name a type",
        "no such type or namespace",
    )),
    (CATEGORY_UNDEFINED, (
        "is not defined",
        "undeclared identifier",
        "cannot find symbol",
        "was not declared in this scope",
        "unknown identifier",
        "does not exist in the current context",
        "name error",
    )),
    (CATEGORY_TYPE, (
        "type mismatch",
        "incompatible types",
        "cannot convert",
        "mismatched types",
        "invalid conversion",
        "expected type",
        "cannot be converted",
    )),
    (CATEGORY_SIGNATURE, (
        "no matching function",
        "too many arguments",
        "too few arguments",
        "invalid arguments",
        "candidate function not viable",
        "arguments do not match",
    )),
    (CATEGORY_FLAGS, (
        "unrecognized command line option",
        "unrecognised command line option",
        "unknown argument",
        "invalid option",
        "unsupported option",
        "not a recognized option",
    )),
    (CATEGORY_TOOLCHAIN, (
        "requires a newer version",
        "unsupported version",
        "toolchain",
        "minimum required version",
        "compiler version too old",
    )),
    (CATEGORY_SYNTAX, (
        "syntaxerror",
        "syntax error",
        "expected",
        "unexpected token",
        "parse error",
        "invalid syntax",
    )),
)


@dataclass
class Diagnostic:
    """One problem reported by a compiler."""

    file: str = ""
    line: int = 0
    column: int = 0
    severity: str = "error"       # error|warning|note
    code: str = ""
    message: str = ""
    category: str = CATEGORY_UNKNOWN
    raw: str = ""
    compiler: str = ""
    context: list[str] = field(default_factory=list)

    @property
    def is_error(self) -> bool:
        return self.severity.lower() == "error"

    def signature(self) -> str:
        """Stable identity of this problem, used to detect repair loops."""
        return f"{self.file}:{self.line}:{self.code or self.category}:{self.message[:80]}"

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "category": self.category,
            "raw": self.raw,
            "compiler": self.compiler,
            "context": list(self.context),
        }

    def text(self) -> str:
        where = self.file or "(unknown file)"
        if self.line:
            where += f":{self.line}"
            if self.column:
                where += f":{self.column}"
        code = f" [{self.code}]" if self.code else ""
        return f"{where}{code} {self.severity}: {self.message}"


def classify_category(message: str, code: str = "") -> str:
    """Map a compiler message to one of the known error categories."""
    text = f"{code} {message}".lower()
    for category, needles in _CATEGORY_RULES:
        for needle in needles:
            if needle in text:
                return category
    return CATEGORY_UNKNOWN


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

_GCC_RE = re.compile(
    r"^(?P<file>[^\s:][^:]*?):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
    r"(?P<sev>fatal error|error|warning|note):\s*(?P<msg>.*)$"
)
_MSVC_RE = re.compile(
    r"^(?P<file>[^(]+?)\((?P<line>\d+)(?:,(?P<col>\d+))?\)\s*:\s*"
    r"(?P<sev>fatal error|error|warning)\s*(?P<code>[A-Z]+\d+)?\s*:?\s*(?P<msg>.*)$"
)
_CARGO_CODE_RE = re.compile(r"^(?P<sev>error|warning)(?:\[(?P<code>[^\]]+)\])?:\s*(?P<msg>.*)$")
_CARGO_POS_RE = re.compile(r"^\s*-->\s+(?P<file>[^:\n]+):(?P<line>\d+):(?:(?P<col>\d+))?")
_PY_FILE_RE = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)')
_PY_ERROR_RE = re.compile(r"^(?P<etype>[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning)):\s*(?P<msg>.*)$")
_JAVA_RE = re.compile(
    r"^(?P<file>[^\s:][^:]*?):(?P<line>\d+):\s*(?P<sev>error|warning):\s*(?P<msg>.*)$"
)
_TS_RE = re.compile(
    r"^(?P<file>[^\(]+?)\((?P<line>\d+),(?P<col>\d+)\):\s*"
    r"(?P<sev>error|warning)\s+(?P<code>TS\d+)\s*:?\s*(?P<msg>.*)$"
)
_GO_RE = re.compile(
    r"^(?P<file>[^\s:][^:]*?):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<msg>.*)$"
)

#: Lines that mean the build failed, even without a file and a line.
_GENERIC_PHRASES: tuple[str, ...] = (
    "undefined reference",
    "unresolved external symbol",
    "ld returned",
    "cannot find -l",
    "linker command failed",
    "duplicate symbol",
    "no such file or directory",
    "command not found",
    "is not recognized as an internal or external command",
    "permission denied",
    "fatal error:",
    "error:",
)


def _mk(**kwargs) -> Diagnostic:
    kwargs.setdefault("category", CATEGORY_UNKNOWN)
    diag = Diagnostic(**kwargs)
    if diag.category == CATEGORY_UNKNOWN:
        diag.category = classify_category(diag.message, diag.code)
    return diag


def parse_gcc(output: str) -> list[Diagnostic]:
    """GCC and Clang diagnostics."""
    found: list[Diagnostic] = []
    for raw in output.splitlines():
        line = raw.rstrip()
        match = _GCC_RE.match(line.strip())
        if not match:
            continue
        severity = match.group("sev").lower()
        if severity.startswith("fatal"):
            severity = "error"
        found.append(
            _mk(
                file=match.group("file").strip(),
                line=int(match.group("line")),
                column=int(match.group("col") or 0),
                severity=severity,
                message=match.group("msg").strip(),
                raw=line,
                compiler="gcc",
            )
        )
    return found


def parse_msvc(output: str) -> list[Diagnostic]:
    """MSVC diagnostics (also used by the C# compiler)."""
    found: list[Diagnostic] = []
    for raw in output.splitlines():
        line = raw.rstrip()
        match = _MSVC_RE.match(line.strip())
        if not match:
            continue
        found.append(
            _mk(
                file=match.group("file").strip(),
                line=int(match.group("line")),
                column=int(match.group("col") or 0),
                severity=match.group("sev").lower(),
                code=(match.group("code") or ""),
                message=match.group("msg").strip(),
                raw=line,
                compiler="msvc",
            )
        )
    return found


def parse_cargo(output: str) -> list[Diagnostic]:
    """Rust (cargo) diagnostics: the code line plus the ``-->`` position."""
    found: list[Diagnostic] = []
    lines = output.splitlines()
    for index, raw in enumerate(lines):
        match = _CARGO_CODE_RE.match(raw.strip())
        if not match:
            continue
        severity = match.group("sev").lower()
        code = match.group("code") or ""
        message = match.group("msg").strip()
        # The position is on one of the next lines.
        file_name = ""
        line_no = 0
        column = 0
        for follow in lines[index + 1 : index + 4]:
            pos = _CARGO_POS_RE.match(follow)
            if pos:
                file_name = pos.group("file").strip()
                line_no = int(pos.group("line"))
                column = int(pos.group("col") or 0)
                break
        found.append(
            _mk(
                file=file_name,
                line=line_no,
                column=column,
                severity=severity,
                code=code,
                message=message,
                raw=raw.rstrip(),
                compiler="rust",
            )
        )
    return found


def parse_go(output: str) -> list[Diagnostic]:
    """Go diagnostics (``file:line:col: message``)."""
    found: list[Diagnostic] = []
    for raw in output.splitlines():
        line = raw.rstrip()
        match = _GO_RE.match(line.strip())
        if not match:
            continue
        file_name = match.group("file").strip()
        if not file_name.endswith(".go"):
            # The Go compiler only ever reports .go files; anything else is
            # another compiler's output and must not be claimed here.
            continue
        message = match.group("msg").strip()
        if not message or message.startswith("go: "):
            continue
        found.append(
            _mk(
                file=file_name,
                line=int(match.group("line")),
                column=int(match.group("col") or 0),
                severity="error",
                message=message,
                raw=line,
                compiler="go",
            )
        )
    return found


def parse_python(output: str) -> list[Diagnostic]:
    """Python tracebacks and syntax errors."""
    found: list[Diagnostic] = []
    lines = output.splitlines()
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        file_match = _PY_FILE_RE.match(stripped)
        if file_match:
            message = ""
            code = ""
            for follow in lines[index + 1 : index + 4]:
                error_match = _PY_ERROR_RE.match(follow.strip())
                if error_match:
                    message = error_match.group("msg").strip()
                    code = error_match.group("etype")
                    break
            found.append(
                _mk(
                    file=file_match.group("file"),
                    line=int(file_match.group("line")),
                    severity="error",
                    code=code,
                    message=message or stripped,
                    raw=raw.rstrip(),
                    compiler="python",
                )
            )
            continue
        error_match = _PY_ERROR_RE.match(stripped)
        if error_match:
            found.append(
                _mk(
                    severity="error",
                    code=error_match.group("etype"),
                    message=error_match.group("msg").strip(),
                    raw=raw.rstrip(),
                    compiler="python",
                )
            )
    return found


def parse_java(output: str) -> list[Diagnostic]:
    """javac diagnostics."""
    found: list[Diagnostic] = []
    for raw in output.splitlines():
        line = raw.rstrip()
        match = _JAVA_RE.match(line.strip())
        if not match:
            continue
        file_name = match.group("file").strip()
        if not file_name.endswith(".java"):
            # javac only reports .java files; do not steal other tools' output.
            continue
        found.append(
            _mk(
                file=file_name,
                line=int(match.group("line")),
                severity=match.group("sev").lower(),
                message=match.group("msg").strip(),
                raw=line,
                compiler="java",
            )
        )
    return found


def parse_typescript(output: str) -> list[Diagnostic]:
    """TypeScript / JavaScript diagnostics."""
    found: list[Diagnostic] = []
    for raw in output.splitlines():
        line = raw.rstrip()
        match = _TS_RE.match(line.strip())
        if not match:
            continue
        found.append(
            _mk(
                file=match.group("file").strip(),
                line=int(match.group("line")),
                column=int(match.group("col")),
                severity=match.group("sev").lower(),
                code=match.group("code") or "",
                message=match.group("msg").strip(),
                raw=line,
                compiler="typescript",
            )
        )
    return found


def parse_generic(output: str, claimed: set[str] | None = None) -> list[Diagnostic]:
    """Catch failures that no other parser recognised.

    Only lines that no other parser claimed are considered, so a GCC error is
    never reported twice.
    """
    claimed = claimed or set()
    found: list[Diagnostic] = []
    for raw in output.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped in claimed:
            continue
        lowered = stripped.lower()
        if not any(phrase in lowered for phrase in _GENERIC_PHRASES):
            continue
        found.append(
            _mk(
                severity="error",
                message=stripped,
                raw=line,
                compiler="generic",
            )
        )
    return found


#: Every parser, tried in order.
PARSERS = (
    parse_cargo,
    parse_typescript,
    parse_java,
    parse_msvc,
    parse_python,
    parse_gcc,
    parse_go,
)


def parse_diagnostics(output: str, compiler: str = "") -> list[Diagnostic]:
    """Parse *output* into diagnostics.

    When *compiler* names a parser ("gcc", "msvc", ...) only that parser runs;
    otherwise every parser is tried and the results are merged.
    """
    if not output:
        return []
    if compiler:
        for parser in PARSERS:
            if parser.__name__ == f"parse_{compiler}":
                return parser(output)
    merged: list[Diagnostic] = []
    seen: set[tuple[str, int, int, str, str]] = set()
    claimed: set[str] = set()
    for parser in PARSERS:
        for diag in parser(output):
            key = (diag.file, diag.line, diag.column, diag.code, diag.message[:60])
            if key in seen:
                continue
            seen.add(key)
            claimed.add(diag.raw.strip())
            merged.append(diag)
    for diag in parse_generic(output, claimed):
        key = (diag.file, diag.line, diag.column, diag.code, diag.message[:60])
        if key in seen:
            continue
        seen.add(key)
        merged.append(diag)
    merged.sort(key=lambda d: (d.file == "", d.file, d.line, d.column))
    return merged


def errors_only(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    """Only the diagnostics that are actual errors."""
    return [d for d in diagnostics if d.is_error]


def add_context(
    diagnostics: list[Diagnostic],
    project_dir,
    *,
    radius: int = 1,
) -> list[Diagnostic]:
    """Attach the source lines around every diagnostic."""
    from pathlib import Path

    root = Path(project_dir)
    for diag in diagnostics:
        if not diag.file or not diag.line:
            continue
        path = Path(diag.file)
        if not path.is_absolute():
            path = root / path
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        start = max(0, diag.line - 1 - radius)
        end = min(len(lines), diag.line + radius)
        diag.context = [f"{index + 1:>5} | {lines[index]}" for index in range(start, end)]
    return diagnostics
