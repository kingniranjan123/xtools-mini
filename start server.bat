@echo off
title Nikethan Reels Toolkit Server
color 0b

echo ========================================================
echo Nikethan Reels Toolkit
echo ========================================================
echo.

:: Kill processes running on port 5050 (as requested)
echo [*] Checking for processes on port 5050 to kill...
FOR /F "tokens=5" %%a IN ('netstat -ano ^| findstr :5050') DO (
    if %%a neq 0 (
        echo Killing process %%a on port 5050...
        taskkill /F /PID %%a >nul 2>&1
    )
)

:: Also kill processes on port 5055 (the port app.py actually uses)
echo [*] Checking for processes on port 5055 to kill...
FOR /F "tokens=5" %%a IN ('netstat -ano ^| findstr :5056') DO (
    if %%a neq 0 (
        echo Killing process %%a on port 5056...
        taskkill /F /PID %%a >nul 2>&1
    )
)

echo.
echo [*] Installing/verifying requirements...
pip install -r requirements.txt >nul 2>&1

echo [*] Starting Server...
:: Wait 2 seconds to ensure the server is up before opening browser
start /b "" cmd /c "ping localhost -n 3 >nul && start http://localhost:5056"

echo [*] App is running. Press CTRL+C to stop.
echo.
python app.py

pause
