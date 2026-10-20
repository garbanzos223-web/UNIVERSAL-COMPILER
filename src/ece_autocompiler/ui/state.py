"""Explicit UI state machine for the main window.

Why: the window receives signals from several workers (detection, build, exe
export) and used to assemble badge/buttons/status ad hoc, which allowed
contradictory combinations (a "Done" badge while Compile stayed disabled).
Now every visible aspect of a state derives from ``STATE_UI`` and the only
transition point is ``MainWindow._set_state``.

States (the honest lifecycle of one task):

    IDLE ──► DETECTING ──► IDLE
      │
      ├──► COMPILING ──► SUCCESS / ERROR / CANCELLED ──► IDLE
      │
      └──► (from any state, an error lands on ERROR)

``STATE_UI`` is pure data so it can be unit-tested without a display.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class UiState(enum.Enum):
    """The honest states of the main window."""

    IDLE = "idle"
    DETECTING = "detecting"
    PLANNING = "planning"
    COMPILING = "compiling"
    PACKAGING = "packaging"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


#: States in which a task owns the window: Compile is disabled and Cancel is on.
BUSY_STATES: frozenset[UiState] = frozenset(
    {UiState.PLANNING, UiState.COMPILING, UiState.PACKAGING}
)


@dataclass(frozen=True)
class StateSpec:
    """Everything the window must show while in one state."""

    badge_text: str          # header pill text
    badge_style: str         # ttk style of the header pill (see theme.py)
    build_state: str         # Compile button state: "normal" | "disabled"
    cancel_state: str        # Cancel button state: "normal" | "disabled"
    log_tag: str             # severity tag for an optional state log line


STATE_UI: dict[UiState, StateSpec] = {
    UiState.IDLE: StateSpec(
        badge_text="Ready",
        badge_style="StateIdle.TLabel",
        build_state="normal",
        cancel_state="disabled",
        log_tag="muted",
    ),
    UiState.DETECTING: StateSpec(
        badge_text="Scanning",
        badge_style="StateScan.TLabel",
        build_state="normal",
        cancel_state="disabled",
        log_tag="accent",
    ),
    UiState.PLANNING: StateSpec(
        badge_text="Planning",
        badge_style="StateScan.TLabel",
        build_state="disabled",
        cancel_state="normal",
        log_tag="accent",
    ),
    UiState.COMPILING: StateSpec(
        badge_text="Compiling",
        badge_style="StateBusy.TLabel",
        build_state="disabled",
        cancel_state="normal",
        log_tag="accent",
    ),
    UiState.PACKAGING: StateSpec(
        badge_text="Packaging",
        badge_style="StateBusy.TLabel",
        build_state="disabled",
        cancel_state="normal",
        log_tag="accent",
    ),
    UiState.SUCCESS: StateSpec(
        badge_text="Done",
        badge_style="StateOk.TLabel",
        build_state="normal",
        cancel_state="disabled",
        log_tag="ok",
    ),
    UiState.ERROR: StateSpec(
        badge_text="Failed",
        badge_style="StateErr.TLabel",
        build_state="normal",
        cancel_state="disabled",
        log_tag="err",
    ),
    UiState.CANCELLED: StateSpec(
        badge_text="Cancelled",
        badge_style="StateWarn.TLabel",
        build_state="normal",
        cancel_state="disabled",
        log_tag="warn",
    ),
}
