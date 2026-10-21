"""The multi-input workspace of the fusion engine.

The workspace is a folder that owns two things: the *inventory*
(``workspace.json``, generated) and the *configuration*
(``ece-project.json``, user-authored). Imported inputs live under
``inputs/<name>/`` with their internal structure preserved - the engine never
flattens directories, because flattening breaks relative imports, asset
references and existing build systems.

By default an input is **copied** into the workspace so the workspace is
self-contained; with ``copy=False`` the input is *linked*: it stays where it
is and the inventory only records the reference. Either way the original
location is recorded so the user can trace every file back to its source.

The engine never silently overwrites a file: when a copy destination exists
with different content the add fails with an explicit error, and when the
content is identical the existing file is kept and the conflict is reported.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path
from typing import Any

from .models import CONFIG_FILENAME, GENERATED_DIRNAME, INPUTS_DIRNAME, FusionConfig, WorkspaceInput, now_utc

logger = logging.getLogger(__name__)

#: Metadata file of the workspace (generated; never user-authored).
META_FILENAME = "workspace.json"

#: Internal directories that are never copied into a workspace.
_SKIPPED_DIRS = frozenset({".git", ".hg", ".svn", GENERATED_DIRNAME, ".ece-cache"})

#: File suffixes treated as prebuilt libraries.
_LIBRARY_SUFFIXES = {
    ".lib", ".a", ".dll", ".so", ".dylib", ".pyd", ".so", ".node", ".framework",
}

#: Suffix → language for single files (no scan possible).
_FILE_LANGUAGE: dict[str, str] = {
    **{s: "cpp" for s in (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh")},
    ".rs": "rust",
    ".go": "go",
    ".cs": "csharp",
    ".java": "java",
    ".kt": "kotlin",
    ".py": "python", ".pyw": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".php": "php", ".rb": "ruby", ".lua": "lua",
    ".uproject": "unreal",
    ".uc": "unrealscript",
}


class WorkspaceError(Exception):
    """Raised when a workspace operation cannot be performed."""


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


#: Suffixes treated as game/engine asset files (subset; the graph stage uses a
#: longer table).
_ASSET_SUFFIX_SHORT = {
    ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".wav", ".ogg", ".mp3",
    ".fbx", ".obj", ".gltf", ".glb", ".uasset", ".umap", ".wav", ".aiff",
}


def _asset_file_check(root: Path) -> tuple[int, int]:
    """Return (asset_files, total_files) for a directory."""
    asset_count = 0
    total = 0
    for item in root.rglob("*"):
        if not item.is_file():
            continue
        if any(part in _SKIPPED_DIRS for part in item.relative_to(root).parts[:-1]):
            continue
        total += 1
        if item.suffix.lower() in _ASSET_SUFFIX_SHORT:
            asset_count += 1
    return asset_count, total


def _safe_name(name: str) -> str:
    """Make a filesystem-safe, filesystem-stable folder name for an input."""
    cleaned = "".join(c if c.isalnum() or c in "-_." else "-" for c in name)
    cleaned = cleaned.strip("-.")
    cleaned = "-".join(part for part in cleaned.split("-") if part) or "input"
    if len(cleaned) > 48:
        cleaned = cleaned[:48]
    return cleaned.lower()


class FusionWorkspace:
    """A folder that collects inputs from many languages into one application."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.inputs: list[WorkspaceInput] = []
        self.config = FusionConfig()

    # -- paths ----------------------------------------------------------

    @property
    def inputs_dir(self) -> Path:
        return self.root / INPUTS_DIRNAME

    @property
    def generated_dir(self) -> Path:
        return self.root / GENERATED_DIRNAME

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def meta_path(self) -> Path:
        return self.root / META_FILENAME

    def input_location(self, inp: WorkspaceInput) -> Path:
        """Absolute path where an input lives (copy) or is referenced (link)."""
        if inp.linked:
            return Path(inp.workspace_path)
        return self.root / inp.workspace_path

    # -- create / load / save -------------------------------------------

    def create(self, name: str | None = None) -> None:
        if self.root.exists() and any(self.root.iterdir()):
            if not (self.root / META_FILENAME).exists():
                raise WorkspaceError(
                    f"Directory is not empty and is not a fusion workspace: {self.root}"
                )
        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        if name:
            self.config.name = name
        if not self.config_path.exists():
            self.save()

    @classmethod
    def load(cls, root: str | Path) -> "FusionWorkspace":
        workspace = cls(root)
        if not workspace.meta_path.exists():
            raise WorkspaceError(
                f"Not a fusion workspace (missing {META_FILENAME}): {workspace.root}"
            )
        raw = json.loads(workspace.meta_path.read_text(encoding="utf-8"))
        workspace.inputs = [WorkspaceInput.from_dict(item) for item in raw.get("inputs", [])]
        if workspace.config_path.exists():
            workspace.config = FusionConfig.from_dict(
                json.loads(workspace.config_path.read_text(encoding="utf-8"))
            )
        return workspace

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.inputs_dir.mkdir(parents=True, exist_ok=True)
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(
            json.dumps(
                {
                    "format": "ece-fusion-workspace",
                    "version": 1,
                    "saved_utc": now_utc(),
                    "inputs": [inp.to_dict() for inp in self.inputs],
                },
                indent=2,
                ensure_ascii=False,
            ) + "\n",
            encoding="utf-8",
        )
        self.config_path.write_text(
            json.dumps(self.config.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # -- adding inputs ---------------------------------------------------

    def add(
        self,
        source: str | Path,
        *,
        kind: str | None = None,
        copy: bool = True,
        module_id: str = "",
    ) -> WorkspaceInput:
        """Import one file or directory into the workspace.

        Directories are copied with their structure preserved. Nothing is ever
        overwritten: an existing destination file with different content fails
        the import, identical content is kept and reported in ``notes``.
        """
        src = Path(source).resolve()
        if not src.exists():
            raise WorkspaceError(f"Input does not exist: {src}")
        if self.root == src or self.root in src.parents:
            raise WorkspaceError("Cannot add a path that is inside the workspace itself.")

        is_file = src.is_file()
        resolved_kind = kind or self._suggest_kind(src, is_file)
        if resolved_kind not in {"file", "directory", "project", "asset", "library"}:
            raise WorkspaceError(f"Unknown input kind: {resolved_kind!r}")

        input_id = self._next_input_id()
        folder_name = _safe_name(src.stem if is_file else src.name)
        dest_root = self._unique_input_dir(folder_name)

        notes: list[str] = []
        if copy:
            files, total_bytes = self._copy_into(src, dest_root, is_file, notes)
            workspace_path = dest_root.relative_to(self.root).as_posix()
            scan_root: Path = dest_root
        else:
            files, total_bytes = self._measure(src, is_file)
            workspace_path = str(src)
            scan_root = src
            notes.append("linked in place (not copied)")

        detected_type = self._detect_type(scan_root, is_file)
        if not detected_type and not is_file:
            # No project manifest: fall back to an asset classification.
            asset_count, total = _asset_file_check(scan_root)
            if total and asset_count == total:
                detected_type = "assets"
                if resolved_kind == "directory":
                    resolved_kind = "asset"
                notes.append("asset directory detected")
        if detected_type and detected_type != "unknown":
            pass
        else:
            detected_type = _FILE_LANGUAGE.get(src.suffix.lower(), "")

        inp = WorkspaceInput(
            input_id=input_id,
            source=str(src),
            kind=resolved_kind,
            workspace_path=workspace_path,
            detected_type=detected_type,
            file_count=files,
            total_bytes=total_bytes,
            module_id=module_id,
            linked=not copy,
            notes=notes,
        )
        self.inputs.append(inp)
        self.save()
        return inp

    def _next_input_id(self) -> str:
        highest = 0
        for inp in self.inputs:
            if inp.input_id.startswith("input-"):
                try:
                    highest = max(highest, int(inp.input_id.rsplit("-", 1)[1]))
                except ValueError:
                    continue
        return f"input-{highest + 1:03d}"

    def _unique_input_dir(self, name: str) -> Path:
        candidate = self.inputs_dir / name
        counter = 2
        while candidate.exists():
            candidate = self.inputs_dir / f"{name}-{counter}"
            counter += 1
        return candidate

    def _copy_into(
        self, src: Path, dest_root: Path, is_file: bool, notes: list[str]
    ) -> tuple[int, int]:
        files = 0
        total_bytes = 0
        if is_file:
            dest_root.mkdir(parents=True, exist_ok=True)
            dest = dest_root / src.name
            if dest.exists():
                self._check_no_overwrite(src, dest, notes)
            else:
                shutil.copy2(src, dest)
            files, total_bytes = 1, dest.stat().st_size
            return files, total_bytes

        dest_root.mkdir(parents=True, exist_ok=True)
        for item in sorted(src.rglob("*")):
            if item.is_dir():
                continue
            if any(part in _SKIPPED_DIRS for part in item.relative_to(src).parts[:-1]):
                continue
            rel = item.relative_to(src)
            dest = dest_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                self._check_no_overwrite(item, dest, notes)
                continue
            shutil.copy2(item, dest)
            files += 1
            total_bytes += item.stat().st_size
        skipped = sorted(
            p.name
            for p in src.rglob("*")
            if p.is_dir() and p.name in _SKIPPED_DIRS
        )
        if skipped:
            notes.append("copy skipped internal folders: " + ", ".join(skipped))
        return files, total_bytes

    def _check_no_overwrite(self, src: Path, dest: Path, notes: list[str]) -> None:
        """Never silently overwrite: identical content is kept, differing fails."""
        if sha256_file(src) == sha256_file(dest):
            notes.append(f"kept existing identical file: {dest.name}")
            return
        raise WorkspaceError(
            f"Refusing to overwrite existing file with different content: {dest} "
            f"(source {src}). Remove or rename it first."
        )

    def _measure(self, src: Path, is_file: bool) -> tuple[int, int]:
        files = 0
        total = 0
        if is_file:
            return 1, src.stat().st_size
        for item in src.rglob("*"):
            if item.is_file() and not any(part in _SKIPPED_DIRS for part in item.relative_to(src).parts[:-1]):
                files += 1
                total += item.stat().st_size
        return files, total

    def _suggest_kind(self, src: Path, is_file: bool) -> str:
        if is_file:
            if src.suffix.lower() in _LIBRARY_SUFFIXES:
                return "library"
            if src.suffix.lower() in {".png", ".jpg", ".jpeg", ".tga", ".wav", ".ogg", ".mp3", ".fbx", ".obj", ".glb", ".gltf"}:
                return "asset"
            return "file"
        if any(src.glob("*.uproject")):
            return "project"
        manifests = ("CMakeLists.txt", "Cargo.toml", "go.mod", "package.json", "pyproject.toml")
        if any((src / m).exists() for m in manifests) or list(src.glob("*.csproj")):
            return "directory"  # multi-project dirs are detected by the graph stage
        return "directory"

    def _detect_type(self, scan_root: Path, is_file: bool) -> str:
        """Detect the adapter/type of an input by scanning its real location."""
        if is_file:
            return _FILE_LANGUAGE.get(scan_root.suffix.lower(), "")
        try:
            from ..detection.detector import scan as detect_scan

            result = detect_scan(scan_root)
        except Exception as exc:  # detection must never block an import
            logger.debug("Detection failed for %s: %s", scan_root, exc)
            return ""
        if not result.candidates:
            return ""
        best = result.best
        return best.adapter_id if best else ""

    # -- mutating inputs -------------------------------------------------

    def get_input(self, input_id: str) -> WorkspaceInput:
        for inp in self.inputs:
            if inp.input_id == input_id:
                return inp
        raise WorkspaceError(f"Unknown input id: {input_id}")

    def remove(self, input_id: str, *, delete_files: bool = True) -> None:
        inp = self.get_input(input_id)
        loc = self.input_location(inp)
        if delete_files and not inp.linked and loc.exists() and self.root in loc.parents:
            shutil.rmtree(loc)
        self.inputs.remove(inp)
        self.save()

    def replace(self, input_id: str, new_source: str | Path) -> WorkspaceInput:
        """Point an input at a new source, keeping its id and module assignment."""
        old = self.get_input(input_id)
        self.remove(input_id)
        try:
            return self.add(
                new_source,
                kind=old.kind,
                copy=not old.linked,
                module_id=old.module_id,
            )
        except WorkspaceError:
            # Roll back: re-register the old input so the workspace is unchanged.
            self.inputs.append(old)
            self.save()
            raise

    def assign(self, input_id: str, module_id: str) -> WorkspaceInput:
        inp = self.get_input(input_id)
        inp.module_id = module_id
        self.save()
        return inp

    # -- validation -------------------------------------------------------

    def verify(self) -> list[str]:
        """Check that every copied input is still present on disk."""
        problems: list[str] = []
        for inp in self.inputs:
            loc = self.input_location(inp)
            if not inp.linked and not loc.exists():
                problems.append(
                    f"Input {inp.input_id} is missing from the workspace: {inp.workspace_path}"
                )
        return problems

    def conflicts(self) -> list[dict[str, Any]]:
        """Detect duplicate files and conflicting assignments across inputs.

        Returns a list of ``{"level": ..., "message": ...}`` entries.
        """
        found: list[dict[str, Any]] = []

        # Same content, same file name, different inputs → true duplicates.
        by_hash: dict[str, list[tuple[str, str]]] = {}
        # Same relative path within two inputs but different content.
        by_relpath: dict[str, list[tuple[str, str]]] = {}
        for inp in self.inputs:
            loc = self.input_location(inp)
            if not loc.exists():
                continue
            files = [loc] if loc.is_file() else [
                item for item in sorted(loc.rglob("*")) if item.is_file()
            ]
            for item in files[:5000]:
                rel = item.relative_to(loc).as_posix() if not item == loc else item.name
                digest = sha256_file(item)
                by_hash.setdefault(digest, []).append((inp.input_id, rel))
                key = (inp.module_id or inp.input_id, rel)

        # Cross-input relative-path collisions with differing content.
        seen_relpath: dict[str, tuple[str, str]] = {}
        for inp in self.inputs:
            loc = self.input_location(inp)
            if not loc.exists():
                continue
            files = [loc] if loc.is_file() else [
                item for item in sorted(loc.rglob("*")) if item.is_file()
            ]
            for item in files[:5000]:
                rel = item.name if item == loc else item.relative_to(loc).as_posix()
                digest = sha256_file(item)
                previous = seen_relpath.get(rel)
                if previous and previous[1] != digest:
                    found.append(
                        {
                            "level": "conflict",
                            "message": (
                                f"Path '{rel}' exists in inputs {previous[0]} and "
                                f"{inp.input_id} with different content."
                            ),
                        }
                    )
                elif previous:
                    found.append(
                        {
                            "level": "warning",
                            "message": (
                                f"Path '{rel}' exists in inputs {previous[0]} and "
                                f"{inp.input_id} (identical content, kept first copy)."
                            ),
                        }
                    )
                else:
                    seen_relpath[rel] = (inp.input_id, digest)
                by_hash.setdefault(digest, []).append((inp.input_id, rel))

        # Identical content anywhere in the workspace (informational).
        for digest, spots in sorted(by_hash.items()):
            if len(spots) > 1 and len({rel for _, rel in spots}) > 1:
                inputs = sorted({input_id for input_id, _ in spots})
                if len(inputs) > 1:
                    found.append(
                        {
                            "level": "info",
                            "message": (
                                "Identical file content across inputs "
                                f"{', '.join(inputs)} (sha256 {digest[:12]})."
                            ),
                        }
                    )

        # Inputs assigned to the same module but of different types.
        by_module: dict[str, set[str]] = {}
        for inp in self.inputs:
            if inp.module_id:
                by_module.setdefault(inp.module_id, set()).add(
                    inp.detected_type or inp.kind
                )
        for module_id, types in by_module.items():
            if len(types) > 1:
                found.append(
                    {
                        "level": "conflict",
                        "message": (
                            f"Module '{module_id}' mixes incompatible input types: "
                            + ", ".join(sorted(types))
                        ),
                    }
                )

        return found

    # -- reporting ---------------------------------------------------------

    def inventory(self) -> dict[str, Any]:
        """The project inventory: every input with its origin and role."""
        return {
            "root": str(self.root),
            "name": self.config.name,
            "inputs": [inp.to_dict() for inp in self.inputs],
            "config": self.config.to_dict(),
            "problems": self.verify(),
        }
