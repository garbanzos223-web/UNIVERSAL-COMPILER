# Build the standalone Windows executables of the app and verify them.
#
# Usage (from the repository root):
#   powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1                       # both, onefile
#   powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -Layout onedir       # faster start
#   powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -Target gui          # desktop only
#   powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -Target cli          # console only
#
# Produces (dist is a build artifact, not committed):
#   -Target gui -> dist\ECE-AutoCompiler.exe            single file, no console (desktop window)
#                  or dist\ECE-AutoCompiler\ECE-AutoCompiler.exe with -Layout onedir
#   -Target cli -> dist\ECE-AutoCompiler-cli.exe        console build: the same CLI as
#                  `python -m ece_autocompiler`, with real exit codes and no window
#
# Every build is verified afterwards by tools\release_check.py: the console
# build must answer --version, the desktop build must start and stay alive.
# Requires: pyinstaller (pip install pyinstaller).

param(
    [ValidateSet("onefile", "onedir")]
    [string]$Layout = "onefile",

    [ValidateSet("both", "gui", "cli")]
    [string]$Target = "both"
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "python not found on PATH."
}

python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller not installed; installing it with pip..."
    python -m pip install --disable-pip-version-check pyinstaller
}

$modeFlag = "--onefile"
if ($Layout -eq "onedir") { $modeFlag = "--onedir" }

function New-Build {
    param(
        [string]$Entry,
        [string]$Name,
        [switch]$Windowed
    )
    $consoleFlag = "--console"
    if ($Windowed) { $consoleFlag = "--windowed" }

    Write-Host "Building $Name from $Entry ($Layout, $consoleFlag)..."
    python -m PyInstaller `
        --noconfirm --clean `
        $modeFlag $consoleFlag `
        --name $Name `
        --paths src `
        --distpath dist `
        --workpath (Join-Path "build\pyi" $Name) `
        --specpath build `
        $Entry
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $Name (exit code $LASTEXITCODE)" }
}

if ($Target -eq "gui" -or $Target -eq "both") {
    New-Build -Entry "launcher_gui.py" -Name "ECE-AutoCompiler" -Windowed
}
if ($Target -eq "cli" -or $Target -eq "both") {
    New-Build -Entry "launcher_cli.py" -Name "ECE-AutoCompiler-cli"
}

# Verify what was just built instead of trusting that it exists.
python tools\release_check.py --dist dist
if ($LASTEXITCODE -ne 0) {
    throw "release_check.py reported a broken build (exit code $LASTEXITCODE); see the output above"
}

Write-Host "Build + verification OK ($Layout, target: $Target)."
