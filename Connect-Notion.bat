@echo off
title OG Gainz - Connect Notion
cd /d "%~dp0"

echo.
echo  ===================================================
echo    CONNECT NOTION - one time setup
echo    Do this when you start logging expenses.
echo  ===================================================
echo.

if exist ".notion_token" goto already

echo  BEFORE you paste anything, do these 2 things in Notion:
echo.
echo  1. Open  https://www.notion.so/my-integrations
echo     Click "New integration", name it  OG Gainz Finance
echo     Click Save, then copy the Internal Integration Secret.
echo.
echo  2. In Notion, open your Business Expenses database.
echo     Click the ... menu at the TOP RIGHT of the page.
echo     Choose Connections, then pick  OG Gainz Finance
echo     Now do the SAME for Personal Expenses.
echo.
echo  That second step is the one people forget.
echo  Without it Notion will refuse to share the data.
echo.
set /p NTOKEN="  Paste the secret here and press Enter: "
if not defined NTOKEN goto empty
>".notion_token" echo %NTOKEN%
echo.
echo  Saved. Testing the connection now...
echo.
python finance\build_finance.py
echo.
echo  If you saw a number of business and personal rows above,
echo  it worked. From now on just use Update-Dashboard.bat.
echo.
pause
exit /b

:already
echo  Notion is already connected.
echo  Delete the file  .notion_token  if you need to redo it.
echo.
pause
exit /b

:empty
echo.
echo  Nothing was entered. Run this file again when you have the secret.
echo.
pause
exit /b
