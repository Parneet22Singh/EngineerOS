# EngineerOS backend launcher (Windows / PowerShell)
# Sets the Playwright browser path and starts the API server.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $ProjectRoot ".ms-playwright"
$env:TMPDIR = Join-Path $ProjectRoot ".tmp"
$env:TEMP = Join-Path $ProjectRoot ".tmp"
$env:TMP = Join-Path $ProjectRoot ".tmp"
& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --port 8000
