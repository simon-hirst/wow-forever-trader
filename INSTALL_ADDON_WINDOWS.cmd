@echo off
setlocal
set "ADDON=%~dp0wow_addon\ForeverTraderBridge"
if not exist "%ADDON%\ForeverTraderBridge.toc" (
  echo Error: Bridge files are missing from this download.
  pause
  exit /b 1
)
rem Battle.net records where it installed WoW, which can be on any drive.
set "WOW="
for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\World of Warcraft" /v InstallLocation 2^>nul ^| find "InstallLocation"') do set "WOW=%%B"
if not defined WOW set "WOW=%ProgramFiles(x86)%\World of Warcraft"
set "TARGET=%WOW%\_forever_\Interface\AddOns"
if not exist "%TARGET%" (
  echo WoW Forever's AddOns folder was not found at:
  echo %TARGET%
  echo Paste the FULL path to your _forever_\Interface\AddOns folder:
  set /p "TARGET=> "
)
if not exist "%TARGET%" (
  echo That folder does not exist.
  pause
  exit /b 1
)
xcopy "%ADDON%" "%TARGET%\ForeverTraderBridge\" /E /I /Y >nul
if errorlevel 1 (
  echo Copy failed. Check the permissions on your WoW folder.
  pause
  exit /b 1
)
echo.
echo Installed ForeverTraderBridge into:
echo %TARGET%\ForeverTraderBridge
echo.
echo Start the Forever Trader app once so it can add the plan files, then restart WoW.
pause
