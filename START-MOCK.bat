@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" server.py mock --open-browser
) else (
    python server.py mock --open-browser
)
if errorlevel 1 pause
