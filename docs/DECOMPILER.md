# Decompiler

The decompiler is the **DECOMPILER** tab of the same ECE Universal AutoCompiler
window. It imports files, identifies them, analyses them read-only, runs the
available backend, shows the reconstructed project and lets you edit and export
it. It never executes the imported program.

## Design principles

1. **Nothing is executed.** Readers parse bytes and metadata with explicit
   bounds. There is no `exec`, no plugin loading and no shell.
2. **Supported operations are reported.** Detection returns the operations this
   build can actually perform; the UI enables only those actions.
3. **Generated output is labelled.** An outline, pseudocode or disassembly is
   never presented as the original source. Each recovered file has a status:
   `recovered`, `partial`, `generated`, `resource` or `unsupported`.
4. **Recompilation is separate.** A generated outline is refused by the
   recompiler; only a complete source project can be rebuilt.
5. **Optional tools are explicit.** ILSpy, `javap`, Capstone and `pefile` improve
   results; without them the application reports the limitation.

## Packages

| Module | Responsibility |
| --- | --- |
| `decompiler/formats.py` | Universal detection by magic bytes, header fields and extension; operation catalog. |
| `decompiler/analyzer.py` | Safe per-file analysis: SHA-256, format details, warnings. |
| `decompiler/pe.py` | Bounded PE reader: headers, sections, entropy, imports, exports, PDB, strings, optional `pefile` enrichment. |
| `decompiler/elf.py` | Pure-standard-library ELF reader: headers, sections, dynamic dependencies, symbols, notes, debug sections. |
| `decompiler/dotnet_metadata.py` | Minimal ECMA-335 `#~`/`#Strings` parser for TypeDef/MethodDef names and counts. |
| `decompiler/java_class.py` | Constant-pool and class-file parser; descriptor mapping; outline generator. |
| `decompiler/python_bytecode.py` | pyc header + `marshal` + `dis`; structural outline generator. |
| `decompiler/pyinstaller.py` | CArchive cookie/TOC reader and PYZ (`marshal` + `zlib`) reader. |
| `decompiler/archive.py` | Safe ZIP/TAR/GZIP/BZIP2/XZ listing and extraction with path and size checks. |
| `decompiler/backends/` | One backend per family: `dotnet`, `java`, `python`, `native` (PE/ELF), `ece`, `archive`. |
| `decompiler/service.py` | Batch session: analysis → backend → merged reconstructed project + manifest. |
| `decompiler/recompiler.py` | Project-kind detection, plan, real commands, artifact verification. |
| `decompiler/tools.py` | Finds ILSpy/`javap`/`objdump`; explicit ILSpy installation. |

## Workflow

```
import files/folders (button or Explorer drop)
    → detect format → bounded analysis → show supported operations
    → choose backend → write project to a new folder
    → project/resources explorer → edit source → export
    → optional recompile with a real toolchain
```

The session writes `recovered-project.json` with the inputs, their formats, the
recovered files, statuses, provenance, warnings and a `recompile_ready` flag.
Exporting copies every file **and** that manifest; the recompiler uses the flag
to refuse incomplete outlines.

## Backends

### .NET

- Always: PE + CLR metadata, type/method counts, resources (with `pefile`).
- With ILSpy: a complete C# project (`.csproj` and sources) is decompiled into
  `dotnet/`. The backend normalises two mechanical ILSpy outputs that would
  otherwise fail with a current SDK: the `netcoreappX.Y` target framework and
  the compiler-reserved `RefSafetyRules` attribute. The fixes are logged.
- Without ILSpy: `outline/<assembly>.cs` + a `.csproj`, marked generated and
  not recompilable.

### Java

- Class models and outlines are always available.
- `javap -p` and `javap -c` listings are added when a JDK is found.
- JARs are extracted safely; class outlines and non-class resources are written
  to the project. A generated Maven skeleton is provided as a starting point.
- Java source recovery is not claimed beyond what is actually present.

### Python

- `.pyc`: structural outline + complete disassembly + analysis JSON.
- PyInstaller: CArchive TOC, every entry extracted with bounds, embedded PYZ
  modules decompressed and unmarshalled into outlines; native libraries and data
  files become resources.
- Archives with real `.py` files: the sources are recovered unchanged and the
  project is marked recompilable.

### Native PE / ELF

- Real headers, sections, imports/exports (PE), needed libraries/symbols (ELF),
  strings, version info/PDB (PE), debug sections (ELF), resources (PE).
- Disassembly with Capstone when installed, else `objdump` when present.
- The backend states explicitly that original C/C++ source cannot be restored.

### ECE

- Uses the application's existing `ContainerReader` and `Extractor` (hashes,
  path traversal, Windows path collisions, depth and size limits).
- Analyses extracted `.pyc` and `.class` files; preserves real source files;
  reports `recompile_ready` from what the payload really contains.

## Integrated editor

- Multiple files, syntax highlighting for Python, Java, C#, C/C++, JS/TS, Go,
  Rust, PHP, JSON, XML and assembly.
- Undo/redo, find in file, search across recovered files, Save / Save As.
- Save creates a timestamped backup in `<work>\editor-backups` before writing.
- Python and JSON files are syntax-checked after saving; the failing line is
  highlighted. Other languages report that the check is unavailable rather than
  pretending to validate them.
- Compare the current text with the recovered original (unified diff).

## Drag and drop

On Windows the main window is registered as an Explorer drop target
(`WM_DROPFILES` via a subclassed window procedure). The Select File / Select
Folder buttons remain the fallback and work everywhere.

## CLI

```powershell
ece-autocompiler decompile <files...> [-o DIR] [--analyze-only] [--json]
ece-autocompiler recompile <project> [-o DIR] [--exe] [--plan] [--consent] [--json]
ece-autocompiler formats [--json]
```

## Optional tools

| Tool | What it enables | How to get it |
| --- | --- | --- |
| ILSpy `ilspycmd` 9.1.0.7988 | Complete C# source recovery | **Prepare .NET decompiler** button (consented `dotnet tool install`), or set `ECE_ILSPYCMD` |
| `javap` (JDK) | Java declarations and bytecode listings | Install a JDK and set `JAVA_HOME` or PATH |
| Capstone (`pip install capstone`) | PE/ELF disassembly | `pip install -e ".[decompile]"` |
| `pefile` | PE resources, version info | `pip install -e ".[decompile]"` |
| `objdump` | Fallback disassembly | LLVM/MinGW toolchain |

## Safety limits

- PE/ELF readers: 512 MiB; class files: 64 MiB; pyc: 256 MiB; CArchive: 2 GiB.
- Archive/container extraction: entry and total size limits, depth limits,
  absolute/`..` paths rejected, symlinks skipped, duplicate Windows paths
  rejected by the `.ece` extractor before writing.
- CArchive/PYZ entry and total byte limits (128 MiB per entry, 512 MiB total).
- No file is written outside the chosen output folder.

## What is *not* implemented

- Native source recovery (C/C++/Rust/Go binaries) — impossible from a stripped
  binary; analysis is the honest product.
- Java source decompilation without an external Java decompiler.
- Exact Python 3.12 source reconstruction (the structural outline is what is
  offered).
- 7z/RAR extraction.
- Automatic installation of any tool other than ILSpy, and only with consent.
