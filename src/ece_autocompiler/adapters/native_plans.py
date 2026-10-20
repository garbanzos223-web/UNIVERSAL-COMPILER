"""Build plans for the tool-gated languages.

Each language gets its own small builder, so adding a toolchain never means
editing a shared function: write a builder, register it, and the adapter,
the plan preview and the window pick it up.

Every builder receives the tool id, the resolved executable and the project
folder, and returns the ordered steps of the build.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .base import BuildPlanStep

#: A builder returns the steps of one build.
StepBuilder = Callable[[str | None, str, Path], list[BuildPlanStep]]

#: adapter id -> builder.
BUILDERS: dict[str, StepBuilder] = {}


def register(adapter_id: str) -> Callable[[StepBuilder], StepBuilder]:
    """Register the build steps of one language."""

    def decorator(builder: StepBuilder) -> StepBuilder:
        BUILDERS[adapter_id] = builder
        return builder

    return decorator


def parallel_jobs() -> str:
    """How many build jobs may run in parallel."""
    from ..build.process import parallel_jobs as jobs

    return jobs()


# ---------------------------------------------------------------------------
# .NET
# ---------------------------------------------------------------------------


@register("dotnet")
def _dotnet_steps(tool_id: str | None, exe: str, root: Path) -> list[BuildPlanStep]:
    return [
        BuildPlanStep(
            id="dotnet-publish",
            title="Build with dotnet publish (SDK required)",
            cmd_args=[exe, "publish", "-c", "Release"],
            workdir=root,
            requires_approval=True,
            timeout=1200,
        )
    ]


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------


@register("rust-cargo")
@register("rust")
def _rust_steps(tool_id: str | None, exe: str, root: Path) -> list[BuildPlanStep]:
    return [
        BuildPlanStep(
            id="cargo-build",
            title="Build with cargo build --release",
            cmd_args=[exe, "build", "--release"],
            workdir=root,
            requires_approval=False,
            timeout=1200,
        )
    ]


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


@register("go")
def _go_steps(tool_id: str | None, exe: str, root: Path) -> list[BuildPlanStep]:
    return [
        BuildPlanStep(
            id="go-build",
            title="Build with go build ./...",
            cmd_args=[exe, "build", "./..."],
            workdir=root,
            requires_approval=False,
            timeout=600,
        )
    ]


# ---------------------------------------------------------------------------
# Java / Kotlin
# ---------------------------------------------------------------------------


@register("java-maven")
def _maven_steps(tool_id: str | None, exe: str, root: Path) -> list[BuildPlanStep]:
    return [
        BuildPlanStep(
            id="mvn-package",
            title="Build with mvn package",
            cmd_args=[exe, "package", "-DskipTests"],
            workdir=root,
            requires_approval=True,
            timeout=1200,
        )
    ]


@register("java-gradle")
@register("kotlin-gradle")
def _gradle_steps(tool_id: str | None, exe: str, root: Path) -> list[BuildPlanStep]:
    return [
        BuildPlanStep(
            id="gradle-build",
            title="Build with gradle build",
            cmd_args=[exe, "build", "-x", "test"],
            workdir=root,
            requires_approval=True,
            timeout=1200,
        )
    ]


# ---------------------------------------------------------------------------
# C / C++
# ---------------------------------------------------------------------------


@register("cpp-cmake")
def _cmake_steps(tool_id: str | None, exe: str, root: Path) -> list[BuildPlanStep]:
    # Configuring runs the project's CMakeLists.txt, which is
    # project-controlled code: it needs the usual consent.
    return [
        BuildPlanStep(
            id="cmake-configure",
            title="Configure with cmake",
            cmd_args=[exe, "-S", ".", "-B", "build"],
            workdir=root,
            requires_approval=True,
            timeout=300,
        ),
        BuildPlanStep(
            id="cmake-build",
            title="Build with cmake --build (parallel)",
            cmd_args=[
                exe, "--build", "build", "--config", "Release",
                "--parallel", parallel_jobs(),
            ],
            workdir=root,
            requires_approval=True,
            timeout=1800,
        ),
    ]


@register("cpp-meson")
def _meson_steps(tool_id: str | None, exe: str, root: Path) -> list[BuildPlanStep]:
    from ..detection import get_tool

    meson = get_tool("meson")
    ninja_exe = exe if exe and exe != "ninja" else "ninja"
    if not (meson and meson.found):
        return [
            BuildPlanStep(
                id="meson-missing",
                title="Meson is required to configure this project",
                cmd_args=[],
                workdir=root,
                requires_approval=False,
            )
        ]
    return [
        BuildPlanStep(
            id="meson-setup",
            title="Configure with meson setup",
            cmd_args=[meson.path, "setup", "build", "--buildtype=release"],
            workdir=root,
            requires_approval=True,
            timeout=300,
        ),
        BuildPlanStep(
            id="ninja-build",
            title="Build with ninja (parallel)",
            cmd_args=[ninja_exe, "-C", "build", "-j", parallel_jobs()],
            workdir=root,
            requires_approval=True,
            timeout=1800,
        ),
    ]


@register("cpp-make")
def _make_steps(tool_id: str | None, exe: str, root: Path) -> list[BuildPlanStep]:
    return [
        BuildPlanStep(
            id="make",
            title="Build with make (parallel)",
            cmd_args=[exe, "-j", parallel_jobs()],
            workdir=root,
            requires_approval=False,
            timeout=1800,
        )
    ]


# ---------------------------------------------------------------------------
# PHP
# ---------------------------------------------------------------------------


@register("php")
def _php_steps(tool_id: str | None, exe: str, root: Path) -> list[BuildPlanStep]:
    php_files = sorted(p for p in root.rglob("*.php") if ".git" not in p.parts)
    if not php_files:
        return [
            BuildPlanStep(
                id="php-none",
                title="No PHP files to check",
                cmd_args=[],
                workdir=root,
                requires_approval=False,
            )
        ]
    # One lint step per file; argv lists avoid shell glob expansion.
    steps: list[BuildPlanStep] = []
    for index, php_file in enumerate(php_files[:200]):
        steps.append(
            BuildPlanStep(
                id=f"php-lint-{index}",
                title=f"Syntax check {php_file.name}",
                cmd_args=[exe, "-l", str(php_file)],
                workdir=root,
                requires_approval=False,
                timeout=60,
            )
        )
    return steps


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


def _noop_steps(tool_id: str | None, exe: str, root: Path) -> list[BuildPlanStep]:
    return [
        BuildPlanStep(
            id="noop",
            title="No automatic build steps for this adapter in this environment",
            cmd_args=[],
            workdir=root,
            requires_approval=False,
        )
    ]


def build_steps(adapter_id: str, tool_id: str | None, root: Path, exe: str) -> list[BuildPlanStep]:
    """The build steps of one adapter."""
    builder = BUILDERS.get(adapter_id, _noop_steps)
    return builder(tool_id, exe, root)
