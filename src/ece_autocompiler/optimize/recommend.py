"""Actionable optimization recommendations.

Every recommendation says whether it was applied automatically or whether it
needs the user's approval, and never claims a result that was not measured.
"""

from __future__ import annotations

from dataclasses import dataclass

from .measure import ComparisonReport
from .plan import OptimizationPlan
from .profiles import ADAPTER_FAMILY, INTERPRETER_FAMILIES


@dataclass
class Recommendation:
    """One suggestion, with its status."""

    text: str
    requires_approval: bool = False
    applied: bool = False

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "requires_approval": self.requires_approval,
            "applied": self.applied,
        }


def recommendations(
    adapter_id: str,
    optimization: OptimizationPlan | None = None,
    report: ComparisonReport | None = None,
) -> list[Recommendation]:
    """Suggestions for this project and toolchain."""
    optimization = optimization or OptimizationPlan()
    family = ADAPTER_FAMILY.get(adapter_id, "")
    items: list[Recommendation] = []

    if family in INTERPRETER_FAMILIES:
        items.append(
            Recommendation(
                "This project runs on an interpreter, so no compiler flags apply. "
                "Bundle it into a native executable (PyInstaller for Python, a "
                "bundler for Node) if you need a single file to ship.",
                requires_approval=True,
            )
        )
        return items

    if not family:
        items.append(
            Recommendation(
                "This project type has no known toolchain in this application, so no "
                "optimization could be selected.",
            )
        )
        return items

    if not optimization.active:
        items.append(
            Recommendation(
                "No optimization profile is selected. The 'Release' profile usually "
                "gives a much faster binary at a similar build time.",
                requires_approval=True,
            )
        )
    elif optimization.profile == "debug":
        items.append(
            Recommendation(
                "The debug profile keeps symbols and does not optimize. Use it for "
                "development only; ship a 'Release', 'Balanced' or 'Size' build.",
                requires_approval=True,
            )
        )

    if optimization.family == "cmake":
        items.append(
            Recommendation(
                "Link-time optimization (-flto) is available with this toolchain. It "
                "usually shrinks the binary and speeds it up, at the cost of a slower "
                "link step.",
                requires_approval=True,
            )
        )
    if optimization.family == "dotnet":
        items.append(
            Recommendation(
                "PublishTrimmed reduces the size but can break reflection-based code. "
                "Only enable it after the tests pass with it.",
                requires_approval=True,
            )
        )

    if report is not None:
        if report.size_change_pct is not None and report.size_change_pct > 0:
            items.append(
                Recommendation(
                    f"The optimized build is {report.size_change_pct:.1f}% larger than "
                    "the baseline. Try the 'Size' profile if a smaller download matters.",
                    requires_approval=True,
                )
            )
        if report.size_change_pct is not None and report.size_change_pct < -10:
            items.append(
                Recommendation(
                    f"The optimized build is {abs(report.size_change_pct):.1f}% smaller.",
                    applied=True,
                )
            )
        if report.time_change_pct is not None and report.time_change_pct > 50:
            items.append(
                Recommendation(
                    f"The optimized build takes {report.time_change_pct:.1f}% longer "
                    "to compile. That is the usual price of link-time optimization.",
                )
            )
        if report.runtime_change_pct is None:
            items.append(
                Recommendation(
                    "No runtime benchmark was run, so no speed claim can be made. "
                    "Pass a benchmark command to measure one.",
                    requires_approval=True,
                )
            )
    return items
