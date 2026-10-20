# Troubleshooting

Everything here follows the same shape as the tool's own errors: **what
happened**, **why**, and **what to do**. If a message in the app or the CLI
sends you here, the three sections below its text are the ones to read.

Windows 10/11 x64 is the only supported platform.

---

## Where the tool keeps its files

Knowing these paths makes most problems obvious:

| What | Path |
| --- | --- |
| Exported `.exe` files | `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\output\` |
| Working data (one folder per `.exe` build) | `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\work\` |
| Build logs | `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\work\logs\*.log` |
| Build history | `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\work\history.jsonl` |
| Timing history used for estimates | `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\work\timings.jsonl` |
| Optimization comparisons | `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\work\optimizations.jsonl` |
| Repair journal (every attempt) | `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\work\repair-journal.jsonl` |
| Backup of the files a repair touched | `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\work\repair-backups\<run>\` |
| Staging area used while packaging | `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\work\package-staging\` |
| Settings and first-run state | `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\config\` |
| Crash log (desktop build only) | `%TEMP%\ece-autocompiler-crash.log` |

Old `work\exe_*` folders are pruned automatically; the most recent ones and
their logs are kept so a finished build can still be inspected.

---

## Building the app's own `.exe`

### "PyInstaller is not installed (pip install pyinstaller)"

**What happened.** The export needs PyInstaller to freeze a Python project, and
it could not be imported by the interpreter that would run it.

**What to do:**

```powershell
python -m pip install pyinstaller
```

Then export again. Nothing else changes; you do not need to reinstall the app.

### The console build says: "This is a standalone build and no Python interpreter was found on PATH"

**Why:** `ECE-AutoCompiler-cli.exe` is a frozen build, so it cannot use itself as
the Python interpreter that hosts PyInstaller. It looks for a real Python on
`PATH` (`py`, `python`). Without one there is nothing to run PyInstaller *in*,
and the tool says that instead of pretending PyInstaller is missing.

**What to do:** install Python (https://www.python.org/downloads/windows/) and
make sure "Add python.exe to PATH" is ticked, then run the export again. If you
only need to package the project, `.ece` builds need no interpreter at all:

```powershell
ECE-AutoCompiler-cli.exe build C:\path\to\project
```

## The build fails with "Access is denied"

**What happened:** PyInstaller could not replace
`dist\ECE-AutoCompiler.exe` (or a file inside `dist\`).

**Why:** almost always a previously launched copy of the app is still open - a
window left open, or a `onefile` build whose child process outlived its
launcher. Windows locks the file while it runs.

**What to do:**

```powershell
Get-Process ECE-AutoCompiler -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.Id -Force }
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

Close the app's window before rebuilding, and remember that a `onefile`
executable takes a moment to shut down after the window disappears.

---

## Build succeeded, but the `.exe` does not open

1. **Read the crash log.** The desktop build records unhandled exceptions to
   `%TEMP%\ece-autocompiler-crash.log`; the last block is the failing import or
   call:
   ```powershell
   Get-Content "$env:TEMP\ece-autocompiler-crash.log" -Tail 40
   ```
2. **Check it really is your build.** `python tools\release_check.py --dist dist`
   runs the console build (`--version`) and starts the desktop build to confirm
   it stays alive, then reports size and SHA256 for each artifact in
   `dist\release.json`. If a build cannot run, that script fails.
3. **Rebuild clean.** `scripts\build_exe.ps1` already passes `--clean`; if you
   built by hand, delete `build\` and `dist\` first.

If the failure is a missing module, it is almost always a project that imports
things dynamically; open an issue with the crash log attached.

---

## Antivirus and SmartScreen

### The antivirus deletes or quarantines the `.exe`

**What happened.** A freshly built PyInstaller executable is a self-extracting
program with no publisher signature, which is exactly the shape antivirus
heuristics flag. This is a false positive, not a sign that the file is
malicious.

**What to do, in order:**

1. **Prefer the folder layout.** `onedir` builds are flagged far less often than
   single-file ones:
   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -Layout onedir
   ```
2. **Exclude the build folder only** â€” not your whole drive. In *Windows
   Security* â†’ **Virus & threat protection** â†’ *Manage settings* â†’
   **Add or remove exclusions** â†’ *Add an exclusion* â†’ **Folder**, pick the
   repository's `dist` folder (for example
   `D:\AUTOCOMPILER\ECE-Universal-AutoCompiler\dist`). Third-party suites
   (Avast, Kaspersky, ESET, Malwarebytes, 360 Total Security...) have the same
   feature under *Exclusions* / *Exceptions*.
3. **Report the false positive** to the vendor, attaching
   `dist\ECE-AutoCompiler.exe`. Vendors fix these quickly, and it helps everyone.
3. **Keep the exclusion temporary.** Remove it once the vendor resolves the
   detection.

### "Windows protected your PC" (SmartScreen)

**What happened.** The executable is not code-signed, so SmartScreen shows an
unknown-publisher warning.

**What to do:** *More info* â†’ **Run anyway** â€” but only for a build you made
yourself or a file whose SHA256 matches the value published with the release
(`dist\release.json`, or the checksum in the release notes). There is **no**
other verification: this project does not sign builds, and nothing in the tool
claims an executable is "safe".

---

## Exporting a project to a `.exe`

### "Cannot build a `<language>` .exe: missing `<tool>`"

Nothing was built - that is the intended behaviour, not a failure to retry.

| Missing tool | What to install |
| --- | --- |
| `PyInstaller` (Python) | `python -m pip install pyinstaller` |
| `dotnet` (.NET) | [.NET SDK](https://dotnet.microsoft.com/download) |
| `cargo` (Rust) | [rustup.rs](https://rustup.rs) |
| `go` (Go) | [go.dev/dl](https://go.dev/dl/) |
| `cmake` (C/C++ CMake) | CMake **and** a C++ compiler (Visual Studio Build Tools) |
| `make` (C/C++ Make) | Make **and** a C/C++ compiler (MinGW or Visual Studio Build Tools) |

After installing, run the same command again â€” nothing else has to be
reconfigured. To see what this PC can and cannot build **before** trying,
run `ece-autocompiler analyze <folder>`: the *Export readiness (.exe)* section
answers per detected project.

### "No entry point" / the wrong file starts

Python projects need a start file. The tool looks for `main.py`, `app.py`,
`run.py` and similar; when there is none, point at it explicitly:

```powershell
python -m ece_autocompiler exe C:\path\to\project -o C:\out\app.exe --entry C:\path\to\project\juego.py
```

### Node.js, Unity and Unreal projects

These are **detected, not built**:

- **Node.js** needs a bundler. `npm install -g pkg`, then build with the
  project's own tooling â€” or package the project as `.ece`, which needs no
  bundler.
- **Unity / Unreal** are built by their own editor (File â†’ Build Settings, or
  the engine's packaging tool). The tool cannot and does not pretend to produce
  those executables.

---

## `.ece` containers

### "A file already exists at the destination"

Extraction never overwrites silently. Either choose an empty folder or:

```powershell
python -m ece_autocompiler extract package.ece C:\out --overwrite
```

### Validation fails (truncated, corrupt, hash mismatch)

The container is damaged: a hash mismatch means the bytes on disk are not the
bytes the manifest describes. Re-obtain the file from its source; if it keeps
failing, verify with `--deep` on a fresh copy and report where it came from.

### "The container declares file names that Windows treats as the same file"

**Why.** Windows file names are case-insensitive, so a container holding both
`readme.txt` and `README.TXT` (or the same path twice) cannot be unpacked
safely: one entry would silently replace the other. Nothing was extracted - the
check runs before the first byte is written.

**What to do.** Ask the container's author to rebuild it without duplicate
names, or extract a single entry on purpose. `validate` reports this as a
warning (the container itself is intact) and `inspect` still lists the entries.

### A signature is reported as "key missing" or "tool missing"

Signatures are **optional and external**. `--verify-signature` uses the
publisher's minisign/cosign/gpg tool: if that tool or its key is not present,
the result is a warning, never a fake "verified". See
[SECURITY.md](SECURITY.md) and [ECE_FORMAT.md](ECE_FORMAT.md).

---

## Long paths

Builds and extractions that go past the classic 260-character Windows limit fail
with an error from the operating system. Either extract to a short path such as
`C:\out`, or enable long path support (group policy *Enable Win32 long
paths*, or `LongPathsEnabled=1` under
`HKLM\SYSTEM\CurrentControlSet\Control\FileSystem`).

---

## Steps that run project scripts ("consent")

`npm install`, a `build` script or a project's own tool can execute code from
the project. The CLI refuses them unless you ask for them:

```powershell
python -m ece_autocompiler build C:\path\to\project --consent
```

In the window, the same thing is the **Run project scripts** checkbox. Nothing
runs without it - and "it did not install dependencies" is usually this, not a
failure. Pass `--skip-install` when you deliberately want the build without
dependency installation.

---

## The desktop window

- **It does not open.** Read `%TEMP%\ece-autocompiler-crash.log` (see above).
- **It closes as soon as the script/shell that started it ends.** Start it in
  its own process, not as a child of a temporary console:
  ```powershell
  Start-Process .\dist\ECE-AutoCompiler.exe
  ```
- **The title bar is light while the rest of the window is dark.** Windows
  paints that caption; where the session cannot paint it dark, the tool reverts
  the request instead of leaving a half-dark window. Everything else keeps the
  dark theme.
- **A scan never finishes.** Detection is bounded (file counts, depth and time
  limits); huge folders are truncated and reported as such. Point the tool at
  the project folder, not at a drive root.

---

## Command line

### `python -m ece_autocompiler` is not recognised

Install the package in editable mode once (works from any folder):

```powershell
python -m pip install -e .
```

or, from the repository root only:

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m ece_autocompiler --version
```

### Exit codes

`0` success Â· `1` the operation failed (the message says why) Â· `2` the command
line itself was wrong. Scripts can rely on these.

---

## Distributable packages

### "The Windows installer (Inno Setup) format is not available on this PC"

The installer formats need their compiler on the machine. The message names it
and prints the install command:

```powershell
winget install JRSoftware.InnoSetup   # Inno Setup  -> format "inno"
winget install NSIS.NSIS              # NSIS        -> format "nsis"
```

`zip`, `tar.gz` and `folder` need nothing and always work.

### "This format needs dpkg-deb, which ships with Debian/Ubuntu"

`deb` and `appimage` can only be produced on Linux. On Windows the generator
still writes the layout it would use (`<name>-<version>.deb-root` and
`<name>-<version>-AppDir` under the work folder) so the package can be built
on a Linux machine.

### "The package was created but did not pass verification"

The file was written but reading it back did not match the manifest. Nothing
is reported as successful in that case. Delete the output file and run the
command again; if it repeats, the output folder is probably being modified by
another program (antivirus, sync client).

### "The ZIP archive already exists in ..."

The generator never overwrites a package by accident. Pass `--overwrite` to
replace it.

### "No license file was found in the project"

This is a warning, not an error: confirm that redistribution is allowed before
shipping the package. A copyleft license (GPL/LGPL/AGPL/MPL) additionally
asks for `--allow-copyleft`.

---

## Automatic repair

### "No automatic repair is safe for this error"

The failure needs a decision (wrong types, a missing library, a design
problem). The analysis below the message says what to fix; the tool does not
guess.

### "The change did not fix the error, so it was reverted"

The repair was applied, the project was rebuilt, the same error was still
there, so the original file was restored from
`%LOCALAPPDATA%\ECE-Universal-AutoCompiler\work\repair-backups\`. Nothing was
lost.

### The repair changed a file I did not expect

Every attempt is recorded in
`%LOCALAPPDATA%\ECE-Universal-AutoCompiler\work\repair-journal.jsonl` with the
original error, the diff, the files modified and the verification result. The
backup folder holds the original file, so it can be restored by hand.

---

## Optimization

### "This project cannot be optimized with that profile"

Interpreter projects (Python, Node.js, PHP) and build systems without
compiler flags (Maven, Gradle) have nothing to optimize. Use `none` (the
default) or bundle the project into a native executable first.

### "Runtime: not measured (no benchmark command was run)"

No runtime claim can be made without a benchmark. Pass one:

```powershell
python -m ece_autocompiler optimize path\to\project --profile release --benchmark "python bench.py"
```

### "The optimized build is slower to compile"

That is the usual price of link-time optimization and of the `performance`
profile. The report shows the measured percentage; nothing is estimated.

---

## Tests

- `python run_tests.py` runs unit, integration and GUI smoke tests.
- Node integration tests **skip** when Node.js is not installed; the GUI smoke
  test skips when there is no display. A skip is not a pass.
- `powershell -File scripts\lint.ps1` runs the stdlib-only static checks.

---

## Reporting a problem

Include, if you can:

1. the exact command and the **full** output (the *What happened / Why / What to
   do* block);
2. `python -m ece_autocompiler --version` (or
  `dist\ECE-AutoCompiler-cli.exe --version`);
3. the build log from
   `%LOCALAPPDATA%\ECE-Universal-AutoCompiler\work\logs\` â€” the newest file;
4. `%TEMP%\ece-autocompiler-crash.log` when the window is involved.

Please report security issues privately, as described in
[SECURITY.md](../SECURITY.md).
