@echo off
cd /d "%~dp0"
title LiveCaption

where pixi >nul 2>nul
if errorlevel 1 (
  echo.
  echo   pixi not found on PATH.
  echo   Expected: %USERPROFILE%\.pixi\bin\pixi.exe
  echo.
  pause
  exit /b 1
)

rem Opens the CONTROL page (http://localhost:8081) in your browser.
rem   - press "start" to begin capturing / recognising / translating
rem   - paste the Zoom caption token there, start/stop Zoom captions, quit
rem   - press "tunnel" to get a URL + QR code to hand to participants
rem Do NOT screen-share the control page.
rem Share the VIEWER page instead (its URL is shown on the control page).
pixi run caption --web %*

rem Close this window on a clean exit (the control page's "quit" button).
rem Keep it open on failure, so the error stays readable.
if errorlevel 1 (
  echo.
  echo   The caption app exited with an error. See the messages above.
  echo.
  pause
  exit /b 1
)
exit /b 0
