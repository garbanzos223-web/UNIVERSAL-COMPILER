# Development log

A factual record of the work done in this repository, newest first. It exists
so a later session can recover the real state of the code without trusting
memory.

---

## Session: advanced features (packaging, repair, optimization)

**Goal.** Extend the ECE Compiler with three subsystems on top of the 1.2
usability work: an advanced package generator, an automatic compilation error
analysis and repair engine, and an intelligent compilation optimizer, all
integrated into the CLI, the desktop window and the tests.

### Completed

- **Advanced package generator** (`src/ece_autocompiler/packaging/`):
  - `models.py` - request, metadata, result and the dependency findings.
  - `collection.py` - artifact collection, entry-point detection, include and
    exclude patterns, system-library policy, runtime dependency checks
    (Python requirements, Node.js dependencies).
  - `licenses.py` - license discovery and classification, copyleft warnings.
  - `manifest.py` - `package-manifest.json` (schema `ece.package-manifest/1`)
    with the file list, SHA-256 checksums, toolchain versions and the pinned
    creation time used for reproducible builds.
  - `validation.py` - reads the package back (ZIP, TAR.GZ, folder, PE header,
    DEB signature) and compares it with the manifest.
  - `formats/` - `zip`, `tar.gz`, `folder`, `inno` (Inno Setup), `nsis`,
    `deb`, `appimage`; the registry is read by the CLI, the window and the
    tests.
  - `packager.py` - the flow: detect, optional build, collect, check, stage,
    write the manifest, produce, verify.
- **Repair engine** (`src/ece_autocompiler/repair/`):
  - `diagnostics.py` - parsers for GCC/Clang, MSVC, Cargo, Go, Python, javac
    and TypeScript, plus a generic parser for linker and tool failures; error
    categories.
  - `analysis.py` - groups the diagnostics into root causes, prefers the
    earliest located error and produces the manual actions.
  - `rules.py` - deterministic repairs (`python-missing-colon`,
    `cpp-missing-include`, `go-unused-import`, `csharp-missing-using`,
    `java-missing-import`, `typescript-missing-semicolon`) with unified diffs
    and a confidence level.
  - `engine.py` - backup, apply, rebuild, verify, roll back, journal, retry
    and time limits, loop detection.
- **Optimizer** (`src/ece_autocompiler/optimize/`):
  - `profiles.py` - `debug`, `release`, `performance`, `size`, `balanced`,
    `custom` and the `none` default, per toolchain family.
  - `plan.py` - resolves the profile, detects conflicts and rewrites the build
    plan steps for CMake, Cargo, Go, .NET, Meson and Make.
  - `measure.py` - baseline/optimized comparison; a percentage only when both
    numbers exist, a runtime claim only with a benchmark.
  - `recommend.py` - actionable advice, split into "applied" and "needs your
    approval".
- **Integration**:
  - `build/orchestrator.py` - `compile_project()` extracted so the repair
    engine and the packager reuse the same detect/plan/build/collect flow;
    `build_and_package()` keeps its behaviour and accepts an `optimization`.
  - `build/compile_service.py` - one place that runs a build and returns its
    output, used by the CLI, the window and the repair engine.
  - CLI: `package`, `optimize` and `repair` commands; `build --profile`,
    `build --custom-flags`, `build --repair`, `build --repair-attempts`.
  - Window: **Profile** selector in the build options, **Tools → Create
    distributable package...**, **Build → Optimize and compare...**, and the
    **Attempt automatic repair** button in the failure dialog, with a report
    dialog that shows every diff.
  - `toolchain_hints.py` - install hints for Inno Setup, NSIS, appimagetool
    and dpkg-deb.

### Files changed

- New: `src/ece_autocompiler/packaging/**` (11 files),
  `src/ece_autocompiler/repair/**` (5 files),
  `src/ece_autocompiler/optimize/**` (5 files),
  `src/ece_autocompiler/build/compile_service.py`,
  `tests/unit/test_packaging.py`, `tests/unit/test_repair.py`,
  `tests/unit/test_optimize.py`, `tests/unit/test_cli_advanced.py`,
  `docs/ADVANCED_FEATURES.md`.
- Modified: `src/ece_autocompiler/cli.py`, `config.py` (unchanged),
  `build/orchestrator.py`, `build/__init__.py`, `toolchain_hints.py`,
  `ui/main_window.py`, `tests/unit/test_user_guidance.py`,
  `tests/smoke/test_gui_smoke.py`, `README.md`, `docs/ARCHITECTURE.md`,
  `docs/TROUBLESHOOTING.md`.

### Tests executed

- `python -m unittest discover -s tests -t .` -> **368 tests, OK (skipped=1)**
  (284 before this session).
- `python tools/lint_stdlib.py` -> **83 files clean**.
- Manual end-to-end runs of `package`, `optimize` and `repair` against a
  throwaway project (a real ZIP was produced and verified; a real syntax error
  was repaired and the project rebuilt).

### Bugs found and fixed by the new tests

- The folder format could not be checksummed: `sha256_file` was called on a
  directory. Added `digest_of`/`size_of` for folder packages.
- The Go and Java parsers claimed other compilers' output (a GCC error was
  reported as a Go error). They now only accept their own file extensions, and
  a generic parser covers linker and tool failures that no other parser
  recognised.
- The TypeScript parser duplicated MSVC diagnostics; it now requires a `TS`
  code.
- A repair whose new output could not be parsed was counted as "resolved".
  The engine now rolls the change back when it cannot verify it.
- `tests/unit/test_user_guidance.py` wrote to the **real** history file
  (`%LOCALAPPDATA%`), because the CLI's `AppConfig` was not patched. Fixed:
  the test now points the configuration at a temporary folder.

### Known limitations

- `inno`, `nsis`, `deb` and `appimage` need their external tool; without it the
  format is refused with an install hint. The DEB and AppImage layouts are
  still written so they can be packaged on Linux.
- The repair rules are intentionally few; everything else is explained.
- Optimization flags are known for CMake, Cargo, Go, .NET, Meson and Make.

### Next recommended task

- Add the package generator to the release build check
  (`tools/release_check.py`), so a release also verifies that a real package
  can be produced.
- Extend the repair rules with more compilers (MSVC include paths, Go build
  tags) once a real failing project of that kind is available.

---

## Session: advanced features, part 2 (correctness and performance)

**Goal.** Review the new code for defects, fix the ones the tests and the
measurements revealed, and remove the slowest avoidable work from the command
line.

### Correctness fixes

- **System-library detection was a substring match.** A project in a folder
  called `Program Files Development` was treated as a system file and left out
  of the package. `is_system_path()` now compares against the real system
  roots (`C:\Windows`, `C:\Program Files`, `/usr`, `/lib`, ...) and the POSIX
  check runs on the original path, so it also works on Windows.
- **A repair that could not be parsed counted as "resolved".** When the new
  build failed with output the parsers did not understand, the engine kept the
  change. It now rolls the change back: a repair that cannot be verified is
  not a repair.
- **`_run_tests` could raise `IndexError`** when the test command produced no
  step result.
- **The packager hid the consent error.** A C++/Node project that needs
  consent now gets the same explanation as the build, with the suggestion to
  package what is already built.
- **The history could not replay a package run.** `BuildRecord` gained
  `format` and `profile`, and `replay_command` now produces
  `package "<path>" --format tar.gz` and `build "<path>" --profile release`
  instead of always saying `build`.

### Real functional gap fixed: the CMake adapter never built anything

`_default_steps()` for `cpp-cmake` only *configured* the project
(`cmake -S . -B build`). The build step was missing, so a C++ project was
"built" without a single object file being compiled.

- The plan now has `cmake-configure` **and** `cmake-build`
  (`cmake --build build --config Release --parallel N`).
- Both steps require consent: configuring runs the project's
  `CMakeLists.txt`, which is project-controlled code.
- `make` builds with `-j N`, and a Meson project is configured with
  `meson setup` and built with `ninja -C build -j N`.
- Meson is now a detected toolchain with its own install hint.

### Performance: the toolchain probe cache

Probing every tool starts one process per tool. Measured on this machine
(`tools` command, warm file system):

| Command | Before | After (cached) |
| --- | --- | --- |
| `tools` | 1392 ms | **173 ms** |
| `doctor` | 1419 ms | **212 ms** |
| `analyze` | 2029 ms | **765 ms** |

The probe results are cached in
`%LOCALAPPDATA%\ECE-Universal-AutoCompiler\config\toolchains.json` for 15
minutes. The cache is dropped automatically when `PATH` changes (that is what
happens when a tool is installed) and can always be dropped by hand:

```powershell
python -m ece_autocompiler tools --refresh
python -m ece_autocompiler doctor --refresh
```

The **Rescan** button in the toolchain dialog now really re-probes (it only
re-read the in-process cache before).

### Tests executed

- `python -m unittest discover -s tests -t .` -> **385 tests, OK (skipped=1)**.
- `python tools/lint_stdlib.py` -> **84 files clean**.

### Next recommended task

- Add the package generator to `tools/release_check.py`.
- Give the `.ece` container the same "verified package" treatment in the
  release check.

---

## Session: release build verified with the new features

**Goal.** Prove that the shipped executables really contain the new
subsystems and that they work, instead of trusting that the build succeeded.

### What was done

- `scripts\build_exe.ps1` was run from scratch: both binaries were rebuilt
  (`dist\ECE-AutoCompiler.exe`, 10.95 MB, and
  `dist\ECE-AutoCompiler-cli.exe`, 10.98 MB).
- `tools\release_check.py` now includes a **functional probe**: the shipped
  console build has to build a throwaway project, validate the container deeply
  and create a real ZIP package with its manifest. The probe runs with
  `ECE_AUTOCOMPILER_HOME` pointing at a temporary folder, so it never touches
  the real application data.
- The probe passed:
  `built demo.ece (658 bytes); validated the container (deep); packaged demo-1.0.0.zip with its manifest`.
- The frozen console build was also exercised by hand with the new commands:
  `package` produced and verified a ZIP, and `repair --apply` parsed a real
  syntax error, proposed the missing colon with a diff, applied it, rebuilt
  the project and kept the change.

### Tests executed

- `python -m unittest discover -s tests -t .` -> **392 tests, OK (skipped=1)**.
- `python tools/lint_stdlib.py` -> **85 files clean**.
- `powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1` -> both
  binaries built and verified.

### Notes

- The `dist\` binaries are build artifacts and are not committed. They are now
  current with the 1.2 source tree and can be attached to the release.

---

## Session: GUI smoke coverage for the new features

**Goal.** The new dialogs were only constructed by the smoke test; the code
paths behind them were never run.

### What was done

Three GUI smoke tests now drive the real workflows:

- the **package dialog** creates a real ZIP package of a throwaway project and
  the test checks the archive and its manifest;
- the **optimization dialog** explains that an interpreter project has no
  compiler options, instead of pretending to optimize something;
- the **Attempt automatic repair** action repairs a project with a real syntax
  error through the window and shows the report dialog.

The GUI smoke test also no longer touches the real application data of the
machine: it sets `ECE_AUTOCOMPILER_HOME` to a temporary folder for the whole
class.

### Tests executed

- `python -m unittest discover -s tests -t .` -> **403 tests, OK (skipped=1)**.
- `python tools/lint_stdlib.py` -> **85 files clean**.

---

## Session: the project scan was 5x slower than it had to be

**Goal.** Profile the command line on a large tree and remove the avoidable
work.

### Measurement

A synthetic project with 3000 Python files in 60 folders:

| Phase | Before | After |
| --- | --- | --- |
| `scan()` (detection) | 207 ms | **39 ms** |
| full `build` (warm) | 808 ms | **430 ms** |

The raw `os.walk` of the same tree takes 8 ms, so the detection logic - not the
disk - was the bottleneck.

### What was wrong

- `_relative()` used `Path.relative_to()`, which is a pure-Python pathlib
  operation and was called once per file (3063 calls, ~180 µs each).
  `os.path.relpath()` does the same job as a string operation.
- `_walk()` rebuilt the relative path of every file from scratch; the
  directory prefix is now computed once per folder.
- `_match_markers()` rebuilt the set of file names of a directory **for every
  marker** (30 markers x 60 folders). The names and suffixes of a folder are
  now indexed once.
- `_detect_by_sources()` compared every file against every language's suffix
  list (14 comparisons per file). There is now one dictionary lookup per
  file.

### Behaviour that changed on purpose

The evidence of a detection now shows the **real file name** with its original
case (`CMakeLists.txt` instead of `cmakelists.txt`), because the index keeps
the original name next to the lowercased one.

### Tests executed

- `python -m unittest discover -s tests -t .` -> **406 tests, OK (skipped=1)**.
- `python tools/lint_stdlib.py` -> **85 files clean**.

---

## Session: 1.2 usability release (40 improvements)

See [docs/RELEASE_NOTES_1.2.md](RELEASE_NOTES_1.2.md) for the user-facing
summary and [docs/NEW_VISUALS_AND_USEFUL_TOOLS.md](NEW_VISUALS_AND_USEFUL_TOOLS.md)
for the plan. Commits `41950bb` (implementation) and `a848d02` (release notes).
