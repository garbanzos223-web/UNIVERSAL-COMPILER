"""GUI smoke: MainWindow constructs, dialogs work, the state machine holds.

This is the safety net for UI refactors: it builds a real .ece container,
loads it through the actual dialog (deep validation runs on a worker thread),
performs a real extraction with the folder picker simulated, and checks the
Bloque-1 surfaces: state badge/button transitions, the log toolbar
(filter/copy/save), honest progress (indeterminate vs determinate) and the
global shortcut bindings.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler.config import AppConfig  # noqa: E402
from ece_autocompiler.container import (  # noqa: E402
    ContainerWriter,
    Manifest,
    PayloadItem,
)

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    from ece_autocompiler.ui.main_window import AppContext, ContainerDialog, MainWindow
    from ece_autocompiler.ui.state import STATE_UI, UiState
    from ece_autocompiler.ui.theme import COLORS, apply_theme, configure_root_window
    from ece_autocompiler.ui.widgets import StatusBar, TaskRunner

    _TK_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - environment dependent
    tk = None  # type: ignore[assignment]
    _TK_ERROR = exc


def _build_sample_container(directory: Path) -> Path:
    files = {
        "main.py": "print('hola desde el smoke')\n",
        "datos/info.txt": "ñandú ✨ contenido\n",
        "docs/leeme.md": "# doc\n",
    }
    items = []
    for i, (rel, data) in enumerate(files.items()):
        src = directory / f"src_{i}"
        src.write_text(data, encoding="utf-8")
        items.append(PayloadItem(path=rel, source=src))
    manifest = Manifest(project_name="smoke", adapter_id="python", payload_type="python")
    out = directory / "smoke.ece"
    ContainerWriter(compression="zlib").write(out, items, manifest, overwrite=True)
    return out


@unittest.skipIf(tk is None, f"tkinter unavailable: {_TK_ERROR}")
class GuiSmokeTests(unittest.TestCase):
    root: "tk.Tk"
    ctx: "AppContext"
    win: "MainWindow"
    sample: Path
    tmp: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="ece_smoke_"))
        cls.sample = _build_sample_container(cls.tmp)
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:  # no display (headless session)
            shutil.rmtree(cls.tmp, ignore_errors=True)
            raise unittest.SkipTest(f"no display available: {exc}") from exc
        configure_root_window(cls.root)
        apply_theme(cls.root)
        cfg = AppConfig()
        cfg.ensure_directories()
        cls.ctx = AppContext(
            root=cls.root,
            runner=TaskRunner(cls.root),
            status=StatusBar(cls.root),
            config=cfg,
        )
        cls.win = MainWindow(cls.root, cls.ctx)
        cls.root.update()

    @classmethod
    def tearDownClass(cls) -> None:
        root = getattr(cls, "root", None)
        if root is not None:
            try:
                root.destroy()
            except Exception:  # pragma: no cover
                pass
        shutil.rmtree(getattr(cls, "tmp", Path("\\")), ignore_errors=True)

    # -------------------------------------------------------------- helpers

    def _wait(self, predicate, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.root.update()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    # --------------------------------------------------------------- checks

    def test_01_main_window_constructs(self) -> None:
        win = self.win
        # Compatibility surface relied on by scripts and future refactors.
        self.assertTrue(hasattr(win, "mode_var"))
        self.assertTrue(hasattr(win, "exe_hint"))
        self.assertTrue(hasattr(win, "output_label_var"))
        self.assertIn(win.mode_var.get(), ("ece", "exe"))
        self.assertTrue(self.root.title())  # window really exists
        self.root.update()

    def test_02_all_dialogs_open_and_close(self) -> None:
        openers = (
            ("container", self.win._show_container),
            ("toolchains", self.win._show_toolchains),
            ("history", self.win._show_history),
            ("about", self.win._show_about),
        )
        for name, opener in openers:
            with self.subTest(dialog=name):
                opener()
                self.root.update()
                dialogs = [
                    w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)
                ]
                self.assertTrue(dialogs, f"{name} dialog did not open")
                dialogs[-1].destroy()
                self.root.update()

    def test_03_container_dialog_validates_and_extracts(self) -> None:
        dlg = ContainerDialog(self.ctx)
        self.root.update()
        try:
            # --- phase 1: deep validation on the worker thread -------------
            dlg._load(self.sample)
            ok = self._wait(lambda: not str(dlg.summary_var.get()).startswith("Validating"))
            self.assertTrue(ok, "validation never finished")
            summary = str(dlg.summary_var.get())
            self.assertTrue(
                summary.startswith("Valid .ece"), f"container not valid: {summary}"
            )
            rows = dlg.tree.get_children()
            self.assertGreater(len(rows), 0, "no entries listed")
            self.assertEqual(str(dlg.extract_btn["state"]), "normal")

            # --- phase 2: real extraction, folder picker simulated ---------
            dest = self.tmp / "extraído ✨"
            shutil.rmtree(dest, ignore_errors=True)
            dest.mkdir(parents=True, exist_ok=True)
            orig_dir, orig_yes, orig_warn = (
                filedialog.askdirectory,
                messagebox.askyesno,
                messagebox.showwarning,
            )
            filedialog.askdirectory = lambda **kw: str(dest)
            messagebox.askyesno = lambda *a, **kw: False
            messagebox.showwarning = lambda *a, **kw: None
            try:
                dlg._extract()
                ok = self._wait(lambda: str(dlg.status_var.get()).startswith("Extracted"))
            finally:
                filedialog.askdirectory = orig_dir
                messagebox.askyesno = orig_yes
                messagebox.showwarning = orig_warn
            self.assertTrue(ok, f"extraction never finished: {dlg.status_var.get()}")

            written = sorted(p.name for p in dest.rglob("*") if p.is_file())
            self.assertGreater(len(written), 0, "nothing extracted to disk")
            self.assertTrue((dest / "datos" / "info.txt").is_file())
            self.assertEqual(list(dest.rglob("*.ece-part")), [], "temp files left behind")
        finally:
            try:
                dlg.destroy()
            except Exception:  # pragma: no cover
                pass
            self.root.update()

    def test_04_widget_surface_still_responds(self) -> None:
        # Mode switching used by the .ece/.exe radio buttons must not raise.
        win = self.win
        for mode in ("exe", "ece"):
            win.mode_var.set(mode)
            win._on_mode_changed()
            self.root.update()
        self.root.update()

    def test_05_state_machine_transitions(self) -> None:
        # One transition point (_set_state) drives badge + buttons + flags.
        win = self.win
        self.assertIs(win._state, UiState.IDLE, "window must start idle")
        self.assertEqual(str(win.state_badge["text"]), "Ready")
        try:
            win._set_state(UiState.COMPILING)
            self.assertEqual(str(win.state_badge["text"]), "Compiling")
            self.assertEqual(str(win.state_badge["style"]), "StateBusy.TLabel")
            self.assertEqual(str(win.build_btn["state"]), "disabled")
            self.assertEqual(str(win.cancel_btn["state"]), "normal")
            self.assertTrue(win._building)

            win._set_state(UiState.SUCCESS)
            self.assertEqual(str(win.state_badge["text"]), "Done")
            self.assertEqual(str(win.state_badge["style"]), "StateOk.TLabel")
            self.assertEqual(str(win.build_btn["state"]), "normal")
            self.assertEqual(str(win.cancel_btn["state"]), "disabled")
            self.assertFalse(win._building)

            # A stale async callback (wrong `when`) must not repaint a newer state.
            applied = win._set_state(UiState.IDLE, when=UiState.ERROR)
            self.assertFalse(applied)
            self.assertEqual(str(win.state_badge["text"]), "Done")

            win._set_state(UiState.CANCELLED)
            self.assertEqual(str(win.state_badge["style"]), "StateWarn.TLabel")
            win._set_state(UiState.ERROR)
            self.assertEqual(str(win.state_badge["style"]), "StateErr.TLabel")
        finally:
            win._set_state(UiState.IDLE)
            self.ctx.status.reset()
        self.assertEqual(str(win.state_badge["text"]), "Ready")
        self.assertEqual(str(win.build_btn["state"]), "normal")
        self.assertEqual(str(win.cancel_btn["state"]), "disabled")

    def test_06_state_badge_styles_resolve_in_theme(self) -> None:
        # Every State*.TLabel referenced by state.py must be configured by
        # theme.py with the intended colours (a missing style would fall back
        # to TLabel defaults and fail these equality checks).
        style = ttk.Style(self.root)
        expected_bg = {
            UiState.IDLE: COLORS["header"],
            UiState.DETECTING: COLORS["header"],
            UiState.COMPILING: COLORS["accent_fill"],
            UiState.SUCCESS: COLORS["ok"],
            UiState.ERROR: COLORS["err"],
            UiState.CANCELLED: COLORS["warn"],
        }
        expected_fg = {
            UiState.IDLE: COLORS["muted"],
            UiState.DETECTING: COLORS["accent"],
            UiState.COMPILING: "#ffffff",
            UiState.SUCCESS: COLORS["on_ok"],
            UiState.ERROR: COLORS["on_err"],
            UiState.CANCELLED: COLORS["on_warn"],
        }
        for state, spec in STATE_UI.items():
            with self.subTest(state=state.value):
                bg = str(style.lookup(spec.badge_style, "background")).lower()
                fg = str(style.lookup(spec.badge_style, "foreground")).lower()
                self.assertEqual(
                    bg, expected_bg[state].lower(),
                    f"{spec.badge_style} background = {bg!r}",
                )
                self.assertEqual(
                    fg, expected_fg[state].lower(),
                    f"{spec.badge_style} foreground = {fg!r}",
                )

    def test_07_log_toolbar_filter_copy_save(self) -> None:
        log = self.win.log
        log.clear()
        try:
            log.append("line one info", "info")
            log.append("line two warn", "warn")
            log.append("line three err", "err")

            # get() = the complete log, regardless of the active filter.
            self.assertEqual(len(log.get().splitlines()), 3)
            self.assertIn("line two warn", log.get())

            # Filter: only the selected severity is rendered.
            log.set_filter("err")
            self.assertEqual(str(log.filter_var.get()), "err")
            visible = log.get_visible()
            self.assertIn("line three err", visible)
            self.assertNotIn("line one info", visible)
            self.assertNotIn("line two warn", visible)
            self.assertEqual(str(log.count_var.get()), "1 of 3 lines")

            # Copy puts the VISIBLE text on the clipboard.
            copied = log.copy_all()
            self.assertIn("line three err", copied)
            self.assertNotIn("line one info", copied)
            try:
                self.assertIn("line three err", str(self.root.clipboard_get()))
            except tk.TclError:
                pass  # clipboard unavailable in this session

            # Save writes exactly the visible text (dialog simulated).
            dest = self.tmp / "salida del log ✨.log"
            if dest.exists():
                dest.unlink()
            orig_save = filedialog.asksaveasfilename
            filedialog.asksaveasfilename = lambda **kw: str(dest)
            try:
                written = log.save_log()
            finally:
                filedialog.asksaveasfilename = orig_save
            self.assertEqual(written, str(dest))
            saved = dest.read_text(encoding="utf-8")
            self.assertIn("line three err", saved)
            self.assertNotIn("line one info", saved)

            # Back to "all": every line visible again.
            log.set_filter("all")
            self.assertIn("line one info", log.get_visible())
            self.assertEqual(str(log.count_var.get()), "3 lines")
        finally:
            log.set_filter("all")
            log.clear()

    def test_08_progress_is_honest(self) -> None:
        # Unknown work animates (indeterminate); real milestones show a
        # determinate percentage; reset() returns the bar to rest.
        status = self.ctx.status
        bar = status.progress
        try:
            status.set_progress(0, 100)  # announcing a phase: animate
            self.assertEqual(str(bar.cget("mode")), "indeterminate")
            status.set_progress(20, 100)  # real milestone: determinate 20%
            self.assertEqual(str(bar.cget("mode")), "determinate")
            self.assertEqual(int(float(bar.cget("value"))), 20)
            status.set_progress(0, 0)  # unknown total: animate
            self.assertEqual(str(bar.cget("mode")), "indeterminate")
            status.set_progress(100, 100)  # completion: determinate 100%
            self.assertEqual(str(bar.cget("mode")), "determinate")
            self.assertEqual(int(float(bar.cget("value"))), 100)
        finally:
            status.reset()
        self.assertEqual(str(bar.cget("mode")), "determinate")
        self.assertEqual(int(float(bar.cget("value"))), 0)

    def test_09_shortcut_bindings_registered(self) -> None:
        # The menu labels advertise these accelerators (see _build_menu).
        for sequence in ("<F5>", "<Control-Return>", "<Escape>",
                         "<Control-Shift-C>", "<Control-O>"):
            with self.subTest(sequence=sequence):
                script = self.root.bind_all(sequence)
                self.assertTrue(script, f"no global binding for {sequence}")
        for handler in ("_key_rescan", "_key_compile", "_key_cancel",
                        "_key_copy_log", "_key_open_project"):
            with self.subTest(handler=handler):
                self.assertTrue(callable(getattr(self.win, handler, None)))

    def test_10_dark_titlebar_request_is_consistent(self) -> None:
        # The helper must leave a consistent DWM state: either the session
        # painted a dark caption (kept, reads 1) or it painted the known
        # blank-white fallback and the helper reverted (reads 0). A stale
        # "requested but not honoured" state is exactly the bug this guards.
        from ece_autocompiler.ui.theme import _apply_dark_titlebar

        try:
            import ctypes
            from ctypes import wintypes

            dwmapi = ctypes.windll.dwmapi
            get_attr = dwmapi.DwmGetWindowAttribute
            get_attr.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_void_p, ctypes.c_int)
            get_attr.restype = ctypes.c_int
            get_parent = ctypes.windll.user32.GetParent
            get_parent.argtypes = (wintypes.HWND,)
            get_parent.restype = wintypes.HWND
        except (AttributeError, OSError, ValueError):
            self.skipTest("requires Windows DWM")
        raw = self.root.winfo_id()
        hwnd = get_parent(raw) or raw
        if not hwnd:
            self.skipTest("no window handle")
        probe = ctypes.c_int(0)
        if get_attr(hwnd, 20, ctypes.byref(probe), ctypes.sizeof(probe)) != 0:
            self.skipTest("DWMWA_USE_IMMERSIVE_DARK_MODE unsupported here")

        applied = _apply_dark_titlebar(self.root)
        after = ctypes.c_int(0)
        if get_attr(hwnd, 20, ctypes.byref(after), ctypes.sizeof(after)) != 0:
            self.skipTest("attribute became unavailable")
        self.assertEqual(
            after.value, 1 if applied else 0,
            "dark-titlebar helper must keep only what DWM really painted",
        )

    def test_11_failed_export_shows_what_and_next_step(self) -> None:
        # Bloque 3: a failed export must leave the three-part contract in
        # the live log (what happened / why / what to do) and land in ERROR.
        # The history write is patched so this smoke never touches the real
        # history.jsonl.
        from unittest import mock

        from ece_autocompiler.build.exe_exporter import ExeExportResult
        from ece_autocompiler.ui import main_window as mw

        win = self.win
        log = win.log
        log.clear()
        failing = ExeExportResult(
            ok=False, exe_path=None, message="boom",
            errors=["why-one"], actions=["step one", "step two"], exit_code=1,
        )
        try:
            with mock.patch.object(mw.build_history, "append"):
                win._exe_done(failing)
            text = log.get()
            self.assertIn("Executable export failed: boom", text)
            self.assertIn("Why:", text)
            self.assertIn("  - why-one", text)
            self.assertIn("What to do:", text)
            self.assertIn("  - step one", text)
            self.assertEqual(win._state, UiState.ERROR)
        finally:
            log.set_filter("all")
            log.clear()
            win._set_state(UiState.IDLE)
            self.ctx.status.reset()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
