"""A tiny TOML subset reader for Python 3.10 (no third-party dependency).

Python 3.11 ships :mod:`tomllib`; 3.10 does not. The fusion engine only needs
the declarative tables used by project manifests (Cargo.toml section keys,
strings, booleans, numbers, arrays and inline tables). This module implements
exactly that subset, with no attempt to be a complete TOML parser.

Callers must prefer ``tomllib`` when available. Unknown syntax is ignored rather
than guessed, so a malformed manifest never turns into invented dependencies.
"""

from __future__ import annotations

from typing import Any

MAX_TEXT_BYTES = 2 * 1024 * 1024


def _strip_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote:
            escaped = True
            continue
        if char in ('"', "'"):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        if char == "#" and quote is None:
            return line[:index]
    return line


def _split_top_level(text: str, *, separator: str = ",") -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    escaped = False
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\" and quote:
            current.append(char)
            escaped = True
            continue
        if char in ('"', "'"):
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            current.append(char)
            continue
        if quote is None:
            if char in "[{(":
                depth += 1
            elif char in "]})":
                depth = max(0, depth - 1)
            elif char == separator and depth == 0:
                parts.append("".join(current).strip())
                current = []
                continue
        current.append(char)
    if current:
        parts.append("".join(current).strip())
    return [part for part in parts if part]


def _value(text: str) -> Any:
    text = text.strip()
    if not text:
        return ""
    if text.startswith("{") and text.endswith("}"):
        result: dict[str, Any] = {}
        for item in _split_top_level(text[1:-1]):
            if "=" not in item:
                continue
            key, _, raw = item.partition("=")
            result[key.strip().strip('"\'')] = _value(raw)
        return result
    if text.startswith("[") and text.endswith("]"):
        return [_value(item) for item in _split_top_level(text[1:-1])]
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
        return text[1:-1].replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
    lowered = text.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        if text.startswith(("0x", "0o", "0b")):
            return int(text, 0)
        if "." in text or "e" in lowered:
            return float(text)
        return int(text)
    except ValueError:
        return text


def loads(text: str) -> dict[str, Any]:
    """Parse the supported TOML subset; return a nested dictionary."""
    if len(text.encode("utf-8", errors="ignore")) > MAX_TEXT_BYTES:
        raise ValueError("manifest is too large for the built-in TOML reader")
    root: dict[str, Any] = {}
    current: dict[str, Any] = root
    for raw_line in text.splitlines():
        line = _strip_comment(raw_line).strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]") and not line.startswith("[["):
            section = line[1:-1].strip()
            parts = [part.strip().strip('"\'') for part in _split_top_level(section, separator=".") if part.strip()]
            current = root
            for part in parts:
                existing = current.get(part)
                if not isinstance(existing, dict):
                    existing = {}
                    current[part] = existing
                current = existing
            continue
        if line.startswith("[[") and line.endswith("]]"):  # array of tables
            section = line[2:-2].strip()
            parts = [part.strip().strip('"\'') for part in _split_top_level(section, separator=".") if part.strip()]
            if not parts:
                continue
            parent = root
            for part in parts[:-1]:
                parent = parent.setdefault(part, {})
                if not isinstance(parent, dict):
                    parent = {}
            array = parent.setdefault(parts[-1], [])
            if not isinstance(array, list):
                array = []
                parent[parts[-1]] = array
            entry: dict[str, Any] = {}
            array.append(entry)
            current = entry
            continue
        if "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip().strip('"\'')
        if not key:
            continue
        current[key] = _value(raw)
    return root
