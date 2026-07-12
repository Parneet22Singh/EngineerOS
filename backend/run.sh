#!/usr/bin/env bash
# EngineerOS backend launcher (Git Bash / WSL)
# Sets the Playwright browser path (browsers live on D:) and starts the API server.
set -euo pipefail
cd "$(dirname "$0")"

export PLAYWRIGHT_BROWSERS_PATH='D:\EngineerOS\.ms-playwright'
export TMPDIR=/d/EngineerOS/.tmp TEMP='D:\EngineerOS\.tmp' TMP='D:\EngineerOS\.tmp'

exec ./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
