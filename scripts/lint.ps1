# Stdlib-only lint for ECE Universal AutoCompiler (Windows).
#   python -m compileall  -> syntax errors anywhere (src + tests)
#   tools/lint_stdlib.py  -> unused imports, print(), shell=True, eval/exec
# Usage:  powershell -File scripts\lint.ps1
# Exit code: 0 clean, non-zero on the first failing stage.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot

python -m compileall -q "$root\src" "$root\tests" "$root\run_tests.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host "compileall FAILED" -ForegroundColor Red
    exit $LASTEXITCODE
}

python "$root\tools\lint_stdlib.py" "$root\src"
if ($LASTEXITCODE -ne 0) {
    Write-Host "lint FAILED" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "lint OK" -ForegroundColor Green
exit 0
