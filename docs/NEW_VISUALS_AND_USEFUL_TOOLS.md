# New visuals and useful tools

Usability roadmap (40 items) for ECE Universal AutoCompiler 1.2.

**Status: implemented in 1.2.** The shared infrastructure and all 40 items below
are part of this release; the verification steps are covered by
`tests/unit/test_usability.py` and the existing suite (`python run_tests.py`).
The document keeps the original item-by-item plan as the record of what was
built and how each item is proven.

Functional usability work only. No visual design (colours, layouts, themes), no
third-party dependencies, Windows 10/11 x64 only, stdlib-only runtime. Existing
behaviour is extended, never re-implemented. Every item below ships only when
its stated verification step passes.

Legend for each item:

* **What** — the feature, in one sentence.
* **Why** — how it stops the user from getting lost / guessing / getting stuck.
* **Where** — the real files and modules it touches.
* **Steps** — concrete implementation work.
* **Verify** — how it is proven (unit test, smoke test, or an exact command).

---

## Shared infrastructure (referenced by many items)

These five modules are created once and reused. They are not numbered items;
each item lists which ones it consumes.

### I1 — `src/ece_autocompiler/local_store.py` (Restriction 1)

Local-only persistence, atomic, offline.

* Config root: `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\config` (the prompt
  writes `ECE-AutoCompiler`; the shipped folder name is
  `ECE-Universal-AutoCompiler`, and it is **not** renamed — renaming it would
  orphan existing history/logs on users' machines).
* `read_json(name, default)` / `write_json_atomic(name, data)` /
  `append_jsonl(name, record)` / `read_jsonl(name)` / `write_csv(name, rows)`.
* Atomic write: `name.tmp` → `os.replace`. Before overwriting, the previous
  file is copied to `name.bak` (once). On a parse error the corrupt file is
  renamed to `name.corrupt-<utc-timestamp>` and the `.bak` is restored; the
  call returns the default plus `recovered=True`.

### I2 — `src/ece_autocompiler/toolchain_hints.py` (Restriction 2)

Single registry of "what is missing and where to get it".

* `InstallHint(tool_id, name, winget, choco, scoop, url, note)`.
* `hint_for(tool_id)`, `hint_lines(tool_id) -> list[str]`,
  `hint_json(tool_id) -> {"missing": ..., "install": {"winget", "choco", "scoop", "url"}}`.
* The fifteen tools required by the prompt are covered in the table in
  "Restriction 2" below. `container/signature.py` (which already carries
  minisign/cosign/gpg hints) is refactored to read from this registry so there
  is exactly one source of truth. The app never installs anything: it prints a
  command the user can copy.

### I3 — `src/ece_autocompiler/build/timing.py` (Special requirement)

* `TimingSample` records one finished build; `Estimate` carries `average`,
  `low`, `high`, `samples`, `rough`, `excluded` and the human message.
* `record_sample(base, adapter_id, mode, duration_seconds, ok, file_count,
  input_bytes)` appends one sample; `estimate_build(base, adapter_id, mode)`
  reads the matching successful samples and never invents a number.
* `Ticker(clock=time.monotonic)`: `elapsed()`, `eta(fraction)` and `summary()`.
  The ETA is returned only when a real progress fraction exists.
* Storage (JSONL only, no DB, no network):
  * `work\timings.jsonl` — one line per finished build:
    `{timestamp, adapter_id, mode, duration_seconds, ok, file_count, input_bytes}`.
  * A rolling summary (`count`, average, low/high) is computed on demand from
    that file; there is no derived file to drift out of sync.
* Confidence rule: fewer than 3 successful samples, or a spread above 50%,
  is labelled a *rough estimate*; 0 samples means "no estimate yet", and both
  the CLI and the window say so instead of inventing a number. Failed and
  cancelled builds are recorded but excluded from the average, and the
  message states how many were excluded.

### I4 — `src/ece_autocompiler/build/progress.py`

* `ProgressEvent(phase, step_index, step_total, step_name, fraction, elapsed_s, eta_s)`.
* Emitted by `build/process.py` (per step) and `build/orchestrator.py`
  (scan → plan → build → package). Both the CLI renderer and the Tk worker
  consume the same events, so the two front-ends can never disagree.

### I5 — `src/ece_autocompiler/ui/dialogs_common.py`

* `confirm_destructive(parent, action, detail) -> bool` — one wording, always
  naming the exact file/folder/rows to be removed.
* `failure_box(parent, what, why, todo, details)` + `copy_details()`.
* One shared "Copy to clipboard" helper (wraps `clipboard_clear/append`).

---

## Restriction 2 — Install hints (exact commands)

Printed by `doctor`, `analyze`, `exe`, the GUI toolchain card, and available as
`install` in every `--json` payload. The winget IDs match the ones already
shipped in `container/signature.py`; the rest must be re-checked with
`winget search` / `choco search` / `scoop search` before release (step in each
item), and the URL is always the fallback that cannot rot.

| Tool (id) | winget | choco | scoop | Official URL |
|---|---|---|---|---|
| MSVC Build Tools (C/C++) `msvc` | `Microsoft.VisualStudio.2022.BuildTools` (+ `--override "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"`) | `visualstudio2022buildtools` | — | https://visualstudio.microsoft.com/downloads/ |
| CMake `cmake` | `Kitware.CMake` | `cmake` | `cmake` | https://cmake.org/download/ |
| Ninja `ninja` | `Ninja-build.Ninja` | `ninja` | `ninja` | https://github.com/ninja-build/ninja/releases |
| MinGW-w64 | `BrechtSanders.WinLibs.POSIX.UCRT` | `mingw` | `mingw` | https://www.mingw-w64.org/downloads/ |
| .NET SDK | `Microsoft.DotNet.SDK.8` | `dotnet-sdk` | `dotnet-sdk` | https://dotnet.microsoft.com/download |
| rustup | `Rustlang.Rustup` | `rustup.install` | `rustup` | https://rustup.rs/ |
| Go | `GoLang.Go` | `golang` | `go` | https://go.dev/dl/ |
| JDK (Temurin 21) | `EclipseAdoptium.Temurin.21.JDK` | `temurin21` | `temurin21-jdk` | https://adoptium.net/ |
| Maven | `Apache.Maven` | `maven` | `maven` | https://maven.apache.org/download.cgi |
| Gradle | `Gradle.Gradle` | `gradle` | `gradle` | https://gradle.org/releases/ |
| Node.js LTS | `OpenJS.NodeJS.LTS` | `nodejs-lts` | `nodejs-lts` | https://nodejs.org/ |
| PyInstaller | — | — | — | `pip install pyinstaller` — https://pyinstaller.org/ |
| minisign | `jedisct1.minisign` | `minisign` | `minisign` | https://github.com/jedisct1/minisign/releases |
| cosign | `Sigstore.Cosign` | `cosign` | `cosign` | https://github.com/sigstore/cosign/releases |
| GPG (Gpg4win) | `GnuPG.Gpg4win` | `gnupg` | `gnupg` | https://gnupg.org/download/ |

**Verify:** unit test asserts every id in `detection/toolchains.py::SPECS` plus
the three signature tools has a non-empty `url` and at least one package-manager
command; a lint test fails the build if a tool is added without a hint.

---

## Restriction 1 — Local-only storage map

| Data | File | Format |
|---|---|---|
| Settings, wizard flag, last used options | `config/settings.json` | JSON (atomic) |
| Saved build profiles | `config/profiles.json` | JSON (atomic) |
| Timing history (per type/toolchain/mode) | `work/timings.jsonl` | JSONL |
| Build history | `work/history.jsonl` | JSONL |
| Logs | `work/logs/*.log` | text (rotating, redacted) |
| Templates (`ece init`) | `config/templates/<lang>/` | text |
| Cache (incremental/size) | `config/cache/` | JSON |

Every write is atomic; every corrupt file is backed up as described in I1.
Nothing leaves the machine.

---

## A — The user never gets lost

### 1. First-run step-by-step wizard

* **What:** a five-step assistant (folder → detect → `.ece`/`.exe` → options →
  compile) shown once, with a "do not show again" checkbox.
* **Why:** a brand-new user with no project loaded sees one clear path instead
  of an empty window with many controls.
* **Where:** new `src/ece_autocompiler/ui/onboarding.py` (`WizardDialog extends
  tk.Toplevel`, `ttk` only); launched from `MainWindow.__init__` via
  `self.after(50, self._maybe_show_wizard)`; the flag lives in
  `config/settings.json` (`"show_wizard": true`) through `local_store.py`.
* **Steps:** (1) build the wizard as a small state machine that reuses the
  existing `_browse_project`/`_detect`/`_build` callbacks rather than
  duplicating them; (2) each step enables "Next" only when its precondition is
  met (folder exists, detection finished); (3) the checkbox writes the flag
  immediately; (4) add a menu entry `Tools ▸ First-run assistant` to reopen it.
* **Verify:** unit test the settings round-trip; smoke test opens the wizard on
  a fresh config dir, ticks "do not show again", and asserts the flag is
  persisted; a second construction must not show it.

### 2. Clear message when no project is selected

* **What:** an explicit empty-state line ("Choose a project folder to start",
  plus the supported layouts) instead of a silent window.
* **Why:** removes the "what is this and what do I click" moment on launch.
* **Where:** `ui/main_window.py` — the initial `self.log.append(...)` and a
  persistent hint label in the PROJECT card; `ui/guidance.py`
  (`empty_state_text()`).
* **Steps:** (1) centralise the copy in `ui/guidance.py`; (2) show it in the log
  and next to the folder field; (3) `_on_project_changed` swaps it for the
  detected summary once a valid path is set.
* **Verify:** smoke test asserts the hint text is present at start and changes
  when a valid path is typed.

### 3. Readable summary after detection

* **What:** a one-screen summary: project type, version (when the manifest has
  one), required toolchain, and either "ready" or exactly what is missing.
* **Why:** the user immediately knows whether it can be built here and what is
  blocking it.
* **Where:** new `detection/metadata.py` (`project_version(root, adapter_id)` —
  bounded reads of `package.json`, `pyproject.toml`, `Cargo.toml`; omit the
  field honestly when absent) and new `detection/summary.py`
  (`build_summary(scan_result, registry) -> Summary`) which calls the adapter's
  existing `inspect_requirements()`; surfaced in `cli.cmd_analyze` and in the
  GUI detect handler.
* **Steps:** (1) add version parsing with a size cap per file; (2) reuse
  `RequirementsReport` (already returns `found`, `version`, `install_hint`);
  (3) render as fixed lines (`Type`, `Version`, `Toolchain`, `Status`); (4) add
  the same block to `analyze --json` under `toolchain`.
* **Verify:** unit test on a fixture project returns the expected version and
  readiness; `python -m ece_autocompiler analyze <dir> --json` contains
  `toolchain.ready` and `toolchain.missing`.

### 4. Contextual "What can I do now?"

* **What:** a button (and a menu item) that prints the sensible next actions
  for the current state.
* **Why:** the user never has to know the workflow by heart.
* **Where:** new `diagnostics/next_steps.py` (`suggest(state, scan_result,
  readiness, last_error) -> list[str]`); button in `ui/main_window.py` header,
  menu `Help ▸ What can I do now?`.
* **Steps:** (1) implement the suggestion table per state (no folder → open
  one; detected+missing tool → install command; ready → compile; failed → the
  diagnosis's action); (2) render into the log console with tags; (3) reuse the
  same function at the end of `analyze` when a toolchain is missing.
* **Verify:** unit test each state returns a non-empty, state-appropriate list;
  smoke test the button writes at least one line.

### 5. Missing-toolchain card with copyable install command

* **What:** a card listing the missing tool, why it is needed, and the exact
  install command (copyable), plus a "Re-run detection" button.
* **Why:** turns a dead end into a one-command fix.
* **Where:** new `ui/toolchain_card.py`; data from `RequirementsReport` (already
  exists) + `toolchain_hints.hint_lines()`; CLI parity in `analyze` (a
  `MISSING:` block) with exit code 3.
* **Steps:** (1) build the card from `report.requirements` where `not found`;
  (2) one "Copy install command" button per tool (I5 helper); (3) "Re-run
  detection" calls `_detect()`; (4) in the CLI, print the same lines to stderr
  as well as the `install` object in `--json`.
* **Verify:** unit test `hint_lines("dotnet")` contains
  `winget install Microsoft.DotNet.SDK.8`; smoke test shows the card when a
  requirement is unsatisfied (injected fake report); CLI test asserts exit 3.

### 6. Tooltip + one-line description for every option

* **What:** hover tooltips and a short sentence beside `onefile/onedir`,
  `console`, `consent`, `compression`, `verify-signature`.
* **Why:** removes guessing about what an option does.
* **Where:** new `Tooltip` class in `ui/widgets.py` (pure Tk
  `<Enter>`/`<Leave>`); option labels in `ui/main_window.py`; text map in
  `ui/guidance.py::OPTION_HELP`.
* **Steps:** (1) implement `Tooltip(widget, text)` with a `Toplevel` label and
  no external dependency; (2) attach to each option widget; (3) put the same
  sentence inline so it is readable without hovering.
* **Verify:** unit test `OPTION_HELP` covers every option key used by the
  window; smoke test constructs the window without raising.

### 7. Confirmation before destructive actions

* **What:** a confirmation dialog naming exactly what will be deleted or
  overwritten.
* **Why:** prevents accidental loss of an existing `.ece`/`.exe` or history.
* **Where:** `ui/main_window.py` (overwrite output), `ui/main_window.py`
  `HistoryDialog` (clear history), `ui/main_window.py` extract overwrite — all
  routed through `dialogs_common.confirm_destructive`.
* **Steps:** (1) implement the helper (I5); (2) replace the ad-hoc
  `messagebox` calls; (3) include the exact path and, for history, the number
  of records.
* **Verify:** unit test the message text contains the path and the record
  count; smoke test that "No" aborts and "Yes" proceeds.

### 7b (A7 continues). — see item 7 above.

### 8. Final result summary with "Open folder"

* **What:** after every successful build/export: what was produced, where, its
  size, the real duration, and how it compared to the estimate.
* **Why:** the user knows where the artefact is without hunting.
* **Where:** new `ui/summary.py` (`build_summary_lines(...)`,
  `copy_summary()`); `MainWindow._build_done` / `_exe_done` already have all the
  values; the existing `_open_output` is wired to a visible button.
* **Steps:** (1) render the fixed block in the log; (2) enable an "Open output
  folder" button in the SUCCESS state only; (3) reuse the same block for the
  copyable summary (item 35).
* **Verify:** unit test the block contains path, size and duration; smoke test
  the button appears after a simulated success.

### 9. Errors in a fixed three-part format + copy details

* **What:** every failure reads "What happened / Why / What to do", with a
  "Copy details" button.
* **Why:** a stable shape is scannable, and the copied text is issue-ready.
* **Where:** new `ui/dialogs_common.failure_box` (I5) and a shared
  `diagnostics/formatting.py::format_failure(what, why, todo, details)`;
  consumed by `MainWindow._fail`, `_exe_done`, `_build_done` and by
  `cli.cmd_build`/`cmd_exe` (which already emit a three-part shape for `.exe`).
* **Steps:** (1) one formatter used by both front-ends; (2) the "what to do"
  line prefers a concrete command when the diagnosis is a missing tool;
  (3) details = redacted log tail + log path.
* **Verify:** unit test the formatter output has the three headings and carries
  no secret (run through `logging_config.redact`); CLI test on a forced failure.

### 10. Warn before closing during a build

* **What:** closing the window while compiling asks for confirmation and
  cancels the build first.
* **Why:** prevents orphaned child processes and half-written outputs.
* **Where:** `ui/main_window.py` — `protocol("WM_DELETE_WINDOW", self._on_close)`;
  decision helper `ui/state.py::should_block_close(state)`.
* **Steps:** (1) intercept close; (2) if `COMPILING`, confirm, then
  `_cancel_build()` and wait for the worker to release the process tree before
  `master.destroy()`; (3) otherwise close immediately.
* **Verify:** unit test `should_block_close` for every `UiState`; smoke test
  that a close during a simulated build calls cancel.

---

## B — The user never has to guess

### 11. Ambiguous detection: candidates with a reason, manual choice

* **What:** when several projects are found, list them with root, confidence,
  evidence and let the user pick.
* **Why:** monorepos and mixed folders otherwise build the wrong thing.
* **Where:** new `ui/candidates_dialog.py`; the orchestration already accepts
  `prefer_adapter`/best-root (`build/orchestrator.py`); CLI gains
  `build --adapter <id>` and `--project <rel-path>`.
* **Steps:** (1) build the dialog from `scan_result.candidates`
  (`ADAPTER_LABELS`, `root`, `confidence`, `evidence`, `notes`); (2) on choice,
  pass `prefer_adapter` and the relative root into the build call; (3) print
  the candidate table in `analyze` so headless users can choose too.
* **Verify:** unit test the row builder; CLI test on a two-project fixture
  builds the selected sub-root and the container manifest shows that adapter.

### 12. Explain why an adapter was chosen

* **What:** show the markers that matched and the tie-break that produced the
  winner.
* **Why:** the user can tell a correct detection from a lucky one.
* **Where:** new `detection/explain.py` (`explain_selection(scan_result,
  chosen) -> list[str]`); shown in the GUI detect log and in `analyze`.
* **Steps:** (1) reuse `DetectionCandidate.evidence` and the marker weights in
  `markers.py`; (2) add the resolution rule (highest confidence, then shallowest
  root, then adapter id) as an explicit sentence; (3) note when a choice was
  forced by `--adapter`.
* **Verify:** unit test asserts the explanation names the winning manifest and
  the runner-up.

### 13. Dry-run before executing

* **What:** preview the build plan (steps, commands, timeouts) without running
  anything.
* **Why:** the user can abandon an expensive or suspicious build before it
  starts.
* **Where:** refactor `build/orchestrator.py` to expose
  `resolve_plan(project, *, prefer_adapter) -> (adapter, project_dir, plan)`
  used by both `build_and_package` and a new read-only `plan()`; CLI
  `plan` command; GUI "Preview plan" button opening a `PlanDialog`.
* **Steps:** (1) extract resolution + `create_build_plan` (no process start);
  (2) render steps with `requires_approval` flags and per-step timeout;
  (3) add `--json` output for `plan`.
* **Verify:** unit test patches `build.process.run_command` and asserts it is
  never called; CLI `plan <dir>` exits 0 with a step list.

### 14. Show and copy the exact command

* **What:** each step's real argv is displayed and copyable.
* **Why:** the user can run the same command in their own terminal to debug.
* **Where:** new `build/commands.py::format_command(argv)` (Windows quoting);
  used by `PlanDialog`, the build log, and `plan`/`build` CLI output.
* **Steps:** (1) implement quoting for spaces, quotes and non-ASCII; (2) copy
  button per step (I5); (3) log the argv at step start (the build log already
  records `cmd:`).
* **Verify:** unit test `format_command` for a path with spaces and an accented
  character; CLI test shows the command for a Python fixture.

### 15. Toolchain versions and minimum requirements

* **What:** report each detected tool's reported version and whether it meets
  the minimum this project needs.
* **Why:** "installed but too old" stops looking like "installed".
* **Where:** `detection/toolchains.py` — add `min_version` and a version regex
  per `_Spec`, and `ToolInfo.meets_minimum: bool | None`; adapters declare
  their minimums.
* **Steps:** (1) parse the first semantic version out of the probe output;
  (2) compare tuple-wise; (3) render "Python 3.12.0 (needs ≥ 3.10 — OK)";
  (4) surface in `tools`, `analyze` and the GUI toolchain dialog.
* **Verify:** unit test the parser/comparison against stored sample strings
  (python, node, dotnet, cargo, go, java).

### 16. Monorepo: list subprojects and choose

* **What:** explicit subproject selection for multi-root trees.
* **Why:** the user controls exactly what is packaged.
* **Where:** the same candidate list as item 11, but driven by the "multiple
  subprojects of the same type" note already produced in
  `detection/detector.py`; `build/orchestrator.py` already builds in
  `best.root`.
* **Steps:** (1) when `result.notes` contains the multi-project note, open the
  chooser automatically; (2) CLI `--project`; (3) remember the choice in the
  profile (item 1 of the build roadmap, if present) keyed by project path.
* **Verify:** unit test on a `packages/a`, `packages/b` fixture builds only the
  chosen root.

### 17. Pre-build warning for long paths or non-ASCII

* **What:** before compiling, warn about paths over 260 characters, non-ASCII
  names, reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`…), trailing
  dots/spaces, and case-only duplicates.
* **Why:** these fail late and confusingly on Windows.
* **Where:** new `project_health.py::check_paths(root, entries) -> list[HealthIssue]`;
  shown in a GUI card and in `analyze`/`build` warnings; `--strict` turns them
  into a refusal.
* **Steps:** (1) implement checks (the writer already detects case collisions
  via `container/security.py`, reuse that helper); (2) count and summarise
  ("3 files have paths >260 chars; extraction will fail on this machine");
  (3) offer the long-path hint (enable `LongPathsEnabled`).
* **Verify:** unit test with crafted names returns exactly the expected issues.

### 18. Consent naming the actual script

* **What:** when a build will run project-controlled code, name the script and
  show the exact command before asking.
* **Why:** informed consent instead of a blind yes/no.
* **Where:** `adapters/python_adapter.py` / `node_adapter.py` already mark
  `requires_approval` steps; add `BuildPlanStep.script_ref` and
  `describe_script_steps(plan) -> list[str]` in `build/commands.py`; the GUI
  confirmation uses it, and `build --consent` prints the same list.
* **Steps:** (1) tag install/build-script steps with a human reference
  (`npm postinstall`, `setup.py`, poetry script); (2) list name + argv in the
  confirmation; (3) when `--no-input`, refuse unless consent was passed
  explicitly.
* **Verify:** unit test that a `package.json` with a `postinstall` produces a
  confirmation line containing `postinstall` and the command.

### 18b — duplicate of 18; handled once.

### 19. Plain-language validate/extract results

* **What:** translate validator/extractor codes into sentences.
* **Why:** "INVALID" alone tells the user nothing to act on.
* **Where:** new `diagnostics/plain_language.py` (`explain_validation(report)`,
  `explain_extraction(report)`); used by `cli.cmd_validate`/`cmd_extract` and
  the GUI container dialog.
* **Steps:** (1) map each issue code to one plain sentence plus a next action;
  (2) keep the raw code in parentheses for support; (3) never soften a real
  failure into a success.
* **Verify:** unit test each validation code maps to a sentence; a corrupt
  fixture produces a readable explanation and a non-zero exit.

### 19b. Diff between two `.ece` files, readable

* **What:** added / removed / modified files with sizes and hashes, plus
  manifest-level differences.
* **Why:** "what changed since the last build" is the first question asked.
* **Where:** new `container/diff.py` (`diff_containers(a, b) -> ContainerDiff`);
  CLI `diff A B` (exit 1 when different, like `diff`); GUI menu
  `Tools ▸ Compare .ece…`.
* **Steps:** (1) open both with `ContainerReader` (header + index only, no
  payload reads); (2) classify by path and `sha256`/size; (3) print a compact
  list and a `+-~=` count; (4) `--json` output.
* **Verify:** unit test with two crafted containers (one added, one modified,
  one removed) asserts the exact classification.

---

## C — The user never gets stuck

### 20. (see item 19b) — container comparison.

### 21. Retry with one click after fixing

* **What:** a Retry button that repeats the last build with identical settings.
* **Why:** after installing a missing tool the user should not re-enter
  everything.
* **Where:** `ui/main_window.py` — persist the last request in a
  `LastBuildRequest` dataclass (project, output, mode, options, consent) kept
  in memory and in `config/settings.json`; Retry enabled in `ERROR`/`CANCELLED`.
* **Steps:** (1) build the request object in `_build`; (2) "Retry" re-uses it
  verbatim; (3) "Re-check toolchains" runs detection again before retrying.
* **Verify:** unit test the request survives `_finish_build(ERROR)`; smoke test
  Retry triggers a second build with the same arguments.

### 22. Cancel at any time with a clear state and cleanup

* **What:** cancel during detection or build, with an explicit log line and no
  leftover temporary files.
* **Why:** long builds must be interruptible without breaking the machine.
* **Where:** already implemented in `build/process.py` (cancel token, process
  tree kill) and `container/writer.py` (`finally` removes `.payload.tmp`/`.part`);
  remaining work: show "Cancelled after 42 s", and sweep the work directory
  (`build/workdirs.py` already has the sweep used by the B6 cleanup).
* **Steps:** (1) record elapsed at cancellation using I3; (2) call the workdir
  sweep after a cancelled build; (3) confirm the child process tree is gone.
* **Verify:** integration test cancels mid-build and asserts no `.tmp`/`.part`
  files remain and the state is `CANCELLED`.

### 23. Skip a step only when it is safe

* **What:** allow skipping explicitly-safe steps (e.g. an optional lint pass),
  never a compile or package step, with a warning.
* **Why:** a stuck optional step should not block a build.
* **Where:** `adapters/base.py::BuildPlanStep` gains `skippable: bool = False`;
  `build/process.py` honours `skip_step_ids`; GUI shows the button only for
  `skippable` steps; CLI `--skip-step <id>`.
* **Steps:** (1) mark safe steps in the adapters (PHP lint, optional checks);
  (2) refuse skipping a non-skippable id with a clear message; (3) log
  "Step X skipped at user request".
* **Verify:** unit test that skipping a compile step is refused and skipping a
  lint step succeeds.

### 24. Recovery from corrupt history/config

* **What:** a corrupt JSON/JSONL file never crashes the app; it is backed up
  and the app continues with defaults plus a clear notice.
* **Why:** a half-written file should not make the app unusable.
* **Where:** `local_store.py` (I1) for settings/profiles/timings;
  `build/history.py::load` already skips bad lines — it gains a
  `recovered`/`skipped_lines` count reported to the caller.
* **Steps:** (1) on parse failure rename to `.corrupt-<ts>`, restore `.bak`;
  (2) return a `LoadResult(value, recovered, detail)`; (3) show a warning line
  in the GUI and in the CLI.
* **Verify:** unit test truncates `history.jsonl` and settings, then asserts
  recovery, a `.bak`/`.corrupt-*` file on disk, and the notice.

### 25. Package-only mode

* **What:** package already-built artefacts without running the build.
* **Why:** users whose build already ran (or that shipped binaries) still get an
  `.ece`.
* **Where:** `build/orchestrator.py::build_and_package(..., run_build=False,
  artifact_dir=None)`; CLI `build --package-only [--from <dir>]`; GUI checkbox.
* **Steps:** (1) skip plan execution and call `adapter.collect_artifacts`;
  (2) if `--from` is given, package that folder instead; (3) state plainly in
  the log that no build ran.
* **Verify:** integration test with a pre-made `dist/` produces a container and
  a log line saying no build was executed.

### 25b. `ece init --template` templates

* **What:** scaffold a minimal, buildable project per language.
* **Why:** the fastest path from zero to a successful build.
* **Where:** new `templates.py` (Python, Node, C++/CMake, Go, Rust minimal
  trees written from inline strings, plus README and `.gitignore`); CLI
  `init <dir> --template <name>`; templates also stored under
  `config/templates/` for user editing.
* **Steps:** (1) define templates with the smallest file set that `scan()`
  recognises; (2) refuse to write into a non-empty directory unless `--force`;
  (4) list available templates in `--help` and in the GUI (optional menu).
* **Verify:** unit test `init --template python` in a temp dir, then `scan()`
  detects `python`; same for node/cpp/go/rust.

### 25c. Package-only corner cases — covered by item 25.

### 26. `doctor` command

* **What:** one environment report: Windows build, Python (frozen or source),
  long-path support, working/output directories writable, free disk space,
  every toolchain with version and minimum check, PyInstaller availability, and
  the exact install command for anything missing.
* **Why:** the single command that answers "why can't it build here?".
* **Where:** new `diagnostics/environment.py::environment_report(config) ->
  EnvironmentReport`; CLI `doctor [--json] [--fix-hints]`; GUI `Tools ▸ Doctor`.
* **Steps:** (1) collect OS/Python/paths/disk via stdlib
  (`platform`, `sys.getwindowsversion`, `shutil.disk_usage`, `winreg` for
  `LongPathsEnabled`); (2) fold in `discover_all()` and `toolchain_hints`;
  (3) emit three-part text and a `--json` object; (4) structural checks only —
  no writes outside a temporary probe file.
* **Verify:** unit test the report shape and that a missing tool carries an
  `install` object; CLI test exits 0 and prints at least one hinted command.

### 27. `--json` everywhere and a non-interactive mode

* **What:** machine-readable output for every command, plus
  `--yes/--no-input` and `--quiet/--verbose`.
* **Why:** makes the tool usable from scripts and CI without a TTY.
* **Where:** `cli.py` — `analyze`, `validate`, `inspect`, `tools` already have
  `--json`; add it to `build`, `exe`, `extract`, `doctor`, `diff`, `plan`,
  `init`, plus global `--json/--quiet/--verbose/--no-input` and documented exit
  codes (`EXIT_OK=0`, `EXIT_ERROR=1`, `EXIT_USAGE=2`, `EXIT_MISSING_TOOL=3`,
  `EXIT_CANCELLED=4`, `EXIT_VALIDATION=5`).
* **Steps:** (1) route all human text through one renderer that is silenced by
  `--quiet` and verbose under `--verbose`; (2) `--no-input` fails instead of
  prompting; (3) every `--json` payload that reports a missing tool includes
  `install` (I2); (4) document the codes in `docs/DEVELOPMENT.md`.
* **Verify:** unit test each command with `--json` parses and contains its
  expected keys; test that `--no-input` never blocks (patch `input` to raise).

### 28. Keyboard shortcuts documented inside the app

* **What:** a `Help ▸ Keyboard shortcuts` window listing what the app really
  binds.
* **Why:** discoverability without leaving the app.
* **Where:** new `ui/shortcuts.py` (single list of `(keys, action)`);
  `MainWindow._bind_shortcuts` reads the same list so it cannot drift;
  `AboutDialog` links to it.
* **Steps:** (1) define the list; (2) bind from it; (3) render in a simple
  `Toplevel` with a copy button.
* **Verify:** unit test that every documented shortcut has a binding and vice
  versa (compare the list with the bound sequences).

### 29. Search / filter in build history

* **What:** a search box and filters (status, project type, adapter) in the
  history dialog.
* **Why:** the user can find "the build that worked yesterday" instantly.
* **Where:** `ui/main_window.py::HistoryDialog` (already loads
  `build_history.load`); add `build/history.py::search(base, text=..., ok=...,
  adapter=...)`.
* **Steps:** (1) implement filtering over the loaded records (case-insensitive
  substring across path/message); (2) wire a search entry and a status combo;
  (3) keep the count label in sync.
* **Verify:** unit test `search` returns only matching records.

### 30. (see item 21) — retry.

---

## D — The user understands what is happening and how long it takes

### 31. Explicit states

* **What:** named states: idle, detecting, planning, compiling, packaging,
  success, error, cancelled.
* **Why:** the header always tells the truth about what the app is doing.
* **Where:** `ui/state.py::UiState` gains `PLANNING` and `PACKAGING` plus their
  `StateSpec` entries; transitions still flow through
  `MainWindow._set_state`.
* **Steps:** (1) add the two enum members and specs (badge text, button
  enablement, log tag); (2) set `PLANNING` around plan resolution and
  `PACKAGING` around the container write; (3) keep `STATE_UI` total (every enum
  member present).
* **Verify:** unit test that every `UiState` member has a `StateSpec` and that
  the documented transition graph is respected.

### 32. Live log with levels, filter and copy

* **What:** colourised log with level filters and copy/save.
* **Why:** the user can isolate errors from noise.
* **Where:** already implemented — `ui/widgets.py::LogConsole` (filters
  `all/info/ok/warn/err/muted/accent`, Copy, Save) and the B7 toolbar.
* **Steps:** (1) add per-level counts and a "Copy errors only" action;
  (2) ensure new messages carry the right tag; (3) no new dependency.
* **Verify:** unit test the counts and that "errors only" copies exactly the
  `err` lines.

### 32b. (Removed duplicate numbering — item 33 continues below.)

### 33. Per-step progress with the current step name

* **What:** a progress line that names the current step, not just a bar.
* **Why:** the user knows which phase is running and roughly how far along.
* **Where:** `build/progress.py` (I4) emitted by `build/orchestrator.py`
  (scan → plan → build → package) and `build/process.py` (per step); rendered by
  `StatusBar.set(message)` in the GUI and a single status line in the CLI.
* **Steps:** (1) emit `ProgressEvent` from the orchestrator and process;
  (2) GUI sets the status text to "Compiling: step 2/3 — npm run build";
  (3) CLI prints the same line unless `--no-progress`.
* **Verify:** unit test that one event per step is emitted with the step name.

### 34. Build time bar / estimation (special requirement)

* **What:** estimate before, live ETA and elapsed during, real duration and
  estimate comparison after; persisted per project type, toolchain and mode.
* **Why:** the user stops wondering if the build is hung and can plan around it.
* **Where:** `build/timing.py` (I3); consumed by `cli.py` (status line +
  `--json` fields `estimate_s`, `elapsed_s`, `eta_s`, plus `--no-progress`) and
  by the GUI (`StatusBar` + a time label, driven by `ProgressEvent`).
* **Steps:**
  1. **Before:** after detection/plan, call `estimate_build(adapter_id, mode,
     input_bytes, file_count)`; render "Estimated: ~30–60 s (4 similar builds)"
     and always the sentence "This is an estimate" — or, with no data, "No
     timing history yet — this estimate will become available after a first
     successful build."
  2. **During:** a `Ticker` started at build start; ETA =
     `elapsed / fraction` once `fraction > 0`, otherwise an indeterminate bar
     and "calculating…". Never show a fake percentage: if no fraction is
     available, the bar stays indeterminate (the current `StatusBar` already
     implements indeterminate mode).
  3. **Per step:** when the history holds per-step durations, show
     "step 2/3 — 12 s elapsed, ~30 s expected".
  4. **After:** print the real duration, the delta against the estimate
     ("25% faster than estimated") and append the sample to `timings.jsonl`,
     updating the rolling average/median. Only successful builds feed the
     average; cancelled/timeout builds are recorded but excluded from the mean,
     and the exclusion is stated.
  5. **Cancel:** show "Cancelled after 42 s".
  6. **Honesty:** if the coefficient of variation is high or the sample count
     is below 3, label the range "rough" instead of precise.
* **Verify:** unit test `estimate_build` with a synthetic `timings.jsonl`
  (average, range widening on variance, "no data" path); unit test the `Ticker`
  ETA is monotonic and `None` at `fraction == 0`; CLI test asserts `--json`
  contains the three fields and that `--no-progress` prints no status line;
  smoke test that the GUI shows the estimate line before the build starts.

### 35. Copyable summary on completion

* **What:** a plain-text block (version, OS, project type, toolchain status,
  outcome, diagnosis, log path, redacted tail) to paste into an issue or chat.
* **Why:** reporting a problem becomes one click.
* **Where:** `ui/summary.py` (item 8) gains `issue_summary(...)`; a
  "Copy summary" button in SUCCESS/ERROR; CLI equivalent under `analyze --json`
  and a `--report <file>` option on `build`.
* **Steps:** (1) assemble the block from existing values; (2) run it through
  `logging_config.redact`; (3) copy or write to a file.
* **Verify:** unit test the block contains the required fields and no secret
  after redaction.

### 36. Failure diagnosis with a category and a suggested action

* **What:** classify a failure (network, permission, syntax, SDK/toolchain,
  dependency, linker) and give the next action, including the install command
  when a tool is missing.
* **Why:** the user fixes the real cause instead of guessing.
* **Where:** `diagnostics/classifier.py` already produces `Category` and
  `first_actionable`; add `diagnostics/next_steps.py::action_for(diagnostic)`
  that attaches the install command from `toolchain_hints` for
  `MISSING_TOOL`/SDK categories; consumed by `cli.cmd_build` (already prints a
  diagnosis) and the GUI `_build_done`.
* **Steps:** (1) map each category to a plain sentence and an action; (2) for
  missing tools, append the copyable command; (3) keep the raw output line.
* **Verify:** unit test each category yields a non-empty action, and that the
  toolchain category contains a `winget`/`choco`/`scoop`/`pip` command.

---

## E — The user can trust it

### 37. Never a fake success

* **What:** if something was not produced or not verified, the app says so
  instead of reporting success.
* **Why:** trust; the project's core rule.
* **Where:** audit of `build/orchestrator.py` (already falls back to packaging
  sources with an explicit note), `container/validator.py` (validation failure
  is reported, not swallowed), `build/exe_exporter.py` (failure lines), and
  `cli.cmd_build` (validation failure is recorded as a failed history entry).
* **Steps:** (1) add `tests/unit/test_no_fake_success.py` with explicit cases:
  no artifacts → the note is present; validation failure → `ok=False` and exit
  code 5; cancelled build → `CANCELLED`, never `SUCCESS`; failed `.exe` export
  → not written to history as a success; (2) grep the codebase for any
  `except: pass` that could hide a failed step.
* **Verify:** the new test file passes and a lint test fails if a build path
  returns success while `errors` is non-empty.

### 38. Wider secret redaction

* **What:** redact tokens, keys, passwords, signed URL queries, `Authorization`
  headers (already), plus home paths (`C:\Users\<name>`), PEM private-key
  blocks, `.npmrc`/`.pypirc` auth lines, connection strings, and `*_TOKEN`/
  `*_KEY`/`*_SECRET` environment assignments.
* **Why:** logs and copies can be shared safely.
* **Where:** `logging_config.py::_REDACTION_PATTERNS` (extend);
  `build/process.py` already applies `redact` to every log line; the GUI
  copy/summary path and `build/history.py` messages must run through the same
  function.
* **Steps:** (1) add the patterns; (2) replace the user's home path with
  `C:\Users\<user>`; (3) ensure `history.make_record` redacts `message`;
  (4) add a unit test with a realistic log sample.
* **Verify:** unit test asserts a PEM block, a home path and an `API_KEY=` line
  are all redacted in one pass.

### 39. Integrity check of the produced `.exe` and `.ece`

* **What:** after producing an artefact, record and display its SHA256 and
  size, and verify the `.ece` by deep validation; warn when a re-check does not
  match.
* **Why:** the user can prove the file is intact and unmodified.
* **Where:** `build/exe_exporter.py` result gains `sha256`/`size` (and a sidecar
  `<name>.exe.sha256`); `tools/release_check.py` already does this for release
  builds — reuse its helper; `.ece` already supports `validate_container(...,
  deep=True)`, which `cli.cmd_build` already runs.
* **Steps:** (1) add the sidecar write + verify after export; (2) include
  `sha256`/`size` in history and `--json`; (3) on deep-validation failure,
  report failure with exit code 5 (already done) and keep the container.
* **Verify:** unit test the sidecar contents; integration test rebuilds and
  compares the recorded hash with the file on disk.

### 40. Persistent local history with export and one-click re-run

* **What:** history in JSONL with JSON/CSV export, one-click re-run, and
  per-type timings that feed the estimator (item 34).
* **Why:** builds become reproducible and future estimates get better.
* **Where:** `build/history.py` (`BuildRecord` carries `mode`, `sha256` and
  `duration_seconds`); `usability/history_query.py` provides filtering,
  JSON/CSV export and replay commands; `HistoryDialog` gains search, filters,
  Export and "Re-run"; timing samples live in `work/timings.jsonl` (I3).
* **Steps:** (1) extend the record and keep `from_dict` tolerant of old lines;
  (2) add export to JSON and CSV (stdlib `csv`); (3) "Re-run" prefills the form
  with the recorded project/mode; (4) on each completed build, append a timing
  sample so the next estimate is computed from real history.
* **Verify:** unit test round-trips old and new history lines, exports a CSV
  with a header, and asserts a completed build appends exactly one timing
  sample that changes the next estimate.

---

## Summary table

| # | Item | Priority | Effort | Order |
|---|---|---|---|---|
| 2 | Empty-state message | High | S | 1 |
| 27 | `doctor` command | High | M | 2 |
| 3 | Summary after detection | High | M | 3 |
| 31 | Explicit states (planning/packaging) | High | S | 4 |
| 5 | Missing-toolchain card + copy | High | M | 5 |
| 24 | Corrupt config/history recovery | High | S | 6 |
| 28 | `--json` everywhere + exit codes | High | M | 7 |
| 34 | Build time bar / estimation | High | L | 8 |
| 7 | Destructive-action confirmations | High | S | 9 |
| 4 | "What can I do now?" | High | M | 10 |
| 13 | Dry-run / plan preview | High | M | 11 |
| 14 | Exact command shown + copy | High | S | 12 |
| 21 | One-click retry | High | S | 13 |
| 36 | Failure category + action | High | S | 14 |
| 39 | Integrity of `.exe`/`.ece` | High | S | 15 |
| 11 | Ambiguous candidates chooser | High | M | 16 |
| 16 | Monorepo subproject choice | High | M | 17 |
| 18 | Consent naming the script | High | M | 18 |
| 22 | Cancel with cleanup + elapsed | High | S | 19 |
| 8 | Result summary + open folder | High | S | 20 |
| 9 | Fixed error format + copy | High | M | 21 |
| 37 | No-fake-success test suite | High | S | 20 |
| 6 | Tooltips per option | Medium | S | 22 |
| 12 | Why this adapter | Medium | S | 23 |
| 15 | Toolchain version vs minimum | Medium | M | 24 |
| 17 | Long-path / non-ASCII warning | Medium | S | 25 |
| 19 | Plain-language validate/extract | Medium | S | 26 |
| 19b/20 | Compare two `.ece` | Medium | M | 27 |
| 32 | Log level counts + copy errors | Medium | S | 28 |
| 33 | Per-step progress names | High | S | 20b |
| 35 | Copyable summary | Medium | S | 30 |
| 25 | Package-only mode | Medium | M | 31 |
| 26 | `ece init --template` | Medium | M | 32 |
| 30 | History search/filter | Medium | S | 33 |
| 38 | Wider secret redaction | Medium | S | 34 |
| 40 | History export + re-run + timings | High | M | 35 |
| 1 | First-run wizard | High | L | 36 |
| 6/… | (see above) | — | — | — |
| 10 | Warn on close during build | Medium | S | 37 |
| 12/… | (see above) | — | — | — |
| 15/… | (see above) | — | — | — |
| 23 | Safe step skipping | Low | M | 38 |
| 25/… | (see above) | — | — | — |
| 29 | Shortcuts documented in-app | Low | S | 39 |
| 32/… | (see above) | — | — | — |

Notes:

* Effort: **S** ≤ half a day, **M** ≈ 1–2 days, **L** > 2 days (one developer,
  stdlib only).
* Touched areas with the most leverage: `ui/main_window.py`,
  `cli.py`, `build/orchestrator.py`, `build/process.py`, `build/history.py`,
  `detection/*`, `diagnostics/*`, `container/*`.
* Cross-cutting: I1 (`local_store`), I2 (`toolchain_hints`), I3 (`timing`),
  I4 (`progress`), I5 (`dialogs_common`) should be built first — items
  3, 4, 5, 8, 9, 21, 26, 27, 28, 34, 36, 40 all depend on them.
