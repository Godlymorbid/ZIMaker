@echo off
title ZIMaker (console)
echo ZIMaker with a visible console, for troubleshooting.
echo Normally just use the desktop icon or "ZIMaker.vbs".
echo.
start "" /b cmd /c "timeout /t 4 >nul & start msedge.exe --app=http://127.0.0.1:8765/ --window-size=1000,950"
set "ZD="
if exist "%~dp0distro.txt" set /p ZD=<"%~dp0distro.txt"
set "ZDA="
if defined ZD set "ZDA=-d %ZD%"
wsl.exe %ZDA% -e sh -c "python3 -I \"$(wslpath '%~dp0')app/server.py\""
echo.
echo ZIMaker has stopped.
pause
