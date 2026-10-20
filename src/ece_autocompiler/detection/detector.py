"""Project detection.

Scans a directory tree (never executing anything) and reports one or more
project candidates. A repository may legitimately contain several subprojects
or several languages, so the detector returns a ranked list and flags ambiguous
cases instead of silently picking one.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Limits
from ..errors import DetectionError
from ..logging_config import get_logger
from .markers import (
    DEFAULT_PACKAGE_MANAGERS,
    IGNORED_DIRECTORIES,
    MARKERS,
    NODE_LOCKFILES,
    PYTHON_LOCKFILES,
    SOURCE_HINTS,
    STRONG,
    WEAK,
    DetectionCandidate,
    Marker,
)

log = get_logger("detection")

# Adapter display metadata used when a candidate is surfaced to the user.
ADAPTER_LABELS = {
    "python": "Python",
    "node": "Node.js",
    "dotnet": ".NET / C#",
    "rust-cargo": "Rust (Cargo)",
    "go": "Go",
    "java-maven": "Java (Maven)",
    "java-gradle": "Java (Gradle)",
    "kotlin-gradle": "Kotlin (Gradle)",
    "cpp-cmake": "C/C++ (CMake)",
    "cpp-meson": "C/C++ (Meson)",
    "cpp-make": "C/C++ (Make)",
    "php": "PHP",
    "unreal": "Unreal Engine",
    "unity": "Unity",
}

#: Source suffix -> language, built once from ``SOURCE_HINTS`` so the scan
#: needs one dictionary lookup per file instead of one comparison per language.
_SUFFIX_TO_LANGUAGE: dict[str, str] = {}
for _language, _extensions in SOURCE_HINTS.items():
    for _extension in _extensions:
        _SUFFIX_TO_LANGUAGE.setdefault(_extension, _language)


@dataclass
class ScanResult:
    root: Path
    files: list[str] = field(default_factory=list)
    directory_count: int = 0
    total_files: int = 0
    truncated: bool = False
    candidates: list[DetectionCandidate] = field(default_factory=list)
    ambiguous: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def best(self) -> DetectionCandidate | None:
        return self.candidates[0] if self.candidates else None

    def summary(self) -> str:
        if not self.candidates:
            return "No supported project type was detected."
        parts = []
        for c in self.candidates[:4]:
            label = ADAPTER_LABELS.get(c.adapter_id, c.adapter_id)
            piece = f"{label} ({int(c.confidence * 100)}%{', at ' + (c.root or '.') if c.root else ''})"
            if c.package_manager:
                piece += f" via {c.package_manager}"
            parts.append(piece)
        text = "Detected: " + "; ".join(parts)
        if self.ambiguous:
            text += " - more than one project found, choose one."
        return text


def _relative(root: Path, path: Path) -> str:
    """The path of *path* relative to *root*, with forward slashes.

    ``os.path.relpath`` is used instead of ``Path.relative_to`` because it is
    a plain string operation: the scan calls this once per file, and the
    pathlib version dominated the scan time on large trees.
    """
    try:
        return os.path.relpath(str(path), str(root)).replace("\\", "/")
    except ValueError:  # different drives on Windows
        return path.as_posix()


def _walk(root: Path, limits: Limits) -> tuple[list[str], set[str], int, bool]:
    """Return (relative files, relative directories, file count, truncated)."""
    files: list[str] = []
    directories: set[str] = set()
    truncated = False

    root_depth = len(root.resolve().parts)
    for current, dirnames, filenames in os.walk(root, topdown=True):
        current_path = Path(current)
        depth = len(current_path.resolve().parts) - root_depth
        # Prune ignored and over-deep directories in place.
        dirnames[:] = [
            d
            for d in dirnames
            if d not in IGNORED_DIRECTORIES and not d.startswith(".") and depth < limits.max_scan_depth
        ]
        rel_dir = _relative(root, current_path)
        if rel_dir != ".":
            directories.add(rel_dir)
            prefix = rel_dir + "/"
        else:
            prefix = ""

        for name in filenames:
            if len(files) >= limits.max_scan_files:
                truncated = True
                return files, directories, len(files), truncated
            files.append(prefix + name)

    return files, directories, len(files), truncated


def _directory_index(directory_files: list[str]) -> tuple[set[str], dict[str, str]]:
    """Index the files of one directory for fast marker matching.

    Returns the lowercased names present and a map from every suffix (and
    from the whole name) to the first file that has it. Building this once per
    directory instead of once per marker is what makes the scan linear in the
    number of files instead of files times markers.
    """
    names: set[str] = set()
    by_suffix: dict[str, str] = {}
    for entry in directory_files:
        base = os.path.basename(entry)
        lowered = base.lower()
        names.add(lowered)
        by_suffix.setdefault(lowered, base)
        suffix = os.path.splitext(lowered)[1]
        if suffix:
            by_suffix.setdefault(suffix, base)
    return names, by_suffix


def _match_markers(
    directory_files: list[str],
    names_present: set[str] | None = None,
    by_suffix: dict[str, str] | None = None,
) -> list[tuple[Marker, str]]:
    """Return (marker, evidence_file) for every marker matching this directory."""
    if names_present is None or by_suffix is None:
        names_present, by_suffix = _directory_index(directory_files)
    matched: list[tuple[Marker, str]] = []

    for marker in MARKERS:
        hit: str | None = None
        if marker.names:
            for name in marker.names:
                if name in names_present:
                    hit = by_suffix.get(name, name)
                    break
        if hit is None and marker.suffixes:
            for suffix in marker.suffixes:
                found = by_suffix.get(suffix)
                if found:
                    hit = found
                    break
        if hit is None:
            continue
        if marker.requires and not all(r.lower() in names_present for r in marker.requires):
            continue
        matched.append((marker, hit))

    return matched


def _detect_unity(root: Path, directory: str, directory_set: set[str]) -> bool:
    """Unity projects are identified by their directory shape, not a manifest."""
    base = directory
    assets = f"{base}/Assets" if base else "Assets"
    settings = f"{base}/ProjectSettings" if base else "ProjectSettings"
    return assets in directory_set and settings in directory_set


def _package_manager_for(adapter_id: str, names_present: set[str]) -> str | None:
    """Pick the package manager for a candidate from its lock files."""
    table = NODE_LOCKFILES if adapter_id == "node" else PYTHON_LOCKFILES if adapter_id == "python" else None
    if table is None:
        return None
    for lock, manager in table.items():
        if lock in names_present:
            return manager
    return DEFAULT_PACKAGE_MANAGERS.get(adapter_id)


def scan(
    root: Path | str,
    *,
    limits: Limits | None = None,
    file_list: list[Path] | None = None,
) -> ScanResult:
    """Scan *root* for project candidates.

    ``file_list`` optionally restricts detection to an explicit set of files
    (used when the user drags individual files in). ``root`` is still the base
    directory for relative paths.
    """
    limits = limits or Limits()
    root = Path(root)
    if not root.exists():
        raise DetectionError("Path does not exist", detail=str(root))

    result = ScanResult(root=root)

    if file_list is not None:
        rel_files = [_relative(root, Path(f)) for f in file_list]
        directories: set[str] = set()
        for rel in rel_files:
            parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
            directories.add(parent)
        result.files = rel_files
        result.total_files = len(rel_files)
        result.directory_count = len(directories)
    else:
        files, directories, count, truncated = _walk(root, limits)
        result.files = files
        result.directory_count = len(directories)
        result.total_files = count
        result.truncated = truncated
        if truncated:
            result.notes.append(
                f"Scan stopped after {limits.max_scan_files} files; detection may be incomplete."
            )

    # Group files by directory once, so marker matching is cheap.
    by_dir: dict[str, list[str]] = {}
    for rel in result.files:
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        by_dir.setdefault(parent, []).append(rel)

    all_dirs = set(by_dir.keys()) | directories
    candidates: dict[tuple[str, str], DetectionCandidate] = {}

    # Directory-shape detections first. Include the scanned root ("") so a
    # project whose Assets/ and ProjectSettings/ sit directly under root is
    # recognised.
    for directory in sorted(all_dirs | {""}):
        if _detect_unity(root, directory, set(by_dir.keys()) | directories):
            key = ("unity", directory)
            candidates[key] = DetectionCandidate(
                adapter_id="unity",
                project_type="unity",
                root=directory,
                confidence=STRONG,
                evidence=[f"{directory + '/' if directory else ''}Assets", "ProjectSettings"],
                notes="Unity project detected by directory layout.",
            )

    # Manifest / marker detections.
    for directory, dir_files in by_dir.items():
        names_present, by_suffix = _directory_index(dir_files)
        matches = _match_markers(dir_files, names_present, by_suffix)
        for marker, evidence in matches:
            key = (marker.adapter_id, directory)
            evidence_path = f"{directory}/{evidence}" if directory else evidence
            existing = candidates.get(key)
            if existing is None:
                candidates[key] = DetectionCandidate(
                    adapter_id=marker.adapter_id,
                    project_type=marker.project_type,
                    root=directory,
                    confidence=marker.confidence,
                    evidence=[evidence_path],
                    package_manager=_package_manager_for(marker.adapter_id, names_present),
                )
            else:
                existing.evidence.append(evidence_path)
                # Corroborating markers raise confidence, capped below 1.0.
                existing.confidence = min(0.98, existing.confidence + 0.05)
                if marker.project_type != "node":
                    existing.project_type = marker.project_type

    # No manifest: fall back to source-file hints so we can still say something
    # useful about a bare directory of sources.
    if not candidates and result.files:
        candidates.update(_detect_by_sources(root, by_dir))

    ranked = sorted(
        candidates.values(),
        key=lambda c: (-c.confidence, c.root.count("/"), c.root, c.adapter_id),
    )

    # Merge duplicate adapter detections: keep the strongest per adapter unless
    # they are genuinely separate subprojects at different roots.
    result.candidates = ranked
    if len(ranked) >= 2:
        top = ranked[0]
        strong = [c for c in ranked if c.confidence >= STRONG - 1e-9]
        distinct_adapters = {c.adapter_id for c in strong}
        if len(distinct_adapters) > 1:
            result.ambiguous = True
        elif len({c.root for c in strong}) > 1:
            result.ambiguous = True
            result.notes.append("Multiple subprojects of the same type were found.")

    log.info(
        "Scanned %s: files=%d candidates=%d ambiguous=%s",
        root,
        result.total_files,
        len(result.candidates),
        result.ambiguous,
    )
    return result


def _detect_by_sources(root: Path, by_dir: dict[str, list[str]]) -> dict[tuple[str, str], DetectionCandidate]:
    """Detect a language from source-file extensions when no manifest exists."""
    found: dict[tuple[str, str], DetectionCandidate] = {}
    for directory, dir_files in by_dir.items():
        counts: dict[str, int] = {}
        for rel in dir_files:
            # One dictionary lookup per file instead of one comparison per
            # language: this runs on every file of a large tree.
            language = _SUFFIX_TO_LANGUAGE.get(os.path.splitext(rel)[1].lower())
            if language:
                counts[language] = counts.get(language, 0) + 1
        if not counts:
            continue
        language = max(counts, key=lambda k: counts[k])
        if counts[language] < 1:
            continue
        adapter = {
            "python": "python",
            "node": "node",
            "typescript": "node",
            "csharp": "dotnet",
            "java": "java-gradle",
            "kotlin": "kotlin-gradle",
            "cpp": "cpp-cmake",
            "rust": "rust-cargo",
            "go": "go",
            "php": "php",
            "web": "web",
        }.get(language)
        if adapter is None:
            continue
        confidence = WEAK if counts[language] < 3 else WEAK + 0.1
        key = (adapter, directory)
        found[key] = DetectionCandidate(
            adapter_id=adapter,
            project_type=language,
            root=directory,
            confidence=confidence,
            evidence=[f"{counts[language]} source file(s)"],
            notes="No build manifest found; detected from source files only.",
            package_manager=_package_manager_for(
                adapter, {os.path.basename(f).lower() for f in dir_files}
            ),
        )
    return found
