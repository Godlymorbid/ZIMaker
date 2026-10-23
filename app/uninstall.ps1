# ZIM Creator uninstaller. Run via "Uninstall ZIM Creator.cmd". ZIM files you made are never touched.
param([string]$Dir = (Split-Path -Parent $PSScriptRoot), [switch]$Quiet)
$Dir = (Resolve-Path $Dir).Path.TrimEnd('\')
$Installed = Join-Path $env:LOCALAPPDATA 'Programs\ZIM Creator'

Write-Host "Uninstalling ZIM Creator" -ForegroundColor Yellow
try { Invoke-WebRequest -UseBasicParsing -Method POST -Body '{"force": true}' 'http://localhost:8765/api/quit' -TimeoutSec 3 | Out-Null; Start-Sleep 2 } catch {}

foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
  $lnk = Join-Path $folder 'ZIM Creator.lnk'
  if (Test-Path $lnk) {
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    if ($s.Arguments -like "*$Dir*") { Remove-Item $lnk -Force; Write-Host "Removed $lnk" }
  }
}

$a = if ($Quiet) { 'y' } else { Read-Host "Also remove its libraries and job logs inside WSL (~/.zim-creator, about 150 MB)? [Y/n]" }
if ($a -notmatch '^[nN]') {
  & wsl.exe -e sh -c 'rm -rf "$HOME/.zim-creator"' 2>$null
  Write-Host "Removed ~/.zim-creator in WSL."
}

if ($Dir -eq $Installed -and (Test-Path (Join-Path $Dir 'ZIM Creator.vbs'))) {
  # the folder is in use while this window is open; remove it once the window (our parent cmd.exe) closes
  $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId
  $cmd = "Wait-Process -Id $parent -ErrorAction SilentlyContinue; Start-Sleep 1; Remove-Item -LiteralPath '$Dir' -Recurse -Force"
  Start-Process powershell.exe -ArgumentList '-NoProfile', '-WindowStyle', 'Hidden', '-Command', $cmd -WindowStyle Hidden -WorkingDirectory $env:TEMP
  Write-Host "The program folder ($Dir) will be removed when you close this window."
} else {
  Write-Host "Program files left in place ($Dir is not the installed copy)."
}
Write-Host "ZIM Creator has been uninstalled. ZIM files you made were not touched." -ForegroundColor Green
