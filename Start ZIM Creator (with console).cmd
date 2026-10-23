@echo off
title ZIM Creator (console)
echo ZIM Creator with a visible console, for troubleshooting.
echo Normally just use the desktop icon or "ZIM Creator.vbs".
echo.
start "" /b cmd /c "timeout /t 4 >nul & start msedge.exe --app=http://localhost:8765/ --window-size=1000,950"
wsl.exe -e sh -c "python3 -I \"$(wslpath '%~dp0')app/server.py\""
echo.
echo ZIM Creator has stopped.
pause
