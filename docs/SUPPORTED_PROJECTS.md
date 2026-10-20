# Supported projects

Honesty first: **only Python and Node.js builds are verified end-to-end in this
project's test suite.** Every other technology is *detected*, and its adapter
*will* run a real command **if and only if** the required tool is installed.
Nothing reports success it did not achieve.

Legend:

- **Real build (verified)** — an integration test builds a sample project here.
- **Tool-gated** — detection always works; the build command runs only when the
  tool is present, otherwise the requirement report says `ready = false` with an
  install hint.

| Technology | Detected by | Adapter id | Required tool | Build status |
| --- | --- | --- | --- | --- |
| Python | `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, `Pipfile` | `python` | `python` | **Real build (verified)** — `compileall` syntax check |
| Node.js | `package.json` | `node` | `node`, `npm` | **Real build (verified)** — `npm install` (optional) + `npm run build` or syntax check |
| TypeScript | `package.json` + `tsconfig.json` | `node` | `node`, `npm` | Tool-gated via the project's npm build script |
| .NET / C# / F# / VB | `*.csproj`, `*.fsproj`, `*.vbproj`, `*.sln` | `dotnet` | .NET SDK (`dotnet`) | Tool-gated — `dotnet publish -c Release` |
| Rust | `Cargo.toml` | `rust-cargo` | `cargo` | Tool-gated — `cargo build --release` |
| Go | `go.mod` | `go` | `go` | Tool-gated — `go build ./...` |
| Java (Maven) | `pom.xml` | `java-maven` | `mvn` | Tool-gated — `mvn package -DskipTests` |
| Java (Gradle) | `build.gradle` | `java-gradle` | `gradle` | Tool-gated — `gradle build -x test` |
| Kotlin (Gradle) | `build.gradle.kts` | `kotlin-gradle` | `gradle` | Tool-gated — `gradle build -x test` |
| C/C++ (CMake) | `CMakeLists.txt` | `cpp-cmake` | `cmake` (+ generator) | Tool-gated — `cmake -S . -B build` **and** `cmake --build build --config Release --parallel N` |
| C/C++ (Make) | `Makefile`, `GNUmakefile` | `cpp-make` | `make` | Tool-gated — `make -j N` |
| C/C++ (Meson) | `meson.build` | `cpp-meson` | `meson` + `ninja` | Tool-gated — `meson setup build` + `ninja -C build -j N` |
| PHP | `composer.json` | `php` | `php` | Tool-gated — `php -l` per file (syntax check) |
| Unity | `Assets/` **and** `ProjectSettings/` directories | `unity` | Unity editor | Tool-gated — no automatic CLI build; detected and reported |
| Unreal Engine | `*.uproject` | `unreal` | Unreal engine | Tool-gated — no automatic CLI build; detected and reported |

## Executable (`.exe`) export

The `exe` command (and the GUI's **Build .exe** mode) turns a project into a
standalone **Windows x64** executable. Layout options: **onefile** (one big
`.exe`, slower start) or **onedir** (`--onedir`: a folder with the `.exe` and
its runtime, faster start).

| Technology | `.exe` export | Layout choice honoured | How |
| --- | --- | --- | --- |
| Python | **Supported (verified)** | yes — `--onefile` (default) / `--onedir` | PyInstaller bundles the entry point + interpreter. |
| C# / .NET | Tool-gated | yes — `PublishSingleFile` in onefile mode | `dotnet publish -c Release -r win-x64 --self-contained`. |
| Rust | Tool-gated | n/a — the toolchain already emits one `.exe` | `cargo build --release` → `target\release\*.exe`. |
| Go | Tool-gated | n/a — already one `.exe` | `go build -o app.exe`. |
| C/C++ (CMake/Make) | Tool-gated | n/a — already one `.exe` | CMake/Make build → the produced `.exe` is located. |
| Node.js | Not yet | — | Would need a bundler such as `pkg`/`nexe`; detection and the `.ece` build still work. |
| Unity, Unreal | Not supported here | — | Games are built inside their engine/editor; this tool detects them and reports the missing engine rather than faking an `.exe`. |

Tool-gated means: the toolchain is looked up in `PATH` and, if it is missing,
the export **fails with an install hint** (`winget`/`scoop`/`choco`/`pip`)
instead of pretending to build.

## Package-manager detection

For Node and Python projects the detector reads lock files to report which
package manager is in use, and the build honors it:

- **Node:** `yarn.lock` → yarn, `pnpm-lock.yaml` → pnpm, `bun.lockb`/`bun.lock` → bun, otherwise npm.
- **Python:** `poetry.lock` → poetry, `uv.lock` → uv, `pdm.lock` → pdm, otherwise pip.

## Distributable packages

The `package` command turns a project into a distributable, independently of
the language it is written in. The formats and what they need:

| Format | Needs | Notes |
| --- | --- | --- |
| `zip` | nothing | Reproducible: two runs over the same inputs are byte-identical. |
| `tar.gz` | nothing | Same, with UNIX permissions. |
| `folder` | nothing | An unpacked, ready-to-run directory. |
| `inno` | Inno Setup | A Windows installer; the generated `.iss` script is kept. |
| `nsis` | NSIS | A Windows installer; the generated `.nsi` script is kept. |
| `deb` | `dpkg-deb` | Debian package (Linux). |
| `appimage` | `appimagetool` | AppImage (Linux). |

Every package is verified after it is written, and its manifest lists the
metadata, the files and their SHA-256 checksums. See
[docs/ADVANCED_FEATURES.md](ADVANCED_FEATURES.md).

## Tool discovery

`detection/toolchains.py` probes for: `python`, `node`, `npm`, `dotnet`,
`cargo`, `rustc`, `go`, `java`, `javac`, `mvn`, `gradle`, `cmake`, `meson`,
`ninja`, `make`, `gcc`, `gpp`, `clang`, `cl`, `msbuild`, `php`, `git`.

Run `python -m ece_autocompiler tools` or open the **Toolchains** tab to see
what is present on your machine. `dotnet` distinguishes "runtime present but no
SDK", which matters because `dotnet publish` needs the SDK.

The probe result is cached locally for 15 minutes (it costs one process per
tool) and dropped as soon as `PATH` changes. `tools --refresh` and
`doctor --refresh` re-probe on demand.

## Adding support for a technology

- For a **CLI tool** with a one-line build command: add an `ExternalToolAdapter`
  entry in `adapters/registry.py`, a detection marker in
  `detection/markers.py`, a probe in `detection/toolchains.py` and a build-plan
  builder in `adapters/native_plans.py`.
- For a **first-class** integration: subclass `BuildAdapter` in `adapters/`,
  implement its six abstract methods, and add it to `DEFAULT_ADAPTERS`.

Add a test that at minimum exercises detection, and a real build test whenever
the tool is installable in CI.
