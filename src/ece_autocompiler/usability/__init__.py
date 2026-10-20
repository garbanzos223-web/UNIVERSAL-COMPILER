"""Usability helpers: guidance, health checks, build plans, history queries.

Everything in this package is plain Python with no Tkinter import, so the CLI,
the desktop window and the tests all share exactly the same wording and the same
decisions. The guiding rule is *honesty*: a helper may say "I do not know yet"
or "this will not work here", but it never invents a result.
"""

from .guidance import (
    FailureReport,
    GuidanceContext,
    NextStep,
    diagnose_failure,
    explain_extraction,
    explain_validation,
    next_step,
    state_text,
)
from .health import (
    HealthIssue,
    ProjectHealth,
    ToolchainVersion,
    check_project_health,
    toolchain_versions,
)
from .history_query import (
    as_dict as history_as_dict,
    export_csv as export_history_csv,
    export_json as export_history_json,
    filter_records as filter_history,
    replay_command,
)
from .plan import (
    PlanPreview,
    PlanStepPreview,
    preview_plan,
    render_plan_text,
)
from .summary import BuildSummary, build_summary
from .wizard import WIZARD_STEPS, WizardStep

__all__ = [
    "BuildSummary",
    "FailureReport",
    "GuidanceContext",
    "HealthIssue",
    "NextStep",
    "PlanPreview",
    "PlanStepPreview",
    "ProjectHealth",
    "ToolchainVersion",
    "WIZARD_STEPS",
    "WizardStep",
    "build_summary",
    "check_project_health",
    "diagnose_failure",
    "explain_extraction",
    "explain_validation",
    "export_history_csv",
    "export_history_json",
    "filter_history",
    "history_as_dict",
    "next_step",
    "preview_plan",
    "render_plan_text",
    "replay_command",
    "state_text",
    "toolchain_versions",
]
