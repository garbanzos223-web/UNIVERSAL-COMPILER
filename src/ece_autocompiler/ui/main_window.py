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
import sys
import threading
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..build import BuildOrchestrator
from ..build import history as build_history
from ..config import AppConfig
from ..container import validate_container
from ..detection import discover_all, scan
from ..detection.detector import ADAPTER_LABELS
from ..diagnostics import classify, first_actionable, summarize
from ..logging_config import get_logger
from ..security import sanitize_filename
from ..version import APP_NAME, APP_VERSION, CONTAINER_FORMAT_VERSION
from .theme import COLORS, apply_theme, configure_root_window
from .widgets import (
    LogConsole,
    StatusBar,
    TaskRunner,
    make_path_row,
    make_tree,
    with_scrollbar,
)

log = get_logger("ui")


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
        self._cancel = threading.Event()
        self._building = False

        self._build_menu(master)

        header = ttk.Frame(self, padding=(16, 14, 16, 0))
        header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(
            header, text=f"v{APP_VERSION}  ·  container v{CONTAINER_FORMAT_VERSION}",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=(10, 0))

        form = ttk.Frame(self, padding=(16, 12, 16, 0))
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        self.project_row = make_path_row(form, "Project folder", browse_command=self._browse_project)
        self.project_row.frame.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.project_row.var.trace_add("write", lambda *_: self._on_project_changed())

        detect = ttk.Frame(form)
        detect.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        detect.columnconfigure(1, weight=1)
        ttk.Label(detect, text="Detected", width=14).grid(row=0, column=0, sticky="w")
        self.detected_var = tk.StringVar(value="(not scanned yet)")
        ttk.Label(detect, textvariable=self.detected_var).grid(row=0, column=1, sticky="w")
        ttk.Button(detect, text="Rescan", command=self._detect).grid(row=0, column=2, sticky="e")

        # --- Output mode -----------------------------------------------------
        mode_row = ttk.Frame(form)
        mode_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Label(mode_row, text="Output", width=14).grid(row=0, column=0, sticky="w")
        self.mode_var = tk.StringVar(value="ece")
        ttk.Radiobutton(
            mode_row, text="Package as .ece", variable=self.mode_var, value="ece",
            command=self._on_mode_changed,
        ).grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(
            mode_row, text="Build .exe", variable=self.mode_var, value="exe",
            command=self._on_mode_changed,
        ).grid(row=0, column=2, sticky="w", padx=(16, 0))
        self.exe_hint = ttk.Label(mode_row, text="", style="Muted.TLabel")
        self.exe_hint.grid(row=0, column=3, sticky="w", padx=(12, 0))

        out = ttk.Frame(form)
        out.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        out.columnconfigure(1, weight=1)
        self.output_label_var = tk.StringVar(value="Output .ece")
        ttk.Label(out, textvariable=self.output_label_var, width=14).grid(row=0, column=0, sticky="w")
        self.output_var = tk.StringVar()
        ttk.Entry(out, textvariable=self.output_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(out, text="Save as...", command=self._browse_output).grid(row=0, column=2, sticky="e", padx=(8, 0))

        # --- Options (adapt to the selected mode) ---------------------------
        opts = ttk.Frame(form)
        opts.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.consent_var = tk.BooleanVar(value=False)
        self.overwrite_var = tk.BooleanVar(value=False)
        self.skip_install_var = tk.BooleanVar(value=True)

        # .ece options
        self.ece_opts = ttk.Frame(opts)
        self.compression_var = tk.StringVar(value="zlib")
        ttk.Label(self.ece_opts, text="Compression").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Combobox(
            self.ece_opts, textvariable=self.compression_var, values=("zlib", "lzma", "store"),
            state="readonly", width=8,
        ).grid(row=0, column=1, sticky="w")
        ttk.Checkbutton(self.ece_opts, text="Skip dependency install", variable=self.skip_install_var).grid(row=0, column=2, sticky="w", padx=(12, 0))

        # .exe options
        self.exe_opts = ttk.Frame(opts)
        self.console_var = tk.BooleanVar(value=False)
        self.onefile_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.exe_opts, text="Console window", variable=self.console_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(self.exe_opts, text="Single file", variable=self.onefile_var).grid(row=0, column=1, sticky="w", padx=(12, 0))

        # shared options
        ttk.Checkbutton(opts, text="Allow project scripts", variable=self.consent_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Checkbutton(opts, text="Overwrite output", variable=self.overwrite_var).grid(row=1, column=3, sticky="w")

        actions = ttk.Frame(form)
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        self.build_btn = ttk.Button(actions, text="Compile", style="Accent.TButton", command=self._build)
        self.build_btn.grid(row=0, column=0, sticky="w")
        self.cancel_btn = ttk.Button(actions, text="Cancel", command=self._cancel_build, state="disabled")
        self.cancel_btn.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(actions, text="Open output folder", command=self._open_output).grid(row=0, column=2, sticky="w")

        self.log = LogConsole(self, height=18)
        self.log.pack(fill="both", expand=True, padx=16, pady=(14, 0))

        ctx.status.pack(fill="x", side="bottom")

        self._on_mode_changed()
        self.log.append("Select a project folder, then press Compile.", "muted")

    # ------------------------------------------------------------------ menu
    def _build_menu(self, master: tk.Tk) -> None:
        menubar = tk.Menu(master)

        filem = tk.Menu(menubar, tearoff=0)
        filem.add_command(label="Open output folder", command=self._open_output)
        filem.add_command(label="Open work folder", command=lambda: _open_folder(self.ctx.config.work_dir))
        filem.add_separator()
        filem.add_command(label="Exit", command=master.destroy)
        menubar.add_cascade(label="File", menu=filem)

        toolsm = tk.Menu(menubar, tearoff=0)
        toolsm.add_command(label="Toolchains...", command=self._show_toolchains)
        toolsm.add_command(label="Build history...", command=self._show_history)
        menubar.add_cascade(label="Tools", menu=toolsm)

        helpm = tk.Menu(menubar, tearoff=0)
        helpm.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=helpm)

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

    def _detect(self) -> None:
        path = self.project_row.var.get().strip()
        if not path or not Path(path).exists():
            messagebox.showwarning(APP_NAME, "Choose an existing project folder first.")
            return
        self.ctx.status.set("Scanning project...")
        self.log.append(f"Scanning {path} ...", "accent")

        def work():
            return scan(Path(path))

        def done(result):
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
                    if best.adapter_id in ("unity", "unreal"):
                        self.log.append(
                            "Note: game-engine projects need their own editor/SDK to build; "
                            "this tool detects them but cannot produce an .exe by itself.",
                            "warn",
                        )
            else:
                self.detected_var.set("no supported project found")
                self.log.append(result.summary(), "warn")
            for note in result.notes:
                self.log.append(f"note: {note}", "muted")
            self.ctx.status.set("Ready.")

        self.ctx.runner.run(work, done, lambda e: self._fail("Detection failed", e))

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
        if self.consent_var.get() and not messagebox.askokcancel(
            APP_NAME,
            "This build may run scripts from the project (for example npm install and a build script).\n\n"
            "Only continue if you trust this project. Allow it?",
        ):
            return

        self._building = True
        self._cancel = threading.Event()
        self.build_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.log.clear()
        self.log.append(f"Building {project}", "accent")
        self.ctx.status.set("Building...")

        def progress(message: str, done: int, total: int) -> None:
            def apply():
                self.ctx.status.set(message)
                self.ctx.status.set_progress(done, total)
                self.log.append(f"[{done:>3}/{total}] {message}", "muted")
            self.ctx.runner.post(apply)

        if self.mode_var.get() == "exe":
            self._build_exe(Path(project), Path(output), progress)
        else:
            self._build_ece(Path(project), Path(output), progress)

    def _build_ece(self, project: Path, output: Path, progress) -> None:
        prefer = None
        if len(self._candidates) == 1:
            prefer = self._candidates[0].adapter_id
        overrides = {"skip_npm_install": True} if self.skip_install_var.get() else {}
        orchestrator = BuildOrchestrator(config=self.ctx.config)

        def work():
            return orchestrator.build_and_package(
                project,
                output,
                config_overrides=overrides,
                consent_granted=self.consent_var.get(),
                overwrite=self.overwrite_var.get(),
                compression=self.compression_var.get(),
                prefer_adapter=prefer,
                progress=progress,
                cancel=self._cancel,
            )

        self.ctx.runner.run(work, self._build_done, lambda e: self._fail("Build failed", e))

    def _build_exe(self, project: Path, output: Path, progress) -> None:
        from ..build.exe_exporter import compile_exe

        def work():
            return compile_exe(
                project,
                output=output,
                onefile=self.onefile_var.get(),
                console=self.console_var.get(),
                progress=progress,
                cancel=self._cancel,
                config=self.ctx.config,
            )

        self.ctx.runner.run(work, self._exe_done, lambda e: self._fail("Executable export failed", e))

    def _exe_done(self, result) -> None:
        self._finish_build()
        if not result.ok:
            self.log.append(f"Executable export failed: {result.message}", "err")
            for err in result.errors[:8]:
                self.log.append(f"  {err}", "err")
            if result.log_path:
                self.log.append(f"Log: {result.log_path}", "muted")
            self.ctx.status.set("Executable export failed.")
            return
        self.log.append(f"Executable created in {result.duration_seconds:.1f}s", "ok")
        self.log.append(f"  .exe: {result.exe_path}", "ok")
        size = result.exe_path.stat().st_size
        self.log.append(f"  size: {size:,} bytes", "ok")
        for note in result.notes:
            self.log.append(f"  {note}", "muted")
        self.ctx.status.set("Done.")
        build_history.append(
            build_history.make_record(
                Path(self.project_row.var.get()),
                ok=True,
                exit_code=result.exit_code,
                duration_seconds=result.duration_seconds,
                container_path=result.exe_path,
                message="Executable export succeeded",
            ),
            base=self.ctx.config.work_dir,
        )

    def _build_done(self, result) -> None:
        build_result, package = result
        self._finish_build()
        if not build_result.ok:
            self.log.append("Build failed.", "err")
            diags = classify("\n".join(build_result.errors))
            if diags:
                self.log.append(f"Diagnosis: {summarize(diags)}", "warn")
                best = first_actionable(diags)
                if best:
                    self.log.append(f"  {best.message}: {best.line}", "warn")
                    if best.hint:
                        self.log.append(f"  hint: {best.hint}", "muted")
            if build_result.log_path:
                self.log.append(f"Log: {build_result.log_path}", "muted")
            self.ctx.status.set("Build failed.")
            self._record(False, build_result, None)
            return

        if not package.ok:
            self.log.append(f"Packaging failed: {package.message}", "err")
            self.ctx.status.set("Packaging failed.")
            return

        self.log.append(f"Build succeeded in {build_result.duration_seconds:.1f}s", "ok")
        self.log.append(f"Package: {package.container_path}", "ok")
        report = validate_container(package.container_path, deep=True)
        if report.ok:
            self.log.append(
                f"Validation: OK ({report.entry_count} files, {report.total_uncompressed} bytes)", "ok"
            )
            self.ctx.status.set("Done.")
        else:
            self.log.append("Validation: FAILED", "err")
            for issue in report.errors:
                self.log.append(f"  {issue}", "err")
            self.ctx.status.set("Validation failed.")
        self._record(True, build_result, package.container_path)

    def _record(self, ok: bool, build_result, container_path) -> None:
        build_history.append(
            build_history.make_record(
                Path(self.project_row.var.get()),
                ok=ok,
                exit_code=build_result.exit_code,
                duration_seconds=build_result.duration_seconds,
                container_path=container_path,
                message="Build and package succeeded" if ok else "Build failed",
            ),
            base=self.ctx.config.work_dir,
        )

    def _cancel_build(self) -> None:
        self._cancel.set()
        self.log.append("Cancellation requested...", "warn")

    def _finish_build(self) -> None:
        self._building = False
        self.build_btn.configure(state="normal")
        self.cancel_btn.configure(state="disabled")
        self.ctx.status.reset()

    def _fail(self, title: str, exc: BaseException) -> None:
        self._finish_build()
        self.log.append(f"{title}: {exc}", "err")
        self.ctx.status.set(title)

    def _open_output(self) -> None:
        value = self.output_var.get().strip()
        target = Path(value).parent if value else self.ctx.config.output_dir
        _open_folder(target)

    # -------------------------------------------------------------- dialogs
    def _show_toolchains(self) -> None:
        ToolchainDialog(self.ctx)

    def _show_history(self) -> None:
        HistoryDialog(self.ctx)

    def _show_about(self) -> None:
        AboutDialog(self.ctx)


class _Dialog(tk.Toplevel):
    """A simple modal-ish dialog with a title bar."""

    def __init__(self, ctx: AppContext, title: str, *, width: int = 720, height: int = 420) -> None:
        super().__init__(ctx.root)
        self.ctx = ctx
        self.title(f"{APP_NAME} — {title}")
        self.geometry(f"{width}x{height}")
        self.configure(bg=COLORS["bg"])
        self.transient(ctx.root)
        ttk.Button(self, text="Close", command=self.destroy).pack(anchor="e", padx=12, pady=(10, 0))


class ToolchainDialog(_Dialog):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, "Toolchains")
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        tree = make_tree(body, [("name", "Toolchain", 180), ("status", "Status", 80),
                                ("version", "Version", 240), ("path", "Path", 300)])
        tree.grid(row=0, column=0, sticky="nsew")
        with_scrollbar(body, tree).grid(row=0, column=1, sticky="ns")
        tree.tag_configure("ok", foreground=COLORS["ok"])
        tree.tag_configure("miss", foreground=COLORS["muted"])

        def work():
            return discover_all()

        def done(tools):
            for tool in tools.values():
                tag = "ok" if tool.found else "miss"
                status = "found" if tool.found else "missing"
                version = tool.version or (tool.install_hint if not tool.found else "")
                tree.insert("", "end", values=(tool.name, status, version, tool.path or ""), tags=(tag,))

        self.ctx.runner.run(work, done, lambda e: self.ctx.status.set(f"Discovery failed: {e}"))


class HistoryDialog(_Dialog):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(ctx, "Build history")
        body = ttk.Frame(self, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        tree = make_tree(body, [("time", "Time (UTC)", 170), ("ok", "Result", 80),
                                ("project", "Project", 280), ("msg", "Message", 240)])
        tree.grid(row=0, column=0, sticky="nsew")
        with_scrollbar(body, tree).grid(row=0, column=1, sticky="ns")
        tree.tag_configure("ok", foreground=COLORS["ok"])
        tree.tag_configure("fail", foreground=COLORS["err"])
        for record in build_history.load(base=ctx.config.work_dir, limit=200):
            tag = "ok" if record.ok else "fail"
            tree.insert("", "end", values=(
                record.timestamp, "ok" if record.ok else "failed",
                record.project_path, record.message,
            ), tags=(tag,))


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


def _open_folder(path: Path) -> None:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{path}"')
        else:
            os.system(f'xdg-open "{path}"')
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
