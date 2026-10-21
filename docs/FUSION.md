# Fusion Engine — Multi-File, Multi-Language Project Assembly

Status: **implemented** (fusion package, CLI, GUI dialog). Everything below
describes shipped code; where a capability depends on an external toolchain
that is missing on a host, the engine reports that instead of faking success.

## What it is

The fusion engine lets one workspace collect inputs written in different
languages (C/C++, Python, C#/.NET, JS/TS, Rust, Go, Java, PHP, Unreal Engine
projects, raw game assets, prebuilt libraries) and assembles **one
application** out of them. Each module is compiled with its own real
toolchain, connections are made through evidence-based integration methods,
and the result is packaged as an ECE container, ZIP or folder.

Core rule: **honesty over theatre.** Two modules that both build are NOT
"integrated"; they are integrated when there is real evidence for a shared
library export, an import that resolves, an engine plugin, or the like. When
a connection is missing, the plan says exactly what is missing.

## Layout

- `src/ece_autocompiler/fusion/workspace.py` — multi-input workspace
  (copy or link imports, no flattening, no silent overwrites, conflict
  detection: same relative path with different content, identical duplicates,
  mixed-type modules).
- `src/ece_autocompiler/fusion/models.py` — `FusionConfig` (user-authored,
  stored in `ece-project.json`), `WorkspaceInput`, `Module`, `BuildTask`,
  `ModuleGraph`.
- `src/ece_autocompiler/fusion/modules.py` — the module graph. Dependencies
  are parsed from the manifests the projects themselves declare: CMake
  (`add_subdirectory`, `target_link_libraries`), Cargo path dependencies,
  `go.mod` local replaces, `.csproj` `ProjectReference`, npm `file:` deps and
  `workspaces`, `.uproject`/`Build.cs`, and Python `import` matched to real
  module names. Cycles are detected and explained. Entry-point candidates are
  reported, never silently chosen.
- `src/ece_autocompiler/fusion/bridges.py` — per-pair integration plans:
  `native-link`, `shared-library` (ctypes), `c-abi` (P/Invoke, FFI),
  `python-extension`, `engine-module` (Unreal), `editor-python`,
  `bundled` (same interpreter) and the honest fallback `process` (separate
  executable distributed with the application).
- `src/ece_autocompiler/fusion/compatibility.py` — real PE/ELF header
  architecture detection (x86/x64/arm64), Python extension vs interpreter
  architecture, Unreal engine association vs installed engines, required
  runtime checks.
- `src/ece_autocompiler/fusion/unreal.py` — builds the actual
  UnrealBuildTool/RunUAT/UnrealEditor-Cmd commands from a discovered engine
  installation and runs them (cancel + timeout). With no engine installed it
  returns `ready=False` with the concrete reasons. Raw assets are never
  treated as cooked Unreal assets; asset-processing Python runs at build
  time and editor-only scripts are never copied into the packaged game.
- `src/ece_autocompiler/fusion/incremental.py` — dependency-aware cache:
  a module's key = SHA-256 over its source hashes + configuration +
  dependency keys. Rebuild only what changed; invalidate consumers when a
  dependency rebuilds; drop the entry when a build fails or outputs vanish.
- `src/ece_autocompiler/fusion/repair.py` — one failed module is analysed by
  the existing repair engine (real diagnostics, minimal proposals, rollback
  that keeps only changes that helped), then the module is rebuilt.
- `src/ece_autocompiler/fusion/orchestrator.py` — the 9-stage pipeline that
  assembles, packages (ECE/ZIP/folder), validates and reports.

## Workflow

CLI (mirrors the GUI dialog "Tools → Fusion workspace"):

```
ece-autocompiler workspace create <dir> --name mygame
ece-autocompiler workspace add <dir> <path...> [--link] [--module id]
ece-autocompiler workspace list <dir>
ece-autocompiler workspace config <dir> --entry-point mygame --depend app=nativelib
ece-autocompiler fuse plan <dir>          # modules, order, bridges, conflicts
ece-autocompiler fuse validate <dir>      # compatibility report
ece-autocompiler fuse build <dir> [--format ece|zip|folder] [--no-cache]
                                        [--repair-attempts N]
```

Build result files live in `<workspace>/ece-generated/`: per-module build
logs, `report.json` (the full build report: tasks, graph, bridges, cache
hits, repairs, validation), the assembled application and the package.

## ECE container extension

The `.ece` format v1 layout is unchanged. Fusion packages extend the
manifest additively: `payload_type = "fusion-application (<fmt>)"`,
`entry_points` describe the chosen module, `runtime_requirements` list every
declared runtime component, and the unknown-field keys `fusion_modules` /
`fusion_graph` carry the module manifests and dependency graph. Unknown
top-level manifest keys survive reads and rewrites (container core keeps
them), so older readers are unaffected. Extraction keeps the standard path
traversal protections.

## Known, explicit limits

- Toolchains that are not installed are never faked: a module whose tool is
  missing fails its build task with an install hint and the run reports
  `ok=false`. Nothing pretends that a project was built.
- The Unreal integration can construct and run the real commands only on
  hosts where the engine is installed; on other hosts the plan says exactly
  that. Unreal build/packaging is executed through the engine's own tools
  (UBT + BuildCookRun); ECE never compiles Unreal sources by itself.
- Python `<-> C++` bridging requires the native side to export a shared
  library or a compiled CPython extension; static libraries need an explicit
  extension build step (CPython API/Cython) — the plan names it instead of
  pretending import works.
- JavaScript/TypeScript `<-> native` needs an N-API addon build; until one
  exists the plan proposes a separate process.
- Files that belong to no project manifest (lone scripts) become modules via
  source hints only when painting is unambiguous; otherwise they are listed
  as unassigned inputs rather than invented into modules.
- The assembler currently launches Python entry points with a generated
  `run_application.py` launcher that puts every bundled module on the import
  path. Native entry points come from their own toolchain's outputs.

## Tests

`tests/unit/test_fusion.py` (37 tests) covers workspace operations and
guarantees, module detection/dependencies/ordering/cycles/entry points,
bridge decisions, PE architecture checks, end-to-end Python fusion (real
builds on this toolchain, including running the assembled application),
cache hit/invalidation, honest missing-toolchain failures, and Unreal
command construction plus honest engine-missing reporting. Tests that need
unavailable toolchains assert honest failure, never success.
