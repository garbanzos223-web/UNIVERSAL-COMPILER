"""Plain-language guidance: what is happening, and what to do next.

This module is the single source of the sentences the user reads when they are
lost, stuck or staring at a failure:

* item 2  - what to do when no project is selected;
* item 4  - a contextual "what do I do now?" for the current state;
* item 9  - the fixed *what happened / why / what to do* failure shape;
* item 19 - validate/extract results in plain language;
* item 31 - an explicit sentence for every state;
* item 35 - a failure category plus the next suggested action;
* item 37 - proof of what was actually done (nothing is ever claimed that the
  tool did not do).

No widget code lives here on purpose: the CLI prints these strings, the window
renders them, and the unit tests assert them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..diagnostics.classifier import (
    CATEGORY_LABELS,
    Category,
    Diagnostic,
    classify,
    first_actionable,
)

# ---------------------------------------------------------------------------
# item 31 - one honest sentence per state
# ---------------------------------------------------------------------------

#: state id -> (short badge, longer explanation for the user).
STATE_TEXT: dict[str, tuple[str, str]] = {
    "idle": (
        "Ready",
        "Nothing is running. Pick a project folder, then Detect to see what it is "
        "or Compile to build it.",
    ),
    "detecting": (
        "Scanning",
        "Reading the folder to find out what kind of project it is. Nothing is "
        "compiled yet and no project script runs during a scan.",
    ),
    "planning": (
        "Planning",
        "Working out the exact commands that would run, without running them. "
        "You can review and copy the plan before committing to a build.",
    ),
    "compiling": (
        "Compiling",
        "The build tools are running right now. You can cancel at any moment "
        "without leaving a half-written output file.",
    ),
    "packaging": (
        "Packaging",
        "The build finished; the output container or executable is being written "
        "and verified now.",
    ),
    "success": (
        "Done",
        "The output was produced. The summary states exactly what was created, "
        "where it is and how big it is.",
    ),
    "error": (
        "Failed",
        "Something stopped the work. The message says what happened, why, and "
        "the concrete next step. Nothing is claimed to have succeeded.",
    ),
    "cancelled": (
        "Cancelled",
        "You cancelled the work. No further step will run.",
    ),
}


def state_text(state: str) -> tuple[str, str]:
    """Return (badge, explanation) for a state id; unknown ids fall back to idle."""
    return STATE_TEXT.get(state, STATE_TEXT["idle"])


# ---------------------------------------------------------------------------
# item 4 - contextual "what do I do now?"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NextStep:
    """One concrete suggestion for the user, with the action that carries it out."""

    kind: str                       # machine key, e.g. "install-toolchain"
    title: str                      # short imperative sentence
    detail: str = ""                # why / what it will do
    commands: tuple[str, ...] = ()  # copyable commands (install hints, ...)
    action: str = ""                # the GUI button/command that performs it


@dataclass
class GuidanceContext:
    """Everything the "what now?" decision needs, gathered by the caller."""

    state: str = "idle"
    project_selected: bool = False
    project_exists: bool = False
    detected: bool = False
    candidates: int = 0
    ambiguous: bool = False
    missing_toolchains: list[str] = field(default_factory=list)
    mode: str = "ece"                 # "ece" or "exe"
    build_attempted: bool = False
    build_failed: bool = False
    health_issues: int = 0


def next_step(ctx: GuidanceContext) -> NextStep:
    """Decide the single most useful next action for the current situation."""
    if ctx.state in ("compiling", "packaging", "planning"):
        return NextStep(
            "wait",
            "A build is running.",
            "Watch the log; press Cancel if you change your mind. "
            "The window will warn you before closing while work is in progress.",
            action="cancel",
        )
    if not ctx.project_selected:
        return NextStep(
            "select-project",
            "Choose a project folder.",
            "Point the tool at the folder that directly contains the project files "
            "(package.json, pyproject.toml, Cargo.toml, main.py, CMakeLists.txt...).",
            action="browse-project",
        )
    if not ctx.project_exists:
        return NextStep(
            "fix-project",
            "That folder does not exist any more.",
            "Pick the folder again; it may have been moved, renamed or deleted.",
            action="browse-project",
        )
    if ctx.candidates == 0 and not ctx.detected:
        return NextStep(
            "detect",
            "Scan the folder.",
            "Press Detect to find out what kind of project it is and whether the "
            "toolchain it needs is installed on this PC.",
            action="detect",
        )
    if not ctx.detected:
        return NextStep(
            "detect",
            "Run a scan to continue.",
            "Detection is what enables Compile and reveals any missing tools.",
            action="detect",
        )
    if ctx.missing_toolchains:
        names = ", ".join(ctx.missing_toolchains[:4])
        return NextStep(
            "install-toolchain",
            f"Install the missing toolchain: {names}.",
            "The project was detected, but the compiler needed to produce the "
            "output is not installed. Install it, then press Detect again.",
            action="show-toolchains",
        )
    if ctx.ambiguous:
        return NextStep(
            "choose-candidate",
            "Choose which subproject to build.",
            "More than one project was found. Select the one you want before compiling.",
            action="choose-candidate",
        )
    if ctx.health_issues:
        return NextStep(
            "review-health",
            "Review the project health notes.",
            "A few things could trip up the build. They are listed in the health "
            "report; you can still compile if you accept them.",
            action="show-health",
        )
    if ctx.build_failed:
        return NextStep(
            "retry",
            "Fix the reported problem, then retry.",
            "The previous attempt failed. Correct the cause named in the message "
            "and press Compile again - the settings are kept.",
            action="build",
        )
    if ctx.build_attempted:
        return NextStep(
            "open-output",
            "Open the output folder.",
            "The build finished. Open the folder to find the produced file.",
            action="open-output",
        )
    return NextStep(
        "build",
        "Compile the project.",
        "Everything looks ready. Press Compile to run the real build and produce "
        + ("an .exe." if ctx.mode == "exe" else "an .ece container."),
        action="build",
    )


# ---------------------------------------------------------------------------
# item 9 / item 35 - the fixed failure shape
# ---------------------------------------------------------------------------


@dataclass
class FailureReport:
    """A failure rendered as what happened / why / what to do."""

    what: str
    why: list[str] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)
    category: str = ""
    category_label: str = ""
    log: str = ""

    def lines(self) -> list[str]:
        lines = [f"What happened: {self.what or 'the operation failed'}"]
        if self.category_label:
            lines.append(f"Category: {self.category_label}")
        if self.why:
            lines.append("Why:")
            lines.extend(f"  - {item}" for item in self.why[:6])
        lines.append("What to do:")
        for item in (self.todo or ["Open the build log for the exact error, fix it and retry."])[:6]:
            lines.append(f"  - {item}")
        if self.log:
            lines.append(f"Log: {self.log}")
        return lines

    def as_text(self) -> str:
        return "\n".join(self.lines())

    def as_dict(self) -> dict:
        return {
            "what": self.what,
            "why": list(self.why),
            "what_to_do": list(self.todo),
            "category": self.category,
            "category_label": self.category_label,
            "log": self.log,
        }


#: The first thing to try for each failure category.
CATEGORY_ACTIONS: dict[Category, str] = {
    Category.MISSING_TOOL: "Install the missing tool, then run the build again.",
    Category.MISSING_DEPENDENCY: "Install the project's dependencies and try again.",
    Category.INVALID_CONFIG: "Check the build configuration or manifest and try again.",
    Category.SYNTAX_TYPE: "Open the source file and line named in the message and fix it.",
    Category.LINKER: "Check that the library or object named in the message is available.",
    Category.MISSING_ASSET: "Restore the file named in the message, then build again.",
    Category.UNSUPPORTED_TARGET: "Choose a supported target (Windows x64) and try again.",
    Category.PERMISSION: "Close any program using the files, or build from a writable folder.",
    Category.NETWORK: "Check the network or proxy, then run the build again.",
    Category.TIMEOUT: "Increase the timeout, or investigate what is hanging.",
    Category.UNKNOWN: "Open the build log; its last lines name the real problem.",
}

#: tool-chain hint id -> lower-case fragments that identify it in raw output.
TOOL_TOKENS: dict[str, tuple[str, ...]] = {
    "cl": ("cl.exe", "msvc"),
    "msbuild": ("msbuild",),
    "cmake": ("cmake",),
    "ninja": ("ninja",),
    "gcc": ("gcc", "g++", "mingw", "mingw32-make"),
    "dotnet": ("dotnet", ".net sdk"),
    "cargo": ("cargo", "rustc", "rustup"),
    "go": ("go build", "golang"),
    "javac": ("javac",),
    "mvn": ("maven",),
    "gradle": ("gradle",),
    "node": ("node", "npm", "npx"),
    "pyinstaller": ("pyinstaller",),
    "minisign": ("minisign",),
    "cosign": ("cosign",),
    "gpg": ("gpg", "gpg4win"),
}

#: Alias map kept for callers that only need fragments -> tool id.
TOOL_ALIASES: dict[str, str] = {
    token: tool for tool, tokens in TOOL_TOKENS.items() for token in tokens
}


def _install_hint_for_output(text: str) -> list[str]:
    """Copyable install commands for whatever tool the output names, if any."""
    from ..toolchain_hints import hint_lines

    lowered = text.lower()
    chosen: str | None = None
    for tool_id, tokens in TOOL_TOKENS.items():
        if any(token in lowered for token in tokens):
            chosen = tool_id
            break
    if chosen is None:
        return []
    return hint_lines(chosen)


def diagnose_failure(output: str, *, what: str = "the build failed") -> FailureReport:
    """Classify raw build output into a three-part, actionable report."""
    diags: list[Diagnostic] = classify(output)
    if not diags:
        return FailureReport(
            what=what,
            why=["The build tool exited with an error; no known pattern matched its output."],
            todo=["Open the build log for the exact error, fix it and try again."],
            category=Category.UNKNOWN.value,
            category_label=CATEGORY_LABELS[Category.UNKNOWN],
        )

    why = [f"{CATEGORY_LABELS[d.category]}: {d.line.strip()[:200]}" for d in diags[:6]]

    todo: list[str] = []
    action = CATEGORY_ACTIONS.get(diags[0].category, "")
    best = first_actionable(diags)
    if best and best.hint:
        action = (action + " " + best.hint).strip()
    if action:
        todo.append(action)

    if any(d.category is Category.MISSING_TOOL for d in diags):
        todo.extend(_install_hint_for_output(output))

    todo.append("If it still fails, copy the build log - it has the full command and output.")

    seen: set[str] = set()
    unique = [t for t in todo if not (t in seen or seen.add(t))]
    return FailureReport(
        what=what,
        why=why,
        todo=unique,
        category=diags[0].category.value,
        category_label=CATEGORY_LABELS[diags[0].category],
    )


# ---------------------------------------------------------------------------
# item 19 - plain-language container results
# ---------------------------------------------------------------------------


def explain_validation(report) -> list[str]:
    """Turn a ValidationReport into human sentences (no codes, no jargon-dump)."""
    lines: list[str] = []
    if report.ok:
        lines.append(
            f"This is a valid .ece file: {report.entry_count} file(s), "
            f"{report.total_uncompressed} bytes of content."
        )
        if report.hashes_verified:
            lines.append(
                "Every file was re-read and its fingerprint matched, so nothing is corrupted."
            )
        else:
            lines.append(
                "The structure and fingerprints were checked without re-reading the payload."
            )
        for warning in report.warnings:
            lines.append(f"Minor note: {warning.message}.")
    else:
        lines.append("This .ece file is not valid, so it should not be trusted or extracted.")
        for issue in report.errors:
            detail = f" ({issue.detail})" if issue.detail else ""
            lines.append(f"Problem: {issue.message}{detail}")
    state = getattr(report, "signature_state", "not-checked")
    if state == "verified":
        lines.append("The publisher's signature was verified.")
    elif state == "present":
        lines.append("A signature is present but was not verified (use --verify-signature).")
    return lines


def explain_extraction(report) -> list[str]:
    """Turn an ExtractionReport into human sentences."""
    lines = [
        f"Wrote {report.files_written} file(s) ({report.bytes_written} bytes) to {report.destination}."
    ]
    if report.overwritten:
        lines.append(f"Replaced {report.overwritten} existing file(s).")
    if report.skipped:
        lines.append(f"Skipped {len(report.skipped)} file(s) (they already existed).")
    for warning in report.warnings:
        lines.append(f"Note: {warning}")
    return lines
