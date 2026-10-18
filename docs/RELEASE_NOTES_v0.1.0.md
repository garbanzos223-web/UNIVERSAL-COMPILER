# ECE Universal AutoCompiler 0.1.0 (alpha) — Windows 10/11 x64

Draft for a GitHub release. Paste it into **Releases → Draft a new release**,
tag `v0.1.0`, and replace the two `sha256` placeholders with the values in
`dist\release.json` produced by your own build (they are per-build and must not
be guessed).

---

Detects what a project is, runs its **real** build with the toolchain installed
on your PC, and either packages the result into a versioned, secure `.ece`
container or exports it to a standalone Windows `.exe` — from a desktop window
or the command line. Standard library only: no third-party Python packages.

## What this release does, for real

- **Detection** for Python, Node/TypeScript, .NET, Rust, Go, Java (Maven and
  Gradle/Kotlin), C/C++ (CMake, Make, Meson), PHP, Unity and Unreal.
- **`.ece` builds** that run for real: Python and Node.js.
- **`.exe` export**: Python (PyInstaller), and — when the toolchain is installed
  — C#/.NET, Rust, Go and C/C++. Anything else is *detected and reported* with
  an install hint; nothing is faked.
- **Honest guidance everywhere.** Failures say what happened, why, and what to
  do next; `analyze <folder>` answers "can this project become an `.exe` on this
  PC?" before you try.
- **Safe containers**: per-file SHA-256, a package content hash, path-traversal,
  symlink and depth checks, and refusal to extract names that collide on
  Windows (case-insensitive) instead of silently overwriting files.
- **Verified releases**: a build only counts when it runs.
  `tools\release_check.py` runs the console build (`--version`), starts the
  desktop build to confirm it stays alive, and writes size + SHA256 for both
  into `dist\release.json`.

## Downloads

| File | What it is | SHA256 |
| --- | --- | --- |
| `ECE-AutoCompiler.exe` | Desktop window. No console, no Python needed. | `<sha256 from dist\release.json>` |
| `ECE-AutoCompiler-cli.exe` | The same product as a command line: `ECE-AutoCompiler-cli.exe exe C:\path\to\project`. | `<sha256 from dist\release.json>` |

Build them yourself from source (Windows 10/11 x64, needs PyInstaller):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

## Verified on

- `ECE-AutoCompiler-cli.exe --version` → `ECE Universal AutoCompiler 0.1.0`
- `ECE-AutoCompiler.exe` starts and stays alive (checked by
  `tools\release_check.py`, which fails the build otherwise)
- Test suite: `python run_tests.py` (unit + integration + GUI smoke)

## First run

Because the executables are not code-signed, Windows SmartScreen may show
"Windows protected your PC" — choose **More info → Run anyway**, and compare the
file against the SHA256 above. Antivirus heuristics also flag freshly built
self-extracting executables; the folder layout (`-Layout onedir`) and a build
folder exclusion are the usual fixes. Details:
[docs/TROUBLESHOOTING.md](https://github.com/garbanzos223-web/UNIVERSAL-COMPILER/blob/main/docs/TROUBLESHOOTING.md).

## Known limits (alpha)

- **Windows 10/11 x64 only.**
- `.ece` is a documented container, **not** a self-executing format. Hashes
  detect corruption; they are not a publisher signature (an optional external
  signature can be checked with `--verify-signature`).
- Unity and Unreal projects are detected but must be built by their own editor.
- The `.ece` format is versioned; 0.1.0 writes and reads format 1.0.
