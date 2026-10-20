"""Reusable Tkinter widgets and a background task runner.

The task runner keeps long operations (builds, scans, extraction) off the UI
thread. Worker threads push callbacks into a queue that the Tk main loop drains
through ``after`` timers, which is the only safe way to touch widgets.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import ttk
from typing import Any

from .theme import COLORS, FONTS, configure_text_widget


class TaskRunner:
    """Runs callables on daemon threads and marshals results back to Tk."""

    def __init__(self, root: tk.Misc, interval_ms: int = 80) -> None:
        self._root = root
        self._queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._interval = interval_ms
        self._threads: list[threading.Thread] = []
        self._poll()

    def _poll(self) -> None:
        drained = 0
        while drained < 200:
            try:
                callback = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:  # pragma: no cover - UI safety net
                pass
            drained += 1
        self._root.after(self._interval, self._poll)

    def post(self, callback: Callable[[], None]) -> None:
        """Schedule *callback* to run on the Tk main thread."""
        self._queue.put(callback)

    def run(self, work: Callable[[], Any] | None, on_done: Callable[[Any], None] | None = None,
            on_error: Callable[[BaseException], None] | None = None) -> threading.Thread:
        def target() -> None:
            try:
                result = work() if work else None
            except BaseException as exc:  # noqa: BLE001 - forwarded to caller
                if on_error:
                    self.post(lambda e=exc: on_error(e))
                return
            if on_done:
                self.post(lambda r=result: on_done(r))

        thread = threading.Thread(target=target, daemon=True)
        self._threads.append(thread)
        thread.start()
        return thread


@dataclass
class PathRow:
    """A labelled entry with a Browse button."""

    frame: ttk.Frame
    var: tk.StringVar
    button: ttk.Button
    entry: ttk.Entry


def make_path_row(
    parent: tk.Misc,
    label: str,
    *,
    browse_command: Callable[[], None],
    browse_text: str = "Browse",
    width: int = 52,
) -> PathRow:
    frame = ttk.Frame(parent)
    frame.columnconfigure(0, weight=0)
    frame.columnconfigure(1, weight=1)
    frame.columnconfigure(2, weight=0)

    ttk.Label(frame, text=label, width=14).grid(row=0, column=0, sticky="w", padx=(0, 8))
    var = tk.StringVar()
    entry = ttk.Entry(frame, textvariable=var, width=width)
    entry.grid(row=0, column=1, sticky="ew")
    button = ttk.Button(frame, text=browse_text, command=browse_command)
    button.grid(row=0, column=2, sticky="e", padx=(8, 0))
    return PathRow(frame=frame, var=var, button=button, entry=entry)


#: Severity filters offered by the log toolbar (``all`` = no filtering).
LOG_FILTERS: tuple[str, ...] = ("all", "info", "ok", "warn", "err", "muted", "accent")


class LogConsole(ttk.Frame):
    """Read-only, colourised text area with a tag for each severity.

    Lines are recorded in full (``get``) and *rendered* through the active
    toolbar filter, so Copy/Save always operate on exactly what is visible
    while ``get`` keeps the complete log for programmatic use.
    """

    def __init__(self, parent: tk.Misc, *, height: int = 14) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._lines: list[tuple[str, str]] = []

        # --- toolbar (row 0) -------------------------------------------------
        bar = ttk.Frame(self, style="Panel.TFrame")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        bar.columnconfigure(0, weight=1)

        self.count_var = tk.StringVar(value="0 lines")
        ttk.Label(bar, textvariable=self.count_var, style="Panel.TLabel").grid(
            row=0, column=0, sticky="w"
        )

        controls = ttk.Frame(bar, style="Panel.TFrame")
        controls.grid(row=0, column=1, sticky="e")
        ttk.Label(controls, text="Show", style="Panel.TLabel").pack(side="left", padx=(0, 4))
        self.filter_var = tk.StringVar(value="all")
        box = ttk.Combobox(
            controls, textvariable=self.filter_var, values=LOG_FILTERS,
            state="readonly", width=8,
        )
        box.pack(side="left", padx=(0, 8))
        box.bind("<<ComboboxSelected>>", lambda _e: self.set_filter(self.filter_var.get()))
        ttk.Button(controls, text="Copy", command=self.copy_all).pack(side="left", padx=(0, 4))
        ttk.Button(controls, text="Save...", command=self.save_log).pack(side="left")

        # --- text + scrollbar (row 1) ---------------------------------------
        self.text = tk.Text(self, height=height, wrap="word", state="disabled")
        configure_text_widget(self.text)
        self.text.grid(row=1, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll.set)

        self.text.tag_configure("info", foreground=COLORS["fg"])
        self.text.tag_configure("muted", foreground=COLORS["muted"])
        self.text.tag_configure("ok", foreground=COLORS["ok"])
        self.text.tag_configure("warn", foreground=COLORS["warn"])
        self.text.tag_configure("err", foreground=COLORS["err"])
        self.text.tag_configure("accent", foreground=COLORS["accent"])

        self._update_count()

    # ------------------------------------------------------------- recording
    def _matches(self, tag: str) -> bool:
        return self.filter_var.get() in ("all", tag)

    def append(self, message: str, tag: str = "info") -> None:
        self._lines.append((message, tag))
        if self._matches(tag):
            self.text.configure(state="normal")
            self.text.insert("end", message + "\n", tag)
            self.text.see("end")
            self.text.configure(state="disabled")
        self._update_count()

    def clear(self) -> None:
        self._lines.clear()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._update_count()

    def get(self) -> str:
        """The complete log as recorded, regardless of the active filter."""
        if not self._lines:
            return ""
        return "\n".join(message for message, _ in self._lines) + "\n"

    def get_visible(self) -> str:
        """What the console currently shows (i.e. what Copy/Save operate on)."""
        return self.text.get("1.0", "end")

    # --------------------------------------------------------------- toolbar
    def set_filter(self, tag: str) -> None:
        """Re-render the console showing only *tag* (or everything for 'all')."""
        if tag not in LOG_FILTERS:
            tag = "all"
        self.filter_var.set(tag)
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for message, line_tag in self._lines:
            if tag in ("all", line_tag):
                self.text.insert("end", message + "\n", line_tag)
        self.text.see("end")
        self.text.configure(state="disabled")
        self._update_count()

    def copy_all(self) -> str:
        """Put the visible log on the clipboard; returns the copied text."""
        content = self.get_visible()
        try:
            top = self.winfo_toplevel()
            top.clipboard_clear()
            top.clipboard_append(content)
        except tk.TclError:  # pragma: no cover - clipboard-less sessions
            pass
        return content

    def save_log(self, parent: tk.Misc | None = None) -> str | None:
        """Ask for a file and write the visible log there; returns the path."""
        from tkinter import filedialog

        path = filedialog.asksaveasfilename(
            parent=parent or self,
            title="Save build log",
            defaultextension=".log",
            filetypes=[("Log file", "*.log"), ("Text file", "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return None
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(self.get_visible())
        except OSError:
            return None
        return str(path)

    def _update_count(self) -> None:
        total = len(self._lines)
        shown = sum(1 for _, tag in self._lines if self._matches(tag))
        if shown == total:
            self.count_var.set(f"{total} line{'s' if total != 1 else ''}")
        else:
            self.count_var.set(f"{shown} of {total} lines")


class StatusBar(ttk.Frame):
    """A thin status line with a message and an indeterminate progress bar."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.columnconfigure(0, weight=1)

        self.var = tk.StringVar(value="Ready.")
        self.label = ttk.Label(self, textvariable=self.var, style="Panel.TLabel")
        self.label.grid(row=0, column=0, sticky="w", padx=12, pady=(5, 5))

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100, length=240)
        self.progress.grid(row=0, column=1, sticky="e", padx=12, pady=(5, 5))

    def set(self, message: str) -> None:
        self.var.set(message)

    def set_indeterminate(self) -> None:
        """Animated bar for work whose length is unknown (scans, packaging).

        An honest animation instead of a bar frozen at 0% pretending to be
        real progress.
        """
        if str(self.progress.cget("mode")) != "indeterminate":
            self.progress.configure(mode="indeterminate")
            self.progress.start(40)

    def set_progress(self, done: int, total: int) -> None:
        """Determinate progress for real milestones; animation otherwise.

        ``done <= 0`` means "announcing a phase, total known later": the bar
        animates instead of showing a fake 0%.
        """
        if total <= 0 or done <= 0:
            self.set_indeterminate()
            return
        if str(self.progress.cget("mode")) != "determinate":
            self.progress.stop()
            self.progress.configure(mode="determinate")
        self.progress.configure(value=max(0, min(100, int(done * 100 / total))))

    def reset(self) -> None:
        """Stop any animation and return the bar to its empty resting state."""
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)


def make_tree(parent: tk.Misc, columns: list[tuple[str, str, int]], *, height: int = 12) -> ttk.Treeview:
    """Create a themed Treeview with the given (id, heading, width) columns."""
    ids = [c[0] for c in columns]
    tree = ttk.Treeview(parent, columns=ids, show="headings", height=height)
    for cid, heading, width in columns:
        tree.heading(cid, text=heading)
        tree.column(cid, width=width, anchor="w")
    return tree


class Tooltip:
    """A hover tooltip that explains a widget in one sentence (item 31).

    Deliberately forgiving: it never raises, and it disappears on leave, click
    or after ``hide_after`` milliseconds so it cannot get stuck over the UI.
    """

    def __init__(self, widget: tk.Misc, text: str, *, delay: int = 500, hide_after: int = 6000) -> None:
        self.widget = widget
        self.text = text
        self.delay = delay
        self.hide_after = hide_after
        self._after_id: str | None = None
        self._hide_id: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        try:
            self._after_id = self.widget.after(self.delay, self._show)
        except tk.TclError:  # pragma: no cover - widget destroyed
            self._after_id = None

    def _show(self) -> None:
        if self._tip is not None or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        except tk.TclError:  # pragma: no cover - widget destroyed
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tip, text=self.text, justify="left", wraplength=380,
            background=COLORS["panel_alt"], foreground=COLORS["fg"],
            relief="solid", borderwidth=1, padx=8, pady=5,
        )
        label.pack()
        self._tip = tip
        try:
            self._hide_id = self.widget.after(self.hide_after, self._hide)
        except tk.TclError:  # pragma: no cover
            self._hide_id = None

    def _cancel(self) -> None:
        for attr in ("_after_id", "_hide_id"):
            ident = getattr(self, attr)
            if ident is not None:
                try:
                    self.widget.after_cancel(ident)
                except tk.TclError:  # pragma: no cover
                    pass
                setattr(self, attr, None)

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:  # pragma: no cover
                pass
            self._tip = None


def add_tooltip(widget: tk.Misc, text: str) -> Tooltip:
    """Attach a :class:`Tooltip` to *widget* (kept alive by the widget)."""
    tip = Tooltip(widget, text)
    widget._ece_tooltip = tip  # type: ignore[attr-defined]
    return tip


def with_scrollbar(parent: tk.Misc, tree: ttk.Treeview) -> ttk.Scrollbar:
    scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    return scroll


def section_title(parent: tk.Misc, text: str) -> ttk.Label:
    return ttk.Label(parent, text=text, style="Title.TLabel", font=FONTS["bold"])
