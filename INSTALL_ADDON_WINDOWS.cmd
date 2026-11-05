@echo off
setlocal
set "ADDON=%~dp0wow_addon\ForeverTraderBridge"
set "TARGET=%ProgramFiles(x86)%\World of Warcraft\_forever_\Interface\AddOns"
if not exist "%ADDON%\ForeverTraderBridge.toc" (
  echo Error: Bridge files are missing from this download.
  pause
  exit /b 1
)
if not exist "%TARGET%" (
  echo WoW Forever's default AddOns folder was not found.
  echo Paste the FULL path to your _forever_\Interface\AddOns folder:
  set /p "TARGET=> "
)
if not exist "%TARGET%" mkdir "%TARGET%"
if not exist "%TARGET%" (
  echo Could not create this AddOns directory.
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
echo Restart WoW, make a detailed search using auxForever, then type /ftb and /reload.
pause
