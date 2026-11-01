# ECE Universal AutoCompiler 1.4.0 — combined executables and the complete binary loop

Release date: **2026-11-01**
Container format: **v1 (unchanged)**

## Highlights

1. **One `.exe` that starts several programs at once.** The new combined
   launcher embeds two or more Windows executables and starts all of them
   concurrently. Mark one as the main program or let every file run as a
   companion.
2. **GUI: Tools → Bundle executables into one .exe...** Add/Remove files, set
   the main program, choose onefile/onedir and console, preview the plan, build
   and see the real size, SHA-256 and log.
3. **CLI:** `ece-autocompiler bundle-exe out.exe --main app.exe --with helper.exe`
   with `--plan`, `--onedir`, `--console` and `--json`.
4. **Complete decompile → edit → recompile loop.** Add files by button or
   drag-and-drop, analyse, decompile, edit the recovered source in the
   integrated editor and recompile from the reconstructed project. Generated
   outlines are refused with a clear message instead of producing a misleading
   binary.
5. **Python 3.10 and CI fixes.** Cargo manifests are read with a bundled
   TOML-subset reader on 3.10, fusion failed-build reporting no longer assumes
   an adapter message, and the test matrix is host-independent.

## How the combined launcher works

- Every selected file is embedded with PyInstaller.
- On start-up the launcher starts the companions first, then the main program;
  all run at the same time.
- It stays alive while any embedded program is running, so onefile resources are
  not deleted underneath a running child.
- The build verifies the embedding in **probe mode**: the launcher checks that
  every embedded file exists and exits without starting your programs. A bundle
  that fails the probe is not reported as usable.
- Building requires Python + PyInstaller on the machine (the same requirement as
  the existing Python `.exe` export). In a standalone build, the application
  looks for a Python interpreter on PATH and reports the exact install step when
  none exists.

See [COMPANION.md](../docs/COMPANION.md).

## Verified on the build machine

Environment: Windows 10, CPython 3.12.0, PyInstaller 6.22.3, .NET SDK 8.0.426,
Capstone 5.0.9, pefile 2024.8.26.

| Check | Result |
| --- | --- |
| Full test suite (`python run_tests.py`) | **524 tests, 0 failures, 2 environment-dependent skips** |
| Lint (`scripts\lint.ps1`) | clean (125 source files) |
| Desktop executable starts and stays alive | passed (5 s probe) |
| Console executable `--version` | `ECE Universal AutoCompiler 1.4.0` |
| Console build/validate/package probe | `demo.ece` built (662 bytes), deep-validated, `demo-1.0.0.zip` verified |
| Combined launcher: embed probe | passed for a launcher with two embedded ECE executables |
| Combined launcher: concurrent execution | two embedded programs wrote their flags and finished in under 4 s (they sleep 2 s each) |
| Frozen CLI `decompile` | recovered project written for a real `.pyc` |
| Frozen CLI `recompile` | `.pyc` artifact verified and log written |
| Frozen CLI `bundle-exe` | combined executable built and both embedded programs ran |

### Executable hashes

`dist\release.json` is the authoritative record produced by
`tools\release_check.py`.

| Artifact | Size | SHA-256 |
| --- | --- | --- |
| `ECE-AutoCompiler.exe` (desktop) | 13,826,175 bytes | `cf8eb615c8ba9d67bba4a1b0e3a448df507629ecc2d83d94b14852231d15bdcd` |
| `ECE-AutoCompiler-cli.exe` (console) | 13,876,222 bytes | `f39855e91e2edf9762d06bfb53b9e9d76007e93e7cddf8e32fe9c9b93d26e7e3` |

The executables are build artifacts and are **not** committed. Download them
from the GitHub release or rebuild with `scripts\build_exe.ps1`.

## Limitations

- Only Windows `.exe` files can be embedded.
- Arguments are not forwarded to embedded programs; they start with no
  arguments.
- The outer launcher is a new, unsigned binary; embedded programs keep their
  own bytes but their signatures do not sign the launcher.
- Native PE/ELF binaries still cannot be restored to original source; the
  decompiler reports real analysis and refuses generated outlines at rebuild.
- Recompiled binaries are new builds; original signatures and layout are not
  preserved.

## Documentation

- [Combined .exe launcher](../docs/COMPANION.md)
- [Decompiler](../docs/DECOMPILER.md)
- [Recompiler](../docs/RECOMPILER.md)
- [Supported formats](../docs/SUPPORTED_FORMATS.md)
- [Installation](../docs/INSTALL.md)
- [Testing](../docs/TESTING.md)

## Security

Embedded programs run with the launcher's privileges; only embed trusted files.
The probe never starts them. Build commands use argv lists without a shell.
See [SECURITY.md](../SECURITY.md).
