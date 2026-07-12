# EngineerOS CLI launcher (PowerShell) — usable from any directory: .\eos scan <url>
$env:PLAYWRIGHT_BROWSERS_PATH = "D:\EngineerOS\.ms-playwright"
$env:TEMP = "D:\EngineerOS\.tmp"
$env:TMP = "D:\EngineerOS\.tmp"
$env:PYTHONPATH = "D:\EngineerOS\backend"
& "D:\EngineerOS\backend\.venv\Scripts\python.exe" -m app.cli @args
exit $LASTEXITCODE
