# Combined `.exe` launcher (embed and run several programs)

The combined launcher embeds one or more Windows executables inside a new
`.exe` and starts all of them **at the same time** when it runs.

This is available in the desktop application (**Tools → Bundle executables into
one .exe...**) and in the CLI (`bundle-exe`).

## What it does

1. You select two or more `.exe` files (or one main and any number of
   companions).
2. The tool builds a launcher with PyInstaller and embeds every selected file.
3. On start-up the launcher:
   - starts every companion concurrently,
   - starts the main program if one was selected,
   - stays alive while any embedded program is still running (so onefile
     temporary resources are not deleted underneath a running child).
4. The build verifies the embedded files with a **probe mode** (`ECE_COMPANION_PROBE=1`):
   it checks that every embedded file exists inside the bundle and exits without
   starting your programs. A bundle that fails the probe is not reported as
   usable.

The launcher does not kill processes it did not create; it is a parent, not a
sandbox or a supervisor.

## CLI

```powershell
# Embed app.exe as main and helper.exe + server.exe as companions
ece-autocompiler bundle-exe C:\work\combined.exe `
    --main C:\work\app.exe `
    --with C:\work\helper.exe `
    --with C:\work\server.exe

# Show the plan, build nothing
ece-autocompiler bundle-exe C:\work\combined.exe `
    --main C:\work\app.exe --with C:\work\helper.exe --plan

# Folder layout (faster start) and console window
ece-autocompiler bundle-exe C:\work\combined.exe `
    --main C:\work\app.exe --with C:\work\helper.exe `
    --onedir --console

# Machine-readable result
ece-autocompiler bundle-exe C:\work\combined.exe `
    --main C:\work\app.exe --with C:\work\helper.exe --json
```

| Option | Meaning |
| --- | --- |
| `output` | Output `.exe`. |
| `--main PATH` | Embedded and started after the companions; the launcher returns its exit code. |
| `--with PATH` | Additional `.exe` embedded and started concurrently (repeatable). |
| `--console` | Keep a console window (useful for diagnosing start-up). |
| `--onedir` | Build a folder with the launcher and its files instead of one file. |
| `--plan` | Print what would be embedded; run nothing. |
| `--json` | JSON result with `ok`, output path, size, SHA-256, errors and log. |

Without `--main`, all files are companions; the launcher waits for all of them
and exits with 0 only if every one returned 0.

## GUI

1. Open **Tools → Bundle executables into one .exe...**.
2. **Add .exe...** one or more times.
3. Select one entry and **Set as main** (optional). **Clear main** makes every
   file a companion.
4. Choose the output path, single-file or folder layout, and console option.
5. **Show plan** displays what will be embedded without building.
6. **Build combined .exe** runs PyInstaller on a worker thread and shows the
   real result, log, size and SHA-256.

## Notes and limits

- Only Windows `.exe` inputs are accepted.
- The launcher is built with the PyInstaller installed in the current Python
  environment (`python -m pip install pyinstaller`). If it is missing, the
  command fails with that exact instruction.
- The embedded programs run with the same user privileges as the launcher.
  Only embed programs you trust.
- Antivirus tools sometimes flag self-extracting onefile bundles; `--onedir`
  avoids self-extraction and starts faster.
- Original signatures of the embedded programs are preserved as file contents,
  but the outer launcher is a new, unsigned executable.
- This is not a single-process merge: each program remains a separate process
  and can communicate with the others as usual.
- Arguments are not forwarded to the embedded programs (they start with no
  arguments); use small wrapper executables if you need fixed arguments.
