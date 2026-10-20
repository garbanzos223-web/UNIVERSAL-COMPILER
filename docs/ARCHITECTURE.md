# Architecture

ECE Universal AutoCompiler is a standard-library Python application organised
into small, single-responsibility packages. Data flows in one direction:

```
        detection                adapters                 build
  ┌───────────────────┐   ┌───────────────────────┐   ┌──────────────────┐
  │ markers           │   │ AdapterRegistry       │   │ run_command      │
  │ toolchains        │──▶│  ├─ PythonAdapter     │──▶│ orchestrator     │
  │ detector → scan() │   │  ├─ NodeAdapter       │   │ history          │
  └───────────────────┘   │  └─ ExternalToolAdapter│   └────────┬─────────┘
                          └───────────────────────┘            │
                                                                ▼
   ui / cli  ◀──── diagnostics                container ── extraction
   (front ends)     (classify output)         writer/reader   Extractor
                                              validator/spec
```

## Packages

| Package | Responsibility |
| --- | --- |
| `container` | The `.ece` format: `spec` (constants/struct), `codec` (compression/hash), `model` (manifest & index), `writer`, `reader`, `validator`, `security` (path safety), `diff` (container comparison), `integrity` (SHA-256 sidecars). |
| `detection` | `markers` (file/dir markers and source hints), `toolchains` (real tool discovery via `shutil.which` + version probe), `detector.scan()` (ranked candidates, ambiguity, monorepo awareness). |
| `adapters` | `base` (abstract `BuildAdapter` + data types), `registry` (adapter instances), `python_adapter`, `node_adapter`, `external_tool_adapter`. |
| `build` | `process.run_command` (argv execution, timeout, cancel, redaction), `orchestrator.BuildOrchestrator` (detect → plan → build → collect → package, plus `compile_project` for callers that only need the build), `compile_service` (one place that runs a build and returns its output), `history` (JSONL log of attempts), `timing` (local duration estimates + live ETA). |
| `packaging` | Advanced package generator: `models` (request/result), `collection` (artifact + entry-point + dependency discovery), `licenses` (redistribution checks), `manifest` (metadata, file list, checksums), `validation` (read the package back), `formats` (zip, tar.gz, folder, Inno Setup, NSIS, DEB, AppImage), `packager` (the flow). |
| `repair` | `diagnostics` (parsers per compiler + classification), `analysis` (root-cause grouping, manual actions), `rules` (deterministic repairs with diffs), `engine` (backup, apply, rebuild, verify, roll back, journal). |
| `optimize` | `profiles` (debug/release/performance/size/balanced/custom), `plan` (flags per toolchain + conflict detection), `measure` (before/after numbers, only what was measured), `recommend` (actionable advice). |
| `diagnostics` | `classifier` maps tool output to `Category` values and hints; `doctor` checks the machine, folders, long paths and toolchain versions. |
| `usability` | Front-end-neutral helpers shared by CLI, GUI and tests: `guidance` (next step, failures), `health`, `plan` (dry run), `summary`, `history_query`, `wizard`. |
| `templates` | Project skeletons for `init` (Python script/package, Node.js, C++/CMake). |
| `local_store` | Atomic local config/history storage under `%LOCALAPPDATA%`; no database. |
| `toolchain_hints` / `toolchain_versions` | Copyable install commands (winget/choco/scoop/URL) and minimum supported versions. |
| `security` | Filename sanitizing and path-containment helpers for front ends. |
| `ui` | Tkinter interface: `theme`, `widgets` (task runner, console, tables), `main_window` (panels and all dialogs). |
| `cli` / `app` | Argument parsing and sub-command dispatch (`analyze`, `plan`, `build`, `exe`, `doctor`, `init`, `diff`, `history`, `validate`, `inspect`, `extract`, `tools`, `gui`). |

## Key design decisions

### Argument lists, never shell strings
Every command is an `argv` list passed to `subprocess.Popen` with
`shell=False`. On Windows, `.cmd`/`.bat` wrappers (npm, gradle) are routed
through `cmd /c` with an explicit list — still no string concatenation. This
removes an entire class of command-injection bugs.

### Consent for project-controlled code
A `BuildPlanStep.requires_approval` flag marks steps that run scripts from the
project. `BuildOrchestrator` refuses to proceed without `consent_granted`, and
`run_command` re-checks the flag as defence in depth.

### Off the UI thread, on the Tk thread
Long operations run on daemon threads. `TaskRunner` marshals results back to the
Tk main loop through a `queue.Queue` drained by an `after` timer — the only safe
way to touch widgets. Worker threads never call Tk directly.

### Honest capability reporting
`ExternalToolAdapter.inspect_requirements` returns `ready = False` and an
install hint when a tool is absent. Detection still works; the build simply does
not pretend to run.

### Container integrity, not authenticity
The manifest carries a package-level content hash and the index carries per-file
SHA-256 digests. These detect corruption and tampering *relative to the file*,
but they are not a signature: anyone can rewrite a container and its hashes. See
`docs/SECURITY.md`.

### Verified packages, verified repairs
A package is only reported as successful after it has been read back and every
file has been checked against the manifest (`packaging/validation.py`). A
repair is only reported as successful after the project has been rebuilt and
the targeted error is gone (`repair/engine.py`); otherwise the change is
rolled back from the backup taken before it.

### Measured performance only
The optimizer compares two real builds. A percentage is produced only when
both numbers exist, and a runtime claim only when a benchmark command actually
ran (`optimize/measure.py`).

## The build pipeline

1. **Scan** — `detector.scan()` walks the tree (bounded, ignoring VCS/cache
   dirs) and returns ranked `DetectionCandidate`s.
2. **Select** — the orchestrator picks the top candidate, or a candidate the
   user/preference names; a subproject root is honoured for monorepos.
3. **Plan** — `adapter.create_build_plan()` builds an ordered list of
   `BuildPlanStep`s.
4. **Consent** — if any step requires approval and consent was not granted,
   raise `ConfigurationError` before running anything.
5. **Execute** — `run_command` runs the steps with timeout/cancel/redaction and
   writes a build log.
6. **Collect** — `adapter.collect_artifacts()` gathers outputs; a source-tree
   fallback applies when a build legitimately produces no new files.
7. **Package** — `ContainerWriter` writes header, manifest, index and payload.

## Extension points

- **Add a tool-gated adapter**: register a new `ExternalToolAdapter` in
  `adapters/registry.py` and add detection markers in `detection/markers.py`.
- **Add a first-class adapter**: subclass `BuildAdapter`, implement the six
  methods, and list it in `DEFAULT_ADAPTERS`.
- **Add a container format revision**: bump `FORMAT_MINOR` in
  `container/spec.py`; unknown top-level manifest keys already survive a
  rewrite so older tools do not strip newer metadata.
- **Add a package format**: subclass `PackageFormat` in
  `packaging/formats/` and register it in `packaging/formats/__init__.py`;
  the CLI, the window and the tests read the registry.
- **Add a repair rule**: write a function that takes a `Diagnostic` and
  returns a `RepairProposal` (with the patched text and a diff) and add it to
  `RULES` in `repair/rules.py`. Only `high` confidence proposals are applied
  automatically.
- **Add an optimization profile**: add an `OptimizationProfile` in
  `optimize/profiles.py` and, when the toolchain family is new, an applier in
  `optimize/plan.py` that rewrites the build plan steps.
