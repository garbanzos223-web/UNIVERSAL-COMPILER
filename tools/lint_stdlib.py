"""Stdlib-only static checks for ECE Universal AutoCompiler.

No third-party tools: this script uses the ``ast`` module to enforce the few
conventions that matter for this project's safety story:

  1. no unused imports (dead code hides what actually runs);
  2. no ``print(...)`` in the package (the CLI writes through ``_print``,
     the GUI through widgets; stray prints corrupt ``--json`` output);
  3. no ``shell=True`` (commands are always argv lists; shell strings are a
     command-injection footgun);
  4. no ``os.system`` / ``eval`` / ``exec`` (same reasons).

Usage:
    python tools/lint_stdlib.py [src_dir]     # default: <repo>/src

Exit codes: 0 clean, 1 problems found, 2 bad usage.
A ``# noqa`` comment on an import line opts it out of check 1.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Imports that exist only for their side effects or re-export machinery.
_ALWAYS_USED = {"annotations"}  # from __future__ import annotations
_SKIP_PACKAGES = {"__future__"}


def _iter_py_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _used_names(tree: ast.AST) -> set[str]:
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # String annotations and __all__ entries count as usage.
            used.add(node.value)
        elif isinstance(node, ast.Attribute):
            pass  # attribute names are not import bindings
    return used


def check_file(path: Path) -> list[str]:
    problems: list[str] = []
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return [f"{path}: not UTF-8 ({exc})"]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: syntax error: {exc.msg}"]

    lines = source.splitlines()
    used = _used_names(tree)

    for node in ast.walk(tree):
        # ---------------------------------------------------------- imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bound = alias.asname
                else:
                    bound = alias.name.split(".", 1)[0]
                if bound in used or bound in _ALWAYS_USED:
                    continue
                if _noqa(lines, node.lineno):
                    continue
                problems.append(
                    f"{path}:{node.lineno}: unused import {alias.name!r} (bound as {bound!r})"
                )
        elif isinstance(node, ast.ImportFrom):
            pkg = (node.module or "").split(".", 1)[0]
            if pkg in _SKIP_PACKAGES:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                if bound in used or bound in _ALWAYS_USED:
                    continue
                if _noqa(lines, node.lineno):
                    continue
                problems.append(
                    f"{path}:{node.lineno}: unused import "
                    f"{node.module + '.' + alias.name!r} (bound as {bound!r})"
                )

        # ------------------------------------------------------- print calls
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "print":
                problems.append(
                    f"{path}:{node.lineno}: print() in the package; "
                    "use _print (CLI) or the GUI log instead"
                )
            elif node.func.id in ("eval", "exec", "compile"):
                problems.append(f"{path}:{node.lineno}: {node.func.id}() is forbidden")

        # ------------------------------------------------- os.system / shell
        elif isinstance(node, ast.Call):
            func = node.func
            is_os_system = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and func.attr == "system"
            )
            if is_os_system:
                problems.append(f"{path}:{node.lineno}: os.system() is forbidden; use argv lists")
            for kw in node.keywords:
                if (
                    kw.arg == "shell"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True
                ):
                    problems.append(f"{path}:{node.lineno}: shell=True is forbidden")

    return problems


def _noqa(lines: list[str], lineno: int) -> bool:
    if 1 <= lineno <= len(lines):
        return "# noqa" in lines[lineno - 1]
    return False


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        sys.stderr.write("usage: python tools/lint_stdlib.py [src_dir]\n")
        return 2
    repo = Path(__file__).resolve().parents[1]
    root = Path(argv[0]) if argv else repo / "src"
    if not root.is_dir():
        sys.stderr.write(f"not a directory: {root}\n")
        return 2

    problems: list[str] = []
    files = 0
    for path in _iter_py_files(root):
        files += 1
        problems.extend(check_file(path))

    if problems:
        sys.stdout.write("\n".join(problems) + "\n")
        sys.stdout.write(f"\n{len(problems)} problem(s) in {files} file(s).\n")
        return 1
    sys.stdout.write(f"lint: {files} file(s) clean.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
