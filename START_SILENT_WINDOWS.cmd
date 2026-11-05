@echo off
cd /d "%~dp0"
where pyw >nul 2>nul
if not errorlevel 1 (start "" pyw -3 app.py) else (start "" pythonw app.py)
