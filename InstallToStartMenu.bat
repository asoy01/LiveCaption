@echo off
cd /d "%~dp0"
title LiveCaption - Start menu

rem Puts LiveCaption in the Windows Start menu, for the current user only.
rem After this, press the Windows key, type "livecaption", and press Enter.
rem
rem Run it again after you move or rename this folder.
rem To take it out again, pass -Remove:
rem   InstallToStartMenu.bat -Remove

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_start_menu.ps1" %*

if errorlevel 1 (
  echo.
  echo   Failed. See the messages above.
  echo.
  pause
  exit /b 1
)

pause
exit /b 0
