"""Visible build plan: dry-run preview, exact commands, package-only mode.

Items covered:

* item 13 - show the plan before running anything;
* item 14 - show the exact command that will run and let it be copied;
* item 23 - mark which steps can be skipped safely, and why;
* item 25 - "package only" (skip the build, package what is already there);
* item 18 - surface which steps will run project scripts (consent detail).

Nothing in this module executes a command. It reads detection and adapters and
returns data; the CLI prints it and the window renders it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig, Limits
from ..detection import scan
from ..detection.detector import ADAPTER_LABELS

#: Step ids that are safe to skip: they only check or validate, they never
#: produce the artifact the build needs.
_SKIPPABLE_IDS = {"pyinstaller_check", "php-none"}
_SKIPPABLE_PREFIXES = ("php-lint",)


def quote_arg(arg: str) -> str:
    """Quote one argv element for a copy-paste command line on Windows."""
    if arg == "":
        return '""'
    if any(ch in arg for ch in ' \t"&|<>^()'):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


def format_command(argv: list[str]) -> str:
    """Render an argv list as a single copyable command line."""
    return " ".join(quote_arg(str(a)) for a in argv)


def _is_skippable(step) -> bool:
    if getattr(step, "requires_approval", False):
        return False
    sid = str(getattr(step, "id", ""))
    title = str(getattr(step, "title", "")).lower()
    if sid in _SKIPPABLE_IDS or sid.startswith(_SKIPPABLE_PREFIXES):
        return True
    if not getattr(step, "cmd_args", None):
        return True
    return any(word in title for word in ("check", "lint", "compileall"))


@dataclass
class PlanStepPreview:
    """One step of the plan, with the exact command it would run."""

    id: str
    title: str
    command: list[str] = field(default_factory=list)
    workdir: str = ""
    timeout: int | None = None
    requires_consent: bool = False
    skippable: bool = False
    note: str = ""

    @property
    def command_line(self) -> str:
        return format_command(self.command)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "command": list(self.command),
            "command_line": self.command_line,
            "workdir": self.workdir,
            "timeout": self.timeout,
            "requires_consent": self.requires_consent,
            "skippable": self.skippable,
            "note": self.note,
        }


@dataclass
class PlanPreview:
    """What a build would do, decided without doing any of it."""

    root: Path
    adapter_id: str = ""
    project_type: str = ""
    mode: str = "ece"                       # "ece" or "exe"
    steps: list[PlanStepPreview] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    requires_consent: bool = False
    package_only: bool = False
    candidates: list[dict] = field(default_factory=list)
    selected_root: str = ""

    @property
    def ready(self) -> bool:
        return not self.blockers

    @property
    def script_steps(self) -> list[PlanStepPreview]:
        return [s for s in self.steps if s.requires_consent]

    @property
    def skippable_steps(self) -> list[PlanStepPreview]:
        return [s for s in self.steps if s.skippable]

    @property
    def label(self) -> str:
        return ADAPTER_LABELS.get(self.adapter_id, self.adapter_id or "unknown")

    def to_dict(self) -> dict:
        return {
            "root": str(self.root),
            "adapter_id": self.adapter_id,
            "project_type": self.project_type,
            "label": self.label,
            "mode": self.mode,
            "ready": self.ready,
            "requires_consent": self.requires_consent,
            "package_only": self.package_only,
            "blockers": list(self.blockers),
            "notes": list(self.notes),
            "candidates": list(self.candidates),
            "selected_root": self.selected_root,
            "steps": [s.to_dict() for s in self.steps],
        }

    def text(self) -> str:
        return render_plan_text(self)


def _exe_steps(adapter_id: str, project: Path, out_path: Path, *, onefile: bool, console: bool):
    """Synthesize the commands the .exe exporter would run, without running them."""
    from ..build import exe_exporter
    from ..detection.toolchains import get_tool

    steps: list[tuple[str, str, list[str], bool]] = []  # (id, title, argv, needs_consent)
    notes: list[str] = []

    def tool_path(tool_id: str, fallback: str) -> str:
        tool = get_tool(tool_id)
        return tool.path if tool and tool.path else fallback

    if adapter_id == "python":
        entry = exe_exporter.find_entry_point(project)
        interpreter = exe_exporter.pyinstaller_interpreter() or "python"
        mode_flag = "--onefile" if onefile else "--onedir"
        if entry is None:
            notes.append("No entry point found yet (main.py/app.py/run.py); the export would stop here.")
            return steps, notes
        steps.append(
            (
                "pyinstaller",
                f"PyInstaller ({mode_flag.strip('-')})",
                [
                    interpreter, "-m", "PyInstaller", mode_flag,
                    "--name", entry.stem,
                    ("--noconsole" if not console else "--console"),
                    entry.name,
                ],
                False,
            )
        )
    elif adapter_id == "dotnet":
        argv = [tool_path("dotnet", "dotnet"), "publish", ".", "-c", "Release", "-r", "win-x64",
                "--self-contained", "true"]
        if onefile:
            argv.append("-p:PublishSingleFile=true")
        steps.append(("dotnet-publish", "dotnet publish (Release, win-x64)", argv, True))
    elif adapter_id == "rust-cargo":
        steps.append(("cargo-build", "cargo build --release",
                      [tool_path("cargo", "cargo"), "build", "--release"], False))
    elif adapter_id == "go":
        steps.append(("go-build", "go build",
                      [tool_path("go", "go"), "build", "-o", out_path.name, "."], False))
    elif adapter_id == "cpp-cmake":
        steps.append(("cmake-configure", "cmake configure",
                      [tool_path("cmake", "cmake"), "-S", ".", "-B", "build"], False))
        steps.append(("cmake-build", "cmake --build (Release)",
                      [tool_path("cmake", "cmake"), "--build", "build", "--config", "Release"], False))
    elif adapter_id == "cpp-make":
        steps.append(("make-build", "make (Release)", [tool_path("make", "make"), "RELEASE=1"], False))
    return steps, notes


def _exe_plan(project: Path, adapter_id: str, label: str, preview: PlanPreview,
              *, onefile: bool, console: bool) -> None:
    from ..build.exe_exporter import exe_readiness

    readiness = exe_readiness(adapter_id)
    steps, notes = _exe_steps(adapter_id, project, project / f"{project.name}.exe",
                              onefile=onefile, console=console)
    preview.notes.extend(notes)
    preview.notes.append(f"Export readiness: {readiness.summary}")
    for action in readiness.actions:
        preview.notes.append(f"next: {action}")
    if not readiness.possible:
        preview.blockers.extend(readiness.actions or [readiness.summary])
    elif not steps:
        preview.blockers.append(
            f"No .exe command is available for {label} in this environment."
        )
    for sid, title, argv, needs_consent in steps:
        preview.steps.append(
            PlanStepPreview(
                id=sid,
                title=title,
                command=list(argv),
                workdir=str(project),
                requires_consent=needs_consent,
                # Export steps all produce the artifact, so none are skippable.
                skippable=False,
            )
        )
    if any(step.requires_consent for step in preview.steps):
        preview.requires_consent = True


def preview_plan(
    root: Path | str,
    *,
    prefer_adapter: str | None = None,
    mode: str = "ece",
    config_overrides: dict | None = None,
    limits: Limits | None = None,
    config: AppConfig | None = None,
    onefile: bool = True,
    console: bool = False,
) -> PlanPreview:
    """Build a plan for *root* without executing anything."""
    limits = limits or Limits()
    config = config or AppConfig()  # noqa: F841 - kept for signature symmetry
    root = Path(root).resolve()
    preview = PlanPreview(root=root, mode=mode)

    if not root.exists():
        preview.blockers.append("The project folder does not exist.")
        return preview
    if not root.is_dir():
        preview.blockers.append("The selected path is a file, not a project folder.")
        return preview

    try:
        result = scan(root, limits=limits)
    except Exception as exc:  # noqa: BLE001 - surface as a blocker, never a crash
        preview.blockers.append(f"Detection failed: {exc}")
        return preview

    preview.candidates = [
        {
            "adapter_id": c.adapter_id,
            "label": ADAPTER_LABELS.get(c.adapter_id, c.adapter_id),
            "root": c.root,
            "confidence": round(c.confidence, 3),
            "evidence": list(c.evidence),
        }
        for c in result.candidates
    ]
    if result.ambiguous:
        preview.notes.append("More than one project was found; the chosen one is shown below.")

    best = result.best
    if prefer_adapter:
        for candidate in result.candidates:
            if candidate.adapter_id == prefer_adapter:
                best = candidate
                break
    if best is None:
        preview.blockers.append(
            "No supported project was detected. Point at the folder that contains "
            "the project files (package.json, pyproject.toml, Cargo.toml, ...)."
        )
        return preview

    preview.adapter_id = best.adapter_id
    preview.project_type = best.project_type
    preview.selected_root = best.root
    label = ADAPTER_LABELS.get(best.adapter_id, best.adapter_id)
    project_dir = (root / best.root).resolve() if best.root else root

    if mode == "exe":
        _exe_plan(project_dir, best.adapter_id, label, preview,
                  onefile=onefile, console=console)
        return preview

    # --- .ece build plan ---------------------------------------------------
    from ..adapters.registry import AdapterRegistry

    adapter = AdapterRegistry().for_detected(best)
    if adapter is None:
        preview.blockers.append(f"No builder is available for {label}.")
        return preview

    requirements = adapter.inspect_requirements(project_dir)
    if not requirements.ready:
        missing = [r.name for r in requirements.requirements if not r.found]
        preview.blockers.append(
            f"Missing toolchain: {', '.join(missing) or 'unknown'}. "
            "Install it, then run the build."
        )

    plan = adapter.create_build_plan(project_dir, config_overrides)
    preview.requires_consent = bool(plan.requires_consent)
    for note in plan.notes:
        preview.notes.append(note)
    for step in plan.steps:
        preview.steps.append(
            PlanStepPreview(
                id=str(getattr(step, "id", "")),
                title=str(getattr(step, "title", "")),
                command=list(getattr(step, "cmd_args", []) or []),
                workdir=str(getattr(step, "workdir", None) or project_dir),
                timeout=getattr(step, "timeout", None),
                requires_consent=bool(getattr(step, "requires_approval", False)),
                skippable=_is_skippable(step),
            )
        )
    if preview.requires_consent:
        preview.notes.append(
            "Some steps run scripts from the project; they only run if you allow "
            "project scripts (consent)."
        )
    return preview


def render_plan_text(preview: PlanPreview) -> str:
    """Render a plan as stable, copyable text (item 13 / item 14)."""
    lines: list[str] = []
    lines.append(f"Build plan for {preview.root}")
    lines.append(f"  Project : {preview.label}" + (f" ({preview.adapter_id})" if preview.adapter_id else ""))
    if preview.selected_root:
        lines.append(f"  In      : {preview.selected_root or '.'}")
    chosen = next(
        (c for c in preview.candidates if c.get("adapter_id") == preview.adapter_id), None
    )
    if chosen and chosen.get("evidence"):
        lines.append("  Why     : " + ", ".join(str(e) for e in chosen["evidence"][:6]))
    lines.append(f"  Output  : {'.exe' if preview.mode == 'exe' else '.ece'}")
    lines.append(f"  Ready   : {'yes' if preview.ready else 'no'}")
    if preview.requires_consent:
        lines.append("  Consent : required - steps below run project scripts")
    if preview.package_only:
        lines.append("  Mode    : package only (no build step is run)")

    if preview.steps:
        lines.append("  Steps:")
        for index, step in enumerate(preview.steps, start=1):
            lines.append(f"    {index}. {step.title}")
            if step.command:
                lines.append(f"       command: {step.command_line}")
            if step.workdir:
                lines.append(f"       in     : {step.workdir}")
            if step.requires_consent:
                lines.append("       [runs project scripts - needs consent]")
            if step.skippable:
                lines.append("       [can be skipped safely]")
    else:
        lines.append("  Steps: (none - nothing would run)")

    if preview.blockers:
        lines.append("  Blockers:")
        for blocker in preview.blockers:
            lines.append(f"    - {blocker}")
    if preview.notes:
        lines.append("  Notes:")
        for note in preview.notes:
            lines.append(f"    - {note}")
    return "\n".join(lines)
