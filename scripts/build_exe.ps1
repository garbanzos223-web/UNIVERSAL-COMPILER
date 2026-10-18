# Build a standalone Windows executable of the desktop app.
#
# Usage (from the repository root):
#   powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
#
# Requires: pyinstaller  (pip install pyinstaller). Produces dist\ECE-AutoCompiler.exe

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

python -m PyInstaller `
    --noconfirm --clean `
    --onefile --windowed `
    --name "ECE-AutoCompiler" `
    --paths src `
    --distpath dist `
    --workpath build\pyi `
    --specpath build `
    launcher_gui.py

if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$exe = Join-Path "dist" "ECE-AutoCompiler.exe"
if (Test-Path $exe) {
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 2)
    Write-Host "OK -> $exe  ($mb MB)"
} else {
    throw "Expected executable not found: $exe"
}
