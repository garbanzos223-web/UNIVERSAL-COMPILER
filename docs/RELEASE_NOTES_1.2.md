# ECE Universal AutoCompiler 1.2 — New visuals and useful tools

Draft for a GitHub release: **Releases → Draft a new release**, tag `1.2`,
target `main`, title **New visuals and useful tools**, then paste the section
below. This is a source release; `scripts\build_exe.ps1` builds the verified
desktop and console executables locally.

---

1.2 delivers the 40 usability improvements described in
[docs/NEW_VISUALS_AND_USEFUL_TOOLS.md](NEW_VISUALS_AND_USEFUL_TOOLS.md). This is
a usability release: the existing dark visual design is unchanged; the goal is
that you never have to guess what the tool is doing, what is missing, or what to
do next.

Everything stays local and offline: settings, history, logs, timing samples and
templates are plain JSON/JSONL/CSV/text under
`%LOCALAPPDATA%\ECE-Universal-AutoCompiler`. No account, database, telemetry or
network call is required. Runtime is Python standard library only; Windows
10/11 x64.

## Highlights

- **Plan before you build.** `plan`, `build --plan`, `exe --plan` and the
  **Preview plan** dialog show the exact command lines, working folder, consent
  and skippable steps. Nothing runs while you read them.
- **Know what is missing.** `doctor`, `analyze`, `tools`, `.exe` readiness and
  the **Toolchains** dialog name each missing toolchain with a copyable
  `winget` / `choco` / `scoop` command and the official download URL. Minimum
  supported versions are shown when an installed tool is too old.
- **One next action.** A first-run walkthrough, the **What do I do now?**
  button, project-health warnings, hover tooltips, a keyboard-shortcut list and
  explicit PLANNING / COMPILING / PACKAGING states.
- **Failures are readable.** Every failure uses *what happened / why / what to
  do*, with **Copy error** and **Try again**. Project scripts are named in the
  consent prompt, and secrets are redacted from CLI and GUI errors.
- **Copyable proof.** Every build ends with duration, output size, SHA-256, what
  was built, what was skipped and what was **not** done. `.ece` and `.exe`
  outputs get optional `.sha256` sidecars.
- **Find and repeat work.** Searchable, filterable history with JSON/CSV export
  and replay; `diff` compares two `.ece` files; `init` creates Python, Node.js
  and C++/CMake templates.
- **Honest timing.** Before a build, the CLI and the window quote a range from
  this PC's own successful builds (or say there is no timing history yet);
  during the build they show elapsed time and an ETA only when a real progress
  fraction exists; afterwards they compare the real duration with the estimate.
  Package-only runs are timed separately so they never skew build estimates.
- **Monorepos are explicit.** When several projects are detected, the window
  lists them with the reason each was detected and lets you choose which one to
  build.

## Also new in this release: packaging, repair and optimization

Three subsystems were added on top of the usability work
([docs/ADVANCED_FEATURES.md](ADVANCED_FEATURES.md)):

- **Distributable packages.** `ece-autocompiler package` produces real,
  verified packages: reproducible ZIP and TAR.GZ archives, an application
  folder, a Windows installer (Inno Setup or NSIS) and the Linux formats (DEB,
  AppImage) when their tooling is installed. Every package carries a
  `package-manifest.json` with the metadata, the file list and SHA-256
  checksums, and it is **read back and verified** before it is reported as
  successful. Missing runtime dependencies and copyleft licenses are reported,
  never hidden.
- **Automatic error analysis and repair.** `ece-autocompiler repair` parses
  the compiler output (GCC/Clang, MSVC, Cargo, Go, Python, javac, TypeScript
  and linker errors), groups the problems into root causes and applies the
  deterministic fixes it is sure about: a missing `:`, a missing `#include`, an
  unused Go import, a missing `using`/`import`. Every change is backed up,
  rebuilt, verified and **rolled back** when it does not fix the error; the
  diff is always shown and every attempt is journalled.
- **Intelligent optimization.** `ece-autocompiler optimize` builds the project
  twice - baseline and optimized - and reports only what it measured: a
  percentage needs two real numbers and a runtime claim needs a benchmark.
  Profiles: `debug`, `release`, `performance`, `size`, `balanced`, `custom`;
  the default `none` keeps the previous behaviour exactly. The window gains a
  **Profile** selector, a package dialog, an optimization comparison dialog
  and an **Attempt automatic repair** action in the failure dialog.

The command line also starts faster: the toolchain probe result is cached
locally for 15 minutes and dropped when `PATH` changes (`tools` went from
~1.4 s to ~0.2 s here; `tools --refresh` re-probes on demand).

## New commands

```text
ece-autocompiler plan <project> [--mode ece|exe] [--adapter ID] [--json]
ece-autocompiler build <project> [--plan] [--package-only] [--profile NAME] [--repair] [--json] [--quiet] [--no-progress] [--no-input]
ece-autocompiler package <project> [--format zip|tar.gz|folder|inno|nsis|deb|appimage] [--output-dir DIR] [--build] [--consent] [--json]
ece-autocompiler optimize <project> [--profile NAME] [--custom-flags FLAG] [--tests CMD] [--benchmark CMD] [--json]
ece-autocompiler repair <project> [--apply] [--max-attempts N] [--timeout SEC] [--tests CMD] [--json]
ece-autocompiler doctor [--json] [--tools python,node,cmake] [--refresh]
ece-autocompiler diff <old.ece> <new.ece> [--json]
ece-autocompiler init <folder> [--template python-script|python-package|node-app|cpp-cmake] [--list]
ece-autocompiler history [--search TEXT] [--ok|--failed] [--adapter ID] [--export records.json] [--export-csv records.csv] [--replay] [--json]
```

## Verified

- 424 tests pass (1 skipped), including unit, integration and GUI smoke tests.
- `python tools/lint_stdlib.py` reports 85 files clean.
- The release build was verified with `scripts\build_exe.ps1`: both binaries
  were rebuilt, the desktop build starts and stays alive, and the **console
  build really built a project, validated the container deeply and created a
  ZIP package with its manifest** (`tools\release_check.py`).
- A real Python build was verified end to end: the first build took 1.266 s;
  the second used that estimate (1.625 s actual, 28% slower) and recorded it
  locally.
- The automatic repair was verified end to end on a project with a real syntax
  error: the error was parsed, the missing colon was proposed with a diff, the
  change was applied, the project was rebuilt and the repair was kept.
- The command line was measured on a project with 3000 files: the project
  scan went from 207 ms to 18 ms and the whole build from 808 ms to 388 ms,
  after caching the toolchain probe (`tools`: 1392 ms -> 173 ms).

## Notes and limits

- Windows 10/11 x64 only. Linux and macOS are not supported.
- Building `.exe` for Python needs PyInstaller; other languages need their own
  toolchain. Missing tools are reported, never installed silently.
- `.ece` SHA-256 values detect corruption; they are not a publisher signature.
  Optional external signing remains explicit.
- See [README.md](../README.md) and
  [docs/NEW_VISUALS_AND_USEFUL_TOOLS.md](NEW_VISUALS_AND_USEFUL_TOOLS.md) for the
  full documentation.

MIT License — ECE Universal AutoCompiler contributors, 2026.
