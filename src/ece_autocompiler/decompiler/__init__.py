"""Integrated decompiler, reconstructed-project and recompilation subsystem.

Public entry points:

    analyze_file(path)                 safe, read-only analysis
    detect_format(path)                universal format identification
    DecompileSession(options)          batch decompilation
    recompile(root, output_dir, ...)   real rebuild from recovered source
    supported_formats()                catalog for the UI/CLI

The subsystem has no hard third-party dependency. Optional extras improve it:
``pefile`` (resource/version extraction), ``capstone`` (disassembly) and the
external ILSpy command-line tool (complete C# recovery).
"""

from __future__ import annotations

from .analyzer import analyze_file, analyze_many, sha256_file
from .formats import detect_format, supported_formats
from .models import (
    CATEGORY_CONFIG,
    CATEGORY_DISASSEMBLY,
    CATEGORY_METADATA,
    CATEGORY_PSEUDOCODE,
    CATEGORY_RESOURCE,
    CATEGORY_SOURCE,
    STATUS_GENERATED,
    STATUS_LABELS,
    STATUS_PARTIAL,
    STATUS_RECOVERED,
    STATUS_RESOURCE,
    STATUS_UNSUPPORTED,
    BackendOutput,
    DecompileResult,
    FileAnalysis,
    FormatInfo,
    RecoveredFile,
)
from .options import DecompileOptions
from .recompiler import (
    RecompilePlan,
    RecompileResult,
    detect_project_kind,
    plan_recompile,
    recompile,
)
from .service import DecompileSession, backend_catalog, expand_inputs
from .tools import ILSPY_VERSION, find_ilspy, find_javap, install_ilspy

__all__ = [
    "BackendOutput",
    "CATEGORY_CONFIG",
    "CATEGORY_DISASSEMBLY",
    "CATEGORY_METADATA",
    "CATEGORY_PSEUDOCODE",
    "CATEGORY_RESOURCE",
    "CATEGORY_SOURCE",
    "DecompileOptions",
    "DecompileResult",
    "DecompileSession",
    "FileAnalysis",
    "FormatInfo",
    "ILSPY_VERSION",
    "RecompilePlan",
    "RecompileResult",
    "RecoveredFile",
    "STATUS_GENERATED",
    "STATUS_LABELS",
    "STATUS_PARTIAL",
    "STATUS_RECOVERED",
    "STATUS_RESOURCE",
    "STATUS_UNSUPPORTED",
    "analyze_file",
    "analyze_many",
    "backend_catalog",
    "detect_format",
    "detect_project_kind",
    "expand_inputs",
    "find_ilspy",
    "find_javap",
    "install_ilspy",
    "plan_recompile",
    "recompile",
    "sha256_file",
    "supported_formats",
]
