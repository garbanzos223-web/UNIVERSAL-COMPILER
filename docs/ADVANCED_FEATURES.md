# Advanced features: packaging, repair and optimization

This document describes the three subsystems that extend the build pipeline:

1. the **advanced package generator** (`packaging`);
2. the **automatic error analysis and repair engine** (`repair`);
3. the **intelligent compilation optimizer** (`optimize`).

All three are honest by design: a package is only reported as successful after
it has been read back and verified, a repair is only reported as successful
after the project has been rebuilt, and an optimization is only reported as
faster after a benchmark actually ran.

---

## 1. Advanced package generator

### What it does

`packaging` turns a project into a distributable package:

1. detect the project and select the adapter (same detection as the build);
2. optionally run the real build (`--build`), with the usual consent rules;
3. collect the compiled artifacts, the assets and the **entry point**;
4. check the **runtime dependencies** and the **redistribution licenses**;
5. stage the files in a clean, organised folder;
6. write `package-manifest.json` (metadata, files, checksums, toolchains);
7. produce the package in the requested format;
8. **read the package back** and verify every file against the manifest.

### Formats

| id | Format | Needs | Notes |
| --- | --- | --- | --- |
| `zip` | ZIP archive | nothing (stdlib) | The default; works everywhere. |
| `tar.gz` | TAR.GZ archive | nothing (stdlib) | Keeps UNIX permissions. |
| `folder` | Application folder | nothing | An unpacked, ready-to-run directory. |
| `inno` | Windows installer | Inno Setup (`iscc`) | Generates an `.iss` script and compiles it. |
| `nsis` | Windows installer | NSIS (`makensis`) | Generates an `.nsi` script and compiles it. |
| `deb` | Debian package | `dpkg-deb` | `DEBIAN/control` + payload under `/usr/lib/<name>`. |
| `appimage` | AppImage | `appimagetool` | Builds an AppDir, then packages it. |

When the external tool of a format is missing, the command says exactly what
is missing and how to install it (`winget` / `choco` / `scoop` plus the
official URL). It never produces a fake installer.

### Reproducible packages

With the default (reproducible) mode, two runs over the same inputs produce a
**byte-identical** archive:

* archive entries are sorted;
* ZIP timestamps are pinned to 1980-01-01 and the creator system is fixed;
* TAR entries have `mtime=0`, `uid=gid=0` and no owner names;
* the gzip header carries no file name and no timestamp;
* the manifest's creation time is pinned (honouring `SOURCE_DATE_EPOCH`).

`--no-reproducible` writes the real creation time instead.

### Metadata, inclusion and exclusion

| Option | Meaning |
| --- | --- |
| `--name`, `--version`, `--description`, `--publisher` | Application identity written into the manifest and the installer scripts. |
| `--arch` | `x64`, `x86` or `arm64` (mapped to `amd64`/`i386`/`arm64` for DEB). |
| `--icon`, `--license`, `--readme` | Extra files added to the package (`LICENSE`, `README.md`, the icon). |
| `--include PATTERN` | Keep **only** the files that match (repeatable). |
| `--exclude PATTERN` | Leave out the files that match (repeatable). |
| `--allow-copyleft` | Confirm that a copyleft license may be redistributed. |

Patterns are matched against the path inside the package (`build/*`,
`*.log`). Cache folders (`__pycache__`, `node_modules`, `.git`, virtual
environments) are always excluded.

### Dependency and license checks

* **Missing runtime dependencies**: Python requirements that are not installed
  in the running interpreter and Node.js dependencies that are not in
  `node_modules` are reported with the exact command that installs them.
* **System libraries are never bundled**: files under `C:\Windows`,
  `C:\Program Files`, `/usr`, `/lib`, `/opt` and friends are left out and
  reported, because bundling them breaks the target machine.
* **Redistribution licenses**: the license files of the project are found and
  classified (MIT, BSD, Apache-2.0, GPL, LGPL, MPL, ...). A copyleft license
  produces a warning that asks for `--allow-copyleft`; a missing or
  unidentifiable license is reported too.

### Commands

```powershell
# A ZIP package of what is already built
python -m ece_autocompiler package path\to\project --output-dir out

# Build first, then package as a Windows installer
python -m ece_autocompiler package path\to\project --format inno --build --consent

# Only the files that match, with a reproducible archive
python -m ece_autocompiler package path\to\project --include "dist/*" --include "README.md"

# Machine-readable result
python -m ece_autocompiler package path\to\project --json
```

In the desktop window: **Tools → Create distributable package...**

---

## 2. Automatic error analysis and repair

### What it does

1. run the normal build;
2. detect the failure and collect the **full compiler output**;
3. parse it into structured diagnostics (file, line, column, code, category);
4. group the diagnostics into probable **root causes** and pick the earliest
   meaningful error;
5. propose the **minimal** repair;
6. apply it (only high-confidence repairs are applied automatically);
7. rebuild the project;
8. verify that the targeted error is gone;
9. keep the change and continue, or roll it back;
10. stop at the retry limit, the time limit or when no safe repair is left.

### Supported diagnostic formats

| Compiler | Example shape |
| --- | --- |
| GCC / Clang | `main.cpp:12:5: error: 'uint32_t' does not name a type` |
| MSVC | `Program.cs(7,13): error CS0103: The name 'Console' does not exist` |
| Cargo / Rust | `error[E0308]: mismatched types` + `--> src/main.rs:4:9` |
| Go | `main.go:10:2: undefined: fmt.Println` |
| Python | `File "app.py", line 4` + `SyntaxError: expected ':'` |
| javac | `src/Main.java:12: error: cannot find symbol` |
| Maven / Gradle | `[ERROR] /work/src/Main.java:[12,5] cannot find symbol` |
| TypeScript | `app.ts(3,5): error TS2322: Type 'string' is not assignable` |
| Node.js runtime | `ReferenceError: x is not defined` + `at Object.<anonymous> (app.js:3:11)` |
| Linkers and tools | `undefined reference to ...`, `ld returned 1 exit status`, `CMake Error at ...`, `command not found` |

Diagnostics are classified into: syntax, missing import, undefined identifier,
type mismatch, invalid signature, linker, missing dependency, incorrect flags,
incompatible toolchain.

### Automatic repairs

| Rule | What it fixes | Confidence |
| --- | --- | --- |
| `python-missing-colon` | A block header without its `:` | high |
| `cpp-missing-include` | A missing `<cstdint>`, `<cstddef>`, `<string>`, `<vector>`, ... | high |
| `go-unused-import` | An import the Go compiler says is unused | high |
| `csharp-missing-using` | A missing `using System;` (and other known namespaces) | high |
| `java-missing-import` | A missing `import java.util.List;` (and other known classes) | high |
| `typescript-missing-semicolon` | A missing `;` | medium (shown, never applied automatically) |

Everything else is **explained, not changed**: the analysis lists what to do
(install a module, update the toolchain, fix the types, ...).

### Safety

* The original file is copied to
  `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\work\repair-backups\<run>\` before
  it is touched.
* A repair that does not remove the error it targeted is **rolled back**.
* A repair that makes the test suite fail (when a test command is given) is
  **rolled back**.
* The same error twice in a row stops the loop.
* The number of attempts (`--max-attempts`, default 3) and the total time
  (`--timeout`, default 300 s) are limited.
* Every attempt is recorded in
  `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\work\repair-journal.jsonl` with the
  original error, the proposed change, the files modified and the
  verification result.
* The repair is **never** applied without an explicit request: the CLI needs
  `--apply`, the window needs the **Attempt automatic repair** button.

### Commands

```powershell
# Analyse only: shows the proposed diff, changes nothing
python -m ece_autocompiler repair path\to\project

# Apply the safe repairs and rebuild
python -m ece_autocompiler repair path\to\project --apply --max-attempts 3

# Undo a change that breaks the tests
python -m ece_autocompiler repair path\to\project --apply --tests "python -m pytest -q"

# As part of a normal build
python -m ece_autocompiler build path\to\project --repair --consent
```

In the desktop window: the **Attempt automatic repair** button in the failure
dialog. The report shows every diff and whether the change was kept or
reverted.

---

## 3. Intelligent compilation optimizer

### Profiles

| Profile | Effect |
| --- | --- |
| `none` (default) | Nothing is added; the project builds exactly as it asks. |
| `debug` | Fast build, full debug information (`-g`, `CMAKE_BUILD_TYPE=Debug`, `cargo` dev profile). |
| `release` | Optimized production build (`-O2 -DNDEBUG`, `Release`, `cargo build --release`). |
| `performance` | Runtime speed first (`-O3`, LTO, tiered PGO). **Needs approval.** |
| `size` | Smallest binary (`-Os`, `MinSizeRel`, `strip`, trimmed .NET publish). **Needs approval.** |
| `balanced` | The middle ground (`-O2`, thin LTO). |
| `custom` | Exactly the options you pass (`--custom-flags`). |

Interpreter projects (Python, Node.js, PHP) and build systems without
compiler flags (Maven, Gradle) report **"not applied"** with the reason
instead of pretending to optimize something.

### How the flags reach the build

The optimizer does not rewrite the project: it adjusts the commands of the
existing build plan.

| Toolchain | What is changed |
| --- | --- |
| CMake | `-DCMAKE_BUILD_TYPE=<type>`, `-DCMAKE_C_FLAGS` / `-DCMAKE_CXX_FLAGS`, `--config <type>` |
| Cargo | `--release` (or removed for `debug`), `CARGO_PROFILE_RELEASE_*` environment variables |
| Go | `-trimpath`, `-gcflags=all=-N -l` (debug), `-ldflags=-s -w` (size) |
| .NET | `-c <Debug|Release>`, `-p:TieredPGO=true`, `-p:PublishTrimmed=true`, ... |

Conflicting options are reported, not silently applied: two different `-O`
levels, `-g` in an optimized build, stripping symbols while keeping debug
information, or `-march=native` (which would make the binary machine
specific).

The options are also checked against the toolchain that is really installed:
a Cargo older than 1.21 cannot do link-time optimization, tiered PGO needs the
.NET 8 SDK, and a missing toolchain is reported as *"could not be checked"*
rather than assumed to be fine.

### Measurement

`optimize` builds the project **twice** - once as the baseline, once with the
profile - and compares:

* compilation duration;
* total output size;
* the test result (when a test command is given);
* the runtime (when a benchmark command is given).

A percentage is only produced when both numbers exist and are positive. A
runtime claim is only produced when a benchmark actually ran; otherwise the
report says *"Runtime: not measured (no benchmark command was run)"*.

### Commands

```powershell
# Compare the baseline with a release build
python -m ece_autocompiler optimize path\to\project --profile release

# With tests and a benchmark
python -m ece_autocompiler optimize path\to\project --profile size `
    --tests "python -m pytest -q" --benchmark "python bench.py"

# Use a profile for a normal build
python -m ece_autocompiler build path\to\project --profile balanced --consent

# Your own flags
python -m ece_autocompiler build path\to\project --profile custom --custom-flags "-O2" --custom-flags "-Wall"
```

In the desktop window: the **Profile** selector in the build options, and
**Build → Optimize and compare...** for the full comparison.

---

## Known limitations

* The installer and Linux formats need their external tool on the machine;
  without it the format is refused with an install hint.
* `.deb` and AppImage can only be built where `dpkg-deb` / `appimagetool`
  exist (Linux). The generated layout is kept so it can be packaged there.
* The repair rules are deliberately few: only deterministic, verifiable fixes
  are applied. Everything else is explained.
* Optimization flags are only known for the toolchains listed above; other
  toolchains are left untouched.
* Reproducibility assumes the same inputs and the same toolchain version.
