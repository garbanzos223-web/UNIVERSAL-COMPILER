# Testing

The suite uses the standard library's `unittest`; no test dependency is
required. Run it from the repository root:

```powershell
python run_tests.py              # unit + integration + GUI smoke
python run_tests.py unit
python run_tests.py integration
python run_tests.py smoke
python run_tests.py -v
powershell -File scripts\lint.ps1
```

`run_tests.py` adds `src` to `sys.path`; no editable install is required.

## What each layer covers

### Unit (`tests/unit`)

- `.ece` writer/reader/validator/extractor, corruption handling, signatures,
  path collisions and limits.
- Project detection, toolchain cache, adapters, diagnostics, history, timing.
- CLI argument handling, exit codes and JSON shapes.
- Packaging, repair, optimization and the fusion engine.
- **Decompiler** (`test_decompiler.py`):
  - format detection for pyc, Java class, ELF, ZIP, TAR and ECE;
  - Python bytecode reconstruction and `dis` listing;
  - Java class model + outline from a valid class-file fixture;
  - CArchive and PYZ readers built to the documented structures;
  - `.ece` extraction and recursive recovery;
  - multi-input namespacing;
  - unsupported-format reporting;
  - archive path-traversal rejection.
- **Recompiler** (`test_recompiler.py`):
  - project-kind detection and plans;
  - refusal of loose C sources and unknown projects;
  - real Python `compileall` build with artifact verification;
  - honest failure on a syntax error;
  - manifest gate refusing generated outlines;
  - real .NET `dotnet build` (skips when the SDK is absent).
- **Decompiler CLI** (`test_decompiler_cli.py`): `formats`, analyze-only,
  project output, JSON shapes, missing input, recompile plan and real build.

### Integration (`tests/integration`)

Runs real builds (Python and Node.js, plus `.exe` export when PyInstaller is
present) and skips cleanly when a toolchain is missing. It never asserts a build
that did not run.

### GUI smoke (`tests/smoke`)

- The 1.2 compiler window: dialogs, container validation/extraction, state
  machine, log filters, honest progress, shortcuts, package preview/creation,
  repair.
- **Decompiler tab** (`test_decompiler_gui.py`): constructs the panel, imports a
  real `.pyc`, waits for real analysis and backend output, checks the project and
  editor, exports the project, and runs the recompiler. Folder pickers are
  simulated; no other step is mocked.
- The suite skips itself when no display is available.

## Decompiler fixtures

The tests build their own inputs, so they are deterministic and safe:

- `.pyc` with `py_compile` from a small source file;
- a valid Java class file assembled byte by byte (constant pool, methods, Code
  and SourceFile attributes);
- a minimal ELF64 header;
- a CArchive with real cookie/TOC layout and compressed entries;
- a PYZ with real `marshal` + `zlib` entries;
- a real `.ece` container written by the project's own writer;
- a ZIP containing a path-traversal entry to prove it is skipped.

No fixture is an untrusted executable, and no test runs an imported binary.

## Optional-tool tests

Tests that need an external tool skip with a clear reason:

- `.NET` recompilation: skipped when `dotnet` is missing;
- disassembly: skipped when neither Capstone nor `objdump` exists;
- PyInstaller export: skipped when PyInstaller is missing.

The GUI tests use `ECE_AUTOCOMPILER_HOME` with a temporary directory so they do
not read or write the real application data.

## Lint

`scripts\lint.ps1` runs:

1. `python -m compileall` over `src`, `tests` and `run_tests.py`;
2. `tools/lint_stdlib.py` (no unused imports, no `print()` in the package, no
   `shell=True`, no `os.system`/`eval`/`exec`).

## CI

`.github/workflows/ci.yml` runs lint, unit tests, integration tests and the GUI
smoke on Windows for Python 3.10, 3.11 and 3.12. `.github/workflows/release.yml`
builds and verifies the Windows executables on a version tag and attaches them
to a GitHub release. Workflows use read-only permissions by default and never
print secrets.

## Verified results

Results are reported exactly as observed; they are not pre-filled here. Run the
commands above on your machine. Release builds additionally run
`tools\release_check.py`, which starts the desktop build and asks the console
build for `--version` before a release is considered valid.
