"""The DECOMPILER workspace: import, analyse, reconstruct, edit and rebuild.

Everything runs on the shared :class:`TaskRunner`; the panel only touches Tk
from callbacks. Buttons are enabled only when the operation they name can
actually run, and every result is taken from the real backend output — no
placeholder success messages.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..decompiler import (
    DecompileOptions,
    DecompileSession,
    analyze_file,
    expand_inputs,
    find_ilspy,
    find_javap,
    install_ilspy,
    plan_recompile,
    recompile,
)
from ..decompiler.models import (
    CATEGORY_CONFIG,
    CATEGORY_DISASSEMBLY,
    CATEGORY_METADATA,
    CATEGORY_RESOURCE,
    CATEGORY_SOURCE,
    STATUS_LABELS,
    DecompileResult,
    RecoveredFile,
)
from ..decompiler.tools import ILSPY_VERSION, capstone_available
from ..version import APP_NAME
from .dnd import enable_file_drop
from .editor import EditorTabs, language_from_path
from .theme import COLORS, configure_text_widget, enable_dark_titlebar
from .widgets import LogConsole, add_tooltip, make_tree, with_scrollbar

TEXT_SUFFIXES = {
    ".py", ".java", ".cs", ".c", ".h", ".cc", ".cpp", ".hpp", ".js", ".ts", ".json",
    ".xml", ".csproj", ".sln", ".md", ".txt", ".asm", ".go", ".rs", ".php", ".kt",
    ".yml", ".yaml", ".toml", ".html", ".css", ".sql", ".props", ".targets", ".resx",
}
MAX_EDITOR_BYTES = 4 * 1024 * 1024
MAX_ANALYZE_FILES = 2000


def _unique_dir(base: Path) -> Path:
    base = Path(base)
    if not base.exists():
        return base
    try:
        if not any(base.iterdir()):
            return base
    except OSError:
        return base
    for counter in range(2, 1000):
        candidate = base.with_name(f"{base.name}-{counter}")
        if not candidate.exists() or not any(candidate.iterdir()):
            return candidate
    return base


def _readonly_text(parent: tk.Misc, content: str, *, height: int = 12) -> tk.Text:
    box = tk.Text(parent, height=height, wrap="word", state="normal")
    configure_text_widget(box)
    box.insert("1.0", content)
    box.configure(state="disabled")
    return box


def _open_folder(path: Path) -> None:
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))  # type: ignore[attr-defined]
    except OSError:
        pass


class DecompilerPanel(ttk.Frame):
    """Integrated decompiler page (same window, same navigation)."""

    def __init__(self, parent: tk.Misc, ctx) -> None:
        super().__init__(parent, style="TFrame")
        self.ctx = ctx
        self._analyses: dict[str, object] = {}
        self._imported: set[str] = set()
        self._result: DecompileResult | None = None
        self._exported_root: Path | None = None
        self._busy = False
        self._cancel = threading.Event()
        self._decompile_options = DecompileOptions()
        self._drop_enabled = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # ---------------------------------------------------------- toolbar
        toolbar = ttk.Frame(self, padding=(16, 12, 16, 4))
        toolbar.grid(row=0, column=0, sticky="ew")
        self.add_files_btn = ttk.Button(toolbar, text="Add files", command=self._add_files)
        self.add_files_btn.pack(side="left")
        add_tooltip(self.add_files_btn, "Import one or more files. You can also drop them from Explorer onto this window.")
        self.add_folder_btn = ttk.Button(toolbar, text="Add folder", command=self._add_folder)
        self.add_folder_btn.pack(side="left", padx=6)
        add_tooltip(self.add_folder_btn, "Import the files of a folder (build noise and caches are skipped, up to 2000 files).")
        self.remove_btn = ttk.Button(toolbar, text="Remove", command=self._remove_selected, state="disabled")
        self.remove_btn.pack(side="left", padx=(6, 0))
        self.clear_btn = ttk.Button(toolbar, text="Clear", command=self._clear)
        self.clear_btn.pack(side="left", padx=6)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        self.analyze_btn = ttk.Button(toolbar, text="Analyze", command=self._analyze_all, state="disabled")
        self.analyze_btn.pack(side="left")
        add_tooltip(self.analyze_btn, "Identify every imported file and report its format, architecture, metadata and supported operations.")
        self.decompile_btn = ttk.Button(
            toolbar, text="Decompile", style="Accent.TButton", command=self._decompile, state="disabled"
        )
        self.decompile_btn.pack(side="left", padx=8)
        add_tooltip(
            self.decompile_btn,
            "Run the real backend for each file and build an editable project in a new folder.\n"
            "The imported files are never executed.",
        )
        self.export_btn = ttk.Button(toolbar, text="Export project...", command=self._export, state="disabled")
        self.export_btn.pack(side="left")
        add_tooltip(self.export_btn, "Save the reconstructed project to the folder you choose.")
        self.recompile_btn = ttk.Button(
            toolbar, text="Recompile project", style="Accent.TButton", command=self._recompile, state="disabled"
        )
        self.recompile_btn.pack(side="left", padx=8)
        add_tooltip(
            self.recompile_btn,
            "Build a NEW binary from the recovered source with the real toolchain.\n"
            "Original signatures and binary layout are not preserved.",
        )
        self.cancel_btn = ttk.Button(toolbar, text="Cancel", command=self._cancel_work, state="disabled")
        self.cancel_btn.pack(side="left")

        # ------------------------------------------------------- second row
        info = ttk.Frame(self, padding=(16, 0, 16, 6))
        info.grid(row=1, column=0, sticky="ew")
        info.columnconfigure(1, weight=1)
        self.summary_var = tk.StringVar(
            value="Import files or drop them here. Analysis is read-only; nothing is executed."
        )
        ttk.Label(info, textvariable=self.summary_var, style="Muted.TLabel", wraplength=860, justify="left").grid(
            row=0, column=0, sticky="w"
        )
        self.backend_var = tk.StringVar(value="")
        ttk.Label(info, textvariable=self.backend_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="e", padx=(12, 0)
        )
        self.install_ilspy_btn = ttk.Button(
            info, text="Prepare .NET decompiler", command=self._install_ilspy, state="disabled"
        )
        self.install_ilspy_btn.grid(row=0, column=2, sticky="e", padx=(8, 0))
        add_tooltip(
            self.install_ilspy_btn,
            "Install the verified ILSpy command-line tool into this application's data folder "
            f"(dotnet tool install ilspycmd --version {ILSPY_VERSION}).",
        )

        # -------------------------------------------------------- main split
        split = ttk.PanedWindow(self, orient="horizontal")
        split.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 8))

        left = ttk.Frame(split, style="Card.TFrame", padding=(10, 10))
        split.add(left, weight=1)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)
        ttk.Label(left, text="IMPORTED FILES", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.count_var = tk.StringVar(value="0 files")
        ttk.Label(left, textvariable=self.count_var, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 6))
        self.files_tree = make_tree(
            left,
            [("name", "File", 170), ("format", "Detected format", 190), ("status", "State", 90)],
            height=12,
        )
        self.files_tree.grid(row=2, column=0, sticky="nsew")
        with_scrollbar(left, self.files_tree).grid(row=2, column=1, sticky="ns")
        self.files_tree.bind("<<TreeviewSelect>>", lambda _e: self._update_buttons())
        self.files_tree.bind("<Double-1>", lambda _e: self._show_selected_analysis())

        hint = ttk.Label(
            left,
            text="Tip: drag files from Explorer onto the window. Specialised decompilation is "
                 "offered only when a compatible backend exists.",
            style="Muted.TLabel", wraplength=300, justify="left",
        )
        hint.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        right = ttk.Frame(split)
        split.add(right, weight=3)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        self.tabs = ttk.Notebook(right)
        self.tabs.grid(row=0, column=0, sticky="nsew")

        # --- project tab ---------------------------------------------------
        project = ttk.Frame(self.tabs, padding=8)
        project.columnconfigure(0, weight=1)
        project.rowconfigure(1, weight=1)
        self.project_hint = tk.StringVar(value="No project reconstructed yet.")
        ttk.Label(project, textvariable=self.project_hint, style="Muted.TLabel", wraplength=800).grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        self.project_tree = make_tree(
            project,
            [("path", "Recovered file", 340), ("category", "Category", 90), ("status", "Status", 150)],
            height=14,
        )
        self.project_tree.grid(row=1, column=0, sticky="nsew")
        with_scrollbar(project, self.project_tree).grid(row=1, column=1, sticky="ns")
        self.project_tree.bind("<Double-1>", lambda _e: self._open_project_item())
        self.tabs.add(project, text="Project")

        # --- analysis tab --------------------------------------------------
        analysis = ttk.Frame(self.tabs, padding=8)
        analysis.columnconfigure(0, weight=1)
        analysis.rowconfigure(1, weight=1)
        analyze_bar = ttk.Frame(analysis)
        analyze_bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(analyze_bar, text="Search across recovered files...", command=self._search_all).pack(side="left")
        ttk.Button(analyze_bar, text="Open output folder", command=self._open_output_folder).pack(side="left", padx=6)
        self.analysis_box = _readonly_text(analysis, "", height=18)
        self.analysis_box.grid(row=1, column=0, sticky="nsew")
        analysis_scroll = ttk.Scrollbar(analysis, orient="vertical", command=self.analysis_box.yview)
        analysis_scroll.grid(row=1, column=1, sticky="ns")
        self.analysis_box.configure(yscrollcommand=analysis_scroll.set)
        self.tabs.add(analysis, text="Analysis")

        # --- editor tab ----------------------------------------------------
        editor_page = ttk.Frame(self.tabs, padding=8)
        editor_page.columnconfigure(0, weight=1)
        editor_page.rowconfigure(0, weight=1)
        self.editor_tabs = EditorTabs(
            editor_page,
            backup_dir=self.ctx.config.work_dir / "editor-backups",
            on_status=self._editor_status,
        )
        self.editor_tabs.grid(row=0, column=0, sticky="nsew")
        self.tabs.add(editor_page, text="Editor")

        # --- resources tab -------------------------------------------------
        resources = ttk.Frame(self.tabs, padding=8)
        resources.columnconfigure(0, weight=1)
        resources.rowconfigure(0, weight=1)
        self.resources_tree = make_tree(
            resources,
            [("path", "Resource", 400), ("size", "Size", 100), ("status", "Type", 140)],
            height=16,
        )
        self.resources_tree.grid(row=0, column=0, sticky="nsew")
        with_scrollbar(resources, self.resources_tree).grid(row=0, column=1, sticky="ns")
        self.resources_tree.bind("<Double-1>", lambda _e: self._open_resource())
        self.tabs.add(resources, text="Resources")

        # --- log tab -------------------------------------------------------
        log_page = ttk.Frame(self.tabs, padding=8)
        log_page.columnconfigure(0, weight=1)
        log_page.rowconfigure(0, weight=1)
        self.log = LogConsole(log_page, height=16)
        self.log.grid(row=0, column=0, sticky="nsew")
        self.tabs.add(log_page, text="Log")

        # Options row under the tabs.
        options = ttk.Frame(right)
        options.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.include_disassembly_var = tk.BooleanVar(value=True)
        self.extract_resources_var = tk.BooleanVar(value=True)
        self.build_exe_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text="Disassemble when supported", variable=self.include_disassembly_var).pack(side="left")
        ttk.Checkbutton(options, text="Extract resources", variable=self.extract_resources_var).pack(side="left", padx=10)
        ttk.Checkbutton(options, text="Python: build .exe on recompile", variable=self.build_exe_var).pack(side="left", padx=10)

        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel", padding=(16, 0, 16, 8)).grid(
            row=3, column=0, sticky="w"
        )

        self._refresh_backends()
        self._update_buttons()
        self.log.append("DECOMPILER ready. Import a file to begin; the imported file is never executed.", "muted")

    # ----------------------------------------------------------- lifecycle
    def attach_drag_and_drop(self) -> None:
        if self._drop_enabled:
            return
        self._drop_enabled = enable_file_drop(self.winfo_toplevel(), self._on_drop)

    def _on_drop(self, paths: list[str]) -> None:
        self.log.append(f"Dropped {len(paths)} item(s).", "accent")
        self._add_paths([Path(p) for p in paths])

    # ------------------------------------------------------------- buttons
    def _update_buttons(self) -> None:
        has_files = bool(self._analyses)
        has_result = self._result is not None and bool(self._result.files)
        selected = bool(self.files_tree.selection()) if hasattr(self, "files_tree") else False
        idle = not self._busy
        self.remove_btn.configure(state="normal" if selected and idle else "disabled")
        self.clear_btn.configure(state="normal" if has_files and idle else "disabled")
        self.analyze_btn.configure(state="normal" if has_files and idle else "disabled")
        self.decompile_btn.configure(state="normal" if has_files and idle else "disabled")
        self.export_btn.configure(state="normal" if has_result and idle else "disabled")
        self.recompile_btn.configure(state="normal" if has_result and idle else "disabled")
        self.cancel_btn.configure(state="normal" if self._busy else "disabled")
        self.add_files_btn.configure(state="normal" if idle else "disabled")
        self.add_folder_btn.configure(state="normal" if idle else "disabled")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        if message:
            self.status_var.set(message)
            self.ctx.status.set(message)
            if busy:
                self.ctx.status.set_indeterminate()
        if not busy:
            self.ctx.status.reset()
        self._update_buttons()

    def _cancel_work(self) -> None:
        self._cancel.set()
        self.log.append("Cancellation requested; waiting for the current step to stop...", "warn")
        self.status_var.set("Cancelling...")

    def _editor_status(self, message: str) -> None:
        self.status_var.set(message)
        self.ctx.status.set(message)

    # ------------------------------------------------------------- importing
    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self,
            title="Import files for analysis",
            filetypes=[("All files", "*.*")],
        )
        if paths:
            self._add_paths([Path(path) for path in paths])

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Import every file in a folder")
        if not folder:
            return
        self._add_paths([Path(folder)])

    def _add_paths(self, paths: list[Path]) -> None:
        if self._busy:
            messagebox.showinfo(APP_NAME, "Wait for the running task to finish before importing more files.", parent=self)
            return
        expanded = expand_inputs(paths, max_files=MAX_ANALYZE_FILES)
        fresh = [path for path in expanded if str(path) not in self._imported]
        if not fresh:
            self.status_var.set("Those files are already imported.")
            return
        for path in fresh:
            self._imported.add(str(path))
            self.files_tree.insert(
                "", "end", iid=str(path),
                values=(path.name, "(analysing...)", "new"),
                tags=("new",),
            )
        self.files_tree.tag_configure("ok", foreground=COLORS["ok"])
        self.files_tree.tag_configure("warn", foreground=COLORS["warn"])
        self.files_tree.tag_configure("err", foreground=COLORS["err"])
        self.files_tree.tag_configure("new", foreground=COLORS["muted"])
        self.count_var.set(f"{len(self._imported)} file(s)")
        self.log.append(f"Importing {len(fresh)} file(s).", "accent")
        self._update_buttons()
        self._analyze_paths(fresh)

    def _analyze_paths(self, paths: list[Path]) -> None:
        if not paths:
            return
        self._set_busy(True, f"Analysing {len(paths)} file(s)...")
        self._cancel.clear()

        def work():
            results = []
            for index, path in enumerate(paths):
                if self._cancel.is_set():
                    break
                self.ctx.runner.post(
                    lambda i=index, p=path: self.status_var.set(f"Analysing {p.name} ({i + 1}/{len(paths)})")
                )
                try:
                    results.append(analyze_file(path))
                except Exception as exc:
                    results.append((path, exc))
            return results

        def done(results) -> None:
            failures = 0
            for item in results:
                if isinstance(item, tuple):
                    path, exc = item
                    failures += 1
                    self.files_tree.item(
                        str(path), values=(path.name, "unreadable", "error"), tags=("err",)
                    )
                    self.log.append(f"{path.name}: {exc}", "err")
                    continue
                self._analyses[str(item.path)] = item
                fmt = item.format
                self.files_tree.item(
                    str(item.path),
                    values=(item.name, fmt.label, f"{int(fmt.confidence * 100)}%"),
                    tags=("ok" if fmt.confidence >= 0.8 else "warn",),
                )
                self.log.append(
                    f"{item.name}: {fmt.label} · {fmt.language} · {fmt.architecture} · "
                    f"ops: {', '.join(fmt.operations)}",
                    "info",
                )
                for warning in item.warnings:
                    self.log.append(f"  {warning}", "warn")
                self._append_analysis(item)
            self._update_buttons()
            self._set_busy(False)
            self.summary_var.set(
                f"{len(self._analyses)} file(s) imported, {failures} unreadable. "
                "Analysis is read-only; press Decompile to reconstruct a project."
            )
            self.count_var.set(f"{len(self._analyses)} file(s)")
            self.status_var.set("Analysis finished.")
            self.ctx.status.set("Analysis finished.")

        def fail(exc: BaseException) -> None:
            self._set_busy(False)
            self.log.append(f"Analysis failed: {exc}", "err")
            self.status_var.set("Analysis failed.")
            messagebox.showerror(APP_NAME, f"Analysis failed:\n{exc}", parent=self)

        self.ctx.runner.run(work, done, fail)

    def _append_analysis(self, analysis) -> None:
        text = "\n".join(analysis.summary_lines()) + "\n" + ("-" * 78) + "\n\n"
        self.analysis_box.configure(state="normal")
        self.analysis_box.insert("end", text)
        self.analysis_box.see("end")
        self.analysis_box.configure(state="disabled")

    def _show_selected_analysis(self) -> None:
        selection = self.files_tree.selection()
        if not selection:
            return
        analysis = self._analyses.get(selection[0])
        if analysis is None:
            return
        self.tabs.select(1)
        self.analysis_box.configure(state="normal")
        self.analysis_box.delete("1.0", "end")
        self.analysis_box.insert("1.0", "\n".join(analysis.summary_lines()) + "\n")
        self.analysis_box.configure(state="disabled")

    def _analyze_all(self) -> None:
        paths = [Path(item.path) for item in self._analyses.values()]
        if not paths:
            messagebox.showinfo(APP_NAME, "Import a file first.", parent=self)
            return
        self.analysis_box.configure(state="normal")
        self.analysis_box.delete("1.0", "end")
        self.analysis_box.configure(state="disabled")
        self._analyze_paths(paths)

    def _remove_selected(self) -> None:
        selection = self.files_tree.selection()
        for iid in selection:
            self.files_tree.delete(iid)
            self._analyses.pop(iid, None)
            self._imported.discard(iid)
        self.count_var.set(f"{len(self._imported)} file(s)")
        self.summary_var.set(f"{len(self._analyses)} file(s) imported.")
        self._update_buttons()

    def _clear(self) -> None:
        if self._imported and not messagebox.askokcancel(
            APP_NAME, "Remove every imported file from this workspace?\n\nThe files on disk are not touched.", parent=self
        ):
            return
        self.files_tree.delete(*self.files_tree.get_children())
        self.project_tree.delete(*self.project_tree.get_children())
        self.resources_tree.delete(*self.resources_tree.get_children())
        self._analyses.clear()
        self._imported.clear()
        self._result = None
        self._exported_root = None
        self.count_var.set("0 files")
        self.summary_var.set("Import files or drop them here. Analysis is read-only; nothing is executed.")
        self.project_hint.set("No project reconstructed yet.")
        self.analysis_box.configure(state="normal")
        self.analysis_box.delete("1.0", "end")
        self.analysis_box.configure(state="disabled")
        self._update_buttons()

    # ------------------------------------------------------------- decompile
    def _decompile(self) -> None:
        if not self._analyses:
            messagebox.showinfo(APP_NAME, "Import and analyse a file first.", parent=self)
            return
        base = _unique_dir(self.ctx.config.work_dir / "decompiled" / f"{time.strftime('%Y%m%d-%H%M%S')}")
        chosen = filedialog.askdirectory(
            parent=self,
            title="Choose where to write the reconstructed project (Cancel uses the work folder)",
            initialdir=str(self.ctx.config.output_dir),
        )
        if chosen:
            stem = next(iter(self._analyses.values())).path.stem
            base = _unique_dir(Path(chosen) / f"{stem}-recovered")
        options = DecompileOptions(
            disassemble=self.include_disassembly_var.get(),
            extract_resources=self.extract_resources_var.get(),
        )
        self._decompile_options = options
        self._cancel.clear()
        self._set_busy(True, "Decompiling...")
        self.log.append(f"Starting decompilation into {base}", "accent")
        paths = [item.path for item in self._analyses.values()]

        def work():
            session = DecompileSession(options)
            return session.decompile(
                paths,
                base,
                progress=lambda message, done, total: self.ctx.runner.post(
                    lambda m=message, d=done, t=total: self._progress(m, d, t)
                ),
                log_callback=lambda message, tag: self.ctx.runner.post(
                    lambda m=message, g=tag: self.log.append(m, g)
                ),
                cancel=self._cancel,
            )

        def done(result: DecompileResult) -> None:
            self._set_busy(False)
            self._result = result
            self._exported_root = None
            self._fill_project(result)
            self.project_hint.set(
                f"{len(result.sources)} source file(s), {len(result.resources)} resource(s) · "
                f"project kind: {result.project_kind or 'unknown'} · "
                f"recompilable as-is: {'yes' if result.recompile_ready else 'no'}"
            )
            self.summary_var.set(
                f"Reconstructed {len(result.files)} file(s) into {result.output_dir}"
            )
            self.status_var.set("Decompilation finished.")
            self.ctx.status.set("Decompilation finished.")
            self.log.append(
                f"Recovered {len(result.files)} file(s) into {result.output_dir}", "ok"
            )
            self._update_buttons()
            if result.errors:
                messagebox.showwarning(
                    APP_NAME,
                    "Decompilation finished with errors:\n\n" + "\n".join(result.errors[:10]),
                    parent=self,
                )
            self.tabs.select(0)

        def fail(exc: BaseException) -> None:
            self._set_busy(False)
            self.log.append(f"Decompilation failed: {exc}", "err")
            self.status_var.set("Decompilation failed.")
            messagebox.showerror(APP_NAME, f"Decompilation failed:\n{exc}", parent=self)

        self.ctx.runner.run(work, done, fail)

    def _progress(self, message: str, done: int, total: int) -> None:
        self.status_var.set(message)
        self.ctx.status.set(message)
        if total > 0:
            self.ctx.status.set_progress(done + 1, total)

    def _fill_project(self, result: DecompileResult) -> None:
        self.project_tree.delete(*self.project_tree.get_children())
        self.project_tree.tag_configure("source", foreground=COLORS["ok"])
        self.project_tree.tag_configure("generated", foreground=COLORS["warn"])
        self.project_tree.tag_configure("resource", foreground=COLORS["muted"])
        self.project_tree.tag_configure("config", foreground=COLORS["accent"])
        for index, item in enumerate(result.files):
            if item.category == CATEGORY_RESOURCE:
                continue
            tag = {
                CATEGORY_SOURCE: "source",
                CATEGORY_CONFIG: "config",
                CATEGORY_DISASSEMBLY: "generated",
                CATEGORY_METADATA: "resource",
            }.get(item.category, "resource")
            self.project_tree.insert(
                "", "end", iid=str(index),
                values=(item.path, item.category, STATUS_LABELS.get(item.status, item.status)),
                tags=(tag,),
            )
        self.resources_tree.delete(*self.resources_tree.get_children())
        for index, item in enumerate(result.files):
            if item.category != CATEGORY_RESOURCE:
                continue
            self.resources_tree.insert(
                "", "end", iid=str(index),
                values=(item.path, f"{item.size:,} B", STATUS_LABELS.get(item.status, item.status)),
            )
        sources = [item for item in result.files if item.category == CATEGORY_SOURCE and item.is_text]
        if sources:
            first = sources[0]
            if first.disk_path is not None:
                self.editor_tabs.open_file(first.disk_path, language=first.language or language_from_path(first.disk_path))
                self.tabs.select(2)

    def _open_project_item(self) -> None:
        selection = self.project_tree.selection()
        if not selection or self._result is None:
            return
        try:
            item = self._result.files[int(selection[0])]
        except (ValueError, IndexError):
            return
        if item.disk_path is not None and item.is_text and item.size <= MAX_EDITOR_BYTES:
            self.editor_tabs.open_file(item.disk_path, language=item.language or language_from_path(item.disk_path))
            self.tabs.select(2)
            return
        self._show_file_info(item)

    def _open_resource(self) -> None:
        selection = self.resources_tree.selection()
        if not selection or self._result is None:
            return
        try:
            item = self._result.files[int(selection[0])]
        except (ValueError, IndexError):
            return
        suffix = Path(item.path).suffix.lower()
        if item.disk_path is not None and suffix in TEXT_SUFFIXES and item.size <= MAX_EDITOR_BYTES:
            self.editor_tabs.open_file(item.disk_path, language=language_from_path(item.disk_path))
            self.tabs.select(2)
            return
        self._show_file_info(item)

    def _show_file_info(self, item: RecoveredFile) -> None:
        details = (
            f"{item.path}\n\nCategory: {item.category}\n"
            f"Status: {STATUS_LABELS.get(item.status, item.status)}\n"
            f"Size: {item.size:,} bytes\nLanguage: {item.language or 'binary'}\n\n"
            f"Provenance: {item.provenance or '(not recorded)'}\n\n"
            "This is not a text file, so it is not opened in the editor."
        )
        if item.disk_path is not None and messagebox.askyesno(
            APP_NAME, details + "\n\nOpen the containing folder?", parent=self
        ):
            _open_folder(item.disk_path.parent)

    def _open_output_folder(self) -> None:
        folder = self._result.output_dir if self._result else self.ctx.config.output_dir
        _open_folder(folder)

    # ---------------------------------------------------------------- export
    def _export(self) -> None:
        if self._result is None:
            return
        destination = filedialog.askdirectory(
            parent=self, title="Export the reconstructed project to a folder"
        )
        if not destination:
            return
        target = Path(destination)
        try:
            non_empty = target.exists() and any(target.iterdir())
        except OSError:
            non_empty = False
        if non_empty and not messagebox.askokcancel(
            APP_NAME,
            f"{target} is not empty.\n\nRecovered files are written into it and existing "
            "files with the same names are replaced.\n\nContinue?",
            parent=self,
        ):
            return
        self._set_busy(True, "Exporting the reconstructed project...")

        def work():
            return self._result.export(target)

        def done(written) -> None:
            self._set_busy(False)
            self._exported_root = target
            self.log.append(f"Exported {len(written)} file(s) to {target}", "ok")
            self.status_var.set(f"Exported {len(written)} file(s).")
            self.recompile_btn.configure(state="normal")
            if messagebox.askyesno(APP_NAME, f"Exported {len(written)} file(s).\n\nOpen the folder?", parent=self):
                _open_folder(target)

        def fail(exc: BaseException) -> None:
            self._set_busy(False)
            messagebox.showerror(APP_NAME, f"Export failed:\n{exc}", parent=self)

        self.ctx.runner.run(work, done, fail)

    # ------------------------------------------------------------- recompile
    def _recompile(self) -> None:
        if self._result is None:
            return
        root = self._exported_root if self._exported_root is not None else self._result.output_dir
        if not root.is_dir():
            messagebox.showerror(APP_NAME, "The reconstructed project folder no longer exists.", parent=self)
            return
        output = _unique_dir(self.ctx.config.output_dir / "recompiled" / f"{root.name}-{time.strftime('%H%M%S')}")
        plan = plan_recompile(root, output, build_exe=self.build_exe_var.get())
        lines = plan.summary_lines()
        if not plan.ready:
            messagebox.showwarning(
                APP_NAME,
                "This project cannot be recompiled as it is:\n\n" + "\n".join(plan.issues)
                + "\n\nNo command was run.",
                parent=self,
            )
            return
        script_steps = [step for step in plan.steps if step.requires_approval]
        message = (
            "Recompile the recovered project?\n\n"
            + "\n".join(lines)
            + "\n\nThis builds a NEW binary. Original signatures, metadata and binary "
              "layout are not preserved."
        )
        if not messagebox.askokcancel(APP_NAME, message, parent=self):
            return
        consent = False
        if script_steps:
            consent = messagebox.askyesno(
                APP_NAME,
                "This build executes project-defined scripts or packagers:\n\n"
                + "\n".join(" ".join(step.cmd_args) for step in script_steps[:5])
                + "\n\nAllow them to run?",
                parent=self,
            )
            if not consent:
                return
        self._cancel.clear()
        build_exe = self.build_exe_var.get()
        self._set_busy(True, "Recompiling...")
        self.log.append(f"Recompiling {root} into {output}", "accent")

        def work():
            return recompile(
                root,
                output,
                build_exe=build_exe,
                consent_granted=consent,
                progress=lambda message, done, total: self.ctx.runner.post(
                    lambda m=message, d=done, t=total: self._progress(m, d, t)
                ),
                cancel=self._cancel,
                config=self.ctx.config,
            )

        def done(result) -> None:
            self._set_busy(False)
            for line in result.summary_lines():
                self.log.append(line, "ok" if result.ok else "err")
            self.status_var.set(
                "Recompilation succeeded." if result.ok else "Recompilation did not produce a verified artifact."
            )
            RecompileResultDialog(self, result)

        def fail(exc: BaseException) -> None:
            self._set_busy(False)
            self.log.append(f"Recompilation failed: {exc}", "err")
            messagebox.showerror(APP_NAME, f"Recompilation failed:\n{exc}", parent=self)

        self.ctx.runner.run(work, done, fail)

    # ---------------------------------------------------------------- search
    def _search_all(self) -> None:
        if self._result is None:
            messagebox.showinfo(APP_NAME, "Decompile a file first.", parent=self)
            return
        SearchDialog(self, self._result, self.editor_tabs, self.tabs)

    # -------------------------------------------------------------- backends
    def _refresh_backends(self) -> None:
        ilspy = find_ilspy(getattr(self._decompile_options, "ilspy_path", ""))
        javap = find_javap()
        dotnet = shutil.which("dotnet")
        parts = [
            f"ILSpy: {'ready' if ilspy else 'missing'}",
            f"javap: {'ready' if javap else 'missing'}",
            f"disassembler: {'Capstone' if capstone_available() else 'missing'}",
            f"PE extras: {'pefile' if importlib.util.find_spec('pefile') else 'missing'}",
        ]
        self.backend_var.set("  ·  ".join(parts))
        self.install_ilspy_btn.configure(
            state="normal" if (dotnet and not ilspy and not self._busy) else "disabled"
        )

    def _install_ilspy(self) -> None:
        if not messagebox.askokcancel(
            APP_NAME,
            "Install ILSpy into this application's data folder?\n\n"
            f"dotnet tool install ilspycmd --version {ILSPY_VERSION} --tool-path <app data>\\tools\\ilspycmd\n\n"
            "This downloads the tool with the .NET SDK already installed on this PC. "
            "It is used only to decompile .NET assemblies you import.",
            parent=self,
        ):
            return
        self._set_busy(True, "Installing ILSpy...")
        self.log.append("Installing ILSpy with dotnet tool install...", "accent")

        def progress(message: str) -> None:
            self.ctx.runner.post(lambda: self.log.append(message, "muted"))

        def work():
            return install_ilspy(progress=progress)

        def done(payload) -> None:
            self._set_busy(False)
            ok, path, output = payload
            for line in (output or "").splitlines()[-20:]:
                self.log.append(f"  {line}", "muted")
            if ok:
                self.log.append(f"ILSpy installed at {path}", "ok")
                self._refresh_backends()
                self.status_var.set("ILSpy is ready. Re-run Decompile on a .NET assembly.")
            else:
                self.log.append(f"ILSpy installation failed: {output}", "err")
                messagebox.showerror(APP_NAME, f"ILSpy could not be installed:\n\n{output[-1500:]}", parent=self)

        def fail(exc: BaseException) -> None:
            self._set_busy(False)
            messagebox.showerror(APP_NAME, f"ILSpy installation failed:\n{exc}", parent=self)

        self.ctx.runner.run(work, done, fail)


class SearchDialog(tk.Toplevel):
    """Search every recovered text file and open a hit in the editor."""

    def __init__(self, panel: DecompilerPanel, result: DecompileResult,
                 editor: EditorTabs, tabs: ttk.Notebook) -> None:
        super().__init__(panel)
        self.title(f"{APP_NAME} — Search recovered files")
        self.geometry("900x520")
        self.configure(bg=COLORS["bg"])
        enable_dark_titlebar(self)
        self.panel = panel
        self.result = result
        self.editor = editor
        self.tabs = tabs
        self.matches: list[tuple[RecoveredFile, int, str]] = []

        bar = ttk.Frame(self, padding=10)
        bar.pack(fill="x")
        ttk.Label(bar, text="Search").pack(side="left", padx=(0, 6))
        self.var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.var, width=48)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e: self._run())
        ttk.Button(bar, text="Search", command=self._run).pack(side="left", padx=6)
        self.status = tk.StringVar(value="Searches recovered text files only.")
        ttk.Label(bar, textvariable=self.status, style="Muted.TLabel").pack(side="left", padx=8)
        entry.focus_set()

        body = ttk.Frame(self, padding=(10, 0))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.tree = make_tree(
            body,
            [("file", "File", 300), ("line", "Line", 60), ("text", "Match", 480)],
            height=18,
        )
        self.tree.grid(row=0, column=0, sticky="nsew")
        with_scrollbar(body, self.tree).grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", lambda _e: self._open())

    def _run(self) -> None:
        term = self.var.get().strip()
        if not term:
            return
        self.tree.delete(*self.tree.get_children())
        self.matches.clear()
        limit = 500
        for item in self.result.files:
            if len(self.matches) >= limit:
                break
            if item.category not in (CATEGORY_SOURCE, CATEGORY_CONFIG, CATEGORY_METADATA, CATEGORY_DISASSEMBLY):
                continue
            if not item.is_text or item.size > MAX_EDITOR_BYTES:
                continue
            text = item.read_text()
            for number, line in enumerate(text.splitlines(), start=1):
                if term.lower() in line.lower():
                    self.matches.append((item, number, line.strip()[:200]))
                    self.tree.insert("", "end", iid=str(len(self.matches) - 1),
                                     values=(item.path, number, line.strip()[:200]))
                    if len(self.matches) >= limit:
                        break
        self.status.set(f"{len(self.matches)} match(es). Double-click to open.")

    def _open(self) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        try:
            item, line, _text = self.matches[int(selection[0])]
        except (ValueError, IndexError):
            return
        if item.disk_path is None:
            return
        buffer = self.editor.open_file(item.disk_path, language=item.language or language_from_path(item.disk_path))
        if buffer is not None and buffer.text_widget is not None:
            buffer.text_widget.mark_set("insert", f"{line}.0")
            buffer.text_widget.see(f"{line}.0")
            self.tabs.select(2)
        self.destroy()


class RecompileResultDialog(tk.Toplevel):
    """Honest build report: artifacts only when they really exist on disk."""

    def __init__(self, panel: DecompilerPanel, result) -> None:
        super().__init__(panel.winfo_toplevel())
        self.title(f"{APP_NAME} — Recompilation report")
        self.geometry("820x520")
        self.configure(bg=COLORS["bg"])
        enable_dark_titlebar(self)
        self.result = result
        header = ttk.Frame(self, style="Header.TFrame", padding=(12, 8))
        header.pack(fill="x")
        ttk.Label(
            header,
            text="Recompilation succeeded" if result.ok else "Recompilation did not produce a verified artifact",
            style="Dialog.TLabel",
        ).pack(side="left")
        box = _readonly_text(self, "\n".join(result.summary_lines()), height=18)
        box.pack(fill="both", expand=True, padx=12, pady=12)
        row = ttk.Frame(self, padding=(12, 0, 12, 12))
        row.pack(fill="x")
        ttk.Button(row, text="Copy report", command=lambda: self._copy(box)).pack(side="left")
        if result.output_dir.is_dir():
            ttk.Button(row, text="Open output folder", command=lambda: _open_folder(result.output_dir)).pack(
                side="left", padx=6
            )
        ttk.Button(row, text="Close", style="Ghost.TButton", command=self.destroy).pack(side="right")

    def _copy(self, box: tk.Text) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(box.get("1.0", "end").strip())
        except tk.TclError:  # pragma: no cover
            pass


__all__ = ["DecompilerPanel", "SearchDialog", "RecompileResultDialog"]
