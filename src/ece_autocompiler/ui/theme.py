"""Dark theme for the Tkinter GUI.

Kept in one module so colours, fonts and widget styles are defined once and
reused by every window. ttk cannot style everything (notably menus and the text
widget), so a few raw Tk options are set here too.

Design notes:

* The window background and the cards share one colour; cards are delineated by
  a hairline border instead of a fill, so nested widgets — which all default to
  the app colour — never paint mismatched patches inside a card.
* The header bar and the log console are the only large surfaces with their own
  colour; every widget placed on them carries an explicit style whose
  background matches (see ``Header.TFrame`` / ``Console``).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

COLORS = {
    "bg": "#0f1115",        # window + card surface
    "panel": "#171a20",     # status bar / log console frame
    "panel_alt": "#20242c",  # inputs, buttons, badges
    "header": "#14171d",    # title bars (main window + dialogs)
    "border": "#2b303a",    # hairlines
    "fg": "#e8eaed",
    "muted": "#9aa4b2",
    "accent": "#4f8cff",
    "accent_active": "#74a6ff",
    "ok": "#3fb950",
    "warn": "#d29922",
    "err": "#f85149",
    "selection": "#2b4a80",
    "console": "#0c0e12",   # recessed log area
}

FONTS = {
    "base": ("Segoe UI", 10),
    "bold": ("Segoe UI", 10, "bold"),
    "title": ("Segoe UI Semibold", 16),
    "dialog_title": ("Segoe UI Semibold", 12),
    "subtitle": ("Segoe UI", 10),
    "section": ("Segoe UI", 9, "bold"),
    "chip": ("Segoe UI", 9),
    "menu": ("Segoe UI", 9),
    "mono": ("Consolas", 9),
}


def apply_theme(root: tk.Misc) -> ttk.Style:
    """Apply the dark theme to *root* and return the configured style."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=COLORS["bg"], foreground=COLORS["fg"], font=FONTS["base"])

    # --- frames ------------------------------------------------------------
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Panel.TFrame", background=COLORS["panel"])
    style.configure("Header.TFrame", background=COLORS["header"])
    # Cards share the app colour and are drawn with a 1px hairline instead of
    # a fill (see the module docstring).
    style.configure(
        "Card.TFrame",
        background=COLORS["bg"],
        borderwidth=1,
        relief="solid",
        bordercolor=COLORS["border"],
        lightcolor=COLORS["border"],
        darkcolor=COLORS["border"],
    )
    # Thin accent strip under the header bar.
    style.configure("AccentStrip.TFrame", background=COLORS["accent"])

    # --- labels ------------------------------------------------------------
    style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["fg"])
    style.configure("Panel.TLabel", background=COLORS["panel"], foreground=COLORS["fg"])
    style.configure(
        "Title.TLabel",
        background=COLORS["header"],
        foreground="#ffffff",
        font=FONTS["title"],
    )
    style.configure(
        "Subtitle.TLabel",
        background=COLORS["header"],
        foreground=COLORS["muted"],
        font=FONTS["subtitle"],
    )
    style.configure("Dialog.TLabel", background=COLORS["header"], foreground="#ffffff", font=FONTS["dialog_title"])
    style.configure("Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
    style.configure("Ok.TLabel", foreground=COLORS["ok"])
    style.configure("Warn.TLabel", foreground=COLORS["warn"])
    style.configure("Err.TLabel", foreground=COLORS["err"])
    # Version chip in the header.
    style.configure(
        "Chip.TLabel",
        background=COLORS["accent"],
        foreground="#ffffff",
        font=FONTS["chip"],
        padding=(7, 2),
    )
    # Pill badge for the detected-technology value.
    style.configure(
        "Badge.TLabel",
        background=COLORS["panel_alt"],
        foreground=COLORS["fg"],
        padding=(10, 4),
    )
    # Accent section header inside a card.
    style.configure(
        "Section.TLabel",
        background=COLORS["bg"],
        foreground=COLORS["accent"],
        font=FONTS["section"],
    )
    # Kept for callers that style a label sitting on a card surface.
    style.configure("Card.TLabel", background=COLORS["bg"], foreground=COLORS["fg"])
    style.configure("Card.Muted.TLabel", background=COLORS["bg"], foreground=COLORS["muted"])
    style.configure("Section.Card.TLabel", background=COLORS["bg"], foreground=COLORS["accent"], font=FONTS["section"])

    # --- buttons -----------------------------------------------------------
    style.configure(
        "TButton",
        background=COLORS["panel_alt"],
        foreground=COLORS["fg"],
        borderwidth=0,
        focusthickness=0,
        padding=(14, 7),
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
        font=FONTS["bold"],
        padding=(20, 9),
    )
    style.map(
        "Accent.TButton",
        background=[("active", COLORS["accent_active"]), ("disabled", COLORS["panel_alt"])],
        foreground=[("disabled", COLORS["muted"])],
    )
    # Bare text button for title bars (header/dialog headers).
    style.configure(
        "Ghost.TButton",
        background=COLORS["header"],
        foreground=COLORS["muted"],
        borderwidth=0,
        focusthickness=0,
        padding=(10, 5),
    )
    style.map(
        "Ghost.TButton",
        background=[("active", COLORS["border"])],
        foreground=[("active", COLORS["fg"]), ("disabled", COLORS["muted"])],
    )

    # --- inputs ------------------------------------------------------------
    style.configure(
        "TEntry",
        fieldbackground=COLORS["panel_alt"],
        foreground=COLORS["fg"],
        insertcolor=COLORS["fg"],
        borderwidth=0,
        padding=(8, 6),
    )
    style.map(
        "TEntry",
        fieldbackground=[("focus", COLORS["panel_alt"]), ("readonly", COLORS["panel_alt"])],
        bordercolor=[("focus", COLORS["accent"])],
        lightcolor=[("focus", COLORS["accent"])],
    )
    style.configure(
        "TCombobox",
        fieldbackground=COLORS["panel_alt"],
        foreground=COLORS["fg"],
        background=COLORS["panel_alt"],
        arrowcolor=COLORS["fg"],
        borderwidth=0,
        padding=(6, 5),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", COLORS["panel_alt"])],
        foreground=[("readonly", COLORS["fg"])],
        arrowcolor=[("active", COLORS["accent"])],
    )

    style.configure("TCheckbutton", background=COLORS["bg"], foreground=COLORS["fg"])
    style.map("TCheckbutton", background=[("active", COLORS["bg"])], foreground=[("disabled", COLORS["muted"])])

    style.configure("TRadiobutton", background=COLORS["bg"], foreground=COLORS["fg"])
    style.map(
        "TRadiobutton",
        background=[("active", COLORS["bg"])],
        foreground=[("disabled", COLORS["muted"]), ("selected", COLORS["accent"])],
    )

    # --- tables ------------------------------------------------------------
    style.configure(
        "Treeview",
        background=COLORS["panel"],
        fieldbackground=COLORS["panel"],
        foreground=COLORS["fg"],
        borderwidth=0,
        rowheight=26,
    )
    style.map(
        "Treeview",
        background=[("selected", COLORS["selection"])],
        foreground=[("selected", "#ffffff")],
    )
    style.configure(
        "Treeview.Heading",
        background=COLORS["header"],
        foreground=COLORS["muted"],
        borderwidth=0,
        relief="flat",
        padding=(10, 7),
        font=FONTS["section"],
    )
    style.map("Treeview.Heading", background=[("active", COLORS["panel_alt"])], foreground=[("active", COLORS["fg"])])

    # --- progress, separators, scrollbars ----------------------------------
    style.configure(
        "Horizontal.TProgressbar",
        troughcolor=COLORS["panel_alt"],
        background=COLORS["accent"],
        borderwidth=0,
        relief="flat",
        thickness=6,
    )
    style.configure("TSeparator", background=COLORS["border"])
    style.configure(
        "Vertical.TScrollbar",
        background=COLORS["panel_alt"],
        troughcolor=COLORS["bg"],
        borderwidth=0,
        arrowcolor=COLORS["muted"],
        relief="flat",
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("active", COLORS["border"]), ("pressed", COLORS["accent"])],
        arrowcolor=[("active", COLORS["fg"])],
    )
    style.configure(
        "Horizontal.TScrollbar",
        background=COLORS["panel_alt"],
        troughcolor=COLORS["bg"],
        borderwidth=0,
        arrowcolor=COLORS["muted"],
        relief="flat",
    )
    style.map(
        "Horizontal.TScrollbar",
        background=[("active", COLORS["border"]), ("pressed", COLORS["accent"])],
        arrowcolor=[("active", COLORS["fg"])],
    )

    # --- menus (raw Tk; ttk cannot style them) -----------------------------
    style.configure("TNotebook", background=COLORS["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=COLORS["bg"], foreground=COLORS["muted"], padding=(16, 8), borderwidth=0)
    style.map(
        "TNotebook.Tab",
        background=[("selected", COLORS["panel_alt"])],
        foreground=[("selected", COLORS["fg"])],
    )

    return style


def style_menu(menu: tk.Menu) -> tk.Menu:
    """Apply the dark palette to a raw Tk menu (ttk cannot style menus)."""
    menu.configure(
        background=COLORS["panel"],
        foreground=COLORS["fg"],
        activebackground=COLORS["accent"],
        activeforeground="#ffffff",
        disabledforeground=COLORS["muted"],
        borderwidth=0,
        font=FONTS["menu"],
    )
    return menu


def configure_text_widget(widget: tk.Text) -> None:
    widget.configure(
        background=COLORS["console"],
        foreground=COLORS["fg"],
        insertbackground=COLORS["fg"],
        selectbackground=COLORS["selection"],
        selectforeground="#ffffff",
        font=FONTS["mono"],
        borderwidth=0,
        highlightthickness=0,
        padx=10,
        pady=8,
    )


def configure_root_window(root: tk.Tk) -> None:
    root.title("ECE Universal AutoCompiler")
    root.geometry("1120x760")
    root.minsize(920, 600)
    root.configure(background=COLORS["bg"])
