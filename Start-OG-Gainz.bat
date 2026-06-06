@echo off
title OG Gainz Command Center
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo.
  echo  Python is not installed or not on PATH.
  echo  Install Python 3 from https://python.org  -- tick "Add python.exe to PATH".
  echo.
  pause
  exit /b
)

echo Checking dependencies (first run may take a minute)...
python -m pip install -q -r requirements.txt

echo.
echo  =====================================================
echo   Starting OG Gainz Command Center...
echo   Your browser will open automatically.
echo   KEEP THIS WINDOW OPEN while you use the app.
echo   Close this window to stop the app.
echo  =====================================================
echo.
python app.py
echo.
echo App stopped. You can close this window.
pause
