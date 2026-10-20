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

## New commands

```text
ece-autocompiler plan <project> [--mode ece|exe] [--adapter ID] [--json]
ece-autocompiler build <project> [--plan] [--package-only] [--json] [--quiet] [--no-progress] [--no-input]
ece-autocompiler doctor [--json] [--tools python,node,cmake]
ece-autocompiler diff <old.ece> <new.ece> [--json]
ece-autocompiler init <folder> [--template python-script|python-package|node-app|cpp-cmake] [--list]
ece-autocompiler history [--search TEXT] [--ok|--failed] [--adapter ID] [--export records.json] [--export-csv records.csv] [--replay] [--json]
```

## Verified

- 284 tests pass (1 skipped), including unit, integration and GUI smoke tests.
- `python tools/lint_stdlib.py` reports 58 files clean.
- A real Python build was verified end to end: the first build took 1.266 s;
  the second used that estimate (1.625 s actual, 28% slower) and recorded it
  locally.

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
