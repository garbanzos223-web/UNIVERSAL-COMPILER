"""Deterministic repair rules.

A rule proposes one minimal change for one diagnostic. Rules only fire when
they are sure about the fix, and each proposal carries:

* the file and line it touches;
* the original and the patched text;
* a unified diff, so the user can see exactly what would change;
* a confidence: ``high`` (applied automatically) or ``medium`` (shown, but
  never applied without the user asking).

The rules never refactor: they add a missing colon, a missing include, a
missing ``using`` or remove an import the compiler says is unused.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .diagnostics import Diagnostic

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"

#: C++ symbol -> header that provides it.
_CPP_HEADERS: dict[str, str] = {
    "uint8_t": "cstdint",
    "int8_t": "cstdint",
    "uint16_t": "cstdint",
    "int16_t": "cstdint",
    "uint32_t": "cstdint",
    "int32_t": "cstdint",
    "uint64_t": "cstdint",
    "int64_t": "cstdint",
    "size_t": "cstddef",
    "std::string": "string",
    "std::vector": "vector",
    "std::map": "map",
    "std::set": "set",
    "std::cout": "iostream",
    "std::cin": "iostream",
    "std::endl": "iostream",
    "std::sort": "algorithm",
    "std::shared_ptr": "memory",
    "std::unique_ptr": "memory",
    "std::thread": "thread",
    "std::mutex": "mutex",
    "std::chrono": "chrono",
}

#: C# type -> namespace that has to be imported.
_CSHARP_NAMESPACES: dict[str, str] = {
    "Console": "System",
    "Math": "System",
    "DateTime": "System",
    "Guid": "System",
    "Exception": "System",
    "Action": "System",
    "Func": "System",
    "Environment": "System",
    "Random": "System",
    "File": "System.IO",
    "Directory": "System.IO",
    "Path": "System.IO",
    "List": "System.Collections.Generic",
    "Dictionary": "System.Collections.Generic",
    "IEnumerable": "System.Collections.Generic",
    "Enumerable": "System.Linq",
    "Thread": "System.Threading",
    "Task": "System.Threading.Tasks",
}

#: Java class -> fully qualified name that can be imported.
_JAVA_IMPORTS: dict[str, str] = {
    "List": "java.util.List",
    "ArrayList": "java.util.ArrayList",
    "Map": "java.util.Map",
    "HashMap": "java.util.HashMap",
    "Set": "java.util.Set",
    "HashSet": "java.util.HashSet",
    "Optional": "java.util.Optional",
    "Arrays": "java.util.Arrays",
    "Collections": "java.util.Collections",
    "Objects": "java.util.Objects",
    "Stream": "java.util.stream.Stream",
    "File": "java.io.File",
    "Files": "java.nio.file.Files",
    "Paths": "java.nio.file.Paths",
}

_PYTHON_BLOCK_KEYWORDS = (
    "if",
    "elif",
    "else",
    "for",
    "while",
    "def",
    "class",
    "with",
    "try",
    "except",
    "finally",
    "match",
    "case",
)


@dataclass
class RepairProposal:
    """One proposed change."""

    rule_id: str
    file: Path
    line: int = 0
    title: str = ""
    rationale: str = ""
    confidence: str = CONFIDENCE_MEDIUM
    original: str = ""
    patched: str = ""
    full_text: str = ""
    diff: str = ""
    category: str = ""
    #: Files that must be restored when the repair has to be undone.
    touched: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "file": str(self.file),
            "line": self.line,
            "title": self.title,
            "rationale": self.rationale,
            "confidence": self.confidence,
            "original": self.original,
            "patched": self.patched,
            "diff": self.diff,
            "category": self.category,
        }

    def text(self) -> str:
        head = f"{self.title} ({self.file.name}:{self.line}, confidence: {self.confidence})"
        return f"{head}\n{self.rationale}\n{self.diff}"


def make_diff(path: Path, original: str, patched: str) -> str:
    """A unified diff between two versions of a file."""
    diff = difflib.unified_diff(
        original.splitlines(keepends=True),
        patched.splitlines(keepends=True),
        fromfile=f"a/{path.name}",
        tofile=f"b/{path.name}",
        n=3,
    )
    return "".join(diff)


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _proposal(
    rule_id: str,
    path: Path,
    line: int,
    title: str,
    rationale: str,
    original: str,
    patched: str,
    *,
    confidence: str = CONFIDENCE_HIGH,
    category: str = "",
) -> RepairProposal:
    full = patched
    return RepairProposal(
        rule_id=rule_id,
        file=path,
        line=line,
        title=title,
        rationale=rationale,
        confidence=confidence,
        original=original,
        patched=patched,
        full_text=full,
        diff=make_diff(path, original, patched),
        category=category,
        touched=[str(path)],
    )


def _replace_line(lines: list[str], index: int, new_line: str) -> str:
    """Return the file text with one line replaced."""
    updated = list(lines)
    updated[index] = new_line
    return "\n".join(updated) + "\n"


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def rule_python_missing_colon(
    diag: Diagnostic, path: Path, lines: list[str], output: str
) -> RepairProposal | None:
    """Add the missing colon of a Python block header."""
    if not path.name.endswith(".py"):
        return None
    if "expected ':'" not in diag.message:
        return None
    index = diag.line - 1
    if index < 0 or index >= len(lines):
        return None
    text = lines[index]
    stripped = text.strip()
    if not stripped or stripped.endswith(":"):
        return None
    keyword = stripped.split()[0] if stripped.split() else ""
    if keyword not in _PYTHON_BLOCK_KEYWORDS:
        return None
    patched_lines = list(lines)
    patched_lines[index] = text.rstrip() + ":"
    patched = "\n".join(patched_lines) + "\n"
    return _proposal(
        "python-missing-colon",
        path,
        diag.line,
        "Add the missing ':' to the block header",
        "Python reported 'expected :' for this line and the line starts a block, so "
        "the colon is missing.",
        "\n".join(lines) + "\n",
        patched,
        confidence=CONFIDENCE_HIGH,
        category="syntax",
    )


def rule_cpp_missing_include(
    diag: Diagnostic, path: Path, lines: list[str], output: str
) -> RepairProposal | None:
    """Add the header that provides a missing C++ symbol."""
    if path.suffix.lower() not in (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh"):
        return None
    message = diag.message
    header = ""
    symbol = ""
    for name, candidate in _CPP_HEADERS.items():
        pattern = r"(?<![\w:])" + re.escape(name) + r"(?![\w:])"
        if re.search(pattern, message):
            header = candidate
            symbol = name
            break
    if not header:
        return None
    include_line = f"#include <{header}>"
    if any(include_line in line for line in lines):
        return None
    insert_at = 0
    for index, line in enumerate(lines[:40]):
        if line.strip().startswith("#include"):
            insert_at = index + 1
    patched_lines = list(lines)
    patched_lines.insert(insert_at, include_line)
    patched = "\n".join(patched_lines) + "\n"
    return _proposal(
        "cpp-missing-include",
        path,
        diag.line,
        f"Add #include <{header}>",
        f"The compiler could not find '{symbol}', which is declared in <{header}>.",
        "\n".join(lines) + "\n",
        patched,
        confidence=CONFIDENCE_HIGH,
        category="missing-import",
    )


def rule_go_unused_import(
    diag: Diagnostic, path: Path, lines: list[str], output: str
) -> RepairProposal | None:
    """Remove an import the Go compiler says is unused."""
    if path.suffix.lower() != ".go":
        return None
    match = re.search(r'"([^"]+)"\s+imported and not used', diag.message)
    if not match:
        return None
    import_path = match.group(1)
    index = diag.line - 1
    if index < 0 or index >= len(lines):
        return None
    text = lines[index]
    if import_path not in text or not text.strip().startswith(("import", '"')):
        # The reported line can be the first line of a grouped import block.
        for candidate in range(max(0, index - 5), min(len(lines), index + 5)):
            if import_path in lines[candidate] and "import" in lines[candidate]:
                index = candidate
                text = lines[candidate]
                break
        else:
            return None
    patched_lines = list(lines)
    patched_lines[index] = ""
    patched = "\n".join(patched_lines) + "\n"
    return _proposal(
        "go-unused-import",
        path,
        index + 1,
        f"Remove the unused import '{import_path}'",
        "Go refuses to compile when an import is not used; removing it keeps the "
        "program behaviour identical.",
        "\n".join(lines) + "\n",
        patched,
        confidence=CONFIDENCE_HIGH,
        category="syntax",
    )


def rule_csharp_missing_using(
    diag: Diagnostic, path: Path, lines: list[str], output: str
) -> RepairProposal | None:
    """Add the ``using`` directive for a type C# cannot find."""
    if path.suffix.lower() != ".cs":
        return None
    match = re.search(r"The name '([^']+)' does not exist", diag.message)
    if not match:
        return None
    name = match.group(1)
    namespace = _CSHARP_NAMESPACES.get(name)
    if not namespace:
        return None
    directive = f"using {namespace};"
    if any(line.strip() == directive for line in lines):
        return None
    insert_at = 0
    for index, line in enumerate(lines[:40]):
        if line.strip().startswith("using "):
            insert_at = index + 1
    patched_lines = list(lines)
    patched_lines.insert(insert_at, directive)
    patched = "\n".join(patched_lines) + "\n"
    return _proposal(
        "csharp-missing-using",
        path,
        diag.line,
        f"Add '{directive}'",
        f"C# reported that '{name}' does not exist; it lives in the {namespace} "
        "namespace.",
        "\n".join(lines) + "\n",
        patched,
        confidence=CONFIDENCE_HIGH,
        category="missing-import",
    )


def rule_java_missing_import(
    diag: Diagnostic, path: Path, lines: list[str], output: str
) -> RepairProposal | None:
    """Add the ``import`` for a class javac cannot find."""
    if path.suffix.lower() != ".java":
        return None
    symbol = ""
    match = re.search(r"symbol:\s+(?:class|variable)\s+(\w+)", output)
    if match:
        symbol = match.group(1)
    else:
        match = re.search(r"cannot find symbol", diag.message)
    if not symbol:
        return None
    full_name = _JAVA_IMPORTS.get(symbol)
    if not full_name:
        return None
    directive = f"import {full_name};"
    if any(line.strip() == directive for line in lines):
        return None
    insert_at = 0
    for index, line in enumerate(lines[:60]):
        if line.strip().startswith("import "):
            insert_at = index + 1
    patched_lines = list(lines)
    patched_lines.insert(insert_at, directive)
    patched = "\n".join(patched_lines) + "\n"
    return _proposal(
        "java-missing-import",
        path,
        diag.line,
        f"Add '{directive}'",
        f"javac could not find the symbol '{symbol}'; it is declared in {full_name}.",
        "\n".join(lines) + "\n",
        patched,
        confidence=CONFIDENCE_HIGH,
        category="missing-import",
    )


def rule_typescript_missing_semicolon(
    diag: Diagnostic, path: Path, lines: list[str], output: str
) -> RepairProposal | None:
    """Add a missing semicolon in TypeScript or JavaScript."""
    if path.suffix.lower() not in (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"):
        return None
    if "';' expected" not in diag.message and '";" expected' not in diag.message:
        return None
    index = diag.line - 1
    if diag.column > 0 and 0 <= index < len(lines):
        # When the column points at the first character of the line, the
        # statement that misses its semicolon is the line before.
        prefix = lines[index][: diag.column - 1]
        if not prefix.strip() and index > 0:
            index -= 1
    # The compiler reports the position after the statement, so the statement
    # itself is usually on the reported line or on the line before it.
    for candidate in (index, index - 1):
        if candidate < 0 or candidate >= len(lines):
            continue
        text = lines[candidate]
        stripped = text.strip()
        if not stripped or stripped.endswith((";", "{", "}", ",", ":")):
            continue
        if stripped.startswith(("//", "*", "/*")):
            continue
        if stripped.endswith(("+", "=", "(", "&", "|", ".", "-", "*", "/")):
            continue
        patched_lines = list(lines)
        patched_lines[candidate] = text.rstrip() + ";"
        patched = "\n".join(patched_lines) + "\n"
        return _proposal(
            "typescript-missing-semicolon",
            path,
            candidate + 1,
            "Add the missing ';'",
            "The compiler reported that a semicolon was expected at the end of this "
            "statement.",
            "\n".join(lines) + "\n",
            patched,
            confidence=CONFIDENCE_MEDIUM,
            category="syntax",
        )
    return None


#: Every rule, in the order they are tried.
RULES = (
    rule_python_missing_colon,
    rule_cpp_missing_include,
    rule_go_unused_import,
    rule_csharp_missing_using,
    rule_java_missing_import,
    rule_typescript_missing_semicolon,
)


def propose_repairs(
    diagnostics: list[Diagnostic],
    project_dir: Path,
    *,
    output: str = "",
) -> list[RepairProposal]:
    """Run every rule over the diagnostics and return the proposals."""
    project_dir = Path(project_dir)
    proposals: list[RepairProposal] = []
    seen: set[tuple[str, str, int]] = set()
    for diag in diagnostics:
        if not diag.is_error or not diag.file:
            continue
        path = Path(diag.file)
        if not path.is_absolute():
            path = project_dir / path
        if not path.is_file():
            continue
        lines = _read_lines(path)
        if not lines:
            continue
        for rule in RULES:
            try:
                proposal = rule(diag, path, lines, output)
            except (OSError, IndexError, ValueError):
                proposal = None
            if proposal is None:
                continue
            key = (proposal.rule_id, str(path), proposal.line)
            if key in seen:
                continue
            seen.add(key)
            proposals.append(proposal)
    return proposals
