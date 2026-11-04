@echo off
rem Puts a "ZIMaker" icon on your desktop that opens the app.
rem The folder goes in as a variable, not pasted into the code: names like O'Brien would break it.
set "ZDIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$dir=$env:ZDIR; $desk=[Environment]::GetFolderPath('Desktop'); $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desk 'ZIMaker.lnk')); $s.TargetPath=(Join-Path $env:WINDIR 'System32\wscript.exe'); $s.Arguments=([char]34 + $dir + 'ZIMaker.vbs' + [char]34); $s.WorkingDirectory=$dir; $s.IconLocation=($dir + 'app\icon.ico'); $s.Description='Turn archive.org books, EPUBs and PDFs into ZIM files'; $s.Save(); Write-Host ('Shortcut created: ' + (Join-Path $desk 'ZIMaker.lnk'))"
