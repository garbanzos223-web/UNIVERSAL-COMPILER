# Security model

This document describes what the tool defends against, and — just as
importantly — what it does **not**.

## Threat model

The tool runs on a developer's machine and handles two kinds of untrusted or
semi-trusted input:

1. **A project directory** chosen by the user, which may contain hostile build
   scripts.
2. **An `.ece` container** that may come from an untrusted source.

### Trust boundaries

- **Project scripts are untrusted.** Installing dependencies and running build
  scripts executes third-party code. The tool marks such steps
  `requires_approval` and refuses to run them without explicit consent
  (`BuildOrchestrator` raises `ConfigurationError`; `run_command` re-checks the
  flag).
- **Containers are untrusted.** Every path, offset and length is validated
  before use.

## Defences

### Command execution
- All commands are `argv` lists with `shell=False`. There is **no** shell-string
  concatenation, so project paths cannot inject shell metacharacters.
- On Windows, `.cmd`/`.bat` (`npm`, `gradle`) are launched as
  `["cmd", "/c", *argv]` — still a list, still no interpolation.
- Steps have timeouts; the child is killed if it exceeds them.
- Cancellation is cooperative and also kills the process.

### Container parsing
- Bounds and consistency checks before any read: header size, block ranges,
  entry count, `payload_offset` and every entry's `[offset, offset+length)`
  must lie inside the payload region and not overlap.
- Counts and lengths are capped by `config.Limits` (entries, path length, depth,
  total/entry uncompressed size, manifest/index bytes).
- Decompression is bounded: every inflate call passes a hard `max_output`, so a
  decompression bomb fails fast instead of exhausting memory. Streamed
  decompression enforces the index's declared size exactly (too many or too few
  bytes is corruption).

### Path handling
- Entry paths must be relative, forward-slash, non-empty, within length and
  depth limits, free of control characters, and free of `..`, leading `/`,
  drive prefixes and backslashes (`container/security.py`).
- `resolve_destination` confirms the resolved target — walking up existing
  ancestors and resolving symlinks — still sits inside the extraction root, so a
  planted symlink cannot redirect a write outside it.
- Extraction writes to a temporary file, verifies the hash, then atomically
  replaces the target; symlink/junction targets are refused.

### Secret handling
- Build output is passed through `redact()` before it is written to the log or
  shown, reducing the chance of leaking tokens that appear in tool output on the
  developer's own machine.

## What this does NOT protect against

- **`content_hash` and per-file hashes are integrity checks, not signatures.**
  Anyone who rewrites a container can recompute all hashes. There is no
  publisher identity, no signing, no certificate chain. **Do not treat a passing
  `validate` as evidence of who built a container.**
- **Consent is all-or-nothing per build.** Once granted, project scripts run
  with the user's full privileges. There is no sandbox, no filesystem jail, no
  syscall filtering. Only build projects you trust.
- **The tool does not scan built artifacts for malware.**
- **Toolchain binaries are trusted** as installed on the machine; the tool does
  not verify their provenance.
- **Symlink handling is best-effort** and platform-dependent (notably Windows
  junctions), though the extractor refuses targets that are links.

## Secure defaults

- No dependency installation or script execution without consent.
- `overwrite` is off by default for extraction and packaging.
- Output and work directories default under `%LOCALAPPDATA%`.
- The GUI shows a confirmation dialog before running an approved-script build.
