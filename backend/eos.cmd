@echo off
rem EngineerOS CLI launcher — sets the Playwright browser path and runs the CLI
rem with the project venv, from any working directory.
setlocal
set "PROJECT_ROOT=%~dp0"
set "PLAYWRIGHT_BROWSERS_PATH=%PROJECT_ROOT%.ms-playwright"
set "TEMP=%PROJECT_ROOT%.tmp"
set "TMP=%PROJECT_ROOT%.tmp"
set "PYTHONPATH=%PROJECT_ROOT%backend"
"%PROJECT_ROOT%backend\.venv\Scripts\python.exe" -m app.cli %*
endlocal
