@echo off
title OG Gainz - Money (editable)
cd /d "%~dp0"

echo.
echo  ===================================================
echo    OG GAINZ MONEY - editable dashboard
echo  ===================================================
echo.
echo  Refreshing from Notion, then opening in your browser.
echo  Edits you make save straight back to Notion.
echo.
echo  Keep THIS WINDOW OPEN while you use it.
echo  Close it (or press Ctrl+C) when you're done.
echo.

python finance\serve_dashboard.py
if errorlevel 1 (
  echo.
  echo  Something went wrong. Copy the message above and send it to Claude.
  echo.
  pause
)
