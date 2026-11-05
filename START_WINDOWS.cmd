@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 app.py
) else (
  where python >nul 2>nul
  if not errorlevel 1 (
    python app.py
  ) else (
    echo Python 3 was not found. Install Python from python.org or use the compiled Windows EXE from GitHub Releases.
    pause
    exit /b 1
  )
)
