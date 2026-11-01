# Installation guide

## Requirements

- Windows 10 or Windows 11, x64 (desktop application and release executables).
- Python 3.10+ for running from source (verified on 3.12).
- The toolchains you intend to use (the application reports missing ones).

The base runtime has **no third-party dependencies**. The decompiler improves
with optional extras; the recompiler uses whatever toolchains you already have.

## From source

```powershell
git clone https://github.com/garbanzos223-web/UNIVERSAL-COMPILER.git
cd UNIVERSAL-COMPILER
python -m pip install -e .
```

Optional decompiler extras:

```powershell
python -m pip install -e ".[decompile]"      # pefile + capstone
```

Verify the installation:

```powershell
python -m ece_autocompiler --version
python -m ece_autocompiler formats
python run_tests.py unit
```

## Run

```powershell
python -m ece_autocompiler gui        # desktop window
python -m ece_autocompiler --help     # CLI commands
python -m ece_autocompiler doctor     # environment check
```

After `pip install -e .` the console entry points are also available:
`ece-autocompiler` and `ece-autocompiler-gui`.

## Build the Windows executable

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

This produces and verifies:

- `dist\ECE-AutoCompiler.exe` (desktop, windowed)
- `dist\ECE-AutoCompiler-cli.exe` (console)

Use `-Layout onedir` for a folder build with a faster start, `-Target gui` or
`-Target cli` for one artifact. The script installs PyInstaller if missing and
runs `tools\release_check.py`; a bundle that cannot start fails the build.

## Optional external tools

| Purpose | Tool | Notes |
| --- | --- | --- |
| C# recovery | ILSpy `ilspycmd` 9.1.0.7988 | Install from the DECOMPILER tab (**Prepare .NET decompiler**) or `dotnet tool install ilspycmd --version 9.1.0.7988 --tool-path <dir>` |
| Java listings | `javap` | Comes with a JDK; set `JAVA_HOME` or PATH |
| Disassembly | Capstone | `pip install -e ".[decompile]"` |
| PE resources/version info | `pefile` | `pip install -e ".[decompile]"` |
| Fallback disassembly | `objdump` | LLVM/MinGW toolchain |
| Recompilation | `dotnet`, `javac`, `mvn`, `gradle`, `cmake`, `make`, `go`, `cargo`, `npm` | Only the kinds you rebuild need them |

Nothing is installed silently. ILSpy installation is an explicit confirmation
in the UI.

## Local data

Configuration, history, logs, decompiled projects, editor backups and optional
tools live under:

```
%LOCALAPPDATA%\ECE-Universal-AutoCompiler\
```

Set `ECE_AUTOCOMPILER_HOME` to relocate this folder (used by the tests so they
never touch real data).

## Uninstall

- From source: `python -m pip uninstall ece-universal-autocompiler`.
- Remove `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\` to delete local data.
- Release executable: delete the `.exe` (and its folder for `onedir` builds).
