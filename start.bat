@echo off
title Nikethan Reels Toolkit
color 0A

echo.
echo  *** Nikethan Reels Toolkit ***
echo  Starting on http://localhost:5056
echo.

:: Kill anything on port 5055
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :5056') do (
    taskkill /F /PID %%a >nul 2>&1
)

cd /d "%~dp0"

:: Install deps if needed
pip install -r requirements.txt --quiet

:: Run app
python app.py

pause
