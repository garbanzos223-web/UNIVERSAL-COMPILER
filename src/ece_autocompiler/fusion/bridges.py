"""Language bridges: decide - and explain - how modules can actually connect.

The planner never claims two modules are "integrated" because both built. A
pair gets a method only when there is concrete evidence for it (an exported
shared library, a header, an import that resolves to it). Every plan records
what is missing when the pair cannot be connected directly, and what the
fallback would be (separate process distributed with the application).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .compatibility import binary_architecture, python_architecture
from .models import Module, ModuleGraph


@dataclass
class BridgePlan:
    """How one module consumes another."""

    consumer: str = ""
    provider: str = ""
    #: "native-link" | "shared-library" | "c-abi" | "python-extension"
    #: | "engine-module" | "editor-python" | "process" | "bundled" | "none"
    method: str = "none"
    feasible: bool = False
    explanations: list[str] = field(default_factory=list)
    requirements: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "consumer": self.consumer,
            "provider": self.provider,
            "method": self.method,
            "feasible": self.feasible,
            "explanations": list(self.explanations),
            "requirements": list(self.requirements),
            "steps": list(self.steps),
        }


_SHARED_EXPORT_RE = re.compile(r"exports shared library", re.IGNORECASE)
_STATIC_EXPORT_RE = re.compile(r"exports static library", re.IGNORECASE)


def _provider_exports_shared_lib(module: Module) -> bool:
    return any(_SHARED_EXPORT_RE.search(n) for n in module.notes)


def _provider_exports_static_lib(module: Module) -> bool:
    return any(_STATIC_EXPORT_RE.search(n) for n in module.notes)


def plan_pair(consumer: Module, provider: Module) -> BridgePlan:
    """Plan how *consumer* can use *provider* based on real evidence."""
    plan = BridgePlan(consumer=consumer.module_id, provider=provider.module_id)
    reason = consumer.dependency_reasons.get(provider.module_id, "")

    if provider.language == "unreal":
        # The provider is assembled by UnrealBuildTool; consumers ride along.
        plan.method = "engine-module"
        plan.feasible = True
        plan.explanations.append(
            "Provider is an Unreal project: it is assembled by UnrealBuildTool "
            "and the packaged game, and external code must be attached through "
            "Unreal's module system, not linked independently."
        )
        if consumer.language == "python":
            role = "editor"  # refined by the caller with the user's config
            plan.method = "editor-python"
            plan.feasible = True
            plan.explanations.append(
                "Python inside an Unreal project runs as editor automation by "
                "default (consumes the unreal module at build time). Editor "
                "scripts are NOT copied into the packaged game."
            )
        if consumer.language == "cpp":
            plan.requirements.append(
                "The C++ library must be integrated through a Build.cs "
                "PublicDependencyModuleNames entry or a plugin, built as x64 "
                "static/shared library for the engine configuration."
            )
        plan.steps.append("Run asset processing before cooking when scripts apply.")
        return plan

    if consumer.language == "unreal":
        plan.method = "engine-module"
        plan.feasible = provider.language != "assets"
        if provider.language == "python":
            plan.method = "editor-python"
            plan.explanations.append(
                "Python scripts run before cooking (asset processing) unless "
                "the project explicitly configures a runtime scripting plugin."
            )
        plan.requirements.append(
            "Extra C++ libraries are attached through Unreal's module system."
        )
        return plan

    if consumer.language == "python" and provider.language == "cpp":
        if _provider_exports_shared_lib(provider):
            plan.method = "shared-library"
            plan.feasible = True
            plan.explanations.append(
                f"Provider exports a shared library ({reason or 'declared in its build files'}); "
                "the Python side can load it through ctypes or a Python extension."
            )
            plan.requirements.append(
                "Provider's DLL must be built with the same architecture as the "
                "packaged Python interpreter."
            )
            plan.steps.append("Deploy the provider DLL next to the Python entry files.")
        elif _provider_exports_static_lib(provider):
            plan.method = "python-extension"
            plan.feasible = False
            plan.explanations.append(
                "Provider builds a static library; direct import from Python "
                "requires a compiled CPython extension module (.pyd) built "
                "against the same interpreter version and architecture."
            )
            plan.requirements.append(
                "Build an extension module (CPython API or Cython) from the "
                "provider's static library for this interpreter."
            )
        else:
            plan.method = "process"
            plan.explanations.append(
                "No exported shared-library/static-library interface was found, "
                "so there is no in-process link; the honest fallback is a "
                "separate process distributed with the application."
            )
        return plan

    if consumer.language == "cpp" and provider.language == "cpp":
        plan.method = "native-link"
        plan.explanations.append(
            "Native C/C++ modules link through the existing build system."
        )
        if _provider_exports_static_lib(provider):
            plan.requirements.append("Link the provider's static library.")
        elif _provider_exports_shared_lib(provider):
            plan.requirements.append("Link against the provider's import library.")
        else:
            plan.requirements.append(
                "Provider must declare a library target (STATIC or SHARED) so "
                "its symbols are linkable."
            )
        plan.feasible = bool(_provider_exports_shared_lib(provider) or _provider_exports_static_lib(provider))
        return plan

    if consumer.language == "python" and provider.language == "python":
        plan.method = "bundled"
        plan.feasible = True
        plan.explanations.append(
            "Same-language Python modules run together in one interpreter; "
            "packaging bundles them as one application."
        )
        return plan

    if consumer.language == "python" and provider.language in {"rust", "go"}:
        if _provider_exports_shared_lib(provider) or provider.required_runtime == []:
            plan.method = "c-abi"
            feasible = _provider_exports_shared_lib(provider)
            plan.feasible = feasible
            plan.explanations.append(
                "Rust/Go cdylib libraries expose a C ABI consumable from Python "
                "via ctypes. Exported functions must be verified after build."
            )
            plan.requirements.append(
                "Build the provider as a cdylib and verify its exported symbols."
            )
            return plan
        plan.method = "process"
        plan.explanations.append(
            "Provider is a binary program; it can be launched as a separate "
            "process distributed with the application."
        )
        plan.feasible = True
        return plan

    if consumer.language in {"csharp", "dotnet", "csharp"} and provider.language == "cpp":
        plan.method = "c-abi"
        plan.explanations.append(
            "C# consumes native code through P/Invoke; it needs exported C "
            "functions and matching architecture."
        )
        plan.requirements.append(
            "Provider must export C functions (extern \"C\"); verify exports "
            "after build with a symbol dump."
        )
        plan.feasible = _provider_exports_shared_lib(provider)
        return plan

    if consumer.language in {"javascript", "typescript", "node"} and provider.language == "cpp":
        plan.method = "process"
        plan.explanations.append(
            "Node consumes native code via N-API addons; building an addon is "
            "a real build step (node-gyp). Until an addon exists the fallback "
            "is a separate process."
        )
        plan.requirements.append(
            "Build an N-API addon from the provider (node-gyp) to link in-process."
        )
        plan.feasible = False
        return plan

    plan.method = "process"
    plan.feasible = True
    plan.explanations.append(
        f"{consumer.language} consumer and {provider.language} provider have no "
        "direct in-process interface in this version; the application runs them "
        "as separate processes distributed side by side."
    )
    return plan


def plan_python_role(graph: ModuleGraph, workspace) -> None:
    """Decide the role of Python modules relative to Unreal modules (§4).

    Fills ``Consumer.integration_method`` and adds honest notes.
    """
    unreal_modules = [m for m in graph.modules if m.language == "unreal"]
    if not unreal_modules:
        return
    for module in graph.modules:
        if module.language != "python":
            continue
        role = workspace.config.python_roles.get(module.module_id, "")
        if not role:
            hints_unreal = any("unreal" in note.lower() for note in module.notes)
            imports_unreal = any(
                "unreal" in req.lower() for req in module.required_runtime
            )
            if hints_unreal or imports_unreal:
                role = "editor"
                module.notes.append(
                    "role inferred: editor automation (scripts reference Unreal); "
                    "override in ece-project.json python_roles."
                )
            else:
                role = "external-app"
                module.notes.append(
                    "role inferred: separate Python application (no Unreal "
                    "imports found); override in ece-project.json python_roles."
                )
        module.integration_method = {
            "editor": "editor-python",
            "runtime": "runtime-python-plugin",
            "asset": "asset-processing-python",
            "build": "build-time-python",
            "external-app": "separate-application",
        }.get(role, "separate-application")
        if role == "runtime":
            module.notes.append(
                "Runtime scripting requires a deliberately configured plugin "
                "(e.g. an embedded interpreter) or an external process; editor "
                "scripts alone do not run in a packaged game."
            )


def plan_all(graph: ModuleGraph, workspace) -> dict[tuple[str, str], BridgePlan]:
    """Plan integration for every declared dependency pair in the graph."""
    plan_integrations: dict[tuple[str, str], BridgePlan] = {}
    plan_python_role(graph, workspace)
    modules = {m.module_id: m for m in graph.modules}
    for consumer in graph.modules:
        for dep in consumer.dependencies:
            provider = modules.get(dep)
            if provider is None:
                continue
            plan_integrations[(consumer.module_id, dep)] = plan_pair(consumer, provider)
    return plan_integrations


def verify_architecture_pair(consumer: Module, provider: Module, artifacts: dict[str, list[Path]]) -> list[str]:
    """Post-build verification of an in-process integration (§5)."""
    problems: list[str] = []
    pair = (consumer.module_id, provider.module_id)
    consumer_files = artifacts.get(consumer.module_id, [])
    provider_files = artifacts.get(provider.module_id, [])
    arches: dict[str, str] = {}
    for label, files in (("consumer", consumer_files), ("provider", provider_files)):
        for path in files:
            if path.suffix.lower() in {".exe", ".dll", ".pyd", ".so", ".node"}:
                arch = binary_architecture(path)
                if arch:
                    arches[label] = arch
                    break
    if len(set(arches.values())) > 1:
        problems.append(
            f"{pair}: mixed architectures {arches}; the pair cannot load in one process."
        )
    if consumer.language == "python" and provider.language == "cpp":
        py_arch = python_architecture()
        if arches and all(a != py_arch for a in arches.values()):
            problems.append(
                f"{pair}: native artifacts are {arches} but the interpreter is {py_arch}."
            )
    return problems
