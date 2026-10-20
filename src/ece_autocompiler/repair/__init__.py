"""Automatic compilation error analysis and repair.

The engine parses the compiler output, groups the problems into probable root
causes, proposes the fixes it is sure about, applies them, rebuilds and checks
the result. Anything it cannot fix safely is explained instead.
"""

from __future__ import annotations

from .analysis import FailureAnalysis, RootCause, analyze, group_root_causes
from .diagnostics import Diagnostic, add_context, errors_only, parse_diagnostics
from .engine import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_TIMEOUT_SECONDS,
    BuildOutcome,
    RepairAttempt,
    RepairEngine,
    RepairOutcome,
)
from .rules import RepairProposal, propose_repairs

__all__ = [
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_TIMEOUT_SECONDS",
    "BuildOutcome",
    "Diagnostic",
    "FailureAnalysis",
    "RepairAttempt",
    "RepairEngine",
    "RepairOutcome",
    "RepairProposal",
    "RootCause",
    "add_context",
    "analyze",
    "errors_only",
    "group_root_causes",
    "parse_diagnostics",
    "propose_repairs",
]
