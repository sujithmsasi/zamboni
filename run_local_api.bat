@echo off
REM Zamboni -- Local API Full-Stack Launcher (Windows)
REM Runs FastAPI + the built React UI in mode=local (pure SQLite, no AWS).
REM Usage: double-click or run from project root

cd /d "%~dp0"

REM Check if local DB exists, seed if not
if not exist zamboni_local.db (
    echo Seeding local database...
    python scripts\seed_local_db.py
)

REM Build the React UI if it hasn't been built yet
if not exist ui\dist\index.html (
    echo ui\dist not found -- building React UI...
    pushd ui
    call npm ci
    call npm run build
    popd
)

REM Set environment for local mode
set PYTHONPATH=%~dp0
set ZAMBONI_MODE=local
set ZAMBONI_LOCAL_MODE=true
set ZAMBONI_LOCAL_DB=%~dp0zamboni_local.db
set ZAMBONI_TEST_MODE=false

echo.
echo Starting Zamboni API + UI in LOCAL MODE...
echo No AWS connectivity required.
echo.
echo Open: http://localhost:8000
echo.

start "" http://localhost:8000
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000

pause
