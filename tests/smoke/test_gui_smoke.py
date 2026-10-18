"""GUI smoke: MainWindow constructs, every dialog opens, ContainerDialog works.

This is the safety net for UI refactors: it builds a real .ece container,
loads it through the actual dialog (deep validation runs on a worker thread),
and performs a real extraction with the folder picker simulated.
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
    from tkinter import filedialog, messagebox

    from ece_autocompiler.ui.main_window import AppContext, ContainerDialog, MainWindow
    from ece_autocompiler.ui.theme import apply_theme, configure_root_window
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
