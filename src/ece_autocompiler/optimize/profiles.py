"""Optimization profiles.

A profile is a named set of compiler options for one toolchain family. The
profiles are conservative on purpose:

* ``debug`` keeps debug information and compiles fast;
* ``release`` is the ordinary production build;
* ``performance`` trades build time for runtime speed;
* ``size`` trades speed for a smaller binary;
* ``balanced`` is the middle ground;
* ``custom`` is whatever the user asks for.

Options that can change the behaviour of the program (link-time optimization,
trimming, tiered PGO) are marked as needing approval instead of being applied
silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Adapter id -> toolchain family that understands the flags.
ADAPTER_FAMILY: dict[str, str] = {
    "python": "python",
    "node": "node",
    "cpp-cmake": "cmake",
    "cpp-meson": "meson",
    "cpp-make": "make",
    "rust-cargo": "cargo",
    "rust": "cargo",
    "go": "go",
    "dotnet": "dotnet",
    "java-maven": "maven",
    "java-gradle": "gradle",
    "kotlin-gradle": "gradle",
    "php": "php",
}

#: Families whose compiler exposes real optimization flags.
SUPPORTED_FAMILIES = ("cmake", "meson", "make", "cargo", "go", "dotnet")

#: Families that run an interpreter: there is no compiler to optimize.
INTERPRETER_FAMILIES = ("python", "node", "php", "maven", "gradle")


@dataclass(frozen=True)
class OptimizationProfile:
    """One named optimization profile."""

    key: str
    title: str
    description: str
    #: cmake build type, when the family uses one.
    build_type: str = ""
    #: Compiler flags, when the family uses them.
    flags: tuple[str, ...] = ()
    #: cargo profile to select ("dev" or "release").
    cargo_profile: str = ""
    #: .NET build configuration.
    configuration: str = ""
    #: MSBuild / dotnet properties.
    properties: tuple[str, ...] = ()
    #: Environment variables for the build step.
    env: dict[str, str] = field(default_factory=dict)
    #: True when the profile may change program behaviour and needs approval.
    requires_approval: bool = False
    #: Explanation shown when the family does not support the profile.
    unsupported_reason: str = ""

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "description": self.description,
            "build_type": self.build_type,
            "flags": list(self.flags),
            "cargo_profile": self.cargo_profile,
            "configuration": self.configuration,
            "properties": list(self.properties),
            "env": dict(self.env),
            "requires_approval": self.requires_approval,
        }


#: The profiles the user can pick. ``custom`` is completed by the user.
PROFILES: dict[str, OptimizationProfile] = {}


def _register(profiles: list[OptimizationProfile]) -> None:
    for profile in profiles:
        PROFILES[profile.key] = profile


_register(
    [
        OptimizationProfile(
            key="debug",
            title="Debug",
            description="Fast compilation with full debug information.",
            build_type="Debug",
            flags=("-g",),
            cargo_profile="dev",
            configuration="Debug",
            env={},
        ),
        OptimizationProfile(
            key="release",
            title="Release",
            description="Optimized production build with debug symbols removed.",
            build_type="Release",
            flags=("-O2", "-DNDEBUG"),
            cargo_profile="release",
            configuration="Release",
        ),
        OptimizationProfile(
            key="performance",
            title="Performance",
            description="Prioritises runtime speed; link-time optimization is enabled "
                       "where the toolchain supports it.",
            build_type="Release",
            flags=("-O3", "-DNDEBUG"),
            cargo_profile="release",
            configuration="Release",
            properties=("TieredPGO=true", "PublishReadyToRun=true"),
            env={"CARGO_PROFILE_RELEASE_LTO": "fat", "CARGO_PROFILE_RELEASE_CODEGEN_UNITS": "1"},
            requires_approval=True,
        ),
        OptimizationProfile(
            key="size",
            title="Size",
            description="Smallest binary: size-oriented optimization and stripped symbols.",
            build_type="MinSizeRel",
            flags=("-Os", "-DNDEBUG"),
            cargo_profile="release",
            configuration="Release",
            properties=("PublishTrimmed=true", "InvariantGlobalization=true"),
            env={"CARGO_PROFILE_RELEASE_OPT_LEVEL": "z", "CARGO_PROFILE_RELEASE_LTO": "true",
                 "CARGO_PROFILE_RELEASE_STRIP": "symbols"},
            requires_approval=True,
        ),
        OptimizationProfile(
            key="balanced",
            title="Balanced",
            description="Sensible trade-off between build time, speed and size.",
            build_type="Release",
            flags=("-O2", "-DNDEBUG"),
            cargo_profile="release",
            configuration="Release",
            env={"CARGO_PROFILE_RELEASE_LTO": "thin"},
        ),
        OptimizationProfile(
            key="custom",
            title="Custom",
            description="Your own compiler options.",
            unsupported_reason="Custom options are supplied by the user.",
        ),
    ]
)

#: The default: no override at all, so existing builds behave exactly as before.
DEFAULT_PROFILE = "none"

#: Every profile id the user can choose, including the no-override default.
PROFILE_KEYS: tuple[str, ...] = (DEFAULT_PROFILE,) + tuple(PROFILES.keys())


def profile_keys() -> list[str]:
    """Every profile id, including the "no override" default."""
    return list(PROFILE_KEYS)


def get_profile(key: str) -> OptimizationProfile | None:
    """The profile with this key, or None."""
    return PROFILES.get((key or "").strip().lower())


def describe_profiles() -> list[dict]:
    """A machine-readable description of every profile."""
    described: list[dict] = []
    for key in PROFILE_KEYS:
        profile = PROFILES.get(key)
        if profile is None:
            described.append(
                {
                    "key": key,
                    "title": "Default (no override)",
                    "description": "Build exactly as the project asks; no optimization "
                                   "flags are added.",
                    "flags": [],
                    "requires_approval": False,
                }
            )
            continue
        described.append(profile.as_dict())
    return described
