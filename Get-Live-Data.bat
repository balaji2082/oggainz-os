@echo off
title OG Gainz - Get Live Data
cd /d "%~dp0"

echo.
echo  =====================================================
echo    OG GAINZ - DOWNLOAD LIVE DATA
echo    Copies the real data from the internet server
echo    down to this laptop. Read-only and safe.
echo  =====================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo  Python is not installed or not on PATH.
  echo  Install Python 3 from https://python.org
  echo  and tick "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b
)

if exist ".pa_token" goto pull

echo  FIRST TIME SETUP - you only do this once.
echo.
echo  1. Open this page in your browser:
echo.
echo       https://www.pythonanywhere.com/account/#api_token
echo.
echo  2. Log in as:  oggainz
echo  3. Click "Create a new API token"
echo     (if a token is already shown, just copy that one)
echo  4. Copy the long code it gives you
echo.
echo  Then come back here.
echo.
set /p TOKEN="  Paste the token and press Enter: "

if "%TOKEN%"=="" (
  echo.
  echo  Nothing was entered. Run this file again when you have the token.
  echo.
  pause
  exit /b
)

>".pa_token" echo %TOKEN%
echo.
echo  Token saved. You will not be asked again.
echo.

:pull
echo  Downloading live data...
echo.
python pull_live_db.py
echo.
echo  =====================================================
echo    Done. You can close this window.
echo  =====================================================
echo.
pause
