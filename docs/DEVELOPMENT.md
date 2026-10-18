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
