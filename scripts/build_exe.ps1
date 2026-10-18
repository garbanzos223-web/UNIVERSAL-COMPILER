# Build a standalone Windows executable of the desktop app.
#
# Usage (from the repository root):
#   powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1              # onefile (default)
#   powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -Mode onedir # folder mode
#
# Requires: pyinstaller  (pip install pyinstaller).
#   -Mode onefile -> dist\ECE-AutoCompiler.exe        (single file; slower start)
#   -Mode onedir  -> dist\ECE-AutoCompiler\ECE-AutoCompiler.exe  (folder; faster start)

param(
    [ValidateSet("onefile", "onedir")]
    [string]$Mode = "onefile"
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "python not found on PATH."
}

python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller not installed; installing..."
    python -m pip install --disable-pip-version-check pyinstaller
}

$modeFlag = "--onefile"
if ($Mode -eq "onedir") { $modeFlag = "--onedir" }

python -m PyInstaller `
    --noconfirm --clean `
    $modeFlag --windowed `
    --name "ECE-AutoCompiler" `
    --paths src `
    --distpath dist `
    --workpath build\pyi `
    --specpath build `
    launcher_gui.py

if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

if ($Mode -eq "onedir") {
    $exe = Join-Path "dist" (Join-Path "ECE-AutoCompiler" "ECE-AutoCompiler.exe")
} else {
    $exe = Join-Path "dist" "ECE-AutoCompiler.exe"
}

if (Test-Path $exe) {
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 2)
    Write-Host "Mode  : $Mode"
    Write-Host "OK -> $exe  ($mb MB)"
} else {
    throw "Expected executable not found: $exe"
}
