"""Automatic error diagnosis for build output."""

from .classifier import (
    CATEGORY_LABELS,
    Category,
    Diagnostic,
    classify,
    first_actionable,
    summarize,
)

__all__ = [
    "CATEGORY_LABELS",
    "Category",
    "Diagnostic",
    "classify",
    "first_actionable",
    "summarize",
]
