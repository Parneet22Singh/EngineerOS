# EngineerOS backend launcher (Windows / PowerShell)
# Sets the Playwright browser path (browsers live on D:) and starts the API server.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$env:PLAYWRIGHT_BROWSERS_PATH = "D:\EngineerOS\.ms-playwright"
$env:TMPDIR = "D:\EngineerOS\.tmp"
$env:TEMP = "D:\EngineerOS\.tmp"
$env:TMP = "D:\EngineerOS\.tmp"

& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn app.main:app --reload --port 8000
