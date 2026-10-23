@echo off
title Uninstall ZIM Creator
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\uninstall.ps1" -Dir "%~dp0."
echo.
pause
