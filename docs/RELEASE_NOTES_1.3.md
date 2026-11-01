# ECE Universal AutoCompiler 1.3.0 — integrated decompiler and recompiler

Release date: **2026-11-01**
Container format: **v1 (unchanged)**

## Highlights

1. **DECOMPILER tab in the same window.** Import by button or Explorer drag and
   drop, identify formats automatically, analyse safely, reconstruct a project,
   edit source and recompile — with the existing theme, status bar, logging and
   background task runner.
2. **Universal format analysis.** PE, .NET/CLR, ELF, Java class/JAR, Python
   bytecode, PyInstaller CArchive/PYZ, ECE, ZIP/TAR/GZIP/BZIP2/XZ and source
   files.
3. **Modular backends.** .NET metadata + ILSpy C# recovery; Java class models,
   JAR extraction and `javap` listings; Python structural reconstruction and
   PyInstaller module extraction; native PE/ELF analysis and disassembly; ECE
   extraction through the hardened reader.
4. **Real recompilation with a manifest gate.** Python, .NET, Java
   (Maven/Gradle/javac), CMake, Make, Go, Rust and Node.js plans. Generated
   outlines are refused instead of producing a misleading binary.
5. **Integrated editor.** Syntax highlighting, multi-tab editing, find/search,
   automatic backups, Python/JSON syntax checks and diff against the recovered
   original.
6. **Repository and release engineering.** New `.gitattributes`, a stricter
   `.gitignore`, a Windows test matrix, a tag-driven release workflow and full
   documentation.

## Verified on the build machine

Environment: Windows, CPython 3.12.0, PyInstaller 6.22.3, .NET SDK 8.0.426,
Capstone 5.0.9, pefile 2024.8.26.

| Check | Result |
| --- | --- |
| Full test suite (`python run_tests.py`) | **518 tests, 0 failures, 2 environment-dependent skips** |
| Lint (`scripts\lint.ps1`) | clean (123 source files) |
| Desktop executable starts and stays alive | passed (5 s probe) |
| Console executable `--version` | `ECE Universal AutoCompiler 1.3.0` |
| Console build/validate/package probe | `demo.ece` built (662 bytes), deep-validated, `demo-1.0.0.zip` verified |
| Frozen CLI decompilation of a real `.pyc` | recovered project written (outline + disassembly + manifest) |
| Frozen CLI recompilation of a real Python project | `.pyc` artifact verified; log written |

### Executable hashes

`dist\release.json` is the authoritative record produced by
`tools\release_check.py` during the build.

| Artifact | Size | SHA-256 |
| --- | --- | --- |
| `ECE-AutoCompiler.exe` (desktop) | 13,807,143 bytes | `49138750a5b0c53c94e93f2aed3c3f5f1c675865a8f515983519822cde70aa44` |
| `ECE-AutoCompiler-cli.exe` (console) | 13,854,242 bytes | `976ea6aa53335548ea44045bf72225e9afd32bcf21e239d3e6f151d552766cdd` |

The executables are build artifacts and are **not** committed to the
repository. Download them from the GitHub release or rebuild with
`scripts\build_exe.ps1`.

## What is real, and what is not

- **Real:** format identification, bounded analysis, PE/ELF metadata, .NET
  metadata, Java class models, Python bytecode structure and disassembly,
  PyInstaller CArchive/PYZ extraction, ECE extraction, safe archive extraction,
  project explorer, editor, export, recompilation commands and artifact
  verification.
- **Conditional:** complete C# source (needs ILSpy), Java declaration/bytecode
  listings (needs a JDK), disassembly (Capstone or `objdump`), PE
  resources/version info (`pefile`).
- **Not supported:** native source recovery from C/C++ binaries, exact Python
  3.12 source reconstruction, Java source recovery without a Java decompiler,
  7z/RAR extraction, and recompiling analysis-only output.

Recompiled binaries are new builds. Original signatures, compiler settings,
metadata and binary layout are not preserved.

## Upgrade notes

- The application version is now `1.3.0`; the `.ece` format remains version 1,
  so existing containers stay readable and no data migration is needed.
- Optional extras: `pip install -e ".[decompile]"` adds `pefile` and
  `capstone`.
- ILSpy is still not bundled. Install it from the DECOMPILER tab (consented) or
  set `ECE_ILSPYCMD`.
- The decompiler writes to new folders and does not modify imported files.
  Editor backups live under `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\work\editor-backups`.

## Documentation

- [Decompiler](../docs/DECOMPILER.md)
- [Recompiler](../docs/RECOMPILER.md)
- [Supported formats](../docs/SUPPORTED_FORMATS.md)
- [Installation](../docs/INSTALL.md)
- [Testing](../docs/TESTING.md)
- [Troubleshooting](../docs/TROUBLESHOOTING.md)

## Security

Imported files are never executed. Readers are bounded and reject hostile
paths, sizes and container layouts. Recompilation uses argv lists without a
shell, timeouts, cancellation and explicit consent for project scripts. See
[SECURITY.md](../SECURITY.md).
