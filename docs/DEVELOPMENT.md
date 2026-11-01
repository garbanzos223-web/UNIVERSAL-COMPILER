# Development guide

## Environment

- Python 3.10–3.12. The project uses only the standard library.
- **Windows 10/11 x64 is the only supported platform.**
- No third-party packages are required to run or test.

Run everything from the source tree:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m ece_autocompiler --version
python -m ece_autocompiler gui
```

## Running tests

Tests use the standard-library `unittest` (no pytest dependency).

```powershell
python run_tests.py                 # unit + integration + GUI smoke
python run_tests.py unit
python run_tests.py integration -v
python run_tests.py smoke           # GUI only; skips without a display
```

Or directly with unittest discovery (the test tree bootstraps `src` onto
`sys.path` itself):

```powershell
python -m unittest discover -s tests -t .
```

The integration tests build real sample projects. The Node ones skip
automatically when Node.js is not installed. The GUI smoke test builds a real
`.ece` container, loads it through the actual `ContainerDialog` (deep
validation included) and performs a real extraction with the folder picker
simulated; it skips itself when no display is available.

## Building the release

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1                  # both builds, onefile
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -Layout onedir  # folder layout
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -Target cli     # console build only
```

`launcher_gui.py` is the windowed entry point, `launcher_cli.py` the console one
(the frozen CLI has no window, so its output and exit codes stay usable from a
script). The script verifies what it built instead of trusting that the files
exist: `tools\release_check.py` runs the console build (`--version`), starts the
desktop build to confirm it stays alive, and writes size and SHA256 for each
artifact to `dist\release.json`. A build that cannot run fails the script, so a
release is never announced on top of a broken artifact.

`build/` and `dist/` are build artifacts and are not committed (see
`.gitignore`). In a frozen build `sys.executable` is the app's own executable,
so anything that must run *outside* the bundle (PyInstaller, a compiler) is
located on `PATH` instead — see `build/exe_exporter.py`.

## Code layout

See [ARCHITECTURE.md](ARCHITECTURE.md). In short:

```
src/ece_autocompiler/
  container/    # .ece format and safety
  extraction/   # safe extractor
  detection/    # detection and toolchain discovery
  adapters/     # build adapters
  build/        # subprocess runner, orchestrator, history
  diagnostics/  # output classification
  security/     # UI/CLI path helpers
  ui/           # Tkinter GUI
  cli.py, app.py
```

## Conventions

- **English** in code, comments, docstrings, docs and commit messages.
- **Standard library only** for runtime code. Do not add a dependency without a
  strong, documented reason; the GUI deliberately uses Tkinter for this reason.
- **No comments that merely restate the code.** Comments explain *why*.
- Commands are **argv lists**, never shell strings.
- Anything that runs project-controlled code must set `requires_approval=True`
  and be reachable only through the consent path.
- Keep modules single-purpose and small; prefer pure functions where possible.

## Adding an adapter

1. **Detection**: add markers in `detection/markers.py` (or directory-shape
   logic in `detection/detector.py`) and, if needed, a tool probe in
   `detection/toolchains.py`.
2. **Adapter**: for a simple CLI tool, register an `ExternalToolAdapter` in
   `adapters/registry.py`. For a richer integration, subclass `BuildAdapter`.
3. **Artifacts**: implement `collect_artifacts` to return the built outputs.
4. **Tests**: add a detection test, and a real build test when the tool is
   installable in CI.
5. **Docs**: update [SUPPORTED_PROJECTS.md](SUPPORTED_PROJECTS.md).

## Changing the container format

`container/spec.py` is the executable reference. If you change the byte layout,
update [ECE_FORMAT.md](ECE_FORMAT.md) in the same change. Backward-compatible
changes bump `FORMAT_MINOR`; breaking changes bump `FORMAT_MAJOR`.

## Lint (stdlib-only)

The repository ships its own zero-dependency lint: byte-compilation plus AST
checks for unused imports, stray `print()` calls, `shell=True`, `os.system`
and `eval`/`exec`:

```powershell
powershell -File scripts\lint.ps1
```

CI runs it on every push. Exit code is non-zero on the first problem.

## Style tools (optional)

`pyproject.toml` configures `ruff` and `mypy` if you have them, but neither is
required to contribute:

```powershell
python -m ruff check src tests
python -m mypy src
```

## Decompiler development

The decompiler is split so that readers and backends can be tested without a
display or an external tool:

```
formats.py          detection only (magic bytes + headers)
analyzer.py         bounded, read-only analysis
pe.py / elf.py / dotnet_metadata.py / java_class.py /
python_bytecode.py / pyinstaller.py / archive.py    readers
backends/           one module per family; writes RecoveredFile records
service.py          batch session, namespacing, manifest
recompiler.py       plan + run_command + artifact verification
ui/decompiler_panel.py, ui/editor.py, ui/dnd.py     the tab
```

### Adding a backend

1. Pick/implement a format kind in `decompiler/formats.py` and list its real
   operations.
2. Create `decompiler/backends/<name>.py` with a `Backend` subclass
   (`id`, `label`, `kinds`, `decompile`).
3. Write files through `BackendContext.write_text/write_bytes/copy_file`; every
   file needs a `category`, a `status` and a `provenance` sentence.
4. Set `output.recompile_ready = True` only when the output is complete,
   compilable source. Add a plan in `decompiler/recompiler.py` for that project
   kind.
5. Register it in `decompiler/backends/__init__.py`.
6. Add tests with a self-built fixture (see `tests/unit/test_decompiler.py`).
   Never execute an imported binary in a test.

### UI rules

- Long work runs through `TaskRunner.run`; never call Tk from the worker.
- Read Tk variables **before** starting the worker and pass copies.
- Enable a button only when the operation can run (backend present, project
  ready, no busy task).
- Report the real result; do not show a success dialog without a verified
  artifact.

### Optional tools

- `pefile` and `capstone` are optional extras; import them inside a `try`.
- ILSpy/`javap`/`objdump` are discovered with `shutil.which` and explicit paths.
- ILSpy installation is a consented action; never install anything silently.

