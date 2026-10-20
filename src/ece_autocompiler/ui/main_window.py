"""Desktop interface for ECE Universal AutoCompiler.

The interface is deliberately simple: pick a project folder, review the detected
technology, press **Compile**, and watch the log. Toolchain discovery, build
history and the About box live in dialogs behind the menu bar.

Every long operation runs on a :class:`TaskRunner` thread; panels only touch
widgets from Tk callbacks (the runner marshals worker results back to the Tk
main loop, which is the only safe way to touch widgets).
"""

from __future__ import annotations

import os
import threading
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..build import BuildOrchestrator
from ..build import history as build_history
from ..build.exe_exporter import ExeReadiness, exe_readiness
from ..build.timing import Ticker, compare_to_estimate, estimate_build, record_sample
from ..config import AppConfig
from ..container import validate_container
from ..container.reader import ContainerReader
from ..detection import discover_all, scan
from ..detection.detector import ADAPTER_LABELS
from ..diagnostics import classify, first_actionable, summarize
from ..diagnostics.doctor import run_checks
from ..errors import EceError, BuildCancelledError
from ..extraction import Extractor
from ..local_store import load_settings, save_settings
from ..logging_config import get_logger, redact
from ..security import sanitize_filename
from .. import templates
from ..toolchain_hints import hint_lines
from ..usability import history_query
from ..usability.guidance import (
    GuidanceContext,
    diagnose_failure,
    explain_extraction,
    explain_validation,
    next_step,
)
from ..usability.health import check_project_health
from ..usability.plan import preview_plan
from ..usability.summary import build_summary
from ..usability.wizard import WIZARD_STEPS, mark_seen, should_show
from ..version import APP_NAME, APP_VERSION, CONTAINER_FORMAT_VERSION
from .state import BUSY_STATES, STATE_UI, UiState
from .theme import (
    COLORS,
    apply_theme,
    configure_root_window,
    configure_text_widget,
    enable_dark_titlebar,
    style_menu,
)
from .widgets import (
    LogConsole,
    StatusBar,
    TaskRunner,
    add_tooltip,
    make_path_row,
    make_tree,
    with_scrollbar,
)

log = get_logger("ui")


def _make_card(parent: tk.Misc, title: str, *, expand: bool = False) -> ttk.Frame:
    """Create a bordered card with an accent section header; return its body.

    The card shares the window colour and is delineated by a hairline border
    (see ``theme``), so every plain child widget blends into it seamlessly.
    """
    card = ttk.Frame(parent, style="Card.TFrame", padding=(14, 11, 14, 13))
    card.pack(fill="both" if expand else "x", expand=expand, padx=16, pady=(14, 0))
    ttk.Label(card, text=title, style="Section.TLabel").pack(anchor="w", pady=(0, 7))
    body = ttk.Frame(card)
    body.pack(fill="both", expand=True)
    return body


@dataclass
class AppContext:
    root: tk.Tk
    runner: TaskRunner
    status: StatusBar
    config: AppConfig


class MainWindow(ttk.Frame):
    """The single, simple compile window."""

    def __init__(self, master: tk.Tk, ctx: AppContext) -> None:
        super().__init__(master, style="TFrame")
        self.pack(fill="both", expand=True)
        self.ctx = ctx

        self._candidates: list = []
        self._adapter_by_label: dict[str, str] = {}
        self._preferred_adapter: str = ""
        self._estimate = None
        self._estimate_note = ""
        self._ticker: Ticker | None = None
        self._cancel = threading.Event()
        self._building = False
        self._state = UiState.IDLE
        self._last_summary = None
        self._last_adapter_holder: list[str] = []
        self._settings = self._load_settings()

        self._build_menu(master)
        master.protocol("WM_DELETE_WINDOW", self._on_close)

        header = ttk.Frame(self, style="Header.TFrame", padding=(18, 13, 14, 11))
        header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(header, text=f"v{APP_VERSION}", style="Chip.TLabel").pack(side="left", padx=(10, 0))
        ttk.Label(
            header, text=f"container format v{CONTAINER_FORMAT_VERSION}", style="Subtitle.TLabel"
        ).pack(side="left", padx=(10, 0))
        # State pill: packed first so it sits at the far right of the header.
        self.state_badge = ttk.Label(header, text="Ready", style="StateIdle.TLabel")
        self.state_badge.pack(side="right", padx=(10, 0))
        ttk.Button(header, text="Toolchains", style="Ghost.TButton", command=self._show_toolchains).pack(
            side="right", padx=(0, 6)
        )
        ttk.Button(header, text="Inspect .ece", style="Ghost.TButton", command=self._show_container).pack(
            side="right", padx=(0, 12)
        )
        # Thin accent strip separating the header from the content.
        ttk.Frame(self, style="AccentStrip.TFrame", height=3).pack(fill="x")

        # --- Project card -----------------------------------------------------
        proj = _make_card(self, "PROJECT")
        proj.columnconfigure(1, weight=1)
        self.project_row = make_path_row(proj, "Project folder", browse_command=self._browse_project)
        self.project_row.frame.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.project_row.var.trace_add("write", lambda *_: self._on_project_changed())

        detect = ttk.Frame(proj)
        detect.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(9, 0))
        detect.columnconfigure(1, weight=1)
        ttk.Label(detect, text="Detected", width=14, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.detected_var = tk.StringVar(value="(not scanned yet)")
        ttk.Label(detect, textvariable=self.detected_var, style="Badge.TLabel").grid(row=0, column=1, sticky="w")
        rescan_btn = ttk.Button(detect, text="Rescan", command=self._detect)
        rescan_btn.grid(row=0, column=2, sticky="e")
        add_tooltip(
            rescan_btn,
            "Scan the folder again (F5). Use it after installing a missing toolchain.",
        )

        # --- Output card ------------------------------------------------------
        out = _make_card(self, "OUTPUT")
        out.columnconfigure(1, weight=1)

        mode_row = ttk.Frame(out)
        mode_row.grid(row=0, column=0, columnspan=3, sticky="ew")
        ttk.Label(mode_row, text="Format", width=14, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.mode_var = tk.StringVar(value="ece")
        ece_radio = ttk.Radiobutton(
            mode_row, text="Package as .ece", variable=self.mode_var, value="ece",
            command=self._on_mode_changed,
        )
        ece_radio.grid(row=0, column=1, sticky="w")
        add_tooltip(
            ece_radio,
            "Build the project and store the result in a versioned .ece container "
            "that this tool can inspect, validate and extract.",
        )
        exe_radio = ttk.Radiobutton(
            mode_row, text="Build .exe", variable=self.mode_var, value="exe",
            command=self._on_mode_changed,
        )
        exe_radio.grid(row=0, column=2, sticky="w", padx=(16, 0))
        add_tooltip(
            exe_radio,
            "Compile a standalone Windows .exe. Python uses PyInstaller; other "
            "languages need their own toolchain.",
        )
        self.exe_hint = ttk.Label(mode_row, text="", style="Muted.TLabel")
        self.exe_hint.grid(row=0, column=3, sticky="w", padx=(12, 0))

        out_row = ttk.Frame(out)
        out_row.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(9, 0))
        out_row.columnconfigure(1, weight=1)
        self.output_label_var = tk.StringVar(value="Output .ece")
        ttk.Label(out_row, textvariable=self.output_label_var, width=14, style="Muted.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.output_var = tk.StringVar()
        ttk.Entry(out_row, textvariable=self.output_var).grid(row=0, column=1, sticky="ew")
        save_btn = ttk.Button(out_row, text="Save as...", command=self._browse_output)
        save_btn.grid(row=0, column=2, sticky="e", padx=(8, 0))
        add_tooltip(
            save_btn,
            "Choose where the .ece or .exe is written. The folder must be writable.",
        )

        # --- Options (adapt to the selected mode) ----------------------------
        opts = ttk.Frame(out)
        opts.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(9, 0))
        self.consent_var = tk.BooleanVar(value=False)
        self.overwrite_var = tk.BooleanVar(value=False)
        self.skip_install_var = tk.BooleanVar(value=True)
        self.package_only_var = tk.BooleanVar(value=False)

        # .ece options
        self.ece_opts = ttk.Frame(opts)
        self.compression_var = tk.StringVar(value="zlib")
        ttk.Label(self.ece_opts, text="Compression").grid(row=0, column=0, sticky="w", padx=(0, 6))
        compression_box = ttk.Combobox(
            self.ece_opts, textvariable=self.compression_var, values=("zlib", "lzma", "store"),
            state="readonly", width=8,
        )
        compression_box.grid(row=0, column=1, sticky="w")
        add_tooltip(
            compression_box,
            "zlib is the balanced default. lzma makes smaller containers but takes "
            "longer. store skips compression and is fastest.",
        )
        skip_install = ttk.Checkbutton(
            self.ece_opts, text="Skip dependency install", variable=self.skip_install_var
        )
        skip_install.grid(row=0, column=2, sticky="w", padx=(12, 0))
        add_tooltip(
            skip_install,
            "Do not run npm install before the build. Useful when dependencies are "
            "already installed; the build may fail if they are not.",
        )

        # .exe options
        self.exe_opts = ttk.Frame(opts)
        self.console_var = tk.BooleanVar(value=False)
        self.exe_mode_var = tk.StringVar(value="onefile")
        onefile_radio = ttk.Radiobutton(
            self.exe_opts, text="Single file", variable=self.exe_mode_var, value="onefile",
        )
        onefile_radio.grid(row=0, column=0, sticky="w")
        add_tooltip(
            onefile_radio,
            "One .exe that unpacks itself at start. Easiest to share; starts slower.",
        )
        onedir_radio = ttk.Radiobutton(
            self.exe_opts, text="Folder", variable=self.exe_mode_var, value="onedir",
        )
        onedir_radio.grid(row=0, column=1, sticky="w", padx=(12, 0))
        add_tooltip(
            onedir_radio,
            "An .exe beside its files in a folder. Starts faster; share the whole folder.",
        )
        console_check = ttk.Checkbutton(
            self.exe_opts, text="Console window", variable=self.console_var
        )
        console_check.grid(row=0, column=2, sticky="w", padx=(12, 0))
        add_tooltip(
            console_check,
            "Keep a console window so printed text and errors stay visible. "
            "Leave it off for a windowed app.",
        )

        # shared options
        consent_check = ttk.Checkbutton(
            opts, text="Allow project scripts", variable=self.consent_var
        )
        consent_check.grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        add_tooltip(
            consent_check,
            "Allow the build to run commands defined by the project (for example an "
            "npm build script) and download dependencies. Only enable it for projects "
            "you trust; Preview plan shows the exact commands.",
        )
        overwrite_check = ttk.Checkbutton(
            opts, text="Overwrite output", variable=self.overwrite_var
        )
        overwrite_check.grid(row=1, column=3, sticky="w")
        add_tooltip(
            overwrite_check,
            "Replace an existing output file without asking again. The previous file "
            "is not backed up.",
        )
        pkg_only = ttk.Checkbutton(
            opts, text="Package only (skip the build)", variable=self.package_only_var,
        )
        pkg_only.grid(row=2, column=0, sticky="w", pady=(6, 0))
        add_tooltip(
            pkg_only,
            "Skip running the build and package what is already on disk. Useful when the "
            "project is already built and you only need an .ece container.",
        )

        # Optimization profile: "none" keeps the previous behaviour exactly.
        from ..optimize.profiles import profile_keys

        self.profile_var = tk.StringVar(value="none")
        ttk.Label(opts, text="Profile", style="Muted.TLabel").grid(
            row=2, column=1, sticky="w", padx=(16, 4), pady=(6, 0)
        )
        self.profile_box = ttk.Combobox(
            opts, textvariable=self.profile_var, values=profile_keys(), state="readonly", width=14,
        )
        self.profile_box.grid(row=2, column=2, sticky="w", pady=(6, 0))
        add_tooltip(
            self.profile_box,
            "Optimization profile for the build.\n"
            "none: the project builds exactly as it asks.\n"
            "release / balanced / performance / size: compiler options are added.\n"
            "debug: fast build with debug information.",
        )

        actions = ttk.Frame(out)
        actions.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(13, 0))
        self.build_btn = ttk.Button(actions, text="Compile", style="Accent.TButton", command=self._build)
        self.build_btn.grid(row=0, column=0, sticky="w")
        add_tooltip(
            self.build_btn,
            "Runs the real build with the toolchain on this PC and packages the result.\n"
            "Nothing runs until you press this (Ctrl+Enter).",
        )
        self.cancel_btn = ttk.Button(actions, text="Cancel", command=self._cancel_build, state="disabled")
        self.cancel_btn.grid(row=0, column=1, sticky="w", padx=8)
        add_tooltip(self.cancel_btn, "Stop the running build (Esc). A cancelled build never leaves a partial output file.")
        # Single transition point: badge + buttons always derive from state.py.
        self._set_state(UiState.IDLE)
        self.open_btn = ttk.Button(actions, text="Open output folder", command=self._open_output)
        self.open_btn.grid(row=0, column=2, sticky="w")
        add_tooltip(self.open_btn, "Open the folder that holds the last output in Explorer.")
        self.plan_btn = ttk.Button(actions, text="Preview plan", command=self._preview_plan)
        self.plan_btn.grid(row=0, column=3, sticky="w", padx=8)
        add_tooltip(self.plan_btn, "Show the exact commands that would run, without running anything.")
        self.next_btn = ttk.Button(actions, text="What do I do now?", style="Ghost.TButton",
                                   command=self._show_next_step)
        self.next_btn.grid(row=0, column=4, sticky="w")
        add_tooltip(self.next_btn, "Get one concrete next action for the current state of the project.")

        # --- Log card ---------------------------------------------------------
        log_card = _make_card(self, "LOG", expand=True)
        self.log = LogConsole(log_card, height=14)
        self.log.pack(fill="both", expand=True)

        ctx.status.pack(fill="x", side="bottom")

        self._on_mode_changed()
        self.log.append("Select a project folder, then press Compile.", "muted")
        self._bind_shortcuts(master)

        # First run: show the short walkthrough (item 1). It can be reopened from
        # Help > Getting started. Shown after the window is mapped, so it can be
        # centred on a real geometry.
        if should_show(self._settings):
            master.after(300, self._show_getting_started)

    # --------------------------------------------------------------- settings
    def _load_settings(self) -> dict:
        try:
            return dict(load_settings().value)
        except Exception:  # pragma: no cover - settings are best-effort
            return {}

    def _save_settings(self) -> None:
        try:
            save_settings(self._settings)
        except Exception:  # pragma: no cover - settings are best-effort
            pass

    # ---------------------------------------------------------------- states
    def _set_state(self, state: UiState, *, when: UiState | None = None) -> bool:
        """The single transition point: badge, buttons and flags derive from STATE_UI.

        ``when`` guards asynchronous transitions: a detection callback that
        arrives after a build already started must not clobber the new state.
        Returns whether the transition was applied.
        """
        if when is not None and self._state is not when:
            return False
        spec = STATE_UI[state]
        self._state = state
        self._building = state in BUSY_STATES
        self.state_badge.configure(text=spec.badge_text, style=spec.badge_style)
        self.build_btn.configure(state=spec.build_state)
        self.cancel_btn.configure(state=spec.cancel_state)
        # The plan preview is only meaningful when nothing is running.
        if hasattr(self, "plan_btn"):
            self.plan_btn.configure(state="disabled" if self._building else "normal")
        return True

    # -------------------------------------------------------------- shortcuts
    def _bind_shortcuts(self, master: tk.Tk) -> None:
        """Global accelerators (mirrored in the menu labels; see _build_menu)."""
        master.bind_all("<F5>", self._key_rescan)
        master.bind_all("<Control-Return>", self._key_compile)
        master.bind_all("<Escape>", self._key_cancel)
        master.bind_all("<Control-Shift-C>", self._key_copy_log)
        master.bind_all("<Control-O>", self._key_open_project)
        master.bind_all("<F1>", self._key_shortcuts)
        master.bind_all("<Control-p>", self._key_plan)
        master.bind_all("<Control-d>", self._key_doctor)
        master.bind_all("<Control-h>", self._key_history)

    def _key_rescan(self, _event=None):
        if not self._building:
            self._detect()
        return "break"

    def _key_compile(self, _event=None):
        if not self._building:
            self._build()
        return "break"

    def _key_cancel(self, _event=None):
        # Only claim Escape while a task is actually running, so Escape can still
        # close a dialog the user opened.
        if self._state in BUSY_STATES:
            self._cancel_build()
            return "break"
        return None

    def _key_copy_log(self, _event=None):
        self.log.copy_all()
        return "break"

    def _key_open_project(self, _event=None):
        self._browse_project()
        return "break"

    def _key_shortcuts(self, _event=None):
        ShortcutsDialog(self.ctx)
        return "break"

    def _key_plan(self, _event=None):
        if not self._building:
            self._preview_plan()
        return "break"

    def _key_doctor(self, _event=None):
        DoctorDialog(self.ctx)
        return "break"

    def _key_history(self, _event=None):
        HistoryDialog(self.ctx)
        return "break"

    # ------------------------------------------------------------ window close
    def _on_close(self) -> None:
        """Warn before closing while a build is running (item: safe exit)."""
        if self._building:
            if not messagebox.askokcancel(
                APP_NAME,
                "A build is running.\n\nClosing now cancels it and the output file is not written.\n\nClose anyway?",
            ):
                return
            self._cancel.set()
        self._save_settings()
        self.ctx.root.destroy()

    # ------------------------------------------------------------------ menu
    def _build_menu(self, master: tk.Tk) -> None:
        menubar = tk.Menu(master)

        filem = tk.Menu(menubar, tearoff=0)
        filem.add_command(label="Open project folder...", accelerator="Ctrl+O", command=self._browse_project)
        filem.add_command(label="New project from template...", command=self._show_template)
        filem.add_command(label="Rescan detection", accelerator="F5", command=self._detect)
        filem.add_separator()
        filem.add_command(label="Open output folder", command=self._open_output)
        filem.add_command(label="Open work folder", command=lambda: _open_folder(self.ctx.config.work_dir))
        filem.add_separator()
        filem.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=filem)

        buildm = tk.Menu(menubar, tearoff=0)
        buildm.add_command(label="Choose project to build...", command=self._choose_subproject)
        buildm.add_command(label="Preview plan", accelerator="Ctrl+P", command=self._preview_plan)
        buildm.add_command(label="Compile", accelerator="Ctrl+Return", command=self._build)
        buildm.add_command(label="Cancel build", accelerator="Esc", command=self._cancel_build)
        buildm.add_separator()
        buildm.add_command(label="Optimize and compare...", command=self._show_optimize)
        menubar.add_cascade(label="Build", menu=buildm)

        toolsm = tk.Menu(menubar, tearoff=0)
        toolsm.add_command(label="Inspect / Extract / Compare .ece...", command=self._show_container)
        toolsm.add_command(label="Create distributable package...", command=self._show_package)
        toolsm.add_command(label="Toolchains...", command=self._show_toolchains)
        toolsm.add_command(label="Build history...", accelerator="Ctrl+H", command=self._show_history)
        toolsm.add_separator()
        toolsm.add_command(label="Run environment check (doctor)...", accelerator="Ctrl+D", command=self._show_doctor)
        menubar.add_cascade(label="Tools", menu=toolsm)

        helpm = tk.Menu(menubar, tearoff=0)
        helpm.add_command(label="Getting started", command=self._show_getting_started)
        helpm.add_command(label="What do I do now?", command=self._show_next_step)
        helpm.add_command(label="Keyboard shortcuts", accelerator="F1", command=self._show_shortcuts)
        helpm.add_separator()
        helpm.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=helpm)

        for menu in (menubar, filem, buildm, toolsm, helpm):
            style_menu(menu)
        master.config(menu=menubar)

    # -------------------------------------------------------------- project
    def _browse_project(self) -> None:
        path = filedialog.askdirectory(title="Select project folder")
        if path:
            self.project_row.var.set(path)
            self._detect()

    def _on_project_changed(self) -> None:
        path = self.project_row.var.get().strip()
        if path and not self.output_var.get().strip():
            name = sanitize_filename(Path(path).name or "project")
            ext = ".exe" if self.mode_var.get() == "exe" else ".ece"
            self.output_var.set(str(self.ctx.config.output_dir / f"{name}{ext}"))

    def _on_mode_changed(self) -> None:
        is_exe = self.mode_var.get() == "exe"
        self.output_label_var.set("Output .exe" if is_exe else "Output .ece")
        self.exe_hint.configure(
            text="detects the language" if is_exe else ""
        )
        # Swap the option group and refresh the default extension.
        self.ece_opts.grid_forget()
        self.exe_opts.grid_forget()
        (self.exe_opts if is_exe else self.ece_opts).grid(row=0, column=0, sticky="w")
        # The optimization profile only applies to the .ece build.
        if hasattr(self, "profile_box"):
            self.profile_box.configure(state="disabled" if is_exe else "readonly")
        # Rewrite a default-looking output path when it ends with the old ext.
        path = self.project_row.var.get().strip()
        out = self.output_var.get().strip()
        if path and out:
            old_ext = ".ece" if is_exe else ".exe"
            new_ext = ".exe" if is_exe else ".ece"
            if out.lower().endswith(old_ext):
                self.output_var.set(out[: -len(old_ext)] + new_ext)

    def _browse_output(self) -> None:
        if self.mode_var.get() == "exe":
            path = filedialog.asksaveasfilename(
                title="Save executable as", defaultextension=".exe",
                filetypes=[("Windows executable", "*.exe")],
            )
        else:
            path = filedialog.asksaveasfilename(
                title="Save container as", defaultextension=".ece",
                filetypes=[("ECE container", "*.ece")],
            )
        if path:
            self.output_var.set(path)

    def _choose_subproject(self) -> None:
        """Let the user pick which detected project a monorepo build uses."""
        if not self._candidates:
            messagebox.showinfo(
                APP_NAME,
                "Scan a project first (Rescan). This then lists every project found "
                "in the folder, with the reason it was detected.",
            )
            return
        base = Path(self.project_row.var.get().strip() or ".")
        dialog = CandidateDialog(self.ctx, base, self._candidates,
                                 current=self._preferred_adapter)
        self.wait_window(dialog)
        candidate = dialog.selection
        if candidate is None:
            return
        label = ADAPTER_LABELS.get(candidate.adapter_id, candidate.adapter_id)
        root = (candidate.root or "").strip()
        if root:
            target = (base / root).resolve()
            if target != base.resolve():
                if messagebox.askyesno(
                    APP_NAME,
                    f"Build the {label} project at:\n{target}\n\nSwitch the project folder to it?",
                ):
                    self.project_row.var.set(str(target))
                    self._preferred_adapter = candidate.adapter_id
                    self._detect()
                return
        self._preferred_adapter = candidate.adapter_id
        self.log.append(f"Selected project type: {label} ({candidate.adapter_id})", "accent")
        self.ctx.status.set(f"Compile will build the {label} project.")

    def _consent_scripts(self, project: Path) -> str:
        """The exact script steps the build would run, for the consent prompt (item 18)."""
        try:
            preview = preview_plan(
                project,
                prefer_adapter=self._preferred_adapter or None,
                mode=self.mode_var.get(),
            )
        except Exception:  # pragma: no cover - the prompt can always fall back
            return ""
        lines = [
            f"  - {step.title}: {step.command_line}"
            for step in preview.script_steps[:8]
        ]
        return "\n".join(lines)

    def _detect(self) -> None:
        path = self.project_row.var.get().strip()
        if not path or not Path(path).exists():
            messagebox.showwarning(APP_NAME, "Choose an existing project folder first.")
            return
        if self._state in BUSY_STATES:
            self.ctx.status.set("A task is running — rescan when it finishes (F5).")
            return
        if self._state is UiState.DETECTING:
            return  # a scan is already in flight
        self._set_state(UiState.DETECTING)
        self.ctx.status.set("Scanning project...")
        self.ctx.status.set_indeterminate()  # scan length is unknown: animate, don't fake 0%
        self.log.append(f"Scanning {path} ...", "accent")

        def work():
            result = scan(Path(path))
            # Probing PyInstaller/toolchains spawns processes: run it here on
            # the worker thread so the window never freezes during Detect.
            readiness: dict[str, ExeReadiness] = {}
            for cand in result.candidates:
                readiness.setdefault(cand.adapter_id, exe_readiness(cand.adapter_id))
            health = check_project_health(
                Path(path), candidate_root=(result.best.root if result.best else "")
            )
            return result, readiness, health

        def done(payload):
            result, readiness, health = payload
            still_scanning = self._state is UiState.DETECTING
            self._candidates = result.candidates
            self._adapter_by_label = {}
            labels = []
            for c in result.candidates:
                label = ADAPTER_LABELS.get(c.adapter_id, c.adapter_id)
                suffix = f" @ {c.root}" if c.root else ""
                text = f"{label}{suffix} ({int(c.confidence * 100)}%)"
                self._adapter_by_label[text] = c.adapter_id
                labels.append(text)
            if labels:
                self.detected_var.set("  ·  ".join(labels))
                self.log.append(result.summary(), "ok")
                best = result.best
                if best:
                    if best.package_manager:
                        self.log.append(f"Package manager: {best.package_manager}", "info")
                    if best.evidence:
                        self.log.append(f"Evidence: {', '.join(best.evidence[:6])}", "muted")
                    # Why this adapter, in plain words (item: reason for adapter).
                    self.log.append(
                        f"Why this project type: {_why_adapter(best)}", "muted"
                    )
                    ready = readiness.get(best.adapter_id)
                    if ready is not None:
                        # Honest answer to "can this become an .exe here?".
                        self.log.append(
                            f"Export to .exe: {ready.summary}",
                            "ok" if ready.possible else "warn",
                        )
                        for action in ready.actions:
                            self.log.append(f"  - {action}", "muted")
                # Monorepo / ambiguous: list the alternatives explicitly.
                if result.ambiguous and len(result.candidates) > 1:
                    self.log.append(
                        "Several projects were found. Compile builds the best match; "
                        "use Build > Choose project to build... to pick another.", "warn",
                    )
                    for c in result.candidates[1:6]:
                        where = c.root or "."
                        self.log.append(f"  other: {ADAPTER_LABELS.get(c.adapter_id, c.adapter_id)} at {where}", "muted")
                if self._preferred_adapter and not any(
                    c.adapter_id == self._preferred_adapter for c in result.candidates
                ):
                    self._preferred_adapter = ""
            else:
                self.detected_var.set("no supported project found")
                self.log.append(result.summary(), "warn")
            for note in result.notes:
                self.log.append(f"note: {note}", "muted")
            # Project health, honestly reported (item 36).
            for issue in health.issues:
                self.log.append(f"{issue.severity}: {issue.title} - {issue.detail}", "warn" if issue.severity != "info" else "muted")
            if health.issues:
                self.log.append(f"Health: {health.summary}", "warn")
            else:
                self.log.append("Health: no problems found in this project.", "ok")
            # Toolchain card: what is missing and the exact command to install it.
            best = result.best
            if best is not None:
                from ..toolchain_hints import ADAPTER_TO_TOOL

                tool_id = ADAPTER_TO_TOOL.get(best.adapter_id, "")
                if tool_id:
                    from ..detection import get_tool

                    info = get_tool(tool_id)
                    if info is not None and not info.found:
                        self.log.append(f"Missing toolchain: {info.name} (needed to build {best.adapter_id}).", "err")
                        for line in hint_lines(tool_id):
                            self.log.append(f"  {line}", "warn")
                        self.log.append("Install it, then press Rescan (F5).", "warn")
            if still_scanning:
                self.ctx.status.set("Ready.")
                self.ctx.status.reset()
            self._set_state(UiState.IDLE, when=UiState.DETECTING)

        self.ctx.runner.run(work, done, lambda e: self._fail("Detection failed", e, when=UiState.DETECTING))

    # ----------------------------------------------------------- build flow
    def _build(self) -> None:
        if self._building:
            return
        project = self.project_row.var.get().strip()
        output = self.output_var.get().strip()
        if not project or not Path(project).exists():
            messagebox.showwarning(APP_NAME, "Choose an existing project folder first.")
            return
        if not output:
            messagebox.showwarning(APP_NAME, "Choose an output file.")
            return
        # Consent is a real gate: warn, and name what will run (item 18/consent).
        if self.consent_var.get():
            script_lines = self._consent_scripts(Path(project))
            message = (
                "Allow project scripts?\n\n"
                "This build may run commands defined by the project and download dependencies.\n\n"
                "The plan shows these script steps:\n"
                + (script_lines or "  (none listed; press Preview plan to inspect the exact commands)")
                + "\n\nOnly continue if you trust this project."
            )
            if not messagebox.askokcancel(APP_NAME, message):
                return
        # Existing output: confirm before overwriting (destructive action).
        target = Path(output)
        if target.exists() and not self.overwrite_var.get():
            if not messagebox.askokcancel(
                APP_NAME, f"{target.name} already exists.\n\nOverwrite it?"
            ):
                return
            self.overwrite_var.set(True)

        self._cancel = threading.Event()
        self._last_adapter_holder.clear()
        self._set_state(UiState.PLANNING)
        self.log.clear()
        self.log.append(f"Building {project}", "accent")
        self.ctx.status.set("Planning build...")
        self._estimate_note = ""
        self._ticker = Ticker()

        # Item 34: estimate from this machine's own successful builds.
        guess = self._preferred_adapter
        if not guess and len(self._candidates) == 1:
            guess = self._candidates[0].adapter_id
        self._estimate = None
        if guess:
            timing_mode = self.mode_var.get()
            if timing_mode == "ece" and self.package_only_var.get():
                timing_mode = "package"
            self._estimate = estimate_build(
                self.ctx.config.work_dir, guess, timing_mode
            )
            self.log.append(self._estimate.message, "muted" if self._estimate.available else "warn")

        def progress(message: str, done: int, total: int) -> None:
            fraction = (done / total) if total else None
            timing = self._ticker.summary(fraction) if self._ticker is not None else ""
            suffix = f"  ({timing})" if timing else ""

            def apply():
                self.ctx.status.set(message + suffix)
                self.ctx.status.set_progress(done, total)
                self.log.append(f"[{done:>3}/{total}] {message}{suffix}", "muted")
                # Real phase changes update the badge honestly.
                if self._state in BUSY_STATES:
                    if "Packaging" in message or "Collecting" in message:
                        self._set_state(UiState.PACKAGING)
                    elif "Build" in message:
                        self._set_state(UiState.COMPILING)
            self.ctx.runner.post(apply)

        if self.mode_var.get() == "exe":
            self._build_exe(Path(project), Path(output), progress)
        else:
            self._build_ece(Path(project), Path(output), progress)

    def _build_ece(self, project: Path, output: Path, progress) -> None:
        prefer = self._preferred_adapter or None
        if prefer is None and len(self._candidates) == 1:
            prefer = self._candidates[0].adapter_id
        overrides = {"skip_npm_install": True} if self.skip_install_var.get() else {}
        orchestrator = BuildOrchestrator(config=self.ctx.config)
        optimization = self._optimization()
        if optimization is not None:
            for line in optimization.text().splitlines():
                self.log.append(f"  {line}", "muted")

        def work():
            return orchestrator.build_and_package(
                project,
                output,
                config_overrides=overrides,
                consent_granted=self.consent_var.get(),
                overwrite=self.overwrite_var.get(),
                compression=self.compression_var.get(),
                prefer_adapter=prefer,
                package_only=self.package_only_var.get(),
                selected_adapter=self._last_adapter_holder,
                progress=progress,
                cancel=self._cancel,
                optimization=optimization,
            )

        self.ctx.runner.run(work, self._build_done, lambda e: self._fail("Build failed", e))

    def _optimization(self):
        """The optimization plan for the selected profile, or None."""
        from ..optimize.plan import OptimizationRequest, build_optimization_plan

        key = self.profile_var.get().strip().lower()
        if not key or key == "none":
            return None
        prefer = self._preferred_adapter or None
        if prefer is None and len(self._candidates) == 1:
            prefer = self._candidates[0].adapter_id
        plan = build_optimization_plan(prefer or "", OptimizationRequest.from_args(key, []))
        if plan.unsupported_reason:
            messagebox.showwarning(
                APP_NAME,
                f"This project cannot be optimized with the '{key}' profile.\n\n"
                f"{plan.unsupported_reason}\n\n"
                "Choose 'none' or another profile.",
            )
            return None
        return plan

    def _build_exe(self, project: Path, output: Path, progress) -> None:
        from ..build.exe_exporter import compile_exe

        def work():
            return compile_exe(
                project,
                output=output,
                onefile=self.exe_mode_var.get() == "onefile",
                console=self.console_var.get(),
                progress=progress,
                cancel=self._cancel,
                config=self.ctx.config,
                consent_granted=self.consent_var.get(),
            )

        self.ctx.runner.run(work, self._exe_done, lambda e: self._fail("Executable export failed", e))

    @property
    def onefile_var(self):
        """Backward-compatible view of the .exe layout radio (True = onefile)."""
        return self.exe_mode_var.get() == "onefile"

    def _exe_done(self, result) -> None:
        if not result.ok:
            self._finish_build(UiState.ERROR)
            record_sample(
                self.ctx.config.work_dir,
                adapter_id=result.adapter_id or result.language or "",
                mode="exe", duration_seconds=result.duration_seconds, ok=False,
            )
            # Same three-part shape as the CLI: what happened / why / what to
            # do. The user always leaves with a next step, never a dead end.
            self.log.append(f"Executable export failed: {result.message}", "err")
            if result.errors:
                self.log.append("Why:", "err")
                for err in result.errors[:8]:
                    self.log.append(f"  - {err}", "err")
            self.log.append("What to do:", "warn")
            for act in result.actions[:6]:
                self.log.append(f"  - {act}", "warn")
            if result.log_path:
                self.log.append(f"Log: {result.log_path}", "muted")
            self.ctx.status.set("Executable export failed.")
            # Failed exports belong in the history too; never silently skipped.
            build_history.append(
                build_history.make_record(
                    Path(self.project_row.var.get()),
                    ok=False,
                    exit_code=result.exit_code or -1,
                    duration_seconds=result.duration_seconds,
                    mode="exe",
                    message=f"Executable export failed: {result.message}",
                ),
                base=self.ctx.config.work_dir,
            )
            what = f"Executable export failed: {result.message}"
            todo = "\n".join(f"  - {a}" for a in result.actions[:6]) or "  - Read the log for details."
            self._show_error_dialog("Executable export failed", f"What happened: {what}\nWhat to do:\n{todo}",
                                    log_path=result.log_path, retry=self._build)
            return
        self._finish_build(UiState.SUCCESS)
        self.log.append(f"Executable created in {result.duration_seconds:.1f}s", "ok")
        self.log.append(f"  .exe: {result.exe_path}", "ok")
        size = result.exe_path.stat().st_size
        self.log.append(f"  size: {size:,} bytes", "ok")
        adapter_id = result.adapter_id or result.language or ""
        self._estimate_note = (
            compare_to_estimate(self._estimate, result.duration_seconds)
            if self._estimate is not None else ""
        )
        if self._estimate_note:
            self.log.append(self._estimate_note, "muted")
        record_sample(
            self.ctx.config.work_dir, adapter_id=adapter_id, mode="exe",
            duration_seconds=result.duration_seconds, ok=True, file_count=1,
            input_bytes=size,
        )
        for note in result.notes:
            self.log.append(f"  {note}", "muted")
        self.ctx.status.set("Done.")
        digest = self._write_sidecar(result.exe_path)
        notes = list(result.notes) + ([self._estimate_note] if self._estimate_note else [])
        summary = build_summary(
            ok=True,
            mode="exe",
            project=Path(self.project_row.var.get()),
            adapter_id=adapter_id,
            label=result.language or "",
            output_path=result.exe_path,
            output_size=size,
            artifact_count=1,
            duration_seconds=result.duration_seconds,
            estimated_seconds=(
                self._estimate.average
                if self._estimate is not None and self._estimate.available else None
            ),
            estimate_note=self._estimate_note,
            sha256=digest,
            verified="ok" if digest else "",
            notes=notes,
        )
        self._last_summary = summary
        build_history.append(
            build_history.make_record(
                Path(self.project_row.var.get()),
                ok=True,
                exit_code=result.exit_code,
                duration_seconds=result.duration_seconds,
                adapter_id=adapter_id,
                container_path=result.exe_path,
                mode="exe",
                artifact_count=1,
                sha256=digest,
                message="Executable export succeeded",
            ),
            base=self.ctx.config.work_dir,
        )
        SummaryDialog(self.ctx, summary)

    def _build_done(self, result) -> None:
        build_result, package = result
        if not build_result.ok:
            self._finish_build(UiState.ERROR)
            self.log.append("Build failed.", "err")
            combined = "\n".join(build_result.errors)
            diags = classify(combined)
            if diags:
                self.log.append(f"Diagnosis: {summarize(diags)}", "warn")
                best = first_actionable(diags)
                if best:
                    self.log.append(f"  {best.message}: {best.line}", "warn")
                    if best.hint:
                        self.log.append(f"  hint: {best.hint}", "muted")
            report = diagnose_failure(combined, what="the build failed")
            for line in report.lines():
                self.log.append(line, "warn")
            if build_result.log_path:
                self.log.append(f"Log: {build_result.log_path}", "muted")
            self.ctx.status.set("Build failed.")
            self._record(False, build_result, None)
            self._show_error_dialog("Build failed", "\n".join(report.lines()), log_path=build_result.log_path,
                                    retry=self._build, repair=self._attempt_repair)
            return

        if not package.ok:
            self._finish_build(UiState.ERROR)
            self.log.append(f"Packaging failed: {package.message}", "err")
            self.ctx.status.set("Packaging failed.")
            self._record(False, build_result, None, message=f"Packaging failed: {package.message}")
            self._show_error_dialog(
                "Packaging failed",
                f"What happened: packaging failed ({package.message})\n"
                "What to do: check free disk space and that the output folder is writable.",
                retry=self._build,
            )
            return

        self.log.append(f"Build succeeded in {build_result.duration_seconds:.1f}s", "ok")
        self.log.append(f"Package: {package.container_path}", "ok")
        self._estimate_note = (
            compare_to_estimate(self._estimate, build_result.duration_seconds)
            if self._estimate is not None else ""
        )
        if self._estimate_note:
            self.log.append(self._estimate_note, "muted")
        report = validate_container(package.container_path, deep=True)
        if report.ok:
            self.log.append(
                f"Validation: OK ({report.entry_count} files, {report.total_uncompressed} bytes)", "ok"
            )
            for line in explain_validation(report)[:3]:
                self.log.append(f"  {line}", "muted")
            self.ctx.status.set("Done.")
            self._finish_build(UiState.SUCCESS)
            self._write_sidecar(package.container_path)
            self._record(True, build_result, package.container_path, sha256=package.sha256,
                         artifact_count=package.file_count)
            self._present_summary(self._make_summary(build_result, package, "ece", ok=True))
        else:
            self.log.append("Validation: FAILED", "err")
            for issue in report.errors:
                self.log.append(f"  {issue}", "err")
            self.ctx.status.set("Validation failed.")
            # The container exists but did not validate: record the failure
            # honestly (ok=False) instead of claiming success.
            self._finish_build(UiState.ERROR)
            self._record(False, build_result, package.container_path, message="Validation failed")
            self._show_error_dialog("Validation failed", "\n".join(str(i) for i in report.errors[:6]))

    def _make_summary(self, build_result, package, mode: str, *, ok: bool):
        adapter_id = self._last_adapter_holder[0] if self._last_adapter_holder else ""
        label = ADAPTER_LABELS.get(adapter_id, adapter_id)
        return build_summary(
            ok=ok,
            mode=mode,
            project=Path(self.project_row.var.get()),
            adapter_id=adapter_id,
            label=label,
            output_path=package.container_path,
            output_size=package.size,
            artifact_count=package.file_count,
            duration_seconds=build_result.duration_seconds,
            estimated_seconds=(
                self._estimate.average
                if self._estimate is not None and self._estimate.available else None
            ),
            estimate_note=self._estimate_note,
            sha256=package.sha256,
            verified="ok" if package.sha256 else "",
            notes=list(getattr(build_result, "notes", []) or []),
            log_path=getattr(build_result, "log_path", "") or "",
        )

    def _present_summary(self, summary) -> None:
        self._last_summary = summary
        SummaryDialog(self.ctx, summary)

    def _record(self, ok: bool, build_result, container_path, *, message: str | None = None,
                mode: str = "ece", sha256: str = "", artifact_count: int = 0) -> None:
        adapter_id = self._last_adapter_holder[0] if self._last_adapter_holder else ""
        timing_mode = "package" if (mode == "ece" and self.package_only_var.get()) else mode
        record_sample(
            self.ctx.config.work_dir,
            adapter_id=adapter_id,
            mode=timing_mode,
            duration_seconds=build_result.duration_seconds,
            ok=ok,
            file_count=artifact_count,
        )
        build_history.append(
            build_history.make_record(
                Path(self.project_row.var.get()),
                ok=ok,
                exit_code=build_result.exit_code,
                duration_seconds=build_result.duration_seconds,
                adapter_id=adapter_id,
                mode=mode,
                container_path=container_path,
                artifact_count=artifact_count,
                sha256=sha256,
                message=message or ("Build and package succeeded" if ok else "Build failed"),
                profile=self.profile_var.get() if hasattr(self, "profile_var") else "",
            ),
            base=self.ctx.config.work_dir,
        )

    def _cancel_build(self) -> None:
        if self._state not in BUSY_STATES:
            return
        self._cancel.set()
        self.log.append("Cancellation requested...", "warn")

    def _finish_build(self, state: UiState = UiState.IDLE) -> None:
        """End a build: transition to *state* and return the progress bar to rest."""
        self._set_state(state)
        self.ctx.status.reset()

    def _fail(self, title: str, exc: BaseException, *, when: UiState | None = None) -> None:
        """Land an exception on ERROR (or CANCELLED for a user cancellation).

        The log line is always written; the state/status update only applies
        when ``when`` still matches, so a late failure cannot repaint a window
        that has moved on to another task.
        """
        cancelled = isinstance(exc, BuildCancelledError)
        state = UiState.CANCELLED if cancelled else UiState.ERROR
        applied = self._set_state(state, when=when)
        if cancelled:
            self.log.append("Cancelled by the user.", "warn")
        else:
            self.log.append(f"{title}: {exc}", "err")
        if applied:
            self.ctx.status.set("Cancelled." if cancelled else title)
            self.ctx.status.reset()

    def _open_output(self) -> None:
        value = self.output_var.get().strip()
        target = Path(value).parent if value else self.ctx.config.output_dir
        _open_folder(target)

    # -------------------------------------------------------------- helpers
    def _write_sidecar(self, path: Path) -> str:
        """Write a .sha256 sidecar for a produced file (item 39); return the digest."""
        from ..container.integrity import sha256_file, write_sidecar

        try:
            digest = sha256_file(path)
            write_sidecar(path, digest=digest)
            self.log.append(f"  .sha256: {path.name}.sha256", "muted")
            return digest
        except OSError as exc:  # pragma: no cover - disk issue
            self.log.append(f"  (could not write the .sha256 sidecar: {exc})", "warn")
            return ""

    def _show_error_dialog(self, title: str, text: str, *, log_path: Path | str | None = None,
                           retry=None, repair=None) -> None:
        """Show a failure with the three-part shape and a Copy button (items 3/10/32)."""
        ErrorDialog(self.ctx, title, text, log_path=log_path, retry=retry, repair=repair)

    def _attempt_repair(self) -> None:
        """Analyse the last build failure and repair what is safe to repair."""
        project = self.project_row.var.get().strip()
        if not project or not Path(project).exists():
            messagebox.showwarning(APP_NAME, "Choose an existing project folder first.")
            return

        from ..build.compile_service import make_build_runner
        from ..repair import RepairEngine

        runner = make_build_runner(
            Path(project),
            config=self.ctx.config,
            consent_granted=self.consent_var.get(),
            prefer_adapter=self._preferred_adapter or None,
        )
        engine = RepairEngine(config=self.ctx.config)
        self.ctx.status.set("Analysing the build failure...")

        def work():
            return engine.repair(Path(project), runner, apply=True)

        def done(outcome) -> None:
            self.ctx.status.set("The repair run finished.")
            self.log.append(outcome.message, "ok" if outcome.final_ok else "warn")
            for attempt in outcome.attempts:
                self.log.append(
                    f"  {attempt.title} -> {attempt.verification}", "muted"
                )
            RepairDialog(self.ctx, outcome, on_retry=self._build)

        def fail(exc: BaseException) -> None:
            self.ctx.status.set("The repair run failed.")
            messagebox.showerror(APP_NAME, f"The repair could not run:\n{exc}")

        self.ctx.runner.run(work, done, fail)

    def _preview_plan(self) -> None:
        project = self.project_row.var.get().strip()
        if not project or not Path(project).exists():
            messagebox.showwarning(APP_NAME, "Choose an existing project folder first.")
            return
        mode = self.mode_var.get()
        prefer = self._candidates[0].adapter_id if len(self._candidates) == 1 else None
        try:
            preview = preview_plan(Path(project), prefer_adapter=prefer, mode=mode)
        except EceError as exc:
            messagebox.showerror(APP_NAME, f"Could not build a plan:\n{exc}")
            return
        PlanDialog(self.ctx, preview)

    def _show_next_step(self) -> None:
        path = self.project_row.var.get().strip()
        exists = bool(path) and Path(path).exists()
        from ..toolchain_hints import ADAPTER_TO_TOOL
        from ..detection import get_tool

        missing: list[str] = []
        for cand in self._candidates:
            tool_id = ADAPTER_TO_TOOL.get(cand.adapter_id, "")
            if tool_id:
                info = get_tool(tool_id)
                if info is not None and not info.found and tool_id not in missing:
                    missing.append(tool_id)
        step = next_step(
            GuidanceContext(
                state=self._state.value,
                project_selected=bool(path),
                project_exists=exists,
                detected=bool(self._candidates),
                candidates=len(self._candidates),
                ambiguous=len(self._candidates) > 1,
                missing_toolchains=missing,
                mode=self.mode_var.get(),
                build_attempted=self._state in (UiState.SUCCESS, UiState.ERROR),
                build_failed=self._state is UiState.ERROR,
            )
        )
        lines = [step.title, "", step.detail]
        if step.commands:
            lines.append("")
            lines.extend(step.commands)
        messagebox.showinfo(f"{APP_NAME} - What do I do now?", "\n".join(lines))

    # -------------------------------------------------------------- dialogs
    def _show_toolchains(self) -> None:
        ToolchainDialog(self.ctx)

    def _show_history(self) -> None:
        HistoryDialog(self.ctx)

    def _show_container(self) -> None:
        ContainerDialog(self.ctx)

    def _show_about(self) -> None:
        AboutDialog(self.ctx)

    def _show_getting_started(self) -> None:
        self._settings = mark_seen(self._settings)
        self._save_settings()
        GettingStartedDialog(self.ctx)

    def _show_shortcuts(self) -> None:
        ShortcutsDialog(self.ctx)

    def _show_doctor(self) -> None:
        DoctorDialog(self.ctx)

    def _show_template(self) -> None:
        TemplateDialog(self.ctx, on_created=self._on_template_created)

    def _show_package(self) -> None:
        """Open the distributable package dialog for the current project."""
        project = self.project_row.var.get().strip()
        if not project or not Path(project).exists():
            messagebox.showwarning(APP_NAME, "Choose an existing project folder first.")
            return
        PackageDialog(
            self.ctx,
            Path(project),
            output_dir=self.ctx.config.output_dir,
            prefer_adapter=self._preferred_adapter or None,
        )

    def _show_optimize(self) -> None:
        """Open the optimization comparison dialog for the current project."""
        project = self.project_row.var.get().strip()
        if not project or not Path(project).exists():
            messagebox.showwarning(APP_NAME, "Choose an existing project folder first.")
            return
        OptimizeDialog(
            self.ctx,
            Path(project),
            prefer_adapter=self._preferred_adapter or None,
        )

    def _on_template_created(self, path: Path) -> None:
        self.project_row.var.set(str(path))
        self._detect()


class _Dialog(tk.Toplevel):
    """A simple modal-ish dialog with a title bar."""

    def __init__(self, ctx: AppContext, title: str, *, width: int = 720, height: int = 420) -> None:
        super().__init__(ctx.root)
        self.ctx = ctx
        self.title(f"{APP_NAME} — {title}")
        self.geometry(f"{width}x{height}")
        self.configure(bg=COLORS["bg"])
        enable_dark_titlebar(self)
        self.transient(ctx.root)
        header = ttk.Frame(self, style="Header.TFrame", padding=(14, 9, 10, 9))
        header.pack(fill="x")
        ttk.Label(header, text=title, style="Dialog.TLabel").pack(side="left")
        ttk.Button(header, text="Close", style="Ghost.TButton", command=self.destroy).pack(side="right")


class ToolchainDialog(_Dialog):
    """Every known toolchain, with a copyable install command for the missing ones."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, "Toolchains", width=900, height=560)
        self._tools: dict[str, object] = {}
        self._versions: dict[str, object] = {}

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        self.summary_var = tk.StringVar(value="Discovering toolchains...")
        ttk.Label(body, textvariable=self.summary_var).grid(row=0, column=0, sticky="w", pady=(0, 8))

        tree = make_tree(
            body,
            [("name", "Toolchain", 180), ("status", "Status", 80),
             ("version", "Version", 160), ("minimum", "Minimum", 80),
             ("path", "Path", 340)],
            height=14,
        )
        tree.grid(row=1, column=0, sticky="nsew")
        with_scrollbar(body, tree).grid(row=1, column=1, sticky="ns")
        tree.tag_configure("ok", foreground=COLORS["ok"])
        tree.tag_configure("warn", foreground=COLORS["warn"])
        tree.tag_configure("miss", foreground=COLORS["muted"])
        self.tree = tree

        detail = ttk.Frame(self, padding=(12, 6))
        detail.pack(fill="x")
        ttk.Label(
            detail, text="Install command for the selected tool (copy and run it yourself):",
            style="Muted.TLabel",
        ).pack(anchor="w")
        self.hint_box = _readonly_text(detail, "", height=5)
        self.hint_box.pack(fill="x", pady=(4, 0))
        tree.bind("<<TreeviewSelect>>", lambda _e: self._show_hint())

        row = ttk.Frame(self, padding=(12, 6, 12, 12))
        row.pack(fill="x")
        ttk.Button(row, text="Copy install command", command=self._copy_hint).pack(side="left")
        ttk.Button(row, text="Rescan", command=lambda: self._scan(force=True)).pack(side="left", padx=8)
        ttk.Button(row, text="Close", style="Ghost.TButton", command=self.destroy).pack(side="right")

        self._scan()

    def _scan(self, *, force: bool = False) -> None:
        self.tree.delete(*self.tree.get_children())
        self.summary_var.set("Discovering toolchains...")
        self.ctx.status.set("Discovering toolchains...")

        def work():
            from ..toolchain_versions import toolchain_versions

            if force:
                # The probe results are cached; a rescan must really probe again.
                from ..detection import refresh

                tools = refresh()
            else:
                tools = discover_all()
            return tools, {row.tool_id: row for row in toolchain_versions()}

        def done(payload) -> None:
            tools, versions = payload
            self._tools = dict(tools)
            self._versions = dict(versions)
            found = sum(1 for t in tools.values() if t.found)
            missing = len(tools) - found
            too_old = sum(
                1 for tool_id, row in versions.items()
                if row.found and not row.satisfies
            )
            summary = f"{found} toolchain(s) found, {missing} missing."
            if too_old:
                summary += f" {too_old} older than the minimum version."
            summary += " Select a tool for details and its install command."
            self.summary_var.set(summary)
            for tool_id, tool in tools.items():
                version_row = versions.get(tool_id)
                if tool.found and version_row is not None and not version_row.satisfies:
                    tag, status = "warn", "too old"
                elif tool.found:
                    tag, status = "ok", "found"
                else:
                    tag, status = "miss", "missing"
                version = tool.version or (tool.install_hint if not tool.found else "")
                minimum = version_row.minimum_text if version_row is not None else ""
                self.tree.insert(
                    "", "end", iid=tool_id,
                    values=(tool.name, status, version, minimum, tool.path or ""),
                    tags=(tag,),
                )
            self.ctx.status.set("Toolchain discovery finished.")

        self.ctx.runner.run(work, done, lambda e: self.summary_var.set(f"Discovery failed: {e}"))

    def _selected_tool_id(self) -> str:
        selection = self.tree.selection()
        return selection[0] if selection else ""

    def _show_hint(self) -> None:
        tool_id = self._selected_tool_id()
        tool = self._tools.get(tool_id) if tool_id else None
        version_row = self._versions.get(tool_id) if tool_id else None
        if tool is None:
            content = ""
        elif tool.found and version_row is not None and not version_row.satisfies:
            lines = [
                f"{tool.name} {tool.version or ''} is installed at {tool.path or 'a path outside PATH'},",
                f"but this project needs at least {version_row.minimum_text}.",
                version_row.note or "",
                "",
                *hint_lines(tool_id),
            ]
            content = "\n".join(line for line in lines if line is not None)
        elif tool.found:
            content = f"{tool.name} is installed at {tool.path or 'a path outside PATH'}."
            if version_row is not None and version_row.minimum_text:
                content += f" Minimum supported: {version_row.minimum_text}."
        else:
            content = "\n".join(hint_lines(tool_id)) or "No install hint is known for this tool."
        self.hint_box.configure(state="normal")
        self.hint_box.delete("1.0", "end")
        self.hint_box.insert("1.0", content)
        self.hint_box.configure(state="disabled")

    def _copy_hint(self) -> None:
        content = self.hint_box.get("1.0", "end").strip()
        if not content:
            return
        _copy_text(self, content)
        self.ctx.status.set("Install command copied to the clipboard.")


class HistoryDialog(_Dialog):
    """Search, filter, export and replay past builds (items 30 and 40)."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, "Build history", width=980, height=600)
        self._records = build_history.load_all(base=ctx.config.work_dir)
        self._shown: list = []

        bar = ttk.Frame(self, padding=(12, 10, 12, 0))
        bar.pack(fill="x")
        ttk.Label(bar, text="Search").pack(side="left", padx=(0, 6))
        self.search_var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.search_var, width=36)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e: self._refresh())
        ttk.Label(bar, text="Result").pack(side="left", padx=(12, 6))
        self.result_var = tk.StringVar(value="all")
        ttk.Combobox(
            bar, textvariable=self.result_var, values=("all", "ok", "failed"),
            state="readonly", width=8,
        ).pack(side="left")
        ttk.Button(bar, text="Apply", command=self._refresh).pack(side="left", padx=8)
        ttk.Button(bar, text="Clear", command=self._clear_filters).pack(side="left")

        self.summary_var = tk.StringVar()
        ttk.Label(self, textvariable=self.summary_var, padding=(12, 6)).pack(fill="x")

        body = ttk.Frame(self, padding=(12, 0))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        tree = make_tree(
            body,
            [("time", "Time (UTC)", 150), ("ok", "Result", 70), ("mode", "Mode", 60),
             ("adapter", "Type", 80), ("project", "Project", 300), ("msg", "Message", 220)],
            height=14,
        )
        tree.grid(row=0, column=0, sticky="nsew")
        with_scrollbar(body, tree).grid(row=0, column=1, sticky="ns")
        tree.tag_configure("ok", foreground=COLORS["ok"])
        tree.tag_configure("fail", foreground=COLORS["err"])
        self.tree = tree
        tree.bind("<Double-1>", lambda _e: self._copy_replay())

        row = ttk.Frame(self, padding=(12, 8, 12, 12))
        row.pack(fill="x")
        ttk.Button(row, text="Copy repeat command", command=self._copy_replay).pack(side="left")
        ttk.Button(row, text="Export JSON...", command=self._export_json).pack(side="left", padx=8)
        ttk.Button(row, text="Export CSV...", command=self._export_csv).pack(side="left")
        ttk.Button(row, text="Close", style="Ghost.TButton", command=self.destroy).pack(side="right")

        self._refresh()

    # ------------------------------------------------------------- rendering
    def _clear_filters(self) -> None:
        self.search_var.set("")
        self.result_var.set("all")
        self._refresh()

    def _refresh(self) -> None:
        ok_filter = None
        if self.result_var.get() == "ok":
            ok_filter = True
        elif self.result_var.get() == "failed":
            ok_filter = False
        filtered = history_query.filter_records(
            self._records, query=self.search_var.get().strip(), ok=ok_filter
        )
        self._shown = filtered
        self.tree.delete(*self.tree.get_children())
        for record in reversed(filtered[-200:]):
            data = history_query.as_dict(record)
            tag = "ok" if data.get("ok") else "fail"
            self.tree.insert(
                "", "end",
                values=(
                    data.get("timestamp", ""),
                    "ok" if data.get("ok") else "failed",
                    data.get("mode", ""),
                    data.get("adapter_id", ""),
                    data.get("project_path", ""),
                    data.get("message", ""),
                ),
                tags=(tag,),
            )
        stats = history_query.summarize(filtered)
        self.summary_var.set(
            f"{stats['total']} record(s): {stats['ok']} succeeded, {stats['failed']} failed. "
            "Double-click a row to copy the command that repeats it."
        )

    def _selected_record(self):
        selection = self.tree.selection()
        if not selection:
            return None
        index = self.tree.index(selection[0])
        # Rows are displayed newest-first; map back to the filtered list.
        ordered = list(reversed(self._shown[-200:]))
        if 0 <= index < len(ordered):
            return ordered[index]
        return None

    def _copy_replay(self) -> None:
        record = self._selected_record()
        if record is None:
            self.ctx.status.set("Select a history row first.")
            return
        command = history_query.replay_command(record)
        _copy_text(self, command)
        self.ctx.status.set(f"Copied: {command}")

    def _export_json(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, title="Export history as JSON", defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        history_query.export_json(self._shown, Path(path))
        self.ctx.status.set(f"Exported {len(self._shown)} record(s) to {path}")

    def _export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, title="Export history as CSV", defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        history_query.export_csv(self._shown, Path(path))
        self.ctx.status.set(f"Exported {len(self._shown)} record(s) to {path}")


class ContainerDialog(_Dialog):
    """Inspect an `.ece` container (deep validation) and extract its contents."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, "Inspect / Extract .ece", width=800, height=520)
        self.container: Path | None = None

        bar = ttk.Frame(self, padding=(12, 10, 12, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="Open .ece...", command=self._open).pack(side="left")
        self.compare_btn = ttk.Button(
            bar, text="Compare with another .ece...", command=self._compare, state="disabled"
        )
        self.compare_btn.pack(side="left", padx=(8, 0))
        self.file_var = tk.StringVar(value="No file selected")
        ttk.Label(bar, textvariable=self.file_var, style="Muted.TLabel").pack(
            side="left", padx=10
        )

        self.summary_var = tk.StringVar(
            value="Choose an .ece container to inspect, verify its hashes and extract it."
        )
        ttk.Label(
            self, textvariable=self.summary_var, padding=(12, 8),
            wraplength=760, justify="left",
        ).pack(fill="x")

        body = ttk.Frame(self, padding=(12, 0, 12, 0))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        tree = make_tree(
            body,
            [("path", "Path inside container", 320), ("size", "Size", 90),
             ("comp", "Compression", 100), ("stored", "Stored", 90),
             ("exe", "Executable", 80)],
        )
        tree.grid(row=0, column=0, sticky="nsew")
        with_scrollbar(body, tree).grid(row=0, column=1, sticky="ns")
        tree.tag_configure("exec", foreground=COLORS["accent"])
        tree.tag_configure("bad", foreground=COLORS["err"])
        self.tree = tree

        row = ttk.Frame(self, padding=(12, 8, 12, 12))
        row.pack(fill="x")
        self.extract_btn = ttk.Button(
            row, text="Extract...", command=self._extract, state="disabled"
        )
        self.extract_btn.pack(side="left")
        self.status_var = tk.StringVar(value="")
        ttk.Label(row, textvariable=self.status_var, style="Muted.TLabel").pack(
            side="left", padx=10
        )

    # ------------------------------------------------------------- actions
    def _open(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Open an .ece container",
            filetypes=[("ECE containers", "*.ece"), ("All files", "*.*")],
        )
        if path:
            self._load(Path(path))

    def _compare(self) -> None:
        """Compare the open container with another one (item 20)."""
        if self.container is None:
            return
        other = filedialog.askopenfilename(
            parent=self,
            title="Compare with another .ece container",
            filetypes=[("ECE containers", "*.ece"), ("All files", "*.*")],
        )
        if not other:
            return
        from ..container.diff import diff_containers

        old, new = self.container, Path(other)
        self.ctx.status.set("Comparing containers...")

        def work():
            return diff_containers(old, new)

        def done(result) -> None:
            self.ctx.status.set("Comparison finished.")
            DiffDialog(self.ctx, result)

        def fail(exc: BaseException) -> None:
            self.ctx.status.set("Comparison failed.")
            messagebox.showerror(
                APP_NAME, f"Could not compare the containers:\n{exc}", parent=self
            )

        self.ctx.runner.run(work, done, fail)

    def _load(self, path: Path) -> None:
        self.container = path
        self.file_var.set(str(path))
        self.tree.delete(*self.tree.get_children())
        self.extract_btn.configure(state="disabled")
        self.compare_btn.configure(state="normal")
        self.summary_var.set("Validating (deep: re-hashing every entry)...")
        self.status_var.set("Validating...")
        self.ctx.status.set("Validating container...")

        def work():
            report = validate_container(path, deep=True)
            rows = []
            with ContainerReader(path) as reader:
                for entry in reader.entries():
                    rows.append(
                        (
                            entry.path,
                            _fmt_size(entry.size),
                            entry.compression,
                            _fmt_size(entry.length),
                            "yes" if entry.executable else "",
                        )
                    )
            return report, rows

        def done(result) -> None:
            report, rows = result
            for row in rows:
                tags = ("exec",) if row[4] == "yes" else ()
                self.tree.insert("", "end", values=row, tags=tags)
            if report.ok:
                verified = (
                    f"  ·  all {report.hash_algo} hashes verified"
                    if report.hashes_verified
                    else ""
                )
                signed = ""
                manifest = report.manifest or {}
                sig = manifest.get("signature")
                if isinstance(sig, dict) and sig.get("alg"):
                    signed = f"  ·  signed ({sig['alg']}, not verified here)"
                self.summary_var.set(
                    f"Valid .ece  ·  format v{report.format_version}"
                    f"  ·  {report.entry_count} entries  ·  {report.compression}"
                    f"  ·  {_fmt_size(report.file_size)}{verified}{signed}"
                )
                self.extract_btn.configure(state="normal")
                explanation = explain_validation(report)
                self.status_var.set("  ".join(explanation[:2]))
                self.ctx.status.set("Container is valid")
            else:
                msgs = "; ".join(i.message for i in report.errors[:3]) or "invalid"
                self.summary_var.set(f"INVALID .ece  ·  {msgs}")
                self.status_var.set("")
                self.ctx.status.set("Container validation failed")

        def fail(exc: BaseException) -> None:
            self.summary_var.set(f"Validation failed: {exc}")
            self.status_var.set("")
            self.ctx.status.set(f"Validation failed: {exc}")

        self.ctx.runner.run(work, done, fail)

    def _extract(self) -> None:
        if self.container is None:
            return
        destination = filedialog.askdirectory(parent=self, title="Choose extraction folder")
        if not destination:
            return
        container = self.container
        self.extract_btn.configure(state="disabled")
        self.status_var.set("Extracting...")

        def progress(message: str, done_count: int, total: int) -> None:
            # Worker thread: marshal to the Tk main loop before touching widgets.
            self.ctx.runner.post(lambda: self.ctx.status.set(message))

        def work():
            return Extractor().extract(container, destination, progress=progress)

        def done(report) -> None:
            extra = f"  ·  {len(report.skipped)} skipped" if report.skipped else ""
            self.status_var.set(f"Extracted: {report.summary}{extra}")
            self.extract_btn.configure(state="normal")
            self.ctx.status.set(f"Extracted {report.files_written} file(s)")
            for line in explain_extraction(report)[:2]:
                self.ctx.status.set(line)
            if report.warnings:
                messagebox.showwarning(
                    APP_NAME, "\n".join(report.warnings[:10]), parent=self
                )
            if report.files_written and messagebox.askyesno(
                APP_NAME, f"{report.summary}\n\nOpen the folder?", parent=self
            ):
                _open_folder(Path(destination))

        def fail(exc: BaseException) -> None:
            self.status_var.set("Extraction failed")
            self.extract_btn.configure(state="normal")
            self.ctx.status.set(f"Extraction failed: {exc}")
            messagebox.showerror(APP_NAME, f"Extraction failed:\n{exc}", parent=self)

        self.ctx.runner.run(work, done, fail)


def _copy_text(widget: tk.Misc, text: str) -> None:
    """Copy *text* to the clipboard, ignoring clipboard-less sessions."""
    try:
        widget.clipboard_clear()
        widget.clipboard_append(text)
    except tk.TclError:  # pragma: no cover - no clipboard
        pass


def _readonly_text(parent: tk.Misc, content: str, *, height: int = 12) -> tk.Text:
    box = tk.Text(parent, height=height, wrap="word", state="normal")
    configure_text_widget(box)
    box.insert("1.0", content)
    box.configure(state="disabled")
    return box


class ErrorDialog(_Dialog):
    """A failure with what happened / why / what to do and a Copy button.

    Item 32: the text is selectable and copyable, so the user can paste it in a
    bug report or a terminal without retyping anything.
    """

    def __init__(self, ctx: AppContext, title: str, text: str, *, log_path=None,
                 retry=None, repair=None) -> None:
        super().__init__(ctx, title, width=760, height=420)
        self._text = redact(text)
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        box = _readonly_text(body, self._text, height=14)
        box.pack(fill="both", expand=True)
        row = ttk.Frame(self, padding=(12, 0, 12, 12))
        row.pack(fill="x")
        ttk.Button(row, text="Copy error", command=self._copy).pack(side="left")
        if retry is not None:
            ttk.Button(row, text="Try again", style="Accent.TButton", command=lambda: (self.destroy(), retry())).pack(
                side="left", padx=8
            )
        if repair is not None:
            ttk.Button(
                row, text="Attempt automatic repair",
                command=lambda: (self.destroy(), repair()),
            ).pack(side="left", padx=8)
        if log_path:
            ttk.Button(
                row, text="Open log folder",
                command=lambda: _open_folder(Path(log_path).parent),
            ).pack(side="left", padx=8)
        ttk.Button(row, text="Close", style="Ghost.TButton", command=self.destroy).pack(side="right")

    def _copy(self) -> None:
        _copy_text(self, self._text)
        self.ctx.status.set("Error text copied to the clipboard.")


class RepairDialog(_Dialog):
    """What the repair engine did, with the diffs of every change."""

    def __init__(self, ctx: AppContext, outcome, *, on_retry=None) -> None:
        super().__init__(ctx, "Automatic repair", width=860, height=560)
        self._outcome = outcome
        self._text = outcome.text()
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        box = _readonly_text(body, self._text, height=20)
        box.pack(fill="both", expand=True)
        self.status_var = tk.StringVar(value=outcome.message)
        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel",
                  padding=(12, 0)).pack(fill="x")
        row = ttk.Frame(self, padding=(12, 6, 12, 12))
        row.pack(fill="x")
        ttk.Button(row, text="Copy report", command=self._copy).pack(side="left")
        if on_retry is not None and outcome.final_ok:
            ttk.Button(
                row, text="Build again", style="Accent.TButton",
                command=lambda: (self.destroy(), on_retry()),
            ).pack(side="left", padx=8)
        ttk.Button(row, text="Close", style="Ghost.TButton", command=self.destroy).pack(side="right")

    def _copy(self) -> None:
        _copy_text(self, self._text)
        self.ctx.status.set("Repair report copied to the clipboard.")


class SummaryDialog(_Dialog):
    """The copyable build summary with the two actions a user actually wants."""

    def __init__(self, ctx: AppContext, summary) -> None:
        super().__init__(ctx, "Build summary", width=760, height=460)
        self.summary = summary
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        box = _readonly_text(body, summary.text(), height=16)
        box.pack(fill="both", expand=True)
        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel",
                  padding=(12, 0)).pack(fill="x")
        row = ttk.Frame(self, padding=(12, 6, 12, 12))
        row.pack(fill="x")
        ttk.Button(row, text="Copy summary", command=self._copy).pack(side="left")
        if summary.output_path:
            ttk.Button(
                row, text="Open output folder",
                command=lambda: _open_folder(Path(summary.output_path).parent),
            ).pack(side="left", padx=8)
        ttk.Button(row, text="Close", style="Ghost.TButton", command=self.destroy).pack(side="right")

    def _copy(self) -> None:
        _copy_text(self, self.summary.text())
        self.status_var.set("Summary copied to the clipboard.")


class DiffDialog(_Dialog):
    """Item 20: a plain-language comparison of two containers."""

    def __init__(self, ctx: AppContext, result) -> None:
        super().__init__(ctx, "Compare containers", width=820, height=520)
        self._result = result
        content = result.text()
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        box = _readonly_text(body, content, height=20)
        box.pack(fill="both", expand=True)
        self.status_var = tk.StringVar(value=result.summary)
        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel",
                  padding=(12, 0)).pack(fill="x")
        row = ttk.Frame(self, padding=(12, 6, 12, 12))
        row.pack(fill="x")
        ttk.Button(row, text="Copy comparison", command=self._copy).pack(side="left")
        ttk.Button(row, text="Close", style="Ghost.TButton", command=self.destroy).pack(side="right")

    def _copy(self) -> None:
        _copy_text(self, self._result.text())
        self.status_var.set("Comparison copied to the clipboard.")


class PlanDialog(_Dialog):
    """The dry-run plan: exact commands, copyable, nothing executed."""

    def __init__(self, ctx: AppContext, preview) -> None:
        super().__init__(ctx, "Build plan (nothing has run)", width=820, height=560)
        self.preview = preview
        self._text = preview.text()
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        box = _readonly_text(body, self._text, height=22)
        box.pack(fill="both", expand=True)
        self.status_var = tk.StringVar(
            value="Ready to run." if preview.ready else "This plan has blockers; nothing can run yet."
        )
        ttk.Label(
            self, textvariable=self.status_var, style="Muted.TLabel", padding=(12, 0),
        ).pack(fill="x")
        row = ttk.Frame(self, padding=(12, 6, 12, 12))
        row.pack(fill="x")
        ttk.Button(row, text="Copy plan", command=self._copy).pack(side="left")
        ttk.Button(row, text="Close", style="Ghost.TButton", command=self.destroy).pack(side="right")

    def _copy(self) -> None:
        _copy_text(self, self._text)
        self.status_var.set("Plan copied to the clipboard.")


class DoctorDialog(_Dialog):
    """Item 27: a read-only environment report with copyable fixes."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, "Environment check (doctor)", width=900, height=580)
        self._report_text = ""

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        self.summary_var = tk.StringVar(value="Running checks...")
        ttk.Label(body, textvariable=self.summary_var, padding=(0, 0, 0, 8)).grid(
            row=0, column=0, sticky="w"
        )
        tree = make_tree(
            body,
            [("name", "Check", 230), ("status", "Status", 80), ("detail", "Detail", 520)],
            height=16,
        )
        tree.grid(row=1, column=0, sticky="nsew")
        with_scrollbar(body, tree).grid(row=1, column=1, sticky="ns")
        tree.tag_configure("ok", foreground=COLORS["ok"])
        tree.tag_configure("warn", foreground=COLORS["warn"])
        tree.tag_configure("err", foreground=COLORS["err"])
        self.tree = tree

        detail = ttk.Frame(self, padding=(12, 6))
        detail.pack(fill="x")
        ttk.Label(detail, text="Fix for the selected check:", style="Muted.TLabel").pack(anchor="w")
        self.fix_box = _readonly_text(detail, "", height=5)
        self.fix_box.pack(fill="x", pady=(4, 0))
        tree.bind("<<TreeviewSelect>>", lambda _e: self._show_fix())

        row = ttk.Frame(self, padding=(12, 6, 12, 12))
        row.pack(fill="x")
        ttk.Button(row, text="Re-run", command=self._run).pack(side="left")
        ttk.Button(row, text="Copy report", command=self._copy).pack(side="left", padx=8)
        ttk.Button(row, text="Close", style="Ghost.TButton", command=self.destroy).pack(side="right")

        self._checks: dict[str, object] = {}
        self._run()

    def _run(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.summary_var.set("Running checks...")
        self.ctx.status.set("Running environment checks...")

        def work():
            return run_checks()

        def done(report) -> None:
            self._checks = {check.key: check for check in report.checks}
            for check in report.checks:
                tag = {"ok": "ok", "warn": "warn", "error": "err"}.get(check.status, "warn")
                self.tree.insert(
                    "", "end", iid=check.key,
                    values=(check.name, check.status, check.detail), tags=(tag,),
                )
            self._report_text = report.text()
            self.summary_var.set(report.summary)
            self.ctx.status.set("Environment check finished.")
            if not report.ok:
                self.summary_var.set(report.summary + " Expand a failed check for the fix.")

        def fail(exc: BaseException) -> None:
            self.summary_var.set(f"Could not run the checks: {exc}")
            self.ctx.status.set("Environment check failed.")

        self.ctx.runner.run(work, done, fail)

    def _show_fix(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        check = self._checks.get(selection[0])
        if check is None:
            return
        lines = []
        if check.fix:
            lines.append(check.fix)
        if check.commands:
            lines.append("")
            lines.extend(check.commands)
        content = "\n".join(lines) or "No fix needed."
        self.fix_box.configure(state="normal")
        self.fix_box.delete("1.0", "end")
        self.fix_box.insert("1.0", content)
        self.fix_box.configure(state="disabled")

    def _copy(self) -> None:
        _copy_text(self, self._report_text)
        self.ctx.status.set("Doctor report copied to the clipboard.")


class GettingStartedDialog(_Dialog):
    """Item 1: the short first-run walkthrough, one step at a time."""

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, "Getting started", width=700, height=400)
        self._index = 0
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        self.step_var = tk.StringVar()
        ttk.Label(body, textvariable=self.step_var, style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        self.body_box = _readonly_text(body, "", height=10)
        self.body_box.grid(row=1, column=0, sticky="nsew")

        row = ttk.Frame(self, padding=(16, 0, 16, 14))
        row.pack(fill="x")
        self.back_btn = ttk.Button(row, text="Back", command=self._back)
        self.back_btn.pack(side="left")
        self.counter_var = tk.StringVar()
        ttk.Label(row, textvariable=self.counter_var, style="Muted.TLabel").pack(side="left", padx=12)
        self.next_btn = ttk.Button(row, text="Next", style="Accent.TButton", command=self._next)
        self.next_btn.pack(side="right")
        ttk.Button(row, text="Close", style="Ghost.TButton", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )
        self._render()

    def _render(self) -> None:
        step = WIZARD_STEPS[self._index]
        self.step_var.set(step.title)
        self.body_box.configure(state="normal")
        self.body_box.delete("1.0", "end")
        self.body_box.insert("1.0", step.body)
        self.body_box.configure(state="disabled")
        self.counter_var.set(f"Step {self._index + 1} of {len(WIZARD_STEPS)}")
        self.back_btn.configure(state="normal" if self._index else "disabled")
        self.next_btn.configure(text="Done" if self._index == len(WIZARD_STEPS) - 1 else "Next")

    def _back(self) -> None:
        if self._index > 0:
            self._index -= 1
            self._render()

    def _next(self) -> None:
        if self._index >= len(WIZARD_STEPS) - 1:
            self.destroy()
            return
        self._index += 1
        self._render()


class ShortcutsDialog(_Dialog):
    """Item: the whole keyboard map in one place."""

    SHORTCUTS = (
        ("Ctrl+O", "Open a project folder"),
        ("F5", "Rescan (detect the project again)"),
        ("Ctrl+Enter", "Compile"),
        ("Esc", "Cancel a running build"),
        ("Ctrl+P", "Preview the build plan (dry run)"),
        ("Ctrl+Shift+C", "Copy the visible log"),
        ("Ctrl+H", "Open build history"),
        ("Ctrl+D", "Run the environment check (doctor)"),
        ("F1", "Show this list"),
    )

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, "Keyboard shortcuts", width=560, height=420)
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        tree = make_tree(body, [("key", "Shortcut", 160), ("action", "Action", 340)], height=12)
        tree.pack(fill="both", expand=True)
        for key, action in self.SHORTCUTS:
            tree.insert("", "end", values=(key, action))


class TemplateDialog(_Dialog):
    """Item 26: start a project from a known-good skeleton."""

    def __init__(self, ctx: AppContext, *, on_created=None) -> None:
        super().__init__(ctx, "New project from template", width=760, height=460)
        self._on_created = on_created
        self._templates = templates.list_templates()
        self._by_label = {f"{t.name} - {t.description}": t.key for t in self._templates}

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Template").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.template_var = tk.StringVar(value=next(iter(self._by_label), ""))
        combo = ttk.Combobox(
            body, textvariable=self.template_var, values=list(self._by_label),
            state="readonly", width=60,
        )
        combo.grid(row=0, column=1, columnspan=2, sticky="ew")

        ttk.Label(body, text="Folder").grid(row=1, column=0, sticky="w", pady=(10, 0), padx=(0, 8))
        self.dest_var = tk.StringVar()
        ttk.Entry(body, textvariable=self.dest_var).grid(row=1, column=1, sticky="ew", pady=(10, 0))
        ttk.Button(body, text="Browse...", command=self._browse).grid(
            row=1, column=2, sticky="e", padx=(8, 0), pady=(10, 0)
        )

        ttk.Label(body, text="Name").grid(row=2, column=0, sticky="w", pady=(10, 0), padx=(0, 8))
        self.name_var = tk.StringVar()
        ttk.Entry(body, textvariable=self.name_var).grid(
            row=2, column=1, sticky="ew", pady=(10, 0)
        )

        self.force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            body, text="Write into the folder even if it is not empty", variable=self.force_var,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))

        self.status_var = tk.StringVar(
            value="Pick a template, choose an empty folder and press Create."
        )
        ttk.Label(body, textvariable=self.status_var, style="Muted.TLabel", wraplength=700,
                  justify="left").grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))

        row = ttk.Frame(self, padding=(14, 0, 14, 14))
        row.pack(fill="x")
        ttk.Button(row, text="Create", style="Accent.TButton", command=self._create).pack(side="left")
        ttk.Button(row, text="Cancel", style="Ghost.TButton", command=self.destroy).pack(side="right")

    def _browse(self) -> None:
        path = filedialog.askdirectory(parent=self, title="Choose a folder for the new project")
        if path:
            self.dest_var.set(path)

    def _create(self) -> None:
        key = self._by_label.get(self.template_var.get(), "")
        destination = self.dest_var.get().strip()
        if not key:
            self.status_var.set("Choose a template first.")
            return
        if not destination:
            self.status_var.set("Choose an empty folder for the new project.")
            return
        try:
            written = templates.create_project(
                key, Path(destination), name=self.name_var.get().strip() or None,
                force=self.force_var.get(),
            )
        except EceError as exc:
            self.status_var.set(f"Could not create the project: {exc}")
            return
        self.status_var.set(f"Created {len(written)} file(s) in {destination}.")
        if self._on_created is not None:
            self._on_created(Path(destination))
        if messagebox.askyesno(APP_NAME, f"Created {len(written)} file(s).\n\nOpen the folder?", parent=self):
            _open_folder(Path(destination))


class CandidateDialog(_Dialog):
    """Pick one detected project in a monorepo (items 11 and 16)."""

    def __init__(self, ctx: AppContext, base: Path, candidates, *, current: str = "") -> None:
        super().__init__(ctx, "Choose project to build", width=860, height=480)
        self.selection = None
        self._base = base
        self._candidates = list(candidates)

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        ttk.Label(
            body,
            text="More than one project was found. Pick the one Compile should build. "
                 "The reason each was detected is shown so the choice is not a guess.",
            wraplength=800, justify="left",
        ).grid(row=0, column=0, sticky="w", pady=(0, 8))

        tree = make_tree(
            body,
            [("type", "Project type", 170), ("folder", "Folder", 190),
             ("confidence", "Confidence", 90), ("why", "Why this was detected", 380)],
            height=12,
        )
        tree.grid(row=1, column=0, sticky="nsew")
        with_scrollbar(body, tree).grid(row=1, column=1, sticky="ns")
        self.tree = tree
        for index, candidate in enumerate(self._candidates):
            label = ADAPTER_LABELS.get(candidate.adapter_id, candidate.adapter_id)
            tree.insert(
                "", "end", iid=str(index),
                values=(
                    label,
                    candidate.root or ".",
                    f"{int(candidate.confidence * 100)}%",
                    _why_adapter(candidate),
                ),
            )
        if current:
            for index, candidate in enumerate(self._candidates):
                if candidate.adapter_id == current:
                    tree.selection_set(str(index))
                    tree.see(str(index))
                    break
        elif self._candidates:
            tree.selection_set("0")
        tree.bind("<Double-1>", lambda _e: self._use())

        row = ttk.Frame(self, padding=(12, 6, 12, 12))
        row.pack(fill="x")
        ttk.Button(row, text="Use selected", style="Accent.TButton", command=self._use).pack(side="left")
        ttk.Button(row, text="Cancel", style="Ghost.TButton", command=self.destroy).pack(side="right")

    def _use(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        try:
            self.selection = self._candidates[int(selection[0])]
        except (ValueError, IndexError):
            return
        self.destroy()


class PackageDialog(_Dialog):
    """Create a distributable package (ZIP, TAR.GZ, installer, DEB, AppImage).

    The dialog collects the metadata the package needs, runs the real
    packaging in the background and shows the verified result, including the
    dependencies that could not be resolved.
    """

    def __init__(self, ctx: AppContext, project: Path, *, output_dir: Path,
                 prefer_adapter: str | None = None) -> None:
        super().__init__(ctx, "Create distributable package", width=820, height=620)
        self.project = Path(project)
        self._prefer_adapter = prefer_adapter
        self._result = None

        from ..packaging.formats import describe_formats

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Project").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(body, text=str(self.project), style="Muted.TLabel").grid(
            row=0, column=1, columnspan=3, sticky="w", pady=(0, 4)
        )

        self.format_var = tk.StringVar(value="zip")
        ttk.Label(body, text="Format").grid(row=1, column=0, sticky="w", pady=(0, 4))
        formats = describe_formats()
        self.format_box = ttk.Combobox(
            body, textvariable=self.format_var, state="readonly", width=22,
            values=[f"{f['id']} - {f['name']}" for f in formats],
        )
        self.format_box.grid(row=1, column=1, sticky="w", pady=(0, 4))
        self.format_box.bind("<<ComboboxSelected>>", lambda _e: self._describe_format())
        self.format_note = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.format_note, style="Muted.TLabel", wraplength=520,
                  justify="left").grid(row=1, column=2, columnspan=2, sticky="w", pady=(0, 4))

        self.name_var = tk.StringVar(value=self.project.name)
        self.version_var = tk.StringVar(value="1.0.0")
        self.description_var = tk.StringVar(value="")
        self.publisher_var = tk.StringVar(value="")
        self.arch_var = tk.StringVar(value="x64")

        fields = (
            ("Name", self.name_var),
            ("Version", self.version_var),
            ("Description", self.description_var),
            ("Publisher", self.publisher_var),
        )
        for index, (label, variable) in enumerate(fields):
            row = 2 + index
            ttk.Label(body, text=label, width=12).grid(row=row, column=0, sticky="w", pady=(0, 4))
            ttk.Entry(body, textvariable=variable).grid(
                row=row, column=1, columnspan=3, sticky="ew", pady=(0, 4)
            )

        ttk.Label(body, text="Architecture", width=12).grid(row=6, column=0, sticky="w", pady=(0, 4))
        ttk.Combobox(
            body, textvariable=self.arch_var, state="readonly", width=12,
            values=("x64", "x86", "arm64"),
        ).grid(row=6, column=1, sticky="w", pady=(0, 4))

        self.output_var = tk.StringVar(value=str(output_dir))
        ttk.Label(body, text="Output folder", width=12).grid(row=7, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(body, textvariable=self.output_var).grid(
            row=7, column=1, columnspan=2, sticky="ew", pady=(0, 4)
        )
        ttk.Button(body, text="Browse...", command=self._browse_output).grid(
            row=7, column=3, sticky="e", pady=(0, 4)
        )

        self.include_var = tk.StringVar(value="")
        self.exclude_var = tk.StringVar(value="")
        ttk.Label(body, text="Include only", width=12).grid(row=8, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(body, textvariable=self.include_var).grid(
            row=8, column=1, columnspan=3, sticky="ew", pady=(0, 4)
        )
        ttk.Label(body, text="Exclude", width=12).grid(row=9, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(body, textvariable=self.exclude_var).grid(
            row=9, column=1, columnspan=3, sticky="ew", pady=(0, 4)
        )
        ttk.Label(
            body,
            text="Patterns are separated by commas. Leave them empty to package "
                 "everything the build produced.",
            style="Muted.TLabel",
        ).grid(row=10, column=0, columnspan=4, sticky="w", pady=(0, 6))

        self.build_var = tk.BooleanVar(value=False)
        self.consent_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="Run the build first", variable=self.build_var).grid(
            row=11, column=0, columnspan=2, sticky="w"
        )
        ttk.Checkbutton(body, text="Allow project scripts", variable=self.consent_var).grid(
            row=11, column=2, columnspan=2, sticky="w"
        )

        from ..optimize.profiles import profile_keys

        self.profile_var = tk.StringVar(value="none")
        ttk.Label(body, text="Profile", style="Muted.TLabel").grid(
            row=12, column=0, sticky="w", pady=(8, 0)
        )
        self.profile_box = ttk.Combobox(
            body, textvariable=self.profile_var, state="readonly", width=14,
            values=profile_keys(),
        )
        self.profile_box.grid(row=12, column=1, sticky="w", pady=(8, 0))
        add_tooltip(
            self.profile_box,
            "Optimization profile used when the build runs before packaging.\n"
            "none: the project builds exactly as it asks.",
        )

        self.status_var = tk.StringVar(value="Choose the format and press Create package.")
        ttk.Label(body, textvariable=self.status_var, style="Muted.TLabel", wraplength=760,
                  justify="left").grid(row=13, column=0, columnspan=4, sticky="w", pady=(10, 4))

        self.output_box = _readonly_text(body, "", height=10)
        self.output_box.grid(row=14, column=0, columnspan=4, sticky="nsew", pady=(4, 0))
        body.rowconfigure(14, weight=1)

        row = ttk.Frame(self, padding=(12, 6, 12, 12))
        row.pack(fill="x")
        self.create_btn = ttk.Button(
            row, text="Create package", style="Accent.TButton", command=self._create
        )
        self.create_btn.pack(side="left")
        ttk.Button(row, text="Preview", command=self._preview).pack(side="left", padx=8)
        ttk.Button(row, text="Copy result", command=self._copy).pack(side="left", padx=8)
        ttk.Button(row, text="Close", style="Ghost.TButton", command=self.destroy).pack(side="right")

        self._describe_format()

    # ------------------------------------------------------------------
    def _selected_format(self) -> str:
        return self.format_var.get().split(" - ")[0].strip()

    def _build_request(self):
        """The package request built from the fields of this dialog."""
        from ..packaging import PackageMetadata, PackageRequest

        metadata = PackageMetadata(
            name=self.name_var.get().strip() or self.project.name,
            version=self.version_var.get().strip() or "1.0.0",
            description=self.description_var.get().strip(),
            publisher=self.publisher_var.get().strip(),
            architecture=self.arch_var.get().strip() or "x64",
        )
        return PackageRequest(
            project=self.project,
            output_dir=Path(self.output_var.get().strip() or self.ctx.config.output_dir),
            fmt=self._selected_format(),
            metadata=metadata,
            include=_split_patterns(self.include_var.get()),
            exclude=_split_patterns(self.exclude_var.get()),
            run_build=self.build_var.get(),
            consent=self.consent_var.get(),
            prefer_adapter=self._prefer_adapter,
            overwrite=True,
        )

    def _describe_format(self) -> None:
        from ..packaging.formats import describe_formats

        wanted = self._selected_format()
        for entry in describe_formats():
            if entry["id"] != wanted:
                continue
            if entry["available"]:
                self.format_note.set(f"Ready: {entry['name']}.")
            else:
                note = entry["note"] or "The external tool for this format is not installed."
                self.format_note.set(f"Not available yet: {note}")
            return

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(parent=self, title="Choose the output folder")
        if path:
            self.output_var.set(path)

    def _preview(self) -> None:
        """Show what would be packaged, without writing anything."""
        from ..packaging import AdvancedPackager

        packager = AdvancedPackager(config=self.ctx.config)
        plan = packager.plan(self._build_request())
        self.output_box.configure(state="normal")
        self.output_box.delete("1.0", "end")
        self.output_box.insert("1.0", plan.text())
        self.output_box.configure(state="disabled")
        self.status_var.set(
            "Nothing was written. Press Create package to build it."
            if plan.ok
            else "This package cannot be created as it is."
        )

    def _create(self) -> None:
        from ..optimize.plan import OptimizationRequest, build_optimization_plan
        from ..packaging import AdvancedPackager

        output = self.output_var.get().strip()
        if not output:
            self.status_var.set("Choose an output folder.")
            return
        request = self._build_request()
        optimization = None
        profile = self.profile_var.get().strip().lower()
        if profile and profile != "none":
            optimization = build_optimization_plan(
                self._prefer_adapter or "",
                OptimizationRequest.from_args(profile, []),
            )
            if optimization.unsupported_reason:
                self.status_var.set(optimization.unsupported_reason)
                return
        request.optimization = optimization
        packager = AdvancedPackager(config=self.ctx.config)

        self.create_btn.configure(state="disabled")
        self.status_var.set("Packaging...")

        def work():
            return packager.package(request)

        def done(result) -> None:
            self.create_btn.configure(state="normal")
            self._result = result
            self.output_box.configure(state="normal")
            self.output_box.delete("1.0", "end")
            self.output_box.insert("1.0", result.text())
            self.output_box.configure(state="disabled")
            self.status_var.set(result.message)

        def fail(exc: BaseException) -> None:
            self.create_btn.configure(state="normal")
            self.status_var.set(f"The packaging failed: {exc}")

        self.ctx.runner.run(work, done, fail)

    def _copy(self) -> None:
        if self._result is None:
            return
        _copy_text(self, self._result.text())
        self.ctx.status.set("Package summary copied to the clipboard.")


class OptimizeDialog(_Dialog):
    """Compare a baseline build with an optimized build and report the facts."""

    def __init__(self, ctx: AppContext, project: Path, *, prefer_adapter: str | None = None) -> None:
        super().__init__(ctx, "Optimize build", width=820, height=560)
        self.project = Path(project)
        self._prefer_adapter = prefer_adapter
        self._report = None

        from ..optimize.profiles import PROFILES

        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="Project").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(body, text=str(self.project), style="Muted.TLabel").grid(
            row=0, column=1, columnspan=2, sticky="w", pady=(0, 4)
        )

        self.profile_var = tk.StringVar(value="release")
        ttk.Label(body, text="Profile", width=12).grid(row=1, column=0, sticky="w", pady=(0, 4))
        self.profile_box = ttk.Combobox(
            body, textvariable=self.profile_var, state="readonly", width=20,
            values=[key for key in PROFILES if key != "custom"],
        )
        self.profile_box.grid(row=1, column=1, sticky="w", pady=(0, 4))
        self.profile_box.bind("<<ComboboxSelected>>", lambda _e: self._describe_profile())
        self.profile_note = tk.StringVar(value="")
        ttk.Label(body, textvariable=self.profile_note, style="Muted.TLabel", wraplength=520,
                  justify="left").grid(row=1, column=2, sticky="w", pady=(0, 4))

        self.tests_var = tk.StringVar(value="")
        self.benchmark_var = tk.StringVar(value="")
        self.consent_var = tk.BooleanVar(value=False)
        ttk.Label(body, text="Test command", width=12).grid(row=2, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(body, textvariable=self.tests_var).grid(
            row=2, column=1, columnspan=2, sticky="ew", pady=(0, 4)
        )
        ttk.Label(body, text="Benchmark", width=12).grid(row=3, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(body, textvariable=self.benchmark_var).grid(
            row=3, column=1, columnspan=2, sticky="ew", pady=(0, 4)
        )
        ttk.Checkbutton(body, text="Allow project scripts", variable=self.consent_var).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(0, 4)
        )
        ttk.Label(
            body,
            text="The test and benchmark commands are optional and run exactly as "
                 "written. Without a benchmark no runtime claim is made.",
            style="Muted.TLabel",
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self.status_var = tk.StringVar(value="Press Compare to build twice and compare.")
        ttk.Label(body, textvariable=self.status_var, style="Muted.TLabel", wraplength=760,
                  justify="left").grid(row=6, column=0, columnspan=3, sticky="w", pady=(0, 4))

        self.output_box = _readonly_text(body, "", height=16)
        self.output_box.grid(row=7, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        body.rowconfigure(7, weight=1)

        row = ttk.Frame(self, padding=(12, 6, 12, 12))
        row.pack(fill="x")
        self.compare_btn = ttk.Button(
            row, text="Compare", style="Accent.TButton", command=self._compare
        )
        self.compare_btn.pack(side="left")
        ttk.Button(row, text="Copy report", command=self._copy).pack(side="left", padx=8)
        ttk.Button(row, text="Close", style="Ghost.TButton", command=self.destroy).pack(side="right")

        self._describe_profile()

    # ------------------------------------------------------------------
    def _describe_profile(self) -> None:
        from ..optimize.profiles import get_profile

        profile = get_profile(self.profile_var.get())
        if profile is None:
            self.profile_note.set("")
            return
        self.profile_note.set(profile.description)

    def _compare(self) -> None:
        from ..build.compile_service import compile_project
        from ..optimize.measure import Measurement, compare, record_optimization
        from ..optimize.plan import OptimizationRequest, build_optimization_plan
        from ..optimize.recommend import recommendations
        from ..packaging.manifest import detected_toolchains

        key = self.profile_var.get().strip().lower()
        adapter_id = self._prefer_adapter or ""
        if not adapter_id:
            try:
                detected = scan(self.project)
                if detected.best is not None:
                    adapter_id = detected.best.adapter_id
            except EceError:
                detected = None
        optimization = build_optimization_plan(
            adapter_id, OptimizationRequest.from_args(key, [])
        )
        if optimization.unsupported_reason:
            self.status_var.set(optimization.unsupported_reason)
            return

        tests = _split_command(self.tests_var.get().strip()) if self.tests_var.get().strip() else None
        benchmark = (
            _split_command(self.benchmark_var.get().strip())
            if self.benchmark_var.get().strip()
            else None
        )

        self.compare_btn.configure(state="disabled")
        self.status_var.set("Building the baseline...")

        def measure_run(profile: str, opt) -> Measurement:
            outcome = compile_project(
                self.project,
                config=self.ctx.config,
                consent_granted=self.consent_var.get() if hasattr(self, "consent_var") else False,
                prefer_adapter=self._prefer_adapter,
                optimization=opt,
            )
            tests_ok: bool | None = None
            if tests:
                from ..build.process import run_single

                tests_ok = run_single(tests, self.project, timeout=900).ok
            measured: dict[str, float] = {}
            if benchmark:
                from ..build.process import run_single

                measured = {"seconds": run_single(benchmark, self.project, timeout=1800).duration_seconds}
            return Measurement(
                profile=profile,
                ok=outcome.ok,
                compile_seconds=outcome.duration_seconds,
                total_size=outcome.total_size,
                file_count=outcome.file_count,
                tests_ok=tests_ok,
                benchmark=measured,
                toolchains=detected_toolchains(),
            )

        def work():
            baseline = measure_run("default", None)
            if not baseline.ok:
                raise RuntimeError("The baseline build failed, so there is nothing to compare.")
            optimized = measure_run(optimization.profile, optimization)
            report = compare(baseline, optimized)
            advice = recommendations(adapter_id, optimization, report)
            record_optimization(self.ctx.config.work_dir, report)
            return report, advice, optimization

        def done(payload) -> None:
            report, advice, optimization = payload
            self._report = report
            self.compare_btn.configure(state="normal")
            lines = [optimization.text(), "", report.text()]
            if advice:
                lines.append("")
                lines.append("Recommendations:")
                for item in advice:
                    state = "applied" if item.applied else (
                        "needs your approval" if item.requires_approval else "information"
                    )
                    lines.append(f"  [{state}] {item.text}")
            self.output_box.configure(state="normal")
            self.output_box.delete("1.0", "end")
            self.output_box.insert("1.0", "\n".join(lines))
            self.output_box.configure(state="disabled")
            self.status_var.set("Comparison finished.")

        def fail(exc: BaseException) -> None:
            self.compare_btn.configure(state="normal")
            self.status_var.set(f"The comparison failed: {exc}")

        self.ctx.runner.run(work, done, fail)

    def _copy(self) -> None:
        if self._report is None:
            return
        _copy_text(self, self._report.text())
        self.ctx.status.set("Comparison copied to the clipboard.")


def _split_patterns(text: str) -> list[str]:
    """Split a comma-separated list of patterns."""
    return [piece.strip() for piece in (text or "").split(",") if piece.strip()]


def _split_command(text: str) -> list[str]:
    """Split a command line into an argv list, keeping quoted paths together."""
    import shlex

    parts = shlex.split(text, posix=False)
    cleaned: list[str] = []
    for part in parts:
        if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'":
            cleaned.append(part[1:-1])
        else:
            cleaned.append(part)
    return cleaned


class AboutDialog(_Dialog):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, "About", width=560, height=320)
        text = (
            f"{APP_NAME}  v{APP_VERSION}\n"
            f"ECE container format v{CONTAINER_FORMAT_VERSION}\n\n"
            "Detects a project's technology, runs its real build with the toolchain "
            "installed on this PC, and packages the result into a versioned .ece container.\n\n"
            "Honest by design: if a required compiler is missing, the adapter reports it and "
            "the build stops rather than pretending to succeed. Python and Node.js builds run "
            "for real; other technologies are detected and their toolchains reported.\n\n"
            ".ece is a documented container, not a self-executing format. Hashes detect "
            "corruption; they are not a signature."
        )
        lbl = ttk.Label(self, text=text, wraplength=520, justify="left", padding=14)
        lbl.pack(fill="both", expand=True)
        row = ttk.Frame(self)
        row.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(row, text="Open output folder",
                   command=lambda: _open_folder(ctx.config.output_dir)).pack(side="left")
        ttk.Button(row, text="Open documentation", command=self._docs).pack(side="left", padx=8)

    def _docs(self) -> None:
        from ..version import APP_SLUG

        webbrowser.open(f"https://github.com/{APP_SLUG}")


def _why_adapter(candidate) -> str:
    """One plain sentence explaining why an adapter was selected."""
    label = ADAPTER_LABELS.get(candidate.adapter_id, candidate.adapter_id)
    evidence = list(getattr(candidate, "evidence", []) or [])
    if evidence:
        return f"{label}, because it found {', '.join(evidence[:4])}."
    return f"{label}, based on the files in the folder."


def _fmt_size(num: int) -> str:
    """Human-readable byte size (KiB/MiB/GiB)."""
    value = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{num} B"  # pragma: no cover - unreachable


def _open_folder(path: Path) -> None:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]  # Windows-only tool
    except OSError as exc:
        log.warning("Could not open folder %s: %s", path, exc)


def run_app(*, config: AppConfig | None = None) -> int:
    """Launch the desktop interface. Returns a process exit code."""
    app_config = config or AppConfig()
    app_config.ensure_directories()

    root = tk.Tk()
    configure_root_window(root)
    apply_theme(root)

    runner = TaskRunner(root)
    status = StatusBar(root)
    ctx = AppContext(root=root, runner=runner, status=status, config=app_config)
    MainWindow(root, ctx)

    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_app())
