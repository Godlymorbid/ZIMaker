@echo off
title Install ZIM Creator
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\install.ps1" -Source "%~dp0."
echo.
pause
