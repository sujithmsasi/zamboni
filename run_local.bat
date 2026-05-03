@echo off
REM Zamboni -- Local Mode Launcher (Windows)
REM Runs Streamlit with correct Python path and local SQLite mode
REM Usage: double-click or run from project root

cd /d "%~dp0"

REM Check if local DB exists, seed if not
if not exist zamboni_local.db (
    echo Seeding local database...
    python scripts\seed_local_db.py
)

REM Set environment for local mode
set PYTHONPATH=%~dp0
set ZAMBONI_LOCAL_MODE=true
set ZAMBONI_LOCAL_DB=%~dp0zamboni_local.db
set ZAMBONI_TEST_MODE=false
set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

echo.
echo Starting Zamboni in LOCAL MODE...
echo No AWS connectivity required.
echo.
echo Open: http://localhost:8501
echo.

python -m streamlit run app\Home.py --server.port 8501 --server.headless false --browser.gatherUsageStats false

pause
