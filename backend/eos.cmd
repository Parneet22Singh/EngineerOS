@echo off
rem EngineerOS CLI launcher — sets the Playwright browser path (browsers live on D:)
rem and runs the CLI with the project venv, from any working directory.
setlocal
set "PLAYWRIGHT_BROWSERS_PATH=D:\EngineerOS\.ms-playwright"
set "TEMP=D:\EngineerOS\.tmp"
set "TMP=D:\EngineerOS\.tmp"
set "PYTHONPATH=D:\EngineerOS\backend"
"D:\EngineerOS\backend\.venv\Scripts\python.exe" -m app.cli %*
endlocal
