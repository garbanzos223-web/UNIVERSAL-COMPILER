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
| C/C++ (CMake) | `CMakeLists.txt` | `cpp-cmake` | `cmake` (+ generator) | Tool-gated — `cmake -S . -B build` |
| C/C++ (Make) | `Makefile`, `GNUmakefile` | `cpp-make` | `make` | Tool-gated — `make` |
| C/C++ (Meson) | `meson.build` | `cpp-meson` | `ninja` (with meson) | Tool-gated — grouped with the ninja/meson family |
| PHP | `composer.json` | `php` | `php` | Tool-gated — `php -l` per file (syntax check) |
| Unity | `Assets/` **and** `ProjectSettings/` directories | `unity` | Unity editor | Tool-gated — no automatic CLI build; detected and reported |
| Unreal Engine | `*.uproject` | `unreal` | Unreal engine | Tool-gated — no automatic CLI build; detected and reported |

## Tool discovery

`detection/toolchains.py` probes for: `python`, `node`, `npm`, `dotnet`,
`cargo`, `rustc`, `go`, `java`, `javac`, `mvn`, `gradle`, `cmake`, `ninja`,
`make`, `gcc`, `gpp`, `clang`, `cl`, `msbuild`, `php`, `git`.

Run `python -m ece_autocompiler tools` or open the **Toolchains** tab to see
what is present on your machine. `dotnet` distinguishes "runtime present but no
SDK", which matters because `dotnet publish` needs the SDK.

## Adding support for a technology

- For a **CLI tool** with a one-line build command: add an `ExternalToolAdapter`
  entry in `adapters/registry.py`, a detection marker in
  `detection/markers.py`, and (if needed) a probe in `detection/toolchains.py`.
- For a **first-class** integration: subclass `BuildAdapter` in `adapters/`,
  implement its six abstract methods, and add it to `DEFAULT_ADAPTERS`.

Add a test that at minimum exercises detection, and a real build test whenever
the tool is installable in CI.
