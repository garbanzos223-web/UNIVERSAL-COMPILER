"""Integrated source editor for reconstructed projects.

A Tk text widget with syntax highlighting, undo, find, automatic backups and
multiple tabs. It edits the files written by the decompiler; nothing is loaded
from an untrusted binary as if it were source (only generated text files are
opened here).
"""

from __future__ import annotations

import ast
import difflib
import json
import re
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..version import APP_NAME
from .theme import COLORS, configure_text_widget


def language_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".py": "python", ".java": "java", ".cs": "csharp", ".c": "c", ".h": "c",
        ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".js": "javascript",
        ".ts": "typescript", ".json": "json", ".xml": "xml", ".csproj": "xml",
        ".sln": "xml", ".html": "html", ".css": "css", ".md": "markdown",
        ".txt": "text", ".asm": "asm", ".go": "go", ".rs": "rust", ".php": "php",
    }.get(suffix, "text")


_KEYWORDS = {
    "python": "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case",
    "java": "abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while var record sealed permits yield",
    "csharp": "abstract as base bool break byte case catch char checked class const continue decimal default delegate do double else enum event explicit extern false finally fixed float for foreach goto if implicit in int interface internal is lock long namespace new null object operator out override params private protected public readonly ref return sbyte sealed short sizeof stackalloc static string struct switch this throw true try typeof uint ulong unchecked unsafe ushort using virtual void volatile while var async await record init",
    "c": "auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while",
    "cpp": "alignas alignof and asm auto bool break case catch char class const constexpr continue decltype default delete do double else enum explicit export extern false float for friend goto if inline int long mutable namespace new noexcept nullptr operator private protected public register reinterpret_cast return short signed sizeof static static_cast struct switch template this throw true try typedef typename union unsigned using virtual void volatile wl",
    "javascript": "async await break case catch class const continue debugger default delete do else export extends false finally for function if import in instanceof let new null of return static super switch this throw true try typeof var void while with yield",
    "typescript": "abstract any as async await boolean break case catch class const continue declare default delete do else enum export extends false finally for from function if implements import in instanceof interface let module namespace never new null number object of private protected public readonly return static string super switch symbol this throw true try type typeof undefined union unknown var void while yield",
    "go": "break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var true false nil",
    "rust": "as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while",
    "php": "abstract and array as break callable case catch class clone const continue declare default do echo else elseif empty enddeclare endfor endforeach endif endswitch endwhile eval exit extends final finally fn for foreach function global goto if implements include include_once instanceof insteadof interface isset list match namespace new null or print private protected public readonly require require_once return static switch throw trait true try unset use var while xor yield",
    "asm": "mov lea push pop call ret jmp je jne jz jnz cmp test add sub xor and or shl shr imul idiv nop int syscall leave enter",
}
_BUILTINS = {
    "python": "print len range str int float list dict set tuple open super isinstance getattr setattr hasattr repr enumerate zip map filter sum min max abs any all type id sorted reversed iter next",
    "java": "System String Object Integer Long Double Boolean Math Exception RuntimeException List Map Set ArrayList HashMap",
    "csharp": "System Console Math String Console.WriteLine Enumerable Task",
}
_NUMBER = re.compile(r"\b(?:0x[0-9A-Fa-f]+|\d+(?:\.\d+)?)\b")
_STRING = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
_COMMENT_LINE = re.compile(r"(?m)^\s*#.*$|//.*$")
_COMMENT_BLOCK = re.compile(r"/\*[\s\S]*?\*/")
_TRIPLE = re.compile(r'(?:"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')')


@dataclass
class EditorBuffer:
    path: Path
    language: str
    original_text: str
    text: str
    dirty: bool = False
    backup: Path | None = None
    frame: ttk.Frame | None = None
    text_widget: tk.Text | None = None
    tab_id: str = ""
    tags_applied: bool = field(default=False)


class CodeEditor(ttk.Frame):
    """A text widget with syntax highlighting and a position indicator."""

    def __init__(self, parent: tk.Misc, *, language: str = "text", on_change=None,
                 on_cursor=None) -> None:
        super().__init__(parent)
        self.language = language
        self._on_change = on_change
        self._on_cursor = on_cursor
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.text = tk.Text(self, wrap="none", undo=True, maxundo=500, autoseparators=True)
        configure_text_widget(self.text)
        self.text.configure(font=("Consolas", 10), tabs=("4c",))
        self.text.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

        self._configure_tags()
        self.text.bind("<<Modified>>", self._on_modified)
        self.text.bind("<KeyRelease>", self._cursor_moved)
        self.text.bind("<ButtonRelease-1>", self._cursor_moved)
        self.text.bind("<Tab>", self._insert_indent)
        self.text.bind("<Control-f>", lambda _e: self.find())
        self._find_tag = "find_match"
        self.text.tag_configure(self._find_tag, background=COLORS["accent_fill"], foreground="#ffffff")
        self.text.tag_configure("syntax_error", background="#5a1d1d", foreground="#ffffff")

    def _configure_tags(self) -> None:
        self.text.tag_configure("kw", foreground="#79b8ff")
        self.text.tag_configure("builtin", foreground="#d2a8ff")
        self.text.tag_configure("string", foreground="#a5d6a7")
        self.text.tag_configure("comment", foreground="#7d8799", font=("Consolas", 10, "italic"))
        self.text.tag_configure("number", foreground="#f0b37e")
        self.text.tag_raise("comment")
        self.text.tag_raise("string")

    # ------------------------------------------------------------- content
    def set_text(self, content: str) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.edit_reset()
        self.highlight()
        self.text.edit_modified(False)

    def get_text(self) -> str:
        return self.text.get("1.0", "end-1c")

    def mark_clean(self) -> None:
        self.text.edit_modified(False)

    def is_modified(self) -> bool:
        try:
            return bool(self.text.edit_modified())
        except tk.TclError:  # pragma: no cover
            return False

    def _on_modified(self, _event=None) -> None:
        if self.text.edit_modified():
            self.highlight()
            if self._on_change:
                self._on_change()
        self.text.edit_modified(False)

    def _cursor_moved(self, _event=None) -> None:
        try:
            index = self.text.index("insert")
        except tk.TclError:
            return
        line, column = index.split(".")
        if self._on_cursor:
            self._on_cursor(int(line), int(column) + 1)

    def _insert_indent(self, _event=None):
        self.text.insert("insert", "    ")
        return "break"

    # --------------------------------------------------------- highlighting
    def highlight(self) -> None:
        content = self.text.get("1.0", "end-1c")
        if len(content) > 2_000_000:
            return
        for tag in ("kw", "builtin", "string", "comment", "number"):
            self.text.tag_remove(tag, "1.0", "end")
        language = self.language
        keywords = _KEYWORDS.get(language, "")
        if keywords:
            pattern = re.compile(r"\b(?:" + "|".join(re.escape(word) for word in keywords.split()) + r")\b")
            self._apply(pattern, "kw", content)
        builtins = _BUILTINS.get(language, "")
        if builtins:
            pattern = re.compile(r"\b(?:" + "|".join(re.escape(word) for word in builtins.split()) + r")\b")
            self._apply(pattern, "builtin", content)
        self._apply(_NUMBER, "number", content)
        if language in ("python",):
            self._apply(_TRIPLE, "string", content)
        self._apply(_STRING, "string", content)
        if language in ("python", "ruby"):
            self._apply(_COMMENT_LINE, "comment", content)
        else:
            self._apply(_COMMENT_LINE, "comment", content)
            self._apply(_COMMENT_BLOCK, "comment", content)
        self.text.tag_raise("comment")
        self.text.tag_raise("string")

    def _apply(self, pattern: re.Pattern, tag: str, content: str) -> None:
        for match in pattern.finditer(content):
            start = self.text.index(f"1.0+{match.start()}c")
            end = self.text.index(f"1.0+{match.end()}c")
            self.text.tag_add(tag, start, end)

    # ----------------------------------------------------------------- find
    def find(self, term: str | None = None) -> None:
        FindBar(self, term or "")

    # --------------------------------------------------------- syntax check
    def check_syntax(self) -> tuple[bool, str]:
        """Check Python/JSON syntax after a save; highlight the failing line.

        Other languages are reported as "not checked here" rather than
        pretending to have validated them.
        """
        content = self.get_text()
        self.text.tag_remove("syntax_error", "1.0", "end")
        error: SyntaxError | ValueError | None = None
        if self.language == "python":
            try:
                ast.parse(content)
            except SyntaxError as exc:
                error = exc
        elif self.language == "json":
            try:
                json.loads(content)
            except ValueError as exc:
                error = exc
        else:
            return True, "Syntax check is only available for Python and JSON here."
        if error is None:
            return True, "Syntax OK."
        line = int(getattr(error, "lineno", 1) or 1)
        self.text.tag_add("syntax_error", f"{line}.0", f"{line}.end+1c")
        self.text.see(f"{line}.0")
        return False, f"Syntax error on line {line}: {getattr(error, 'msg', str(error))}"


class FindBar(tk.Toplevel):
    """Small find-in-file dialog; highlights every match and jumps to the next."""

    def __init__(self, editor: CodeEditor, term: str) -> None:
        super().__init__(editor.winfo_toplevel())
        self.title(f"{APP_NAME} — Find")
        self.configure(bg=COLORS["bg"])
        self.transient(editor)
        self.editor = editor
        self.var = tk.StringVar(value=term)
        row = ttk.Frame(self, padding=10)
        row.pack(fill="both", expand=True)
        ttk.Label(row, text="Find").pack(side="left", padx=(0, 6))
        entry = ttk.Entry(row, textvariable=self.var, width=42)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e: self._next())
        ttk.Button(row, text="Find next", command=self._next).pack(side="left", padx=6)
        ttk.Button(row, text="Close", style="Ghost.TButton", command=self.destroy).pack(side="left")
        entry.focus_set()
        self._current = "1.0"

    def _next(self) -> None:
        term = self.var.get()
        if not term:
            return
        self.editor.text.tag_remove("find_match", "1.0", "end")
        content = self.editor.text.get("1.0", "end-1c")
        for match in re.finditer(re.escape(term), content):
            start = self.editor.text.index(f"1.0+{match.start()}c")
            end = self.editor.text.index(f"1.0+{match.end()}c")
            self.editor.text.tag_add("find_match", start, end)
        found = self.editor.text.search(term, self._current, stopindex="end", nocase=True)
        if not found:
            found = self.editor.text.search(term, "1.0", stopindex="end", nocase=True)
        if found:
            end = f"{found}+{len(term)}c"
            self.editor.text.see(found)
            self.editor.text.tag_add("sel", found, end)
            self._current = end


class EditorTabs(ttk.Frame):
    """Multiple editable files with save, save-as, backups and compare."""

    def __init__(self, parent: tk.Misc, *, backup_dir: Path, on_status=None) -> None:
        super().__init__(parent)
        self.backup_dir = Path(backup_dir)
        self.on_status = on_status or (lambda _message: None)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(bar, text="Save", command=self.save_current).pack(side="left")
        ttk.Button(bar, text="Save as...", command=self.save_as_current).pack(side="left", padx=6)
        ttk.Button(bar, text="Find", command=self.find_current).pack(side="left")
        ttk.Button(bar, text="Compare with original", command=self.compare_current).pack(side="left", padx=6)
        ttk.Button(bar, text="Close tab", command=self.close_current).pack(side="left")
        self.position_var = tk.StringVar(value="Ln 1, Col 1")
        ttk.Label(bar, textvariable=self.position_var, style="Muted.TLabel").pack(side="right")

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.buffers: dict[str, EditorBuffer] = {}

    # ------------------------------------------------------------------ tabs
    def open_file(self, path: Path, *, language: str | None = None) -> EditorBuffer | None:
        path = Path(path)
        key = str(path.resolve()) if path.exists() else str(path)
        if key in self.buffers:
            buffer = self.buffers[key]
            self.notebook.select(buffer.frame)
            return buffer
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            messagebox.showerror(APP_NAME, f"Could not open {path}:\n{exc}", parent=self)
            return None
        frame = ttk.Frame(self.notebook)
        editor = CodeEditor(
            frame,
            language=language or language_from_path(path),
            on_change=lambda b_key=key: self._on_change(b_key),
            on_cursor=self._update_position,
        )
        editor.pack(fill="both", expand=True)
        editor.set_text(content)
        self.notebook.add(frame, text=path.name)
        buffer = EditorBuffer(
            path=path,
            language=editor.language,
            original_text=content,
            text=content,
            frame=frame,
            text_widget=editor.text,
            tab_id=str(frame),
        )
        buffer.editor = editor  # type: ignore[attr-defined]
        self.buffers[key] = buffer
        self.notebook.select(frame)
        self._update_position(1, 1)
        return buffer

    def open_text(self, name: str, content: str, *, path: Path | None = None,
                  language: str = "text") -> EditorBuffer:
        """Open an in-memory document (for example a not-yet-exported file)."""
        key = str(path) if path else f"memory:{name}"
        if key in self.buffers:
            self.notebook.select(self.buffers[key].frame)
            return self.buffers[key]
        frame = ttk.Frame(self.notebook)
        editor = CodeEditor(
            frame, language=language,
            on_change=lambda b_key=key: self._on_change(b_key),
            on_cursor=self._update_position,
        )
        editor.pack(fill="both", expand=True)
        editor.set_text(content)
        self.notebook.add(frame, text=name)
        buffer = EditorBuffer(
            path=path or Path(name),
            language=language,
            original_text=content,
            text=content,
            frame=frame,
            text_widget=editor.text,
            tab_id=str(frame),
        )
        buffer.editor = editor  # type: ignore[attr-defined]
        self.buffers[key] = buffer
        self.notebook.select(frame)
        return buffer

    def current(self) -> EditorBuffer | None:
        selected = self.notebook.select()
        if not selected:
            return None
        for buffer in self.buffers.values():
            if buffer.frame is not None and str(buffer.frame) == str(selected):
                return buffer
        return None

    def _on_change(self, key: str) -> None:
        buffer = self.buffers.get(key)
        if buffer is None:
            return
        buffer.dirty = True
        buffer.text = buffer.editor.get_text()  # type: ignore[attr-defined]
        self._refresh_title(buffer)

    def _refresh_title(self, buffer: EditorBuffer) -> None:
        if buffer.frame is None:
            return
        marker = "*" if buffer.dirty else ""
        try:
            self.notebook.tab(buffer.frame, text=f"{buffer.path.name}{marker}")
        except tk.TclError:  # pragma: no cover
            pass

    def _update_position(self, line: int, column: int) -> None:
        self.position_var.set(f"Ln {line}, Col {column}")

    # ---------------------------------------------------------------- saving
    def _backup(self, buffer: EditorBuffer) -> None:
        if buffer.backup is not None:
            return
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            target = self.backup_dir / f"{buffer.path.stem}-{stamp}{buffer.path.suffix}.bak"
            target.write_text(buffer.original_text, encoding="utf-8")
            buffer.backup = target
        except OSError:
            buffer.backup = None

    def save_current(self) -> bool:
        buffer = self.current()
        if buffer is None:
            self.on_status("No editor tab is open.")
            return False
        if not buffer.path.name or str(buffer.path).startswith("memory:"):
            return self.save_as_current()
        return self._write(buffer, buffer.path)

    def save_as_current(self) -> bool:
        buffer = self.current()
        if buffer is None:
            return False
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save recovered file as",
            initialfile=buffer.path.name,
            defaultextension=buffer.path.suffix or ".txt",
        )
        if not path:
            return False
        return self._write(buffer, Path(path))

    def _write(self, buffer: EditorBuffer, path: Path) -> bool:
        content = buffer.editor.get_text()  # type: ignore[attr-defined]
        self._backup(buffer)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not save the file:\n{exc}", parent=self)
            return False
        buffer.path = path
        buffer.text = content
        buffer.dirty = False
        buffer.editor.mark_clean()
        self._refresh_title(buffer)
        ok, message = buffer.editor.check_syntax()  # type: ignore[attr-defined]
        self.on_status(f"Saved {path} · {message}" if ok else f"Saved {path} · {message}")
        return True

    def close_current(self) -> None:
        buffer = self.current()
        if buffer is None or buffer.frame is None:
            return
        if buffer.dirty and not messagebox.askokcancel(
            APP_NAME, f"{buffer.path.name} has unsaved changes.\n\nClose it anyway?", parent=self
        ):
            return
        key = next((k for k, value in self.buffers.items() if value is buffer), None)
        self.notebook.forget(buffer.frame)
        buffer.frame.destroy()
        if key:
            self.buffers.pop(key, None)

    # ------------------------------------------------------------------ find
    def find_current(self) -> None:
        buffer = self.current()
        if buffer is not None:
            buffer.editor.find()  # type: ignore[attr-defined]

    # --------------------------------------------------------------- compare
    def compare_current(self) -> None:
        buffer = self.current()
        if buffer is None:
            return
        current_text = buffer.editor.get_text()  # type: ignore[attr-defined]
        diff = list(
            difflib.unified_diff(
                buffer.original_text.splitlines(),
                current_text.splitlines(),
                fromfile=f"{buffer.path.name} (recovered)",
                tofile=f"{buffer.path.name} (edited)",
                lineterm="",
            )
        )
        window = tk.Toplevel(self)
        window.title(f"{APP_NAME} — Compare")
        window.geometry("900x560")
        window.configure(bg=COLORS["bg"])
        header = ttk.Frame(window, style="Header.TFrame", padding=(12, 8))
        header.pack(fill="x")
        ttk.Label(header, text=f"Compare: {buffer.path.name}", style="Dialog.TLabel").pack(side="left")
        box = tk.Text(window, wrap="none")
        configure_text_widget(box)
        box.pack(fill="both", expand=True, padx=12, pady=12)
        box.insert("1.0", "\n".join(diff) or "(no changes)")
        box.tag_configure("add", foreground=COLORS["ok"])
        box.tag_configure("del", foreground=COLORS["err"])
        for index, line in enumerate(diff):
            if line.startswith("+") and not line.startswith("+++"):
                box.tag_add("add", f"{index + 1}.0", f"{index + 1}.end")
            elif line.startswith("-") and not line.startswith("---"):
                box.tag_add("del", f"{index + 1}.0", f"{index + 1}.end")
        box.configure(state="disabled")

    def save_all(self) -> int:
        saved = 0
        for buffer in list(self.buffers.values()):
            if buffer.dirty and buffer.path and not str(buffer.path).startswith("memory:"):
                if self._write(buffer, buffer.path):
                    saved += 1
        return saved
