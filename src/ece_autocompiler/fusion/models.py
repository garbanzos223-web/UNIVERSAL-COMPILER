"""Data model of the fusion engine: configuration, inputs, modules and graph.

Everything here is plain data: it can be written to JSON, inspected by the
user and compared between runs. The engine never hides a decision - every
field that was inferred says so, and every conflict is reported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: The file that holds the user-authored configuration of a workspace.
CONFIG_FILENAME = "ece-project.json"

#: The folder with everything the engine generates (never user-authored).
GENERATED_DIRNAME = "ece-generated"

#: The folder with the imported inputs.
INPUTS_DIRNAME = "inputs"


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class FusionConfig:
    """The explicit configuration of a fused application.

    Every field can be set by the user in ``ece-project.json``. The engine
    only fills a field when the evidence is strong enough, and it records
    that it did so.
    """

    name: str = "application"
    version: str = "1.0.0"
    #: Module id (or workspace-relative path) of the application entry point.
    entry_point: str = ""
    target_os: str = "windows"
    target_arch: str = "x64"
    configuration: str = "release"
    #: module id -> module ids it needs.
    module_dependencies: dict[str, list[str]] = field(default_factory=dict)
    #: "moduleA+moduleB" -> integration method id.
    integration_methods: dict[str, str] = field(default_factory=dict)
    #: Workspace-relative folders that hold assets.
    asset_dirs: list[str] = field(default_factory=list)
    #: Inclusion/exclusion rules for the final package.
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    #: "ece" | "zip" | "folder" | "exe"
    package_format: str = "ece"
    optimization_profile: str = "none"
    #: module id -> command that tests it.
    test_commands: dict[str, str] = field(default_factory=dict)
    #: Extra runtime components the application needs.
    runtime_requirements: list[dict[str, Any]] = field(default_factory=list)
    #: Toolchain overrides: "cmake" -> "C:/tools/cmake/bin/cmake.exe".
    toolchains: dict[str, str] = field(default_factory=dict)
    #: Roles for Python components inside an engine project.
    python_roles: dict[str, str] = field(default_factory=dict)
    #: Run Unreal's BuildCookRun packaging when the engine is installed.
    package_unreal: bool = False
    description: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "entry_point": self.entry_point,
            "target": {
                "os": self.target_os,
                "architecture": self.target_arch,
                "configuration": self.configuration,
            },
            "module_dependencies": {k: list(v) for k, v in self.module_dependencies.items()},
            "integration_methods": dict(self.integration_methods),
            "asset_dirs": list(self.asset_dirs),
            "include": list(self.include),
            "exclude": list(self.exclude),
            "package_format": self.package_format,
            "optimization_profile": self.optimization_profile,
            "test_commands": dict(self.test_commands),
            "runtime_requirements": list(self.runtime_requirements),
            "toolchains": dict(self.toolchains),
            "python_roles": dict(self.python_roles),
            "package_unreal": self.package_unreal,
            "description": self.description,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FusionConfig":
        raw = raw or {}
        target = raw.get("target") or {}
        known = {
            "name", "version", "entry_point", "target", "module_dependencies",
            "integration_methods", "asset_dirs", "include", "exclude",
            "package_format", "optimization_profile", "test_commands",
            "runtime_requirements", "toolchains", "python_roles", "package_unreal",
            "description", "notes",
        }
        config = cls(
            name=str(raw.get("name", "application")),
            version=str(raw.get("version", "1.0.0")),
            entry_point=str(raw.get("entry_point", "")),
            target_os=str(target.get("os", "windows")),
            target_arch=str(target.get("architecture", "x64")),
            configuration=str(target.get("configuration", "release")),
            module_dependencies={
                str(k): [str(x) for x in v]
                for k, v in (raw.get("module_dependencies") or {}).items()
            },
            integration_methods={
                str(k): str(v) for k, v in (raw.get("integration_methods") or {}).items()
            },
            asset_dirs=[str(x) for x in (raw.get("asset_dirs") or [])],
            include=[str(x) for x in (raw.get("include") or [])],
            exclude=[str(x) for x in (raw.get("exclude") or [])],
            package_format=str(raw.get("package_format", "ece")),
            optimization_profile=str(raw.get("optimization_profile", "none")),
            test_commands={str(k): str(v) for k, v in (raw.get("test_commands") or {}).items()},
            runtime_requirements=list(raw.get("runtime_requirements") or []),
            toolchains={str(k): str(v) for k, v in (raw.get("toolchains") or {}).items()},
            python_roles={str(k): str(v) for k, v in (raw.get("python_roles") or {}).items()},
            package_unreal=bool(raw.get("package_unreal", False)),
            description=str(raw.get("description", "")),
            notes=str(raw.get("notes", "")),
        )
        # Unknown keys are kept so a newer tool's configuration survives.
        config_unknown = {k: v for k, v in raw.items() if k not in known}
        if config_unknown:
            config.notes = (config.notes + "\n" if config.notes else "") + repr(config_unknown)
        return config


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass
class WorkspaceInput:
    """One imported input: a file, a folder, a project or an asset folder."""

    input_id: str
    #: Where the input came from (absolute path on this machine).
    source: str = ""
    #: "file" | "directory" | "project" | "asset" | "library"
    kind: str = "file"
    #: Where it lives inside the workspace (relative to the workspace root).
    workspace_path: str = ""
    #: The adapter that recognised it ("python", "cpp-cmake", "unreal", ...).
    detected_type: str = ""
    file_count: int = 0
    total_bytes: int = 0
    added_utc: str = field(default_factory=now_utc)
    #: Module this input belongs to (empty when it is not assigned yet).
    module_id: str = ""
    #: True when the input is referenced in place instead of copied.
    linked: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_id": self.input_id,
            "source": self.source,
            "kind": self.kind,
            "workspace_path": self.workspace_path,
            "detected_type": self.detected_type,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "added_utc": self.added_utc,
            "module_id": self.module_id,
            "linked": self.linked,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorkspaceInput":
        return cls(
            input_id=str(raw.get("input_id", "")),
            source=str(raw.get("source", "")),
            kind=str(raw.get("kind", "file")),
            workspace_path=str(raw.get("workspace_path", "")),
            detected_type=str(raw.get("detected_type", "")),
            file_count=int(raw.get("file_count", 0)),
            total_bytes=int(raw.get("total_bytes", 0)),
            added_utc=str(raw.get("added_utc", "")),
            module_id=str(raw.get("module_id", "")),
            linked=bool(raw.get("linked", False)),
            notes=[str(n) for n in (raw.get("notes") or [])],
        )


# ---------------------------------------------------------------------------
# Modules
# ---------------------------------------------------------------------------


@dataclass
class Module:
    """One buildable unit of the fused application."""

    module_id: str
    language: str = ""
    project_type: str = ""
    #: The adapter that recognised it ("python", "cpp-cmake", "unreal", ...).
    adapter_id: str = ""
    #: The workspace input this module came from.
    input_id: str = ""
    #: Path of the module root, relative to its input location ('' = input root).
    root: str = ""
    source_files: list[str] = field(default_factory=list)
    asset_files: list[str] = field(default_factory=list)
    #: The build tool that owns this module ("cmake", "cargo", "unreal", ...).
    build_tool: str = ""
    build_configuration: str = "release"
    target_platform: str = "windows"
    target_architecture: str = "x64"
    #: Module ids this module needs (real evidence only).
    dependencies: list[str] = field(default_factory=list)
    #: Why each dependency was detected ("cargo path dependency", ...).
    dependency_reasons: dict[str, str] = field(default_factory=dict)
    #: Interfaces this module exports (headers, .pyd, exports).
    exported_interfaces: list[str] = field(default_factory=list)
    #: Runtime the module needs at run time ("python3.12", "vcruntime140", ...).
    required_runtime: list[str] = field(default_factory=list)
    #: Files this module produces.
    output_artifacts: list[str] = field(default_factory=list)
    #: How this module is integrated ("native-link", "python-extension", ...).
    integration_method: str = ""
    status: str = "pending"        # pending|built|failed|skipped|validated
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "language": self.language,
            "project_type": self.project_type,
            "adapter_id": self.adapter_id,
            "input_id": self.input_id,
            "root": self.root,
            "source_files": list(self.source_files),
            "asset_files": list(self.asset_files),
            "build_tool": self.build_tool,
            "build_configuration": self.build_configuration,
            "target_platform": self.target_platform,
            "target_architecture": self.target_architecture,
            "dependencies": list(self.dependencies),
            "dependency_reasons": dict(self.dependency_reasons),
            "exported_interfaces": list(self.exported_interfaces),
            "required_runtime": list(self.required_runtime),
            "output_artifacts": list(self.output_artifacts),
            "integration_method": self.integration_method,
            "status": self.status,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Module":
        return cls(
            module_id=str(raw.get("module_id", "")),
            language=str(raw.get("language", "")),
            project_type=str(raw.get("project_type", "")),
            adapter_id=str(raw.get("adapter_id", "")),
            input_id=str(raw.get("input_id", "")),
            root=str(raw.get("root", "")),
            source_files=[str(x) for x in (raw.get("source_files") or [])],
            asset_files=[str(x) for x in (raw.get("asset_files") or [])],
            build_tool=str(raw.get("build_tool", "")),
            build_configuration=str(raw.get("build_configuration", "release")),
            target_platform=str(raw.get("target_platform", "windows")),
            target_architecture=str(raw.get("target_architecture", "x64")),
            dependencies=[str(x) for x in (raw.get("dependencies") or [])],
            dependency_reasons={
                str(k): str(v) for k, v in (raw.get("dependency_reasons") or {}).items()
            },
            exported_interfaces=[str(x) for x in (raw.get("exported_interfaces") or [])],
            required_runtime=[str(x) for x in (raw.get("required_runtime") or [])],
            output_artifacts=[str(x) for x in (raw.get("output_artifacts") or [])],
            integration_method=str(raw.get("integration_method", "")),
            status=str(raw.get("status", "pending")),
            notes=[str(x) for x in (raw.get("notes") or [])],
        )


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


@dataclass
class BuildTask:
    """One node of the build graph: a module or an asset-processing step."""

    task_id: str
    kind: str = "module"          # module|asset|package|link
    module_id: str = ""
    title: str = ""
    command: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    status: str = "pending"
    duration_seconds: float = 0.0
    cached: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "kind": self.kind,
            "module_id": self.module_id,
            "title": self.title,
            "command": list(self.command),
            "depends_on": list(self.depends_on),
            "outputs": list(self.outputs),
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 3),
            "cached": self.cached,
            "error": self.error,
        }


@dataclass
class ModuleGraph:
    """The unified representation of the fused application."""

    modules: list[Module] = field(default_factory=list)
    tasks: list[BuildTask] = field(default_factory=list)
    #: Module ids in build order.
    order: list[str] = field(default_factory=list)
    #: Cycles found in the dependency graph.
    cycles: list[list[str]] = field(default_factory=list)
    #: Problems that make the assembly impossible as it is.
    conflicts: list[str] = field(default_factory=list)
    #: Pairs of modules with no compatible integration.
    unconnected: list[dict[str, Any]] = field(default_factory=list)
    entry_point: str = ""

    def module(self, module_id: str) -> Module | None:
        for item in self.modules:
            if item.module_id == module_id:
                return item
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "modules": [m.to_dict() for m in self.modules],
            "tasks": [t.to_dict() for t in self.tasks],
            "order": list(self.order),
            "cycles": [list(c) for c in self.cycles],
            "conflicts": list(self.conflicts),
            "unconnected": list(self.unconnected),
            "entry_point": self.entry_point,
        }
