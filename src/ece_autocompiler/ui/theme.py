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
    # "accent" is the LIGHT blue: text/icons on dark surfaces (>=4.5:1 there).
    "accent": "#4f8cff",
    "accent_active": "#74a6ff",
    # "accent_fill" is the DARK blue: filled surfaces that carry WHITE text
    # (buttons, chips, busy badge) and must stay >=4.5:1 with white (WCAG AA).
    "accent_fill": "#2667d8",
    "accent_fill_active": "#2b6ade",
    "ok": "#3fb950",
    "warn": "#d29922",
    "err": "#f85149",
    # Dark text for filled badges (white on ok/warn/err fails WCAG AA).
    "on_ok": "#0b1a10",
    "on_warn": "#2a1d04",
    "on_err": "#2a0908",
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
        background=COLORS["accent_fill"],
        foreground="#ffffff",
        font=FONTS["chip"],
        padding=(7, 2),
    )
    # --- state pill in the header (one style per UiState; see state.py) ----
    style.configure(
        "StateIdle.TLabel",
        background=COLORS["header"],
        foreground=COLORS["muted"],
        font=FONTS["chip"],
        padding=(9, 3),
    )
    style.configure(
        "StateScan.TLabel",
        background=COLORS["header"],
        foreground=COLORS["accent"],
        font=FONTS["bold"],
        padding=(9, 3),
    )
    style.configure(
        "StateBusy.TLabel",
        background=COLORS["accent_fill"],
        foreground="#ffffff",
        font=FONTS["bold"],
        padding=(9, 3),
    )
    style.configure(
        "StateOk.TLabel",
        background=COLORS["ok"],
        foreground=COLORS["on_ok"],
        font=FONTS["bold"],
        padding=(9, 3),
    )
    style.configure(
        "StateErr.TLabel",
        background=COLORS["err"],
        foreground=COLORS["on_err"],
        font=FONTS["bold"],
        padding=(9, 3),
    )
    style.configure(
        "StateWarn.TLabel",
        background=COLORS["warn"],
        foreground=COLORS["on_warn"],
        font=FONTS["bold"],
        padding=(9, 3),
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
    # focusthickness/focuscolor make keyboard focus visible (WCAG 2.4.7);
    # the theme shipped with focusthickness=0, i.e. no focus indicator at all.
    style.configure(
        "TButton",
        background=COLORS["panel_alt"],
        foreground=COLORS["fg"],
        borderwidth=0,
        focusthickness=2,
        focuscolor=COLORS["accent"],
        padding=(14, 7),
    )
    style.map(
        "TButton",
        background=[("active", COLORS["border"]), ("disabled", COLORS["panel"])],
        foreground=[("disabled", COLORS["muted"])],
    )
    style.configure(
        "Accent.TButton",
        background=COLORS["accent_fill"],
        foreground="#ffffff",
        font=FONTS["bold"],
        focusthickness=2,
        focuscolor=COLORS["accent"],
        padding=(20, 9),
    )
    style.map(
        "Accent.TButton",
        background=[
            ("active", COLORS["accent_fill_active"]),
            ("disabled", COLORS["panel_alt"]),
        ],
        foreground=[("disabled", COLORS["muted"])],
    )
    # Bare text button for title bars (header/dialog headers).
    style.configure(
        "Ghost.TButton",
        background=COLORS["header"],
        foreground=COLORS["muted"],
        borderwidth=0,
        focusthickness=2,
        focuscolor=COLORS["accent"],
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
        activebackground=COLORS["accent_fill"],
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


def _apply_dark_titlebar(window: tk.Misc) -> bool:
    """Set DWMWA_USE_IMMERSIVE_DARK_MODE on the window's visible frame.

    Returns True only when a dark frame was requested *and* the compositor
    actually painted one. Some Windows 10 sessions (light theme, or a custom
    theme whose metrics disagree with the registry) cannot render a dark
    caption: the attribute call succeeds but the caption comes out blank
    WHITE, which looks worse than the standard title bar. That case is
    detected by sampling the composited caption on screen and the attribute
    is reverted, leaving the system's normal themed title bar.
    """
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        get_parent = user32.GetParent
        get_parent.argtypes = (wintypes.HWND,)
        get_parent.restype = wintypes.HWND
        set_attr = dwmapi.DwmSetWindowAttribute
        set_attr.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_void_p, ctypes.c_int)
        set_attr.restype = ctypes.c_int

        raw = window.winfo_id()
        # HWND selection is empirical: before the first map, winfo_id() *is*
        # the top-level window (parent NULL); Tk then re-parents it under a
        # frame that only exists after mapping, so afterwards the parent is
        # the visible window. Both candidates are tried so the attribute
        # always lands on the frame that draws the title bar.
        target = None
        value = ctypes.c_int(1)  # DWMWA_USE_IMMERSIVE_DARK_MODE = on
        for hwnd in (get_parent(raw), raw):
            if not hwnd:
                continue
            for attribute in (20, 19):  # 20: Win10 2004+/11, 19: older Win10
                if set_attr(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                    target = hwnd
                    break
            if target is not None:
                break
        if target is None:
            return False
        if not user32.IsWindowVisible(target):
            # Not mapped yet: nothing is painted, so verification would read
            # the desktop behind it. The delayed re-apply verifies instead.
            return True
        try:
            dwmapi.DwmFlush()  # wait until the frame is actually composited
        except (AttributeError, OSError):  # pragma: no cover - very old OS
            pass
        frame = wintypes.RECT()
        user32.GetWindowRect(target, ctypes.byref(frame))
        hdc = user32.GetDC(0)  # the composed screen, not the window bitmap
        if not hdc:
            return True  # pragma: no cover - cannot sample; keep the request
        try:
            color = ctypes.windll.gdi32.GetPixel(hdc, frame.left + 80, frame.top + 16)
        finally:
            user32.ReleaseDC(0, hdc)
        if color == 0xFFFFFF:
            # Blank white caption: this session cannot render dark. Revert.
            value = ctypes.c_int(0)
            for attribute in (20, 19):
                if set_attr(target, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                    break
            return False
        return True
    except (AttributeError, OSError, tk.TclError, ValueError):  # pragma: no cover
        return False


def enable_dark_titlebar(window: tk.Misc) -> None:
    """Ask DWM for a dark title bar on Windows 10/11; never raise.

    DwmSetWindowAttribute is called through ctypes with explicit signatures
    (a bare call would truncate the 64-bit HWND through the default C int).
    On failure the window simply keeps the standard title bar.

    The call runs twice: once now (for already-mapped windows such as
    dialogs) and again after the first map, because Tk creates a *new* frame
    window when a not-yet-mapped window is first shown — applying the
    attribute only before mapping would target a window that ends up as a
    child, leaving the visible title bar light.
    """
    _apply_dark_titlebar(window)
    try:
        window.after(80, lambda: _apply_dark_titlebar(window))
    except tk.TclError:  # pragma: no cover - window already gone
        pass


def configure_root_window(root: tk.Tk) -> None:
    root.title("ECE Universal AutoCompiler")
    root.geometry("1120x760")
    root.minsize(920, 600)
    root.configure(background=COLORS["bg"])
    enable_dark_titlebar(root)
