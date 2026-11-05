@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Please install Python 3 for Windows from https://www.python.org/downloads/windows/
  echo then reopen this file.
  pause
  exit /b 1
)
py -3 -m pip install --upgrade pyinstaller
if errorlevel 1 (
  echo Could not install PyInstaller.
  pause
  exit /b 1
)
py -3 -m PyInstaller --clean --noconfirm --onefile --windowed --name ForeverTrader --paths . --add-data "wow_addon;wow_addon" app.py
if errorlevel 1 (
  echo Packaging failed. See the error above.
  pause
  exit /b 1
)
echo.
echo Created dist\ForeverTrader.exe
explorer dist
pause
