<div align="center">

# ECE Universal AutoCompiler

**Detect a project's technology, run its real build with the toolchain installed
on your PC, and package the result into a secure, versioned `.ece` container.**

*From a desktop window or the command line — no third-party Python packages.*

[Features](#features) · [Quick start](#quick-start) · [The `.exe`](#standalone-windows-app-exe) · [CLI](#command-line) · [Docs](#documentation) · [Honest by design](#honest-by-design)

</div>

---

> **Status: 0.1.0 (alpha).** Python and Node.js builds run **for real**. Other
> technologies are *detected* and their toolchains *reported*; a build only runs
> when the required compiler is actually present. Nothing about a missing
> toolchain is ever faked.

---

## Features

- **Detects** a project's type from manifests, marker files, directory shape and
  source extensions — Python, Node/TypeScript, .NET, Rust, Go, Java/Maven,
  Java/Kotlin/Gradle, C/C++ (CMake/Make/Meson), PHP, Unity, Unreal.
- **Plans** a build using argument lists (never a shell string), drawing on real
  toolchain discovery on the machine.
- **Runs** the build with timeouts, cancellation, output capture and secret
  redaction. Steps that execute project-controlled code (for example
  `npm install` or an npm build script) require **explicit consent**.
- **Packages** the collected artifacts into an `.ece` container with a JSON
  manifest, a per-file index, per-file SHA-256 hashes and a package content hash.
- **Validates** and **safely extracts** containers (path-traversal, symlink and
  depth checks; hashes verified before a file replaces its target).
- **Diagnoses** build failures by classifying output into actionable categories.

## Quick start

**Option A — portable `.exe` (no Python needed).** See
[The `.exe`](#standalone-windows-app-exe) below, then open it, pick a folder and
press **Compile**.

**Option B — run from source.** Nothing to install:

```powershell
# From the repository root
$env:PYTHONPATH = "$PWD\src"
python -m ece_autocompiler --version
python -m ece_autocompiler gui        # open the desktop window
```

Optional editable install so the `ece-autocompiler` command is on your `PATH`:

```powershell
python -m pip install -e .
```

## Standalone Windows app (`.exe`)

You do **not** need Python installed to *use* the tool. Build a single, portable
executable once, then just open it, select a project folder and press
**Compile**:

```powershell
# One-time: build the executable (needs pyinstaller)
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
# -> dist\ECE-AutoCompiler.exe
```

Double-click `dist\ECE-AutoCompiler.exe` to launch the desktop window. The
`.exe` bundles the Python interpreter and the app; nothing else to install.

> The build is reproducible via `scripts\build_exe.ps1` (PyInstaller, onefile,
> windowed). The generated `.exe` is a build artifact and is **not** committed to
> the repository (see `.gitignore`).

## Desktop window

```powershell
python -m ece_autocompiler gui
```

A single, simple window:

1. **Project folder** — pick the folder (type auto-detected, shown as *Detected*).
2. **Output** — choose **Package as `.ece`** or **Build `.exe`** (Python); the
   output path and options adapt to the mode.
3. **Compile** — runs the build / export and shows progress in the log.

Options per mode — `.ece`: compression, skip install. `.exe`: console window,
single file. Shared: allow project scripts (consent), overwrite output.

Menus: **File** (open output/work folder), **Tools** (Toolchains…, Build history…),
**Help** (About).

## Command line

```powershell
python -m ece_autocompiler analyze  path\to\project
python -m ece_autocompiler build    path\to\project -o out\project.ece --consent
python -m ece_autocompiler exe      path\to\project -o out\app.exe --name app
python -m ece_autocompiler validate out\project.ece --deep
python -m ece_autocompiler inspect  out\project.ece
python -m ece_autocompiler extract  out\project.ece out\unpacked --overwrite
python -m ece_autocompiler tools
```

`build` refuses to run project scripts unless `--consent` is passed.

## Turn code into an `.exe`

For **Python** projects the `exe` command bundles your code — and the Python
interpreter — into a single Windows executable with PyInstaller:

```powershell
python -m ece_autocompiler exe path\to\python-project -o out\app.exe --name app
# auto-detects main.py / app.py / __main__.py; add --console for a terminal app
```

- Options: `--entry` (explicit entry file), `--onedir` (folder instead of one
  file), `--console` (attach a terminal), `--name`, `--timeout`.
- Requires `pip install pyinstaller` (already available in the development
  environment).
- Other languages (C#, C++, Rust, Go, Unity, Unreal) need their own compiler or
  SDK. Those adapters **detect** the project and report the missing tool; they
  do not fabricate an `.exe`. See
  [docs/SUPPORTED_PROJECTS.md](docs/SUPPORTED_PROJECTS.md).

## Tests

```powershell
python run_tests.py            # unit + integration
python run_tests.py unit
python run_tests.py integration -v
```

Integration tests create throwaway projects and run **real** Python and Node
builds (Node tests skip automatically when Node is absent).

## Supported projects

See [docs/SUPPORTED_PROJECTS.md](docs/SUPPORTED_PROJECTS.md) for the full matrix
of detection, which builds actually run, and which adapters are detection-only
when a toolchain is missing.

## Requirements

- Windows 10/11 (primary target) or any OS with Python 3.10–3.12.
- **No third-party Python packages.** The standard library only. The GUI uses
  Tkinter, which ships with CPython on Windows and macOS.
- Toolchains are optional and only needed for the languages you actually build
  (e.g. Node.js + npm for Node projects).

## Honest by design

- If a compiler or SDK is missing, the build **fails with the reason**; the tool
  never reports success it did not achieve.
- An adapter for a missing tool is **detection-only** in that environment. Its
  requirement report says `ready = false` with an install hint.
- `.ece` is a **documented container, not a self-executing format**. Hashes
  detect corruption; they are **not** a publisher signature. See
  [docs/ECE_FORMAT.md](docs/ECE_FORMAT.md) and [docs/SECURITY.md](docs/SECURITY.md).

## Project layout

```
src/ece_autocompiler/
  container/     .ece spec, codec, writer, reader, validator, path safety
  extraction/    safe extractor
  detection/     markers, toolchain discovery and project detection
  adapters/      build adapter interface + Python/Node + tool-gated adapters
  build/         subprocess runner + orchestrator + history
  diagnostics/   build-output classification
  security/      filename/path helpers for the UI and CLI
  ui/            Tkinter desktop interface
examples/        runnable sample projects (Python, Node)
docs/            architecture, format, security, supported projects, dev guide
tests/           unit and integration tests (unittest)
scripts/         smoke/E2E scripts + build_exe.ps1
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the pieces fit.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — how the components fit together.
- [ECE format](docs/ECE_FORMAT.md) — the `.ece` container specification.
- [Security](docs/SECURITY.md) — threat model, limits and consent model.
- [Supported projects](docs/SUPPORTED_PROJECTS.md) — detection/build matrix.
- [Development](docs/DEVELOPMENT.md) — setup, tests, building the `.exe`.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) and
[SECURITY.md](SECURITY.md) (please report vulnerabilities privately).

## License

The license has **not been chosen yet**. Until the project owner selects one, no
license is granted. Do not redistribute without permission.
