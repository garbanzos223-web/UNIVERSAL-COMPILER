"""Dark theme for the Tkinter GUI.

Kept in one module so colors and fonts are defined once and reused by every
panel. ttk cannot style everything (notably menus and the text widget), so a
few raw Tk options are set here too.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

COLORS = {
    "bg": "#1e1f22",
    "panel": "#2b2d30",
    "panel_alt": "#313438",
    "border": "#3a3d41",
    "fg": "#e6e6e6",
    "muted": "#9aa0a6",
    "accent": "#3d7eff",
    "accent_active": "#5a92ff",
    "ok": "#3fb950",
    "warn": "#d29922",
    "err": "#f85149",
    "selection": "#2f4b7c",
}

FONTS = {
    "base": ("Segoe UI", 10),
    "bold": ("Segoe UI", 10, "bold"),
    "title": ("Segoe UI Semibold", 15),
    "subtitle": ("Segoe UI", 10),
    "mono": ("Consolas", 9),
}


def apply_theme(root: tk.Misc) -> ttk.Style:
    """Apply the dark theme to *root* and return the configured style."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    root.configure(background=COLORS["bg"])

    style.configure(".", background=COLORS["bg"], foreground=COLORS["fg"], font=FONTS["base"])
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Panel.TFrame", background=COLORS["panel"])
    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["fg"])
    style.configure("Panel.TLabel", background=COLORS["panel"], foreground=COLORS["fg"])
    style.configure("Title.TLabel", background=COLORS["bg"], foreground=COLORS["fg"], font=FONTS["title"])
    style.configure("Subtitle.TLabel", background=COLORS["bg"], foreground=COLORS["muted"], font=FONTS["subtitle"])
    style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
    style.configure("Ok.TLabel", foreground=COLORS["ok"])
    style.configure("Warn.TLabel", foreground=COLORS["warn"])
    style.configure("Err.TLabel", foreground=COLORS["err"])

    style.configure(
        "TButton",
        background=COLORS["panel_alt"],
        foreground=COLORS["fg"],
        borderwidth=0,
        focusthickness=0,
        padding=(12, 6),
    )
    style.map(
        "TButton",
        background=[("active", COLORS["border"]), ("disabled", COLORS["panel"])],
        foreground=[("disabled", COLORS["muted"])],
    )
    style.configure(
        "Accent.TButton",
        background=COLORS["accent"],
        foreground="#ffffff",
        padding=(14, 7),
    )
    style.map(
        "Accent.TButton",
        background=[("active", COLORS["accent_active"]), ("disabled", COLORS["panel"])],
        foreground=[("disabled", COLORS["muted"])],
    )

    style.configure("TEntry", fieldbackground=COLORS["panel"], foreground=COLORS["fg"], insertcolor=COLORS["fg"], borderwidth=0)
    style.configure("TCombobox", fieldbackground=COLORS["panel"], background=COLORS["panel_alt"], foreground=COLORS["fg"], arrowcolor=COLORS["fg"], borderwidth=0)
    style.map("TCombobox", fieldbackground=[("readonly", COLORS["panel"])], foreground=[("readonly", COLORS["fg"])])

    style.configure("TCheckbutton", background=COLORS["bg"], foreground=COLORS["fg"])
    style.map("TCheckbutton", background=[("active", COLORS["bg"])], foreground=[("disabled", COLORS["muted"])])

    style.configure("TNotebook", background=COLORS["bg"], borderwidth=0, tabmargins=(6, 6, 6, 0))
    style.configure("TNotebook.Tab", background=COLORS["panel"], foreground=COLORS["muted"], padding=(16, 8), borderwidth=0)
    style.map(
        "TNotebook.Tab",
        background=[("selected", COLORS["panel_alt"])],
        foreground=[("selected", COLORS["fg"])],
    )

    style.configure(
        "Treeview",
        background=COLORS["panel"],
        fieldbackground=COLORS["panel"],
        foreground=COLORS["fg"],
        borderwidth=0,
        rowheight=24,
    )
    style.map(
        "Treeview",
        background=[("selected", COLORS["selection"])],
        foreground=[("selected", "#ffffff")],
    )
    style.configure("Treeview.Heading", background=COLORS["panel_alt"], foreground=COLORS["muted"], borderwidth=0, padding=(8, 6))
    style.map("Treeview.Heading", background=[("active", COLORS["border"])])

    style.configure("Horizontal.TProgressbar", troughcolor=COLORS["panel"], background=COLORS["accent"], borderwidth=0)
    style.configure("TSeparator", background=COLORS["border"])
    style.configure("Vertical.TScrollbar", background=COLORS["panel_alt"], troughcolor=COLORS["bg"], borderwidth=0, arrowcolor=COLORS["fg"])
    style.configure("Horizontal.TScrollbar", background=COLORS["panel_alt"], troughcolor=COLORS["bg"], borderwidth=0, arrowcolor=COLORS["fg"])

    return style


def configure_text_widget(widget: tk.Text) -> None:
    widget.configure(
        background="#161719",
        foreground=COLORS["fg"],
        insertbackground=COLORS["fg"],
        selectbackground=COLORS["selection"],
        font=FONTS["mono"],
        borderwidth=0,
        highlightthickness=0,
        padx=8,
        pady=6,
    )


def configure_root_window(root: tk.Tk) -> None:
    root.title("ECE Universal AutoCompiler")
    root.geometry("1080x720")
    root.minsize(880, 560)
    root.configure(background=COLORS["bg"])
