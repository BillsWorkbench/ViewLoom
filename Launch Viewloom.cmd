@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Set up the project environment using the README instructions first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m viewloom %*
