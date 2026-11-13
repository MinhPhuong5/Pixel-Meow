@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set "PML_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PML_PYTHON=.venv\Scripts\python.exe"
"%PML_PYTHON%" -c "import sys,fastapi,uvicorn,dotenv,TikTokLive,PIL; sys.exit(sys.version_info < (3,12))"
if errorlevel 1 (
    echo Pixel Meow Legion needs Python 3.12 and the packages in requirements.txt.
    pause
    exit /b 1
)
"%PML_PYTHON%" server.py live --open-browser
if errorlevel 1 pause
