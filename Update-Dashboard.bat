@echo off
title OG Gainz - Update Finance Dashboard
cd /d "%~dp0"

echo.
echo  ===================================================
echo    OG GAINZ - FINANCE DASHBOARD
echo  ===================================================
echo.

where python >nul 2>&1
if errorlevel 1 goto nopython

echo  Step 1 of 2 - getting the latest revenue...
echo.
if not exist ".pa_token" goto skippull
python pull_live_db.py
goto build

:skippull
echo  [skipped] Run Get-Live-Data.bat once to set this up.

:build
echo.
echo  Step 2 of 2 - building the dashboard...
echo.
python finance\build_finance.py
if errorlevel 1 goto failed

echo.
echo  Done. Opening the dashboard in your browser...
start "" "finance\dashboard.html"
echo.
echo  You can close this window.
echo.
pause
exit /b

:nopython
echo  Python is not installed, or not on PATH.
echo  Install Python 3 from https://python.org
echo  and tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b

:failed
echo.
echo  Something went wrong. Copy the message above and send it to Claude.
echo.
pause
exit /b
