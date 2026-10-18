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


class LogConsole(ttk.Frame):
    """Read-only, colourised text area with a tag for each severity."""

    def __init__(self, parent: tk.Misc, *, height: int = 14) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.text = tk.Text(self, height=height, wrap="word", state="disabled")
        configure_text_widget(self.text)
        self.text.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll.set)

        self.text.tag_configure("info", foreground=COLORS["fg"])
        self.text.tag_configure("muted", foreground=COLORS["muted"])
        self.text.tag_configure("ok", foreground=COLORS["ok"])
        self.text.tag_configure("warn", foreground=COLORS["warn"])
        self.text.tag_configure("err", foreground=COLORS["err"])
        self.text.tag_configure("accent", foreground=COLORS["accent"])

    def append(self, message: str, tag: str = "info") -> None:
        self.text.configure(state="normal")
        self.text.insert("end", message + "\n", tag)
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def get(self) -> str:
        return self.text.get("1.0", "end")


class StatusBar(ttk.Frame):
    """A thin status line with a message and an indeterminate progress bar."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, style="Panel.TFrame")
        self.columnconfigure(0, weight=1)

        self.var = tk.StringVar(value="Ready.")
        self.label = ttk.Label(self, textvariable=self.var, style="Panel.TLabel")
        self.label.grid(row=0, column=0, sticky="w", padx=10, pady=4)

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100, length=220)
        self.progress.grid(row=0, column=1, sticky="e", padx=10, pady=4)

    def set(self, message: str) -> None:
        self.var.set(message)

    def set_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress.configure(value=0)
        else:
            self.progress.configure(value=max(0, min(100, int(done * 100 / total))))

    def reset(self) -> None:
        self.progress.configure(value=0)


def make_tree(parent: tk.Misc, columns: list[tuple[str, str, int]], *, height: int = 12) -> ttk.Treeview:
    """Create a themed Treeview with the given (id, heading, width) columns."""
    ids = [c[0] for c in columns]
    tree = ttk.Treeview(parent, columns=ids, show="headings", height=height)
    for cid, heading, width in columns:
        tree.heading(cid, text=heading)
        tree.column(cid, width=width, anchor="w")
    return tree


def with_scrollbar(parent: tk.Misc, tree: ttk.Treeview) -> ttk.Scrollbar:
    scroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    return scroll


def section_title(parent: tk.Misc, text: str) -> ttk.Label:
    return ttk.Label(parent, text=text, style="Title.TLabel", font=FONTS["bold"])
