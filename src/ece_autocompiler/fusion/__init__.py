"""Fusion engine: multi-input, multi-language project assembly.

The engine imports many inputs (C/C++, Python, Unreal, C#, JS/TS, Rust, Go,
assets), builds one module graph, compiles each module with its own real
toolchain and assembles a single distributable application. See
``docs/FUSION.md`` for the architecture and honest limits.
"""

from .models import (
    BuildTask,
    FusionConfig,
    Module,
    ModuleGraph,
    WorkspaceInput,
)
from .workspace import FusionWorkspace, WorkspaceError

__all__ = [
    "BuildTask",
    "FusionConfig",
    "FusionWorkspace",
    "Module",
    "ModuleGraph",
    "WorkspaceError",
    "WorkspaceInput",
]
