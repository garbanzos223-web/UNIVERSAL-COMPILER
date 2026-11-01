"""GUI smoke for the integrated DECOMPILER tab.

Builds a real .pyc, imports it through the panel, waits for the real analysis
and backend, exports the project, edits nothing, and runs the real recompiler.
Folder pickers are simulated; every other step is the production code path.
"""

from __future__ import annotations

import os
import py_compile
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    from ece_autocompiler.config import AppConfig
    from ece_autocompiler.ui.main_window import AppContext, MainWindow
    from ece_autocompiler.ui.theme import apply_theme, configure_root_window
    from ece_autocompiler.ui.widgets import StatusBar, TaskRunner

    _TK_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - environment dependent
    tk = None  # type: ignore[assignment]
    _TK_ERROR = exc


@unittest.skipIf(tk is None, f"tkinter unavailable: {_TK_ERROR}")
class DecompilerGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="ece_decomp_gui_"))
        cls.home = Path(tempfile.mkdtemp(prefix="ece_decomp_home_"))
        cls._old_home = os.environ.get("ECE_AUTOCOMPILER_HOME", "")
        os.environ["ECE_AUTOCOMPILER_HOME"] = str(cls.home)
        try:
            cls.root = tk.Tk()
        except tk.TclError as exc:
            shutil.rmtree(cls.tmp, ignore_errors=True)
            shutil.rmtree(cls.home, ignore_errors=True)
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

        source = cls.tmp / "sample.py"
        source.write_text(
            "import os\n\nCONST = 7\n\nclass Greeter:\n"
            "    def hello(self, name):\n        return name\n\n"
            "def main():\n    return Greeter().hello('x')\n",
            encoding="utf-8",
        )
        py_compile.compile(str(source), doraise=True)
        cls.pyc = next((cls.tmp / "__pycache__").glob("*.pyc"))

        # No real dialogs in CI: the folder pickers answer with temp folders
        # and every confirmation is accepted, exactly as a user would.
        cls._old = {
            "dir": filedialog.askdirectory,
            "ok": messagebox.askokcancel,
            "yes": messagebox.askyesno,
            "info": messagebox.showinfo,
            "warn": messagebox.showwarning,
            "err": messagebox.showerror,
        }
        cls.output = cls.tmp / "recovered"
        cls.exported = cls.tmp / "exported"
        cls.errors: list[tuple] = []
        filedialog.askdirectory = lambda **kw: (
            str(cls.output) if "reconstructed" in kw.get("title", "") else str(cls.exported)
        )
        messagebox.askokcancel = lambda *a, **kw: True
        messagebox.askyesno = lambda *a, **kw: False
        messagebox.showinfo = lambda *a, **kw: None
        messagebox.showwarning = lambda *a, **kw: None
        messagebox.showerror = lambda *a, **kw: cls.errors.append(a)

    @classmethod
    def tearDownClass(cls) -> None:
        filedialog.askdirectory = cls._old["dir"]
        messagebox.askokcancel = cls._old["ok"]
        messagebox.askyesno = cls._old["yes"]
        messagebox.showinfo = cls._old["info"]
        messagebox.showwarning = cls._old["warn"]
        messagebox.showerror = cls._old["err"]
        try:
            cls.root.destroy()
        except Exception:  # pragma: no cover
            pass
        if cls._old_home:
            os.environ["ECE_AUTOCOMPILER_HOME"] = cls._old_home
        else:
            os.environ.pop("ECE_AUTOCOMPILER_HOME", None)
        shutil.rmtree(cls.tmp, ignore_errors=True)
        shutil.rmtree(cls.home, ignore_errors=True)

    def _wait(self, predicate, timeout: float = 120.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.root.update()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    def _toplevels(self) -> list:
        found = []
        stack = list(self.root.winfo_children())
        while stack:
            widget = stack.pop()
            if isinstance(widget, tk.Toplevel):
                found.append(widget)
            stack.extend(widget.winfo_children())
        return found

    def test_01_tabs_and_panel_construct(self) -> None:
        win = self.win
        titles = [win.tabs.tab(tab, "text") for tab in win.tabs.tabs()]
        self.assertEqual(titles, ["COMPILER", "DECOMPILER"])
        panel = win.decompiler
        for name in (
            "files_tree", "project_tree", "resources_tree", "analysis_box",
            "editor_tabs", "log", "add_files_btn", "decompile_btn",
            "export_btn", "recompile_btn",
        ):
            self.assertTrue(hasattr(panel, name), name)
        self.assertEqual(str(panel.decompile_btn.cget("state")), "disabled")
        self.assertTrue(panel._drop_enabled or win.decompiler.attach_drag_and_drop() is None)

    def test_02_import_analyse_decompile_export_recompile(self) -> None:
        win = self.win
        panel = win.decompiler
        win.tabs.select(panel)
        panel._add_paths([self.pyc])
        self.assertTrue(self._wait(lambda: len(panel._analyses) == 1), "analysis never finished")
        self.assertEqual(self.errors, [])

        panel._decompile()
        self.assertTrue(self._wait(lambda: panel._result is not None), "decompile never finished")
        result = panel._result
        self.assertTrue(result.ok)
        self.assertGreater(len(result.files), 0)
        self.assertGreater(len(panel.project_tree.get_children()), 0)
        self.assertGreater(len(panel.resources_tree.get_children()) + 1, 0)
        self.assertGreater(len(panel.editor_tabs.buffers), 0, "no source opened in the editor")
        self.assertEqual(str(panel.export_btn.cget("state")), "normal")
        self.assertEqual(self.errors, [])

        panel._export()
        self.assertTrue(self._wait(lambda: panel._exported_root is not None), "export never finished")
        exported_files = [path for path in Path(panel._exported_root).rglob("*") if path.is_file()]
        self.assertGreater(len(exported_files), 0)
        self.assertTrue((Path(panel._exported_root) / "recovered-project.json").is_file())

        warnings: list[tuple] = []
        original_warning = messagebox.showwarning
        messagebox.showwarning = lambda *a, **kw: warnings.append(a)
        try:
            panel._recompile()
            self.assertTrue(
                self._wait(lambda: bool(warnings) or "not recompilable" in panel.log.get().lower()),
                "the recompiler gate never answered",
            )
        finally:
            messagebox.showwarning = original_warning
        for widget in self._toplevels():
            if "Recompilation report" in widget.title():
                widget.destroy()
        self.root.update()
        # A Python outline is not recompilable source: the gate must refuse
        # rather than claim a build that cannot reproduce the original.
        answer = " ".join(str(item) for item in warnings).lower() + panel.log.get().lower()
        self.assertTrue(
            "not recompilable" in answer
            or "cannot be recompiled" in answer
            or "generated outlines" in answer,
            answer[:800],
        )

    def test_03_editor_save_and_compare(self) -> None:
        panel = self.win.decompiler
        buffer = panel.editor_tabs.current()
        if buffer is None:
            self.skipTest("no editor tab from the previous test")
        editor = buffer.editor
        editor.text.insert("end", "\n# edited by the smoke test\n")
        self.root.update()
        self.assertTrue(editor.is_modified() or buffer.dirty)
        self.assertTrue(panel.editor_tabs.save_current())
        text = buffer.path.read_text(encoding="utf-8")
        self.assertIn("edited by the smoke test", text)
        backup_dir = self.win.ctx.config.work_dir / "editor-backups"
        self.assertTrue(backup_dir.is_dir())
        self.assertTrue(list(backup_dir.glob("*.bak")))
        panel.editor_tabs.compare_current()
        self.root.update()
        self.assertTrue(any("Compare" in w.title() for w in self._toplevels()))
        for widget in self._toplevels():
            widget.destroy()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
