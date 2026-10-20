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
| `build` | `process.run_command` (argv execution, timeout, cancel, redaction), `orchestrator.BuildOrchestrator` (detect → plan → build → collect → package), `history` (JSONL log of attempts), `timing` (local duration estimates + live ETA). |
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
