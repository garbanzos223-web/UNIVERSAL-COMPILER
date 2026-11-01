# Supported formats and operations

This page lists what the **1.3.0** build can actually do. Identification and
analysis are separate from source recovery: a format may be fully analysable
without any decompilation backend.

Legend:

- **Yes** — implemented and covered by the test suite or verified end to end.
- **Conditional** — implemented, but needs an optional tool or a specific input;
  the UI/CLI reports the exact requirement.
- **No** — not supported; the application says so instead of guessing.

## Decompiler formats

| Format | Detection | Analysis | Source recovery | Disassembly | Resources | Recompile |
| --- | --- | --- | --- | --- | --- | --- |
| Windows PE `.exe` | MZ + PE signature, machine, subsystem | Sections, entropy, imports, exports, version info*, PDB, signatures | **No** (native source cannot be restored) | **Conditional** (Capstone or `objdump`) | **Conditional** (`pefile`) | No |
| Windows PE `.dll` | Same as PE | Same as PE, marked as library | **No** | **Conditional** | **Conditional** | No |
| .NET assembly | CLR data directory | ECMA-335 metadata: types, methods, runtime version | **Conditional** — full C# with ILSpy; otherwise generated outline | **Conditional** (Capstone/objdump on native entry code) | **Conditional** (`pefile`) | **Conditional** (.NET SDK + ILSpy source) |
| Java `.class` | `CAFEBABE` + class-file version | Constant pool, access flags, fields, methods, descriptors, SourceFile | Named outline (not original source) | **Conditional** (`javap -c`) | n/a | **Conditional** (real `.java` sources + JDK) |
| Java `.jar` / `.war` | ZIP with `.class` entries | Archive index + class models | Class outlines; **real** `.java` files inside the archive are recovered unchanged | **Conditional** (`javap`) | Yes (safe extraction) | **Conditional** (Maven/Gradle/javac with sources) |
| Python `.pyc` | CPython magic + version | Code objects, imports, names, signatures | Structural outline; bodies omitted | Yes (`dis.Bytecode`) | n/a | No (outline is refused) |
| PyInstaller application | `MEI` CArchive cookie | CArchive TOC, embedded PYZ TOC | Frozen modules reconstructed as outlines + bytecode listings | Yes (for readable code objects) | Yes (native libs and data) | No (outline is refused) |
| Python wheel / ZIP with `.py` | ZIP entries | Index | **Real source** extracted unchanged | n/a | Yes | **Conditional** (Python) |
| ECE `.ece` | ECE magic | Manifest, index, deep validation | Payload extracted by the hardened reader; real sources preserved | n/a | Yes | **Conditional** (project kind from manifest/sources) |
| Linux ELF | ELF magic, class, endianness | Header, program headers, sections, needed libraries, symbols, notes, debug sections | **No** (native source cannot be restored) | **Conditional** (Capstone/objdump) | n/a | No |
| TAR / GZIP / BZIP2 / XZ | Magic bytes | Listing | Extracted safely | n/a | Yes | **Conditional** (sources) |
| 7z / RAR | Signature | Identification only | No | No | No | No |
| Source files | Extension + content | Encoding, line preview | Used as-is | n/a | n/a | **Conditional** (toolchain) |

\* Version information and full resource listing require the optional `pefile`
package. Capstone is the preferred disassembler; `objdump` is used when
Capstone is absent.

### .NET source recovery

ILSpy's command-line tool (`ilspycmd`) is not bundled. The application can
install the verified version into its own data folder after an explicit
confirmation, using the .NET SDK:

```text
dotnet tool install ilspycmd --version 9.1.0.7988 --tool-path %LOCALAPPDATA%\ECE-Universal-AutoCompiler\tools\ilspycmd
```

`ECE_ILSPYCMD` can point at an existing `ilspycmd.exe`. Without ILSpy, .NET
assemblies are still fully analysed and a namespace/class outline is generated
and marked **generated**, never as original code.

### Java source recovery

`javap` (part of a JDK) adds declaration and bytecode listings. Java source
files are only recovered unchanged when they are present in the input archive.
The generated `.java` files preserve real names, field types and method
signatures, but their bodies are placeholders and are labelled as such.

### Python bytecode

The reader uses the running interpreter's `marshal` format. The reconstruction
keeps real imports, module-level constants, class and function names, argument
lists and a complete instruction listing. Bodies are omitted. Bytecode from a
different CPython version is identified and its disassembly is reported only
when the running interpreter can read it.

PyInstaller bundles are opened by the bundled CArchive reader; the embedded
`PYZ` archive is read with `marshal` + `zlib`, bounded by entry and total size
limits. Frozen modules are reconstructed as outlines. Nothing is executed.

## Compiler project types

The build side keeps its existing support. Only Python and Node.js builds are
covered end to end by the integration suite; every other adapter runs a real
command only when its toolchain is installed.

| Technology | Detected by | Required tool |
| --- | --- | --- |
| Python | `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, `Pipfile` | `python` |
| Node.js / TypeScript | `package.json` (+ `tsconfig.json`) | `node`, `npm` |
| .NET / C# / F# / VB | `*.csproj`, `*.fsproj`, `*.vbproj`, `*.sln` | `dotnet` |
| Rust | `Cargo.toml` | `cargo` |
| Go | `go.mod` | `go` |
| Java (Maven) | `pom.xml` | `mvn` |
| Java / Kotlin (Gradle) | `build.gradle`, `build.gradle.kts` | `gradle` |
| C/C++ (CMake) | `CMakeLists.txt` | `cmake` + generator |
| C/C++ (Meson) | `meson.build` | `meson`, `ninja` |
| C/C++ (Make) | `Makefile`, `GNUmakefile` | `make` |
| PHP | `composer.json` | `php` |
| Unity | `Assets/` + `ProjectSettings/` | Unity editor |
| Unreal Engine | `*.uproject` | Unreal engine |

See [SUPPORTED_PROJECTS.md](SUPPORTED_PROJECTS.md) for the existing compiler
table and `.exe` export details.

## Recompilation plans

| Recovered/real project | Plan | Requirements |
| --- | --- | --- |
| Python | `python -m compileall` (optional PyInstaller `.exe`) | `python` |
| .NET | `dotnet build -c Release -o <out>` | `dotnet`, a `.csproj`/`.sln` |
| Java | `mvn package` / `gradle build` / `javac @sources` | the matching tool + sources |
| CMake | `cmake -S . -B ...` + `cmake --build` | `cmake` |
| Make | `make` | `make` |
| Go | `go build ./...` | `go` |
| Rust | `cargo build --release` | `cargo` |
| Node.js | `npm install` + `npm run build` (consent) | `npm` |
| Loose C/C++ sources | Refused: no build system to link them correctly | a `CMakeLists.txt`/`Makefile` |
| Native PE/ELF without source | Refused | real source |
| Generated outlines | Refused by the manifest gate | complete source |
