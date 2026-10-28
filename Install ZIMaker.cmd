@echo off
title Install ZIMaker
if not exist "%~dp0app\install.ps1" (
  echo.
  echo   ZIMaker's files aren't all here. It looks like this was opened straight from inside the zip file.
  echo.
  echo   Please unzip it first:
  echo     1. Close this window.
  echo     2. Right-click the downloaded zip file and choose "Extract All...".
  echo     3. Open the extracted folder and double-click "Install ZIMaker.cmd" there.
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\install.ps1" -Source "%~dp0."
echo.
pause
