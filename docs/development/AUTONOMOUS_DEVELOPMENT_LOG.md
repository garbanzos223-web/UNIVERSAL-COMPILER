# Autonomous development log

Living checkpoint for the autonomous engineering mission (universal build
orchestration). Update it after each substantial milestone; do not repeat
completed work. The long-form history stays in `docs/development-log.md` and
the fusion architecture in `docs/FUSION.md`.

## Baseline (session start)

- Repository: `D:\AUTOCOMPILER\ECE-Universal-AutoCompiler`, branch `main`,
  clean tree at `a75d64a`.
- Version 1.2; Python 3.12.0; Windows; stdlib-only core.
- `python -m unittest discover -s tests -t .` -> **466 tests, OK (skipped=1)**.
- `python tools/lint_stdlib.py` -> **95 files clean**.
- Toolchains present: python, node, git, PyInstaller, Pillow.
  Absent: cmake, ninja, gcc/cl, dotnet, javac, cargo, go, make, Unreal.
  Native/Unreal paths are verified for honest failure and command
  construction only.

## Completed in this session

### 1. Repair wiring bug (correctness)

`fusion/orchestrator.py` called `repair_module` without importing it, so
`fuse build --repair-attempts N` crashed with `NameError` on the first failed
module. Imported it and covered the path with a test that asserts the repair
engine is invoked and its result is recorded.

Files: `src/ece_autocompiler/fusion/orchestrator.py`,
`src/ece_autocompiler/fusion/repair.py` (per-module repair workspace so
parallel repairs never share backups).

### 2. Bounded, dependency-aware parallel module builds

- Independent modules build concurrently (`ThreadPoolExecutor`), bounded by
  `--jobs` / `FusionConfig.max_parallel` (0 = automatic, max 4, hard cap 16).
- Modules that share a directory never build at the same time.
- A failed dependency marks its consumers `skipped` with the reason instead
  of building them against a missing artifact.
- Cancellation marks unattempted modules `skipped` and reports it.
- Cache keys are computed once, before scheduling; cache entries for failed
  modules are dropped.
- Unit tests prove real overlap (peak concurrency 2), dependency ordering,
  and failure propagation.
- Real run: `fuse build -j 2` reported `Building support, tool (up to 2 in
  parallel)` and all modules built.

Files: `fusion/orchestrator.py`, `fusion/models.py`, `cli.py`,
`ui/main_window.py` (Parallel modules control + progress).

### 3. Artifact verification and provenance (Phase 4.4)

- Every built module's artifacts are discovered, capped and recorded with
  size, SHA-256, kind, language, target, configuration and the real toolchain
  identity (id/path/version).
- A compiled module that declares outputs but produced none is marked
  **failed**; exit code 0 alone is not success.
- The final package is hashed too (`package_artifact`); directory outputs
  record size and file count and explain why there is no single hash.

### 4. Reproducible packages

- Fusion manifests now store a structural graph snapshot taken before any
  build: cache flags, statuses and durations cannot change package bytes.
- `SOURCE_DATE_EPOCH` pins `created_utc` (container manifest) and ZIP entry
  timestamps.
- Test builds the same workspace twice for both `.ece` and ZIP under a fixed
  epoch and asserts identical SHA-256.

Files: `fusion/orchestrator.py`, `container/model.py`.

### 5. Cache invalidation on build settings

Cache keys now include build tool, optimization profile and the resolved
toolchain path/version, so changing a compiler or profile rebuilds modules
even when no source changed.

Files: `fusion/incremental.py`, `fusion/orchestrator.py`.

### 6. Fusion builds in shared history

New `fusion/history.py`; CLI and GUI record each fused build with mode
`fusion`, package hash, artifact count and per-module failures. Verified
through `ece-autocompiler history` after a real build.

### 7. Subprocess stream leak

`build/process.py` never closed the child's stdout pipe after the reader
thread finished (ResourceWarning in real builds). Now closed explicitly.

### 8. Performance

- Module scans are cached per orchestrator run (same root can host several
  modules); a regression test asserts one scan per module root.
- Packaging reuses the run's graph instead of rebuilding it (one less full
  workspace walk per package).
- Cache keys computed once instead of twice per module.
- Measured on a generated workspace with 8 independent Python modules
  (21 files each, `--no-cache`, real compileall subprocesses):
  **serial `-j 1`: 2.80 s; parallel `-j 4`: 1.23 s** (~2.3x).

### 9. Honest package formats and native entry points

- `package_format: "exe"` used to fall through to a ZIP while the report said
  "exe". It is now rejected with an explanation (an .exe needs a native
  entry point or a real packager); no file is written.
- A native entry point's real executable (and its libraries) is placed at the
  application root instead of only under `modules/<id>/`.

### 10. Package include/exclude rules actually applied

`include`/`exclude` in `ece-project.json` were ignored. They now filter
`.ece`, ZIP and folder outputs with the same glob semantics as the packaging
subsystem; counts are recorded in `report.json` (`package_filters`).

### 11. Security: symlinks are never followed

Workspace import, module discovery and file measurement skip symbolic links
with a note, so a link cannot pull unrelated files outside the imported input
into the build or the package. Test skipped on hosts where Windows refuses to
create symlinks.

### 12. Module roots are contained

`_module_root` resolves a module root and refuses it when it escapes its
input folder; all callers fail that module honestly instead of scanning or
copying outside the workspace.

### 13. Virtual environments and atomic reports

- Python modules use a project-local `.venv`/`venv` interpreter when it
  exists; the plan records which interpreter was chosen.
- `report.json` is written atomically (temp + fsync + replace, `.bak` kept),
  so a crash cannot leave a truncated report.

### 14. GUI build safety

The fusion dialog now has a working **Cancel build** button (passes a real
cancel token to the orchestrator), disables Build while a run is active (no
double builds) and reports cancellation distinctly. The dialog is part of the
GUI smoke test.

### 15. Dependency cycles abort the build honestly

Cycle members used to be built in an arbitrary graph order. They are now
marked failed with the cycle path and a "break the cycle" instruction; their
dependents are skipped. An unresolvable order (defensive case) fails too
instead of guessing. No cycle member is compiled against a missing provider.

### 16. Cache integrity and atomic cache writes

Cache entries now store the SHA-256 of every recorded artifact; a modified,
truncated or replaced artifact is a cache miss, not a hit. The cache file is
written atomically (temp + fsync + replace) so a crash cannot truncate it.

### 17. GUI configuration and report visibility

- The fusion dialog can list the real modules (Plan), choose/save the entry
  point, set package format and parallel jobs.
- After a build it shows the package SHA-256 and the path of `report.json`.
- `workspace config` gained `--include`, `--exclude`, `--jobs` and
  `--test-command MODULE=COMMAND` (malformed values are a usage error).

## Verification

Commands (run from the repository root):

```
python -m unittest discover -s tests -t .
python tools/lint_stdlib.py
```

Results after the changes:

- **488 tests, OK (skipped=2)** (was 466). The second skip is the symlink
  test on a Windows session that cannot create links.
- **96 files clean** (was 95; new `fusion/history.py`).
- Real integration (not mocked), workspace
  `C:\Users\robert\AppData\Local\Temp\opencode\paratest\ws`:
  `fuse build -j 2` built `support` and `tool` in parallel, then `runner`;
  `.ece` validated; `fuse launch` printed `answer 21`; second run served
  all three modules from cache; `history` shows the fusion record.

## Known environmental limits

- No cmake/ninja/gcc/cl/dotnet/javac/cargo/go/make/Unreal on this host, so
  those adapters are verified only as command construction + honest failure.
- GitHub release publication of 1.2 still needs Robert (no `gh` CLI).

## Next concrete tasks

1. Phase 6 visual pass: audit theme tokens/focus states in `ui/theme.py`,
   fix the highest-friction screen (build progress/log readability), verify
   by launching the GUI and capturing the actual window.
2. Unreal asset-import scripts: execute a configured editor import script
   before BuildCookRun when the engine exists (currently only recorded).
3. Performance measurement of large-workspace scans (`scan` vs
   `build_graph`) with a generated tree; keep only measured improvements.
