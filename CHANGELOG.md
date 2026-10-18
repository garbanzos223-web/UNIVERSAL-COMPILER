# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/), and this
project adheres to [Semantic Versioning](https://semver.org/) for its own
release version (independent of the `.ece` container format version).

## [Unreleased]

### Added
- `.ece` container format v1: header, bounded-compression manifest/index blocks,
  per-entry SHA-256 hashes, and a deterministic package content hash.
- Container **writer**, **reader**, **validator** (structural + deep) and a
  **safe extractor** (path-traversal, symlink, depth and size protections).
- Project **detection** for Python, Node/TypeScript, .NET, Rust, Go,
  Java/Maven, Java/Kotlin/Gradle, C/C++ (CMake/Make/Meson), PHP, Unity and
  Unreal, with monorepo/ambiguity handling.
- **Toolchain discovery** with version probing and honest "missing" reporting.
- **Build adapters**: `PythonAdapter` and `NodeAdapter` (real, verified builds)
  plus a generic `ExternalToolAdapter` that is detection-only or tool-gated.
- **Build orchestrator**: detect → plan → consent → execute → collect →
  package, with logs, timeouts and cancellation.
- **Diagnostics**: classification of build output into actionable categories.
- **Build history** stored as JSON Lines.
- **CLI** (`analyze`, `build`, `validate`, `inspect`, `extract`, `tools`, `gui`).
- **Desktop GUI** (Tkinter, dark theme) with Build, Analyze, Packages,
  Toolchains, History and About panels.
- Test suite using the standard library `unittest` (unit + real integration
  builds). No third-party dependencies.

### Security
- Commands are argv lists with `shell=False`; no shell-string interpolation.
- Consent required for steps that run project-controlled code.
- Bounded decompression to resist decompression bombs.
- Secrets redacted from build logs.

### Notes
- `content_hash` and per-file hashes are **integrity** checks, not signatures.
- The project license has **not** been chosen yet.
