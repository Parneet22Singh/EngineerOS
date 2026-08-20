# EngineerOS CLI launcher (PowerShell) — usable from any directory: .\eos scan <url>
$ProjectRoot = $PSScriptRoot
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $ProjectRoot ".ms-playwright"
$env:TEMP = Join-Path $ProjectRoot ".tmp"
$env:TMP = Join-Path $ProjectRoot ".tmp"
$env:PYTHONPATH = Join-Path $ProjectRoot "backend"
& (Join-Path $ProjectRoot "backend\.venv\Scripts\python.exe") -m app.cli @args
exit $LASTEXITCODE
