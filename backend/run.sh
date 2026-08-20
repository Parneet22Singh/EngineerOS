#!/usr/bin/env bash
# EngineerOS backend launcher (Git Bash / WSL)
# Sets the Playwright browser path and starts the API server.
set -euo pipefail
cd "$(dirname "$0")"

# POSIX-style root (for bash/TMPDIR)
PROJECT_ROOT_POSIX="$(cd .. && pwd)"
# Windows-style root (for the Windows python.exe subprocess). `pwd -W` is
# Git Bash/MSYS-specific; falls back to the POSIX path on real WSL/Linux.
PROJECT_ROOT_WIN="$(cd .. && pwd -W 2>/dev/null || pwd)"

export PLAYWRIGHT_BROWSERS_PATH="${PROJECT_ROOT_WIN}\\.ms-playwright"
export TMPDIR="${PROJECT_ROOT_POSIX}/.tmp"
export TEMP="${PROJECT_ROOT_WIN}\\.tmp"
export TMP="${PROJECT_ROOT_WIN}\\.tmp"

exec ./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
