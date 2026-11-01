# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/), and the
application release version follows [Semantic Versioning](https://semver.org/).
The `.ece` container format has its own version (currently **1**), independent
of the application version.

## [1.3.0] - 2026-11-01

### Added — integrated DECOMPILER workspace

- New **DECOMPILER** tab in the existing window (same theme, navigation, status
  bar and background task runner), with `Ctrl+2` / `Ctrl+1` shortcuts and menu
  entries.
- Drag-and-drop import of files from Explorer (`WM_DROPFILES`), plus Select
  File / Select Folder, multi-file import and batch processing.
- Universal format detection by magic bytes with extension fallback: PE,
  .NET/CLR, ELF, Java class, JAR, Python bytecode, PyInstaller CArchive, ECE,
  ZIP/TAR/GZIP/BZIP2/XZ, source files, unknown binaries.
- Read-only analysis with SHA-256, architecture, runtime, language, imports,
  exports, sections, symbols, dependencies, metadata, strings, resources, debug
  information and supported operations.
- Modular decompilation backends:
  - **.NET**: ECMA-335 metadata reader; complete C# project via ILSpy
    (`ilspycmd`, verified version 9.1.0.7988); generated outline fallback.
  - **Java**: class-file reader (constant pool, fields, methods, descriptors,
    SourceFile), JAR extraction, generated outlines, optional `javap`
    declaration and bytecode listings.
  - **Python**: pyc header/marshal/dis readers, structural module outline,
    complete instruction listing; PyInstaller CArchive **and embedded PYZ**
    extraction with frozen-module reconstruction.
  - **Native PE**: headers, machine, sections, entropy, imports, exports,
    resources, version info, PDB, signature presence, strings and disassembly
    (Capstone or `objdump`).
  - **ELF**: headers, class/endianness, sections, needed libraries, symbols,
    build-id notes, debug sections, strings and disassembly.
  - **ECE**: manifest/index reading and payload extraction through the existing
    hardened `.ece` reader/extractor, with recursive analysis of `.pyc`/`.class`.
  - **Archives**: safe listing and extraction with path/size checks.
- Reconstructed-project explorer with explicit statuses: recovered, partial,
  generated, extracted resource, unsupported.
- Integrated source editor: multiple tabs, syntax highlighting, undo, find,
  search across recovered files, Save / Save As, automatic timestamped backups,
  Python/JSON syntax checking with the failing line highlighted, and a diff
  against the recovered original.
- Export of the reconstructed project (including `recovered-project.json`) to a
  user-selected folder.
- Real recompilation pipeline: project-kind detection, toolchain verification,
  exact command plan, consent for project scripts, timeout/cancellation,
  captured logs and artifact verification. Supported kinds: Python, .NET, Java
  (Maven/Gradle/javac), CMake, Make, Go, Rust and Node.js.
- Manifest gate: reconstructed outlines are marked `recompile_ready: false` and
  the recompiler refuses them instead of producing a misleading binary.
- Optional ILSpy installation from the UI with explicit consent, into the
  application's own data folder.
- CLI: `decompile` (with `--analyze-only`, `--json`, `--no-disassembly`,
  `--no-resources`), `recompile` (`--plan`, `--consent`, `--exe`, `--json`) and
  `formats`.
- New tests for detection, readers, backends, safe extraction, recompilation,
  the CLI and the full GUI decompiler workflow.

### Changed

- Application interface now hosts two workspaces in one window via a notebook;
  the compiler page is unchanged.
- Version moved to 1.3.0 across metadata, documentation and build verification.
- Background task failures are logged with their traceback instead of being
  silently swallowed.
- Async dialog callbacks check that the dialog still exists before touching
  widgets.
- Added bounded-file guards to the PE, ELF, class and pyc readers.
- `pyproject.toml` documents optional `decompile` extras (`pefile`, `capstone`)
  and the real repository URLs; the base runtime remains dependency-free.

### Fixed

- ILSpy project output now builds with a current SDK: `netcoreappX.Y` is
  normalised to `netX.Y` and the compiler-reserved `RefSafetyRules` module
  attribute is removed (both logged).
- Recovered `.cs`/`.csproj` files are registered without copying a file onto
  itself when ILSpy already wrote them in place.
- PyInstaller parsing uses the real cookie layout (archive length includes the
  cookie) and padded TOC entry names, and reads the embedded PYZ archive.

### Documentation

- Rewrote `README.md` with the real 1.3 capabilities.
- Added `docs/DECOMPILER.md`, `docs/RECOMPILER.md`, `docs/SUPPORTED_FORMATS.md`,
  `docs/INSTALL.md` and `docs/TESTING.md`.
- Updated architecture, security, contributing and troubleshooting docs.

### Security

- Imported files are never executed.
- Archive/container extraction rejects absolute paths, `..`, symlinks and
  oversized entries; the `.ece` extractor's existing checks are reused.
- Recompilation commands are argv lists with `shell=False`, timeouts and
  cancellation.

## [1.2] - 2026

- Usability release: export readiness before building, three-part failure
  messages, Windows path-collision guards, verified release builds, practical
  troubleshooting, first-run walkthrough, tooltips, build-history search and
  export, `.sha256` sidecars, plan preview, project health and timing estimates.
- Advanced package generator (ZIP, TAR.GZ, folder, Inno Setup, NSIS, DEB,
  AppImage), automatic repair with rollback, and baseline/optimized build
  comparison.
- Fusion engine: multi-input/multi-language workspace, module graph, bridges,
  per-module builds, incremental cache and packaging.
- See `docs/RELEASE_NOTES_1.2.md`.

## [1.1]

- Earlier tagged release. See the repository history and the release notes in
  `docs/` for its recorded changes.

## [1.0]

- First tagged version (`firstversion`). See the repository history for its
  recorded changes.

## Notes

- `.ece` per-file and content hashes are **integrity** checks, not signatures.
- The project is MIT licensed (see `LICENSE`).
- Version 1.3.0 does not change the `.ece` container format; version 1
  containers remain readable.
