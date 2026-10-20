"""The first-run walkthrough (item 1).

A short, ordered set of steps shown once (until the user asks to see it again).
The steps are pure data: the desktop window renders them, and
:func:`should_show` decides from the saved settings whether to open them.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Key stored in ``settings.json`` once the walkthrough has been shown.
SETTINGS_KEY = "wizard_seen"


@dataclass(frozen=True)
class WizardStep:
    key: str
    title: str
    body: str


WIZARD_STEPS: tuple[WizardStep, ...] = (
    WizardStep(
        "welcome",
        "Welcome to ECE Universal AutoCompiler",
        "This tool takes a software project on this PC and turns it into a single "
        "file you can keep or share: an .ece container, or a standalone .exe.\n\n"
        "Everything happens on this machine. Nothing is uploaded anywhere and no "
        "account is needed.",
    ),
    WizardStep(
        "pick",
        "Step 1 - pick your project folder",
        "Press Browse and choose the folder that directly contains the project "
        "files (the one with package.json, pyproject.toml, Cargo.toml, main.py or "
        "CMakeLists.txt). If your repository holds several projects, pick the "
        "subfolder for the one you want - the tool also lists the others it finds.",
    ),
    WizardStep(
        "detect",
        "2. Press Detect",
        "Compile stays disabled until the project is understood. Detect reads the "
        "folder, tells you what kind of project it is and checks whether the "
        "compiler it needs is installed. It never runs your code.",
    ),
    WizardStep(
        "tools",
        "3. Install anything that is missing",
        "If a compiler is missing, the window shows exactly what is missing and a "
        "command you can copy to install it. After installing, press Detect again.",
    ),
    WizardStep(
        "plan",
        "4. (Optional) preview the plan",
        "You can press 'Preview plan' to see the exact commands that would run, "
        "before anything happens. Nothing is executed while you review it.",
    ),
    WizardStep(
        "build",
        "5. Press Compile",
        "Compile runs the real build and packages the result. You can cancel at "
        "any time; cancelling never leaves a half-written output file behind.",
    ),
    WizardStep(
        "output",
        "6. Find your output and copy the summary",
        "When it finishes, the summary says exactly what was created, where it is "
        "and how big it is. Press 'Open folder' to find it, or 'Copy summary' to "
        "keep the text.",
    ),
    WizardStep(
        "help",
        "Stuck?",
        "Inside the window: Help > Keyboard shortcuts, Help > Run environment "
        "check (doctor), and the 'What do I do now?' button always suggests the "
        "next useful action. Every error states what happened, why and what to do.",
    ),
)


def should_show(settings: dict) -> bool:
    """True when the walkthrough has not been seen yet."""
    return not bool(settings.get(SETTINGS_KEY, False))


def mark_seen(settings: dict) -> dict:
    """Return a copy of *settings* that records the walkthrough as shown."""
    updated = dict(settings)
    updated[SETTINGS_KEY] = True
    return updated
