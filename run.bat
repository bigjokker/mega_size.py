@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Creating local virtual environment...
  python -m venv .venv
  if errorlevel 1 (
    echo Python was not found. Install Python 3.11+ and try again.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Failed to install packages.
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" app_gui.py
