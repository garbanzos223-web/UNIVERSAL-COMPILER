# Recompiler

The recompiler rebuilds a **new** binary from recovered source with the real
toolchain installed on your PC, then verifies that the artifact exists. It is
integrated in the DECOMPILER tab (**Recompile project**) and in the CLI
(`ece-autocompiler recompile`).

## What "recompile" means here

- It runs real commands (argv lists, `shell=False`, timeout, cancellation).
- It reports the exact command, exit code, captured output and log path.
- It only reports success when a new artifact of the expected kind was found on
  disk after the build.
- It does **not** claim to reproduce the original binary. Signatures, original
  compiler settings, metadata, section layout and symbols are not preserved.

## Supported plans

| Project kind | Detection | Commands | Artifact check |
| --- | --- | --- | --- |
| Python | `.py` files | `python -m compileall -q <root>` (optional `python -m PyInstaller --onefile ...`) | `.pyc` (or `.exe`) created |
| .NET | `.csproj`/`.sln` | `dotnet build <project> -c Release -o <out>` | `.dll` in `<out>` |
| Java (Maven) | `pom.xml` | `mvn -q -DskipTests package` | `.jar` |
| Java (Gradle) | `build.gradle[.kts]` (+ wrapper) | `gradlew build -x test` or `gradle build -x test` | `.jar` |
| Java (sources) | `.java` files | `javac -encoding UTF-8 -d <out>\classes @sources.txt` | `.class` |
| C/C++ (CMake) | `CMakeLists.txt` | `cmake -S . -B <out>\cmake-build -DCMAKE_BUILD_TYPE=Release` then `cmake --build ... --config Release` | executable |
| C/C++ (Make) | `Makefile` | `make` | executable |
| Go | `go.mod` | `go build -o <out>\app.exe ./...` | `.exe` |
| Rust | `Cargo.toml` | `cargo build --release` | executable |
| Node.js | `package.json` | `npm install` then `npm run build` | files under `dist/`/`build/` |

Anything else is refused with an explanation. Loose C/C++ source files without a
build system are refused because the linker inputs cannot be inferred safely.

## The manifest gate

Decompiling writes `recovered-project.json` with `recompile_ready`:

- `true` — every input produced a complete project that can be built
  (for example: a real `.py` source tree, an ILSpy C# project, an ECE payload
  containing sources);
- `false` — at least one input produced a generated outline, analysis output or
  partial reconstruction.

When the manifest says `false`, the recompiler refuses before running anything:

> This recovered project is marked as not recompilable: it contains generated
> outlines without real method bodies.

This is deliberate: building a stub application and calling it a restoration of
the original would be a false result. To rebuild a .NET assembly, install ILSpy
and decompile again so the project contains real C#; for Java, the input must
contain real `.java` sources.

## Consent

The recompiler asks before running steps that execute project-controlled code:

- `npm install` / `npm run build`,
- PyInstaller (it imports and analyses the entry script),
- any future step marked with `requires_approval`.

The exact commands are displayed before the prompt. Refusing consent stops the
build; nothing is run.

## Output verification

1. The plan is printed before execution (`--plan` shows it and exits).
2. `run_command` enforces the timeout and cancellation and records the log.
3. After a successful exit, the recompiler scans for new artifacts with a known
   suffix (`.exe`, `.dll`, `.jar`, `.class`, `.pyc`, ...).
4. No artifact means failure: *"The commands succeeded but no new artifact was
   found. The build did not produce verifiable output."*

Artifacts are listed with path and size in the report. The report can be copied
from the dialog.

## Known limitations

- .NET: an ILSpy project is adjusted mechanically (valid `netX.Y` TFM, removal
  of the reserved `RefSafetyRules` module attribute) so a current SDK can build
  it. NuGet packages referenced by the original project are not restored from
  the assembly; add them manually if the build reports them.
- Java: a generated Maven skeleton is a starting point, not a verified rebuild;
  dependencies are not recovered from a JAR beyond the class references.
- Python: outlines are refused. Real `.py` sources are compiled; a PyInstaller
  `.exe` is a new standalone bundle, not the original one.
- Native: only complete source plus a build system can be rebuilt. Analysis
  output from a PE/ELF binary cannot.
- Signatures and original build flags are never preserved.

## CLI

```powershell
# show what would run; nothing executes
ece-autocompiler recompile C:\work\lib-recovered --plan

# real Python rebuild to .pyc
ece-autocompiler recompile C:\work\pyapp -o C:\work\rebuilt

# Python standalone .exe (asks for consent semantics via --consent)
ece-autocompiler recompile C:\work\pyapp --exe --consent -o C:\work\rebuilt

# machine-readable result
ece-autocompiler recompile C:\work\lib-recovered --json
```

Exit codes: `0` verified build, `1` refused or failed build (details on stdout),
`2` usage error.
