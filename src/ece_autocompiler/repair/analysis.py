"""Group compiler diagnostics into probable root causes.

A build usually reports many symptoms of one problem. This module groups the
diagnostics, points at the earliest meaningful one and explains what to do
when no automatic repair is safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .diagnostics import (
    CATEGORY_DEPENDENCY,
    CATEGORY_FLAGS,
    CATEGORY_LINKER,
    CATEGORY_MISSING_IMPORT,
    CATEGORY_SYNTAX,
    CATEGORY_TOOLCHAIN,
    CATEGORY_TYPE,
    CATEGORY_UNDEFINED,
    Diagnostic,
    errors_only,
)

#: What the user can do when the problem cannot be fixed automatically.
MANUAL_ACTIONS: dict[str, str] = {
    CATEGORY_MISSING_IMPORT: (
        "The build needs a module that is not installed. Install it with "
        "'python -m pip install <name>' (or the equivalent for this toolchain) and "
        "build again."
    ),
    CATEGORY_DEPENDENCY: (
        "A dependency could not be resolved. Install or restore it, then build again."
    ),
    CATEGORY_LINKER: (
        "The linker could not find a symbol or a library. Check that the library is "
        "installed and that the linker flags point at it."
    ),
    CATEGORY_FLAGS: (
        "The compiler rejected one of the build options. Remove or correct the option "
        "that is reported above."
    ),
    CATEGORY_TOOLCHAIN: (
        "The installed toolchain is too old for this project. Update it and build again."
    ),
    CATEGORY_TYPE: (
        "The types do not match. Fix the expression that is reported above; this needs "
        "a decision about the intended behaviour."
    ),
    CATEGORY_UNDEFINED: (
        "A name is not defined in this scope. Check the import, the declaration or the "
        "spelling of the name that is reported above."
    ),
    CATEGORY_SYNTAX: (
        "The source file does not parse. Fix the syntax at the reported line; the "
        "repair engine can only fix a few deterministic syntax mistakes."
    ),
}


@dataclass
class RootCause:
    """One probable cause behind a group of diagnostics."""

    category: str
    summary: str
    diagnostics: list[Diagnostic] = field(default_factory=list)
    suggestion: str = ""

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "summary": self.summary,
            "count": len(self.diagnostics),
            "suggestion": self.suggestion,
        }


@dataclass
class FailureAnalysis:
    """Everything the repair engine needs to know about a failed build."""

    diagnostics: list[Diagnostic] = field(default_factory=list)
    errors: list[Diagnostic] = field(default_factory=list)
    root_causes: list[RootCause] = field(default_factory=list)
    summary: str = ""
    exit_code: int = -1

    @property
    def first_error(self) -> Diagnostic | None:
        """The earliest meaningful error.

        A diagnostic that points at a file and a line is more useful than one
        without a location, so it is preferred.
        """
        located = [d for d in self.errors if d.file and d.line]
        if located:
            return located[0]
        return self.errors[0] if self.errors else None

    def manual_actions(self) -> list[str]:
        """Actionable explanations for the problems that cannot be fixed safely."""
        actions: list[str] = []
        seen: set[str] = set()
        for cause in self.root_causes:
            text = cause.suggestion or MANUAL_ACTIONS.get(cause.category, "")
            if text and text not in seen:
                seen.add(text)
                actions.append(text)
        return actions

    def as_dict(self) -> dict:
        return {
            "summary": self.summary,
            "exit_code": self.exit_code,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "root_causes": [c.to_dict() for c in self.root_causes],
            "manual_actions": self.manual_actions(),
        }

    def text(self) -> str:
        lines = [self.summary or "The build failed."]
        for cause in self.root_causes:
            lines.append(f"  [{cause.category}] {cause.summary}")
            for diag in cause.diagnostics[:3]:
                lines.append(f"      {diag.text()}")
        for action in self.manual_actions():
            lines.append(f"  -> {action}")
        return "\n".join(lines)


def _summarise(cause: RootCause) -> str:
    first = cause.diagnostics[0]
    where = first.file or "the project"
    if first.line:
        where = f"{where}:{first.line}"
    return f"{where}: {first.message}"


def group_root_causes(diagnostics: list[Diagnostic]) -> list[RootCause]:
    """Group diagnostics that most likely share one cause."""
    groups: dict[tuple[str, str], RootCause] = {}
    by_category: dict[str, RootCause] = {}
    for diag in errors_only(diagnostics):
        if not diag.file:
            # A diagnostic without a location belongs to the first cause of
            # the same category: it is the same problem, reported again.
            cause = by_category.get(diag.category)
            if cause is not None:
                cause.diagnostics.append(diag)
                continue
        key = (diag.category, diag.file)
        cause = groups.get(key)
        if cause is None:
            cause = RootCause(
                category=diag.category,
                summary="",
                diagnostics=[],
                suggestion=MANUAL_ACTIONS.get(diag.category, ""),
            )
            groups[key] = cause
            by_category.setdefault(diag.category, cause)
        cause.diagnostics.append(diag)
    causes = list(groups.values())
    for cause in causes:
        cause.summary = _summarise(cause)
    # The earliest error of the earliest file comes first.
    causes.sort(key=lambda c: (c.diagnostics[0].file == "", c.diagnostics[0].file, c.diagnostics[0].line))
    return causes


def analyze(
    diagnostics: list[Diagnostic],
    *,
    exit_code: int = -1,
    errors: list[str] | None = None,
) -> FailureAnalysis:
    """Build the analysis of a failed build."""
    found = errors_only(diagnostics)
    causes = group_root_causes(diagnostics)
    if found:
        first = found[0]
        summary = (
            f"The build failed with {len(found)} error(s). "
            f"The first one is: {first.text()}"
        )
    else:
        detail = (errors or ["the build tool reported a failure"])[0]
        summary = f"The build failed: {detail}"
    return FailureAnalysis(
        diagnostics=list(diagnostics),
        errors=found,
        root_causes=causes,
        summary=summary,
        exit_code=exit_code,
    )
