@echo off
echo ===================================================
echo               GITHUB BLIND REFRESH
echo ===================================================
echo WARNING: This will discard all local changes and sync 
echo exactly with the GitHub base version (origin/main).
echo This prevents merge conflicts by ignoring local edits.
echo.
echo Press Ctrl+C to cancel or any key to continue...
pause

echo.
echo [1/4] Fetching latest branches and commits from GitHub...
git fetch origin

echo.
echo [2/4] Hard resetting local repository to origin/main...
git reset --hard origin/main

echo.
echo [3/4] Cleaning up any untracked files/folders...
git clean -fd

echo.
echo [4/4] Refreshing Python dependencies...
pip install -r requirements.txt

echo.
echo ===================================================
echo REFRESH COMPLETE! Your system matches GitHub exactly.
echo ===================================================
pause
