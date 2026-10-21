"""Multi-language module graph for the fusion engine.

This stage turns imported inputs into :class:`Module` objects, discovers what
each one really depends on (by parsing the manifests the projects themselves
declare - never by guessing from file names), orders the modules so that a
dependency is always built before the module that needs it, and reports
cycles, multiple entry points and unresolved references.

Every dependency recorded here carries its reason: the exact line or manifest
field that proves it. Nothing is inferred from file names alone.
"""

from __future__ import annotations

import ast
import json
import logging
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from .models import Module, ModuleGraph
from .workspace import _FILE_LANGUAGE, FusionWorkspace, WorkspaceInput

logger = logging.getLogger(__name__)

#: Adapter id → human language/build tool.
LANGUAGE_BY_ADAPTER = {
    "python": "python",
    "node": "javascript",
    "cpp-cmake": "cpp",
    "cpp-meson": "cpp",
    "cpp-make": "cpp",
    "dotnet": "csharp",
    "rust-cargo": "rust",
    "go": "go",
    "java-maven": "java",
    "java-gradle": "java",
    "kotlin-gradle": "kotlin",
    "php": "php",
    "unreal": "unreal",
    "unity": "csharp",
}

BUILD_TOOL_BY_ADAPTER = {
    "python": "python",
    "node": "npm",
    "cpp-cmake": "cmake",
    "cpp-meson": "meson",
    "cpp-make": "make",
    "dotnet": "dotnet",
    "rust-cargo": "cargo",
    "go": "go",
    "java-maven": "maven",
    "java-gradle": "gradle",
    "kotlin-gradle": "gradle",
    "php": "php",
    "unreal": "unreal-buildtool",
    "unity": "unity",
    "assets": "asset-pipeline",
}

_ASSET_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".tga", ".bmp", ".hdr", ".exr", ".ktx",
    ".wav", ".ogg", ".mp3", ".flac", ".aiff",
    ".fbx", ".obj", ".gltf", ".glb", ".stl", ".md5mesh",
    ".uasset", ".umap",
    ".ttf", ".otf",
    ".anim", ".bik", ".usd", ".usda", ".usdc",
}

_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "bin", "obj", "target", "build", "dist", "out", "packages", ".gradle",
    "Binaries", "Intermediate", "Saved", "DerivedDataCache", ".vs", ".idea",
})


def _iter_files(root: Path, limit: int = 8000) -> list[Path]:
    files: list[Path] = []
    if root.is_file():
        return [root]
    for item in sorted(root.rglob("*")):
        if item.is_dir():
            continue
        rel_parts = item.relative_to(root).parts[:-1]
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        files.append(item)
        if len(files) >= limit:
            break
    return files


def _read_text(path: Path, limit: int = 400_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


@dataclass
class _Alias:
    """A name by which other modules may refer to a module."""

    module_id: str
    names: set[str] = field(default_factory=set)
    #: how the module can be consumed
    shared_library: bool = False     # SHARED/MODULE library, cdylib, dll target
    python_extension: bool = False   # builds a .pyd the interpreter can import
    static_library: bool = False


@dataclass
class _NameRegistry:
    modules: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))

    def add(self, module_id: str, *names: str) -> None:
        for name in names:
            if name:
                self.modules[module_id].add(name.strip().lower())

    def lookup(self, name: str) -> list[str]:
        key = name.strip().lower()
        matches: list[str] = []
        for module_id, names in self.modules.items():
            if key in names and module_id not in matches:
                matches.append(module_id)
        return matches


def _add_dependency(module: Module, target: str, reason: str) -> None:
    if not target or target == module.module_id:
        return
    if target not in module.dependencies:
        module.dependencies.append(target)
        module.dependency_reasons[target] = reason

# ---------------------------------------------------------------------------
# Per-language dependency extraction (real evidence only)
# ---------------------------------------------------------------------------


def _extract_python(module: Module, root: Path, files: list[Path], registry: _NameRegistry) -> None:
    import sys

    stdlib = getattr(sys, "stdlib_module_names", frozenset()) or frozenset()
    local_names = registry.modules.get(module.module_id, set())
    external: set[str] = set()
    entry_files: list[str] = []

    for py in files[:400]:
        if py.suffix.lower() not in {".py", ".pyw"}:
            continue
        source = _read_text(py, 250_000)
        if not source:
            continue
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            for name in names or []:
                if not name:
                    continue
                if name in local_names:
                    continue
                hits = registry.lookup(name)
                if hits:
                    _add_dependency(
                        module,
                        hits[0],
                        f"python import '{name}' in {py.relative_to(root).as_posix()}",
                    )
                elif name in stdlib or name == module.module_id.lower():
                    continue
                else:
                    external.add(name)

            if isinstance(node, ast.If):
                test = node.test
                if (
                    isinstance(test, ast.Compare)
                    and "main" in ast.dump(test)
                ):
                    entry_files.append(py.relative_to(root).as_posix())
    if entry_files:
        notes = sorted(set(entry_files))[:5]
        module.notes.append(
            "entry point hints (main guard): " + ", ".join(notes)
        )
    for name in sorted(external)[:30]:
        if name not in module.required_runtime:
            module.required_runtime.append(f"python-package:{name}")


def _extract_cmake(module: Module, root: Path, files: list[Path], registry: _NameRegistry) -> None:
    for cmake in files:
        # Parse only the real build manifests; other files are ignored.
        if cmake.name.lower() not in {"cmakelists.txt"}:
            continue
        text = _read_text(cmake)
        if not text:
            continue
        parent = cmake.parent
        for match in re.finditer(r"add_subdirectory\s*\(\s*([^\s\)]+)", text):
            target = match.group(1).strip('"')
            nested = (parent / target).resolve()
            rel = nested.relative_to(root).as_posix() if root in nested.parents or nested == root else ""
            if rel:
                _add_dependency(module, f"{module.module_id}#{rel}", "cmake add_subdirectory")
        for match in re.finditer(
            r"target_link_libraries\s*\(\s*([A-Za-z0-9_\.\:\-]+)([^)]*)\)", text
        ):
            for lib in re.findall(r"[A-Za-z0-9_][A-Za-z0-9_\.\-]*", match.group(2)):
                if lib.lower() in {"public", "private", "interface", "general", "debug", "optimized", "none"}:
                    continue
                hits = registry.lookup(lib)
                if hits and hits[0] != module.module_id:
                    _add_dependency(module, hits[0], f"cmake target_link_libraries({lib})")
        for match in re.finditer(r"add_executable\s*\(\s*([A-Za-z0-9_\-]+)", text):
            target = match.group(1)
            if target not in module.output_artifacts:
                module.output_artifacts.append(target)
            if f"executable target: {target}" not in module.notes:
                module.notes.append(f"executable target: {target}")
        for match in re.finditer(r"add_library\s*\(\s*([A-Za-z0-9_\-]+)\s+(SHARED|MODULE|STATIC)", text):
            name, kind = match.group(1), match.group(2).upper()
            if name not in module.output_artifacts:
                module.output_artifacts.append(name)
            if kind in {"SHARED", "MODULE"}:
                module.notes.append(f"exports shared library: {name}")
        for match in re.finditer(r"find_package\s*\(\s*([A-Za-z0-9_\-]+)", text):
            pkg = match.group(1)
            if f"find-package:{pkg}" not in module.required_runtime:
                module.required_runtime.append(f"find-package:{pkg}")


def _extract_cargo(module: Module, root: Path, files: list[Path], abs_path: Path, registry: _NameRegistry) -> None:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python 3.11 always has tomllib
        tomllib = None
    cargo = abs_path / "Cargo.toml"
    if not cargo.exists() or tomllib is None:
        return
    try:
        data = tomllib.loads(_read_text(cargo))
    except Exception as exc:
        module.notes.append(f"Cargo.toml could not be parsed: {exc}")
        return
    package = data.get("package") or {}
    name = package.get("name")
    if name:
        module.notes.append(f"cargo package: {name} v{package.get('version', '?')}")
    for dep_name, spec in (data.get("dependencies") or {}).items():
        if isinstance(spec, dict) and spec.get("path"):
            _dependency_from_hint(
                module,
                dep_name,
                spec["path"],
                f"cargo path dependency ({spec['path']})",
                registry,
                cargo.parent,
            )
        else:
            if f"cargo:{dep_name}" not in module.required_runtime:
                module.required_runtime.append(f"cargo:{dep_name}")
    bins = data.get("bin") or []
    if bins or (abs_path / "src" / "main.rs").exists():
        module.notes.append("cargo binary target present")
    lib = data.get("lib") or {}
    crate_types = lib.get("crate-type") or []
    for crate_type in crate_types:
        if crate_type in {"cdylib", "dylib"}:
            module.notes.append("exports shared library (cdylib)")
        elif crate_type == "staticlib":
            module.notes.append("exports static library")


def _add_dependency_path(module: Module, target_name: str, reason: str, hint: str, registry: _NameRegistry, abs_path: Path) -> None:
    """Dependency whose manifest declares a relative path."""
    resolved = (abs_path / hint).resolve()
    hits = registry.lookup(target_name)
    if hits and hits[0] != module.module_id:
        _add_dependency(module, hits[0], reason)
    else:
        module.notes.append(
            f"unresolved {reason}: not in any imported input ({hint})"
        )
        _ = resolved


def _extract_go(module: Module, root: Path, files: list[Path], abs_path: Path, registry: _NameRegistry) -> None:
    gomod = abs_path / "go.mod"
    if gomod.exists():
        text = _read_text(gomod)
        match = re.search(r"^module\s+(\S+)", text, re.MULTILINE)
        if match:
            module.notes.append(f"go module: {match.group(1)}")
        for match in re.finditer(
            r"replace\s+\S+\s*=>\s*(\./[^\s]+)", text
        ):
            hint = match.group(1)
            last = hint.rstrip("/\\").rsplit("/", 1)[-1]
            _dependency_from_hint(module, last, hint, f"go.mod replace ({hint})", registry, abs_path)
    if files:
        for go in files[:200]:
            if go.suffix == ".go" and re.search(r"^package main\b", _read_text(go, 2_000), re.MULTILINE):
                module.notes.append("go main package present")
                break


def _dependency_from_hint(
    module: Module, name: str, hint: str, reason: str, registry: _NameRegistry, abs_path: Path
) -> None:
    hits = registry.lookup(name)
    if hits and hits[0] != module.module_id:
        _add_dependency(module, hits[0], reason)
    else:
        module.notes.append(f"unresolved {reason}: not in any imported input ({hint})")
        _ = abs_path


def _extract_dotnet(module: Module, root: Path, files: list[Path], abs_path: Path, registry: _NameRegistry) -> None:
    proj = None
    for candidate in files:
        if candidate.suffix.lower() in {".csproj", ".fsproj", ".vbproj"}:
            proj = candidate
            break
    if proj is None:
        return
    text = _read_text(proj)
    for match in re.finditer(
        r"<ProjectReference\s+[^>]*Include\s*=\s*\"([^\"]+)\"", text
    ):
        hint = match.group(1)
        name = hint.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        name = name.rsplit(".", 1)[0]
        _dependency_from_hint(module, name, hint, f"ProjectReference ({hint})", registry, abs_path)
    for match in re.finditer(
        r"<PackageReference\s+[^>]*Include\s*=\s*\"([^\"]+)\"", text
    ):
        pkg = match.group(1)
        if f"nuget:{pkg}" not in module.required_runtime:
            module.required_runtime.append(f"nuget:{pkg}")
    tfm = re.search(r"<TargetFramework>([^<]+)</TargetFramework>", text)
    if tfm:
        if f"dotnet-runtime:{tfm.group(1)}" not in module.required_runtime:
            module.required_runtime.append(f"dotnet-runtime:{tfm.group(1)}")
    if re.search(r"<OutputType>\s*(Win)?Exe\s*</OutputType>", text):
        module.notes.append("executable assembly target")


def _extract_node(module: Module, root: Path, files: list[Path], abs_path: Path, registry: _NameRegistry) -> None:
    pkg = abs_path / "package.json"
    if not pkg.exists():
        return
    try:
        data = json.loads(_read_text(pkg))
    except Exception as exc:
        module.notes.append(f"package.json could not be parsed: {exc}")
        return
    name = data.get("name")
    if name:
        module.notes.append(f"npm package: {name}")
    dependencies = data.get("dependencies") or {}
    for dep_name, spec in dependencies.items():
        spec_str = spec if isinstance(spec, str) else str(spec)
        hint = ""
        for prefix in ("file:", "link:"):
            if spec_str.startswith(prefix):
                hint = spec_str[len(prefix):]
                break
        if hint:
            _dependency_from_hint(module, dep_name, hint, f"npm {prefix}{hint} dependency ({dep_name})", registry, abs_path)
        else:
            if f"npm:{dep_name}" not in module.required_runtime:
                module.required_runtime.append(f"npm:{dep_name}")
    workspaces = data.get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    for pattern in workspaces or []:
        hits = registry.lookup(str(pattern).replace("*", "").strip("/") or "")
        for hit in hits:
            if hit != module.module_id:
                _add_dependency(module, hit, f"npm workspace pattern '{pattern}'")
    if data.get("main") or data.get("bin") or (data.get("scripts") or {}).get("start"):
        module.notes.append("npm entry point fields present")


def _extract_unreal(module: Module, root: Path, files: list[Path], abs_path: Path, registry: _NameRegistry) -> None:
    uproject = None
    for candidate in files:
        if candidate.suffix.lower() == ".uproject":
            uproject = candidate
            break
    if uproject is None:
        return
    try:
        data = json.loads(_read_text(uproject))
    except Exception as exc:
        module.notes.append(f".uproject could not be parsed: {exc}")
        return
    engine = data.get("EngineAssociation")
    if engine:
        module.required_runtime.append(f"unreal-engine:{engine}")
    for mod in data.get("Modules", []):
        name = mod.get("Name", "")
        mtype = mod.get("Type", "")
        if name:
            module.notes.append(f"unreal module: {name} ({mtype or 'runtime'})")
            if registry:
                registry.add(module.module_id, name)
    for plugin in data.get("Plugins", []):
        pname = plugin.get("Name", "")
        enabled = plugin.get("Enabled", True)
        if pname:
            module.required_runtime.append(f"unreal-plugin:{pname}" + ("" if enabled else " (disabled)"))
    for build_cs in files:
        if build_cs.name.endswith("Build.cs"):
            text = _read_text(build_cs)
            for match in re.finditer(r"PublicDependencyModuleNames\s*=\s*new\[\]\s*\{([^}]*)\}", text):
                for dep in re.findall(r"\"([^\"]+)\"", match.group(1)):
                    hits = registry.lookup(dep)
                    if hits and hits[0] != module.module_id:
                        _add_dependency(module, hits[0], f"Build.cs PublicDependencyModuleNames({dep})")
            engine_match = re.search(r"UE\s*Version|EngineVersion", text)
            if engine_match:
                cls = build_cs.name
                module.notes.append(f"Build.cs: {cls} targets the installed engine version")


def _extract_assets(module: Module, root: Path, files: list[Path]) -> None:
    by_kind: dict[str, int] = defaultdict(int)
    for f in files:
        suffix = f.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".hdr", ".exr"}:
            by_kind["textures"] += 1
        elif suffix in {".wav", ".ogg", ".mp3", ".flac"}:
            by_kind["audio"] += 1
        elif suffix in {".fbx", ".obj", ".gltf", ".glb", ".stl"}:
            by_kind["models"] += 1
        elif suffix in {".uasset", ".umap"}:
            by_kind["unreal-assets"] += 1
        else:
            by_kind["other"] += 1
    module.notes.append(
        "asset inventory: " + (", ".join(f"{k}={v}" for k, v in sorted(by_kind.items())) or "empty")
    )


# ---------------------------------------------------------------------------
# Module discovery
# ---------------------------------------------------------------------------


def _candidate_modules_for_input(workspace: FusionWorkspace, inp: WorkspaceInput) -> list[tuple[str, str]]:
    """Return [(root_relative, adapter_id)] detected inside one input."""
    location = workspace.input_location(inp)
    adapter = inp.detected_type
    if inp.kind == "asset" or (adapter == "" and inp.kind in {"directory", "file"}):
        files = _iter_files(location)
        if files and all(f.suffix.lower() in _ASSET_SUFFIXES for f in files):
            if adapter == "" or inp.kind == "asset":
                return [("", "assets")]
    if not adapter:
        return []
    try:
        from ..detection.detector import scan as detect_scan

        result = detect_scan(location)
    except Exception as exc:
        logger.debug("Graph detection failed on %s: %s", location, exc)
        return []
    candidates = [
        (c.root, c.adapter_id)
        for c in result.candidates
        if c.confidence >= 0.4 and c.adapter_id != adapter
    ]
    return candidates


def _module_language(adapter_id: str, inp: WorkspaceInput) -> str:
    if adapter_id == "assets":
        return "assets"
    return LANGUAGE_BY_ADAPTER.get(adapter_id) or inp.detected_type or "unknown"


def _module_id_for(base: str, taken: set[str], inp: WorkspaceInput) -> str:
    candidate = base if base and base not in taken else ""
    if not candidate:
        stem = inp.workspace_path.rsplit("/", 1)[-1] if inp.workspace_path else inp.input_id
        candidate = stem or inp.input_id
    suffix = 2
    unique = candidate
    while unique in taken:
        unique = f"{candidate}-{suffix}"
        suffix += 1
    taken.add(unique)
    return unique


def _top_level_python_names(module_root: Path) -> list[str]:
    names: list[str] = []
    for item in sorted(module_root.iterdir()) if module_root.exists() else []:
        if item.is_dir() and "__init__.py" in [p.name for p in item.iterdir()]:
            names.append(item.name)
        elif item.is_file() and item.suffix == ".py":
            names.append(item.stem)
    return names


def build_graph(workspace: FusionWorkspace) -> ModuleGraph:
    """Build the unified module graph of a workspace."""
    graph = ModuleGraph()
    graph.entry_point = workspace.config.entry_point
    registry = _NameRegistry()
    modules: list[Module] = []
    taken_ids: set[str] = set()

    inputs_by_id = {inp.input_id: inp for inp in workspace.inputs}
    # First pass: create modules so names can be matched across inputs.
    pending: list[tuple[WorkspaceInput, list[tuple[str, str]]]] = []
    for inp in workspace.inputs:
        location = workspace.input_location(inp)
        if not location.exists():
            continue
        candidates = _candidate_modules_for_input(workspace, inp)
        if not candidates and inp.detected_type:
            candidates = [("", inp.detected_type)]
        elif not candidates and inp.kind == "file":
            language = _FILE_LANGUAGE.get(Path(inp.source).suffix.lower(), inp.detected_type)
            candidates = [("", language)]
        pending.append((inp, candidates))

    for inp, candidates in pending:
        for root, adapter_id in candidates:
            stem = (root.rsplit("/", 1)[-1] if root else "") or (
                Path(inp.workspace_path).stem
                if inp.kind == "file"
                else Path(inp.workspace_path).name
            )
            module_id = _module_id_for(stem, taken_ids, inp)
            module = Module(
                module_id=module_id,
                language=_module_language(adapter_id, inp),
                project_type=adapter_id,
                adapter_id=adapter_id,
                input_id=inp.input_id,
                root=root,
                build_tool=BUILD_TOOL_BY_ADAPTER.get(adapter_id, ""),
                build_configuration=workspace.config.configuration,
                target_platform=workspace.config.target_os,
                target_architecture=workspace.config.target_arch,
            )
            modules.append(module)
            registry.add(module_id, module_id, stem)
            if module.language == "cpp" and module.root == "":
                registry.add(module_id, module_id)

    # Second pass: fill sources, exports, dependencies.
    file_language = _FILE_LANGUAGE
    for module in modules:
        inp = inputs_by_id.get(module.input_id)
        if inp is None:
            continue
        location = workspace.input_location(inp)
        module_root_abs = location / module.root if module.root else location
        files = _iter_files(module_root_abs)
        rel_files = [f.relative_to(module_root_abs).as_posix() for f in files if f.is_file()]
        asset_files = [f for f in rel_files if Path(f).suffix.lower() in _ASSET_SUFFIXES]
        source_files = [f for f in rel_files if f not in asset_files]
        module.source_files = source_files[:4000]
        module.asset_files = asset_files[:4000]

        aliases = _Alias(module.module_id)
        if module.language == "python":
            for name in _top_level_python_names(module_root_abs):
                registry.add(module.module_id, name)
                aliases.names.add(name)
        elif module.language == "cpp":
            registry.add(module.module_id, Path(module.root).name or module.module_id)
        elif module.language == "assets":
            _extract_assets(module, module_root_abs, files)

        if module.adapter_id == "python":
            _extract_python(module, module_root_abs, files, registry)
        elif module.adapter_id in {"cpp-cmake", "cpp-meson", "cpp-make"}:
            _extract_cmake(module, module_root_abs, files, registry)
        elif module.adapter_id == "rust-cargo":
            _extract_cargo(module, module_root_abs, files, module_root_abs, registry)
        elif module.adapter_id == "go":
            _extract_go(module, module_root_abs, files, module_root_abs, registry)
        elif module.adapter_id == "dotnet":
            _extract_dotnet(module, module_root_abs, files, module_root_abs, registry)
        elif module.adapter_id == "node":
            _extract_node(module, module_root_abs, files, module_root_abs, registry)
        elif module.adapter_id == "unreal":
            _extract_unreal(module, module_root_abs, files, module_root_abs, registry)
            sub = module_root_abs / "Source"
            if sub.exists():
                for mod_dir in dir_entries(sub):
                    registry.add(module.module_id, mod_dir.name)

    graph.modules = modules

    # Deduplicate container-level pseudo dependencies created by add_subdirectory.
    for module in modules:
        module.dependencies = [
            dep for dep in module.dependencies if dep != module.module_id
        ]

    _resolve_hints_and_order(graph, workspace)
    _detect_entry_points(graph, workspace)
    return graph


def dir_entries(path: Path) -> list[Path]:
    return sorted(p for p in path.iterdir() if p.is_dir())


def _resolve_hints_and_order(graph: ModuleGraph, workspace: FusionWorkspace) -> None:
    """Attach configured dependencies, detect cycles and compute the order."""
    modules = graph.modules
    by_id = {m.module_id for m in modules}
    # Resolve pseudo targets first (e.g. "lib#sub" produced by add_subdirectory).
    for module in modules:
        for dep in list(module.dependencies):
            if "#" in dep:
                container = dep.split("#", 1)[0]
                module.dependencies.remove(dep)
                module.dependency_reasons.pop(dep, None)
                note = module.dependency_reasons.get(container)
                reason = note or "nested build target"
                if container in by_id and container != module.module_id:
                    _add_dependency(module, container, reason)
                elif container == module.module_id:
                    if reason not in module.notes:
                        module.notes.append(f"nested build target: {dep}")
    # Config-declared dependencies always apply (user-authored, explicit).
    for module_id, deps in workspace.config.module_dependencies.items():
        target = module_id if module_id in by_id else _match_alias(module_id, modules)
        for dep in deps:
            dep_target = dep if dep in by_id else _match_alias(dep, modules)
            module = next((m for m in modules if m.module_id == target), None)
            if module and dep_target and dep_target != target:
                _add_dependency(module, dep_target, "configured in ece-project.json")

    # Kahn's algorithm with deterministic tie-breaks.
    deps_of: dict[str, set[str]] = {}
    for module in modules:
        declared = {d for d in module.dependencies if d in by_id}
        # Pseudo targets (module#sub) resolve to their container module.
        resolved: set[str] = set()
        for dep in declared:
            container = dep.split("#", 1)[0]
            if container in by_id and container != module.module_id:
                resolved.add(container)
                reason = module.dependency_reasons.get(dep, "")
                module.dependency_reasons.setdefault(container, reason or "nested target")
        deps_of[module.module_id] = resolved

    indegree: dict[str, int] = defaultdict(int)
    dependents: dict[str, list[str]] = defaultdict(list)
    for module_id, deps in deps_of.items():
        for dep in deps:
            indegree[module_id] += 1
            dependents[dep].append(module_id)

    ready = deque(sorted(mid for mid in deps_of if indegree[mid] == 0))
    order: list[str] = []
    while ready:
        module_id = ready.popleft()
        order.append(module_id)
        for dep in sorted(dependents[module_id]):
            indegree[dep] -= 1
            if indegree[dep] == 0:
                ready.append(dep)
        ready = deque(sorted(ready))

    if len(order) < len(deps_of):
        cyclic = sorted(set(deps_of) - set(order))
        graph.cycles = [cyclic]
        graph.conflicts.append(
            "Dependency cycle detected among: "
            + ", ".join(cyclic)
            + ". Break the cycle by removing one of the "
            "declared dependencies (each dependency lists the manifest line "
            "that proves it in the module notes)."
        )
        order.extend(cyclic)
    graph.order = order

    # Keep only dependencies that resolve to real modules (with their reasons).
    for module in modules:
        kept = [dep for dep in module.dependencies if dep in by_id]
        module.dependencies = kept
        module.dependency_reasons = {
            dep: reason for dep, reason in module.dependency_reasons.items() if dep in by_id
        }


def _match_alias(name: str, modules: list[Module]) -> str:
    key = name.strip().lower()
    for module in modules:
        if module.module_id.lower() == key:
            return module.module_id
    return ""


def _detect_entry_points(graph: ModuleGraph, workspace: FusionWorkspace) -> None:
    """Collect entry-point candidates; never silently choose one."""
    candidates: list[str] = []
    for module in graph.modules:
        if module.adapter_id == "unreal":
            candidates.append(module.module_id)
        elif any("main guard" in note for note in module.notes):
            candidates.append(module.module_id)
        elif module.adapter_id == "rust-cargo" and any(
            "cargo binary" in note for note in module.notes
        ):
            candidates.append(module.module_id)
        elif module.adapter_id == "go" and any("go main package" in n for n in module.notes):
            candidates.append(module.module_id)
        elif module.adapter_id == "node" and any(
            "npm entry point" in note for note in module.notes
        ):
            candidates.append(module.module_id)
        elif module.adapter_id == "dotnet" and any(
            "executable assembly" in note for note in module.notes
        ):
            candidates.append(module.module_id)
        elif module.adapter_id in {"cpp-cmake", "cpp-meson", "cpp-make"} and any(
            "executable target:" in note for note in module.notes
        ):
            candidates.append(module.module_id)

    config_entry = workspace.config.entry_point.strip()
    if config_entry:
        resolved = _match_alias(config_entry, graph.modules)
        if not resolved:
            module = next((m for m in graph.modules if config_entry in m.source_files), None)
            resolved = module.module_id if module else ""
        if resolved:
            graph.entry_point = resolved
        else:
            graph.conflicts.append(
                f"Configured entry point '{config_entry}' does not match any module."
            )
        return

    unique = sorted(set(candidates))
    if not unique:
        graph.conflicts.append(
            "No entry point found. Set 'entry_point' in ece-project.json to a "
            "module id (e.g. the module whose code starts the application)."
        )
    elif len(unique) == 1:
        graph.entry_point = unique[0]
    else:
        graph.conflicts.append(
            "Multiple entry-point candidates ("
            + ", ".join(unique)
            + "). Set 'entry_point' in ece-project.json to the module that "
            "should start the application; ECE will not choose silently."
        )