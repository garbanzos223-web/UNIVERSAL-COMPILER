"""Turn an optimization profile into concrete build-plan changes.

The optimizer never rewrites the project: it adjusts the commands the build
plan already contains, and it reports every change it makes. When the
toolchain has no meaningful optimization options (an interpreter, for
example), the plan says so instead of pretending.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..adapters.base import BuildPlan, BuildPlanStep
from .profiles import (
    ADAPTER_FAMILY,
    DEFAULT_PROFILE,
    INTERPRETER_FAMILIES,
    PROFILES,
    OptimizationProfile,
    get_profile,
)


@dataclass
class OptimizationRequest:
    """What the user asked for."""

    profile: str = DEFAULT_PROFILE
    custom_flags: list[str] = field(default_factory=list)

    @classmethod
    def from_args(cls, profile: str | None, custom_flags: list[str] | None = None) -> "OptimizationRequest":
        return cls(
            profile=(profile or DEFAULT_PROFILE).strip().lower() or DEFAULT_PROFILE,
            custom_flags=[f for f in (custom_flags or []) if f.strip()],
        )


@dataclass
class OptimizationPlan:
    """The resolved optimization for one project."""

    profile: str = DEFAULT_PROFILE
    title: str = "Default (no override)"
    family: str = ""
    flags: list[str] = field(default_factory=list)
    linker_flags: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    properties: list[str] = field(default_factory=list)
    build_type: str = ""
    cargo_profile: str = ""
    configuration: str = ""
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    unsupported_reason: str = ""
    requires_approval: bool = False
    applied_steps: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        """True when the plan really changes the build."""
        return self.profile != DEFAULT_PROFILE and not self.unsupported_reason

    def as_dict(self) -> dict:
        return {
            "profile": self.profile,
            "title": self.title,
            "family": self.family,
            "flags": list(self.flags),
            "linker_flags": list(self.linker_flags),
            "env": dict(self.env),
            "properties": list(self.properties),
            "build_type": self.build_type,
            "cargo_profile": self.cargo_profile,
            "configuration": self.configuration,
            "warnings": list(self.warnings),
            "notes": list(self.notes),
            "unsupported_reason": self.unsupported_reason,
            "requires_approval": self.requires_approval,
            "applied_steps": list(self.applied_steps),
            "active": self.active,
        }

    def text(self) -> str:
        lines = [f"Optimization profile: {self.title} ({self.profile})"]
        if self.unsupported_reason:
            lines.append(f"  Not applied: {self.unsupported_reason}")
            return "\n".join(lines)
        if not self.active:
            lines.append("  No flags are added; the project builds as it asks.")
            return "\n".join(lines)
        if self.flags:
            lines.append(f"  Flags      : {' '.join(self.flags)}")
        if self.linker_flags:
            lines.append(f"  Linker     : {' '.join(self.linker_flags)}")
        if self.build_type:
            lines.append(f"  Build type : {self.build_type}")
        if self.cargo_profile:
            lines.append(f"  Cargo      : {self.cargo_profile}")
        if self.configuration:
            lines.append(f"  Config     : {self.configuration}")
        for key, value in sorted(self.env.items()):
            lines.append(f"  env        : {key}={value}")
        for prop in self.properties:
            lines.append(f"  property   : {prop}")
        for note in self.notes:
            lines.append(f"  note       : {note}")
        for warning in self.warnings:
            lines.append(f"  ! {warning}")
        return "\n".join(lines)


def _detect_conflicts(flags: list[str], profile_key: str) -> list[str]:
    """Warnings about flags that fight each other or the selected profile."""
    warnings: list[str] = []
    joined = " ".join(flags)
    opt_levels = [f for f in flags if f.startswith("-O")]
    if len({f for f in opt_levels}) > 1:
        warnings.append(
            f"More than one optimization level is set ({', '.join(opt_levels)}); "
            "the last one wins and the result is compiler dependent."
        )
    if "-g" in flags and profile_key in ("release", "size", "performance"):
        warnings.append(
            "Debug symbols (-g) are kept in an optimized build, so the binary will "
            "be larger than expected."
        )
    if ("-s" in flags or "--strip" in flags) and "-g" in flags:
        warnings.append("Stripping symbols while keeping debug information conflicts.")
    if "-march=native" in flags:
        warnings.append(
            "-march=native produces a binary that only runs on this CPU. It is not "
            "used by default and should not be shipped to other machines."
        )
    if "-flto" in joined and profile_key == "debug":
        warnings.append("Link-time optimization slows the build down; it is unusual in a debug build.")
    return warnings


def detect_compiler_family() -> str:
    """Which C/C++ compiler this machine would use: gcc, clang, msvc or unknown.

    The flags of a profile depend on it: MSVC does not understand
    ``-ffunction-sections``, and GCC does not understand ``/Gy``.
    """
    from ..detection import get_tool

    def _found(tool_id: str) -> bool:
        info = get_tool(tool_id)
        return bool(info and info.found)

    if _found("gcc") or _found("clang"):
        return "gcc"
    if _found("cl"):
        return "msvc"
    return "unknown"


def _toolchain_warnings(adapter_id: str, optimization: OptimizationPlan) -> list[str]:
    """Check the selected options against the toolchain that is installed.

    A flag the installed compiler does not understand is a build failure, so
    it is reported before the build runs - never after.
    """
    from ..detection import get_tool
    from ..toolchain_versions import parse_version, version_satisfies

    warnings: list[str] = []
    family = optimization.family

    def _version_of(tool_id: str) -> tuple[int, ...] | None:
        info = get_tool(tool_id)
        if info is None or not info.found:
            return None
        return parse_version(info.version)

    def _tool_missing(tool_id: str) -> bool:
        info = get_tool(tool_id)
        return info is None or not info.found

    if family == "cargo":
        if optimization.env.get("CARGO_PROFILE_RELEASE_LTO"):
            version = _version_of("cargo")
            if version is not None and not version_satisfies(".".join(map(str, version)), (1, 21)):
                warnings.append(
                    "This Cargo is older than 1.21, which is when link-time "
                    "optimization became available; the build will fail."
                )
            elif _tool_missing("cargo"):
                warnings.append(
                    "Cargo was not found, so the cargo options could not be checked "
                    "against a real toolchain."
                )
    elif family == "dotnet":
        version = _version_of("dotnet")
        for prop in optimization.properties:
            if prop.startswith("TieredPGO") and version is not None:
                if not version_satisfies(".".join(map(str, version)), (8, 0)):
                    warnings.append(
                        "Tiered PGO needs the .NET 8 SDK or newer; this SDK is older."
                    )
            if prop.startswith("PublishTrimmed") and version is not None:
                if not version_satisfies(".".join(map(str, version)), (6, 0)):
                    warnings.append(
                        "PublishTrimmed needs the .NET 6 SDK or newer; this SDK is older."
                    )
        if version is None and optimization.properties:
            warnings.append(
                "The .NET SDK was not found, so the publish options could not be "
                "checked against a real toolchain."
            )
    elif family in ("cmake", "meson", "make"):
        if "-flto" in optimization.flags:
            for tool_id in ("gcc", "clang"):
                version = _version_of(tool_id)
                if version is not None and not version_satisfies(
                    ".".join(map(str, version)), (4, 5)
                ):
                    warnings.append(
                        f"{tool_id} is older than 4.5, which is when link-time "
                        "optimization became available; the build will fail."
                    )
    return warnings


#: GCC/Clang options translated to their MSVC equivalent.
_MSVC_TRANSLATION: dict[str, str] = {
    "-O0": "/Od",
    "-O1": "/O1",
    "-O2": "/O2",
    "-O3": "/O2",
    "-Os": "/O1",
    "-g": "/Zi",
    "-DNDEBUG": "/DNDEBUG",
}


def _translate_to_msvc(flags: list[str]) -> list[str]:
    """Translate GCC-style options to the MSVC ones."""
    translated: list[str] = []
    for flag in flags:
        translated.append(_MSVC_TRANSLATION.get(flag, flag))
    return translated


def _apply_compiler_specific_options(plan: OptimizationPlan) -> None:
    """Add the options that only work with the compiler that is installed.

    Dead-code elimination and link-time optimization need different flags for
    GCC/Clang and for MSVC, and using the wrong ones is a build failure.
    """
    compiler = detect_compiler_family()
    if compiler == "unknown":
        plan.notes.append(
            "No C/C++ compiler was found, so the compiler-specific options "
            "(dead-code elimination, link-time optimization) were left out."
        )
        return

    if compiler == "msvc":
        plan.flags = _translate_to_msvc(plan.flags)

    if plan.profile == "size":
        if compiler == "gcc":
            # Section per function and per variable, then drop the unused
            # sections at link time.
            plan.flags.extend(["-ffunction-sections", "-fdata-sections"])
            plan.linker_flags.append("-Wl,--gc-sections")
            plan.notes.append(
                "Dead-code elimination: sections per function (-ffunction-sections) "
                "and unused sections dropped at link time (-Wl,--gc-sections)."
            )
        else:
            # MSVC: function-level linking and global data optimization.
            plan.flags.extend(["/Gy", "/Gw"])
            plan.linker_flags.append("/OPT:REF")
            plan.notes.append(
                "Dead-code elimination: function-level linking (/Gy) and "
                "unreferenced symbols removed (/OPT:REF)."
            )
    elif plan.profile == "performance":
        plan.properties.append("-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=TRUE")
        plan.notes.append(
            "Link-time optimization is enabled through CMake "
            "(-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=TRUE)."
        )


def build_optimization_plan(
    adapter_id: str,
    request: OptimizationRequest | None = None,
) -> OptimizationPlan:
    """Resolve the profile for *adapter_id* into a concrete plan."""
    request = request or OptimizationRequest()
    family = ADAPTER_FAMILY.get(adapter_id, "")
    key = (request.profile or DEFAULT_PROFILE).strip().lower() or DEFAULT_PROFILE

    plan = OptimizationPlan(profile=key, family=family)

    if key == DEFAULT_PROFILE:
        plan.title = "Default (no override)"
        plan.notes.append("The project builds exactly as it asks; no flags are added.")
        return plan

    profile: OptimizationProfile | None = get_profile(key)
    if profile is None:
        plan.unsupported_reason = (
            f"'{key}' is not a known profile. Use one of: "
            + ", ".join(sorted(PROFILES.keys()))
        )
        return plan

    plan.title = profile.title
    plan.flags = list(profile.flags)
    plan.env = dict(profile.env)
    plan.properties = list(profile.properties)
    plan.build_type = profile.build_type
    plan.cargo_profile = profile.cargo_profile
    plan.configuration = profile.configuration
    plan.requires_approval = profile.requires_approval

    if key == "custom":
        plan.flags = list(request.custom_flags)
        if not plan.flags:
            plan.unsupported_reason = (
                "The custom profile needs at least one compiler option. "
                "Pass them with --custom-flags."
            )
            return plan
        plan.notes.append("Only the options you supplied are used.")

    if not family:
        plan.unsupported_reason = (
            "No project type was detected for this folder, so there are no compiler "
            "options to set. Run 'analyze' to see what was detected."
            if not adapter_id
            else f"The '{adapter_id}' project type has no compiler options this tool can set."
        )
        return plan

    if family in INTERPRETER_FAMILIES:
        plan.unsupported_reason = (
            f"{adapter_id} runs on an interpreter, so there are no compiler flags to "
            "optimize. The build itself already runs the interpreter's own compiler."
        )
        plan.notes.append(
            "Use a bundler or a native toolchain (CMake, Cargo, Go, .NET) if you need "
            "a compiled, optimized binary."
        )
        return plan

    plan.warnings.extend(_detect_conflicts(plan.flags, key))
    plan.warnings.extend(_toolchain_warnings(adapter_id, plan))
    if family == "cmake":
        _apply_compiler_specific_options(plan)
    if plan.requires_approval:
        plan.notes.append(
            "This profile enables options that can change program behaviour "
            "(link-time optimization, trimming or tiered PGO). Review them before shipping."
        )
    if family == "cmake" and key in ("release", "balanced", "performance"):
        plan.notes.append(
            "Link-time optimization is available with -flto but is not enabled by "
            "default because it changes link time and memory use."
        )
    return plan


def _tool_name(step: BuildPlanStep) -> str:
    if not step.cmd_args:
        return ""
    return Path(str(step.cmd_args[0])).name.lower()


def _set_env(step: BuildPlanStep, env: dict[str, str]) -> None:
    merged = dict(step.env or {})
    merged.update(env)
    step.env = merged


def _apply_cmake(step: BuildPlanStep, plan: OptimizationPlan) -> bool:
    args = list(step.cmd_args)
    tool = _tool_name(step)
    if "cmake" not in tool:
        return False
    if "--build" in args:
        config = "RelWithDebInfo" if plan.profile == "debug" else "Release"
        if "--config" in args:
            index = args.index("--config")
            if index + 1 < len(args):
                args[index + 1] = config
        else:
            args.extend(["--config", config])
        step.cmd_args = args
        return True
    # Configure step.
    if plan.build_type and "-DCMAKE_BUILD_TYPE=" not in args:
        args.append(f"-DCMAKE_BUILD_TYPE={plan.build_type}")
    if plan.flags:
        joined = " ".join(plan.flags)
        if "-DCMAKE_C_FLAGS=" not in args:
            args.append(f"-DCMAKE_C_FLAGS={joined}")
        if "-DCMAKE_CXX_FLAGS=" not in args:
            args.append(f"-DCMAKE_CXX_FLAGS={joined}")
    if plan.linker_flags:
        joined = " ".join(plan.linker_flags)
        if "-DCMAKE_EXE_LINKER_FLAGS=" not in args:
            args.append(f"-DCMAKE_EXE_LINKER_FLAGS={joined}")
        if "-DCMAKE_SHARED_LINKER_FLAGS=" not in args:
            args.append(f"-DCMAKE_SHARED_LINKER_FLAGS={joined}")
    for prop in plan.properties:
        if prop.startswith("-D") and prop not in args:
            args.append(prop)
    step.cmd_args = args
    return True


def _apply_meson(step: BuildPlanStep, plan: OptimizationPlan) -> bool:
    args = list(step.cmd_args)
    if "meson" not in _tool_name(step):
        return False
    buildtype = {
        "debug": "debug",
        "release": "release",
        "balanced": "release",
        "performance": "release",
        "size": "minsize",
    }.get(plan.profile, "release")
    if not any(a.startswith("--buildtype=") for a in args):
        args.append(f"--buildtype={buildtype}")
    if plan.flags:
        joined = " ".join(plan.flags)
        if "-Dc_args=" not in args:
            args.append(f"-Dc_args={joined}")
        if "-Dcpp_args=" not in args:
            args.append(f"-Dcpp_args={joined}")
    step.cmd_args = args
    return True


def _apply_make(step: BuildPlanStep, plan: OptimizationPlan) -> bool:
    if "make" not in _tool_name(step):
        return False
    if plan.flags:
        joined = " ".join(plan.flags)
        _set_env(step, {"CFLAGS": joined, "CXXFLAGS": joined})
    if plan.build_type == "Debug":
        _set_env(step, {"CMAKE_BUILD_TYPE": "Debug"})
    return True


def _apply_cargo(step: BuildPlanStep, plan: OptimizationPlan) -> bool:
    if "cargo" not in _tool_name(step):
        return False
    args = list(step.cmd_args)
    if plan.cargo_profile == "release" and "--release" not in args:
        args.append("--release")
    elif plan.cargo_profile == "dev":
        while "--release" in args:
            args.remove("--release")
    step.cmd_args = args
    if plan.env:
        _set_env(step, plan.env)
    return True


def _apply_go(step: BuildPlanStep, plan: OptimizationPlan) -> bool:
    if "go" not in _tool_name(step):
        return False
    args = list(step.cmd_args)
    if "build" not in args:
        return False
    index = args.index("build")
    extra: list[str] = []
    if plan.profile == "debug":
        extra.append("-gcflags=all=-N -l")
    elif plan.profile == "size":
        extra.append("-ldflags=-s -w")
    else:
        extra.append("-trimpath")
    for flag in extra:
        if flag not in args:
            args.insert(index + 1, flag)
    step.cmd_args = args
    return True


def _apply_dotnet(step: BuildPlanStep, plan: OptimizationPlan) -> bool:
    if "dotnet" not in _tool_name(step):
        return False
    args = list(step.cmd_args)
    if plan.configuration:
        if "-c" in args:
            index = args.index("-c")
            if index + 1 < len(args):
                args[index + 1] = plan.configuration
        else:
            args.extend(["-c", plan.configuration])
    for prop in plan.properties:
        flag = f"-p:{prop}"
        if flag not in args:
            args.append(flag)
    step.cmd_args = args
    return True


_APPLIERS = {
    "cmake": _apply_cmake,
    "meson": _apply_meson,
    "make": _apply_make,
    "cargo": _apply_cargo,
    "go": _apply_go,
    "dotnet": _apply_dotnet,
}


def apply_optimization(build_plan: BuildPlan, optimization: OptimizationPlan) -> BuildPlan:
    """Apply *optimization* to the steps of *build_plan* (in place)."""
    if not optimization.active:
        return build_plan
    applier = _APPLIERS.get(optimization.family)
    if applier is None:
        return build_plan
    for step in build_plan.steps:
        try:
            changed = applier(step, optimization)
        except (IndexError, ValueError):
            changed = False
        if changed:
            optimization.applied_steps.append(str(getattr(step, "id", "")))
    return build_plan


def environment_for(optimization: OptimizationPlan) -> dict[str, str]:
    """Environment variables the whole build needs."""
    env = dict(optimization.env)
    if optimization.cargo_profile == "release":
        env.setdefault("CARGO_PROFILE_RELEASE_OPT_LEVEL", "3")
    return env


def source_date_epoch() -> str:
    """The reproducible-build timestamp, when the environment defines one."""
    return os.environ.get("SOURCE_DATE_EPOCH", "").strip()
