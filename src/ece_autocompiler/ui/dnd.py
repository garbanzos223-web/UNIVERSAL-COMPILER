"""Best-effort native file drag-and-drop for Tk on Windows.

Explorer drops are delivered as ``WM_DROPFILES`` to a window whose style has
``WS_EX_ACCEPTFILES``. Tk does not expose that message, so the top-level window
procedure is subclassed through ctypes and restored only when the callback can
no longer run. On other platforms this module is a no-op: the Select File and
Select Folder buttons always work.
"""

from __future__ import annotations

import ctypes
import os
import sys
import tkinter as tk
from collections.abc import Callable

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4


def _top_level_hwnd(widget: tk.Misc) -> int:
    user32 = ctypes.windll.user32
    user32.GetParent.restype = ctypes.c_void_p
    user32.GetParent.argtypes = [ctypes.c_void_p]
    raw = widget.winfo_id()
    parent = user32.GetParent(ctypes.c_void_p(raw))
    return int(parent or raw)


class WindowsDropTarget:
    """Subclass a Tk window procedure to accept Explorer file drops."""

    def __init__(self, widget: tk.Misc, callback: Callable[[list[str]], None]) -> None:
        self.widget = widget
        self.callback = callback
        self._hwnd = 0
        self._old_proc = None
        self._proc = None

    def enable(self) -> bool:
        if os.name != "nt" or not sys.platform.startswith("win"):
            return False
        try:
            from ctypes import wintypes

            shell32 = ctypes.windll.shell32
            user32 = ctypes.windll.user32
            self._hwnd = _top_level_hwnd(self.widget)
            if not self._hwnd:
                return False
            shell32.DragAcceptFiles(wintypes.HWND(self._hwnd), True)

            lresult = ctypes.c_ssize_t
            wndproc_type = ctypes.WINFUNCTYPE(
                lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            )
            user32.SetWindowLongPtrW.restype = ctypes.c_void_p
            user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            user32.CallWindowProcW.restype = lresult
            user32.CallWindowProcW.argtypes = [
                ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
            ]
            shell32.DragQueryFileW.restype = wintypes.UINT
            shell32.DragQueryFileW.argtypes = [
                ctypes.c_void_p, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT
            ]
            shell32.DragFinish.argtypes = [ctypes.c_void_p]

            user32.GetWindowLongPtrW.restype = ctypes.c_void_p
            user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
            old = user32.GetWindowLongPtrW(wintypes.HWND(self._hwnd), GWLP_WNDPROC)
            if not old:
                return False
            self._old_proc = old

            def handler(hwnd, msg, wparam, lparam):
                if msg == WM_DROPFILES:
                    paths = self._read_drop(wparam)
                    if paths:
                        try:
                            self.widget.after(0, lambda p=paths: self.callback(p))
                        except tk.TclError:  # pragma: no cover - window gone
                            pass
                    try:
                        shell32.DragFinish(ctypes.c_void_p(wparam))
                    except Exception:  # pragma: no cover
                        pass
                    return 0
                return user32.CallWindowProcW(
                    ctypes.c_void_p(self._old_proc), hwnd, msg, wparam, lparam
                )

            self._proc = wndproc_type(handler)
            user32.SetWindowLongPtrW(
                wintypes.HWND(self._hwnd), GWLP_WNDPROC, ctypes.cast(self._proc, ctypes.c_void_p)
            )
            return True
        except Exception:
            return False

    @staticmethod
    def _read_drop(hdrop) -> list[str]:
        shell32 = ctypes.windll.shell32
        try:
            count = shell32.DragQueryFileW(ctypes.c_void_p(hdrop), 0xFFFFFFFF, None, 0)
        except Exception:  # pragma: no cover
            return []
        paths: list[str] = []
        for index in range(min(int(count), 512)):
            buffer = ctypes.create_unicode_buffer(32768)
            length = shell32.DragQueryFileW(ctypes.c_void_p(hdrop), index, buffer, len(buffer))
            if length:
                paths.append(buffer.value)
        return paths


def enable_file_drop(widget: tk.Misc, callback: Callable[[list[str]], None]) -> bool:
    """Enable Explorer→window drops when possible; return whether it worked."""
    target = WindowsDropTarget(widget, callback)
    enabled = target.enable()
    if enabled:
        widget._ece_drop_target = target  # type: ignore[attr-defined]
    return enabled
