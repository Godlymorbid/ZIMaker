# ZIMaker uninstaller. Run via "Uninstall ZIMaker.cmd". ZIM files you made are never touched.
param([string]$Dir = (Split-Path -Parent $PSScriptRoot), [switch]$Quiet)
$Dir = (Resolve-Path $Dir).Path.TrimEnd('\')
$Installed = Join-Path $env:LOCALAPPDATA 'Programs\ZIMaker'

Write-Host "Uninstalling ZIMaker" -ForegroundColor Yellow
try { Invoke-WebRequest -UseBasicParsing -Method POST -Body '{"force": true}' 'http://127.0.0.1:8765/api/quit' -TimeoutSec 3 | Out-Null; Start-Sleep 2 } catch {}

foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
  $lnk = Join-Path $folder 'ZIMaker.lnk'
  if (Test-Path $lnk) {
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    if ($s.Arguments -like "*$Dir*") { Remove-Item $lnk -Force; Write-Host "Removed $lnk" }
  }
}

$env:WSL_UTF8 = '1'   # plain text from wsl.exe instead of UTF-16
$f = Join-Path $Dir 'distro.txt'   # line 1: the WSL distribution ZIMaker ran in; line 2: whether ZIMaker installed it
$lines = @(if (Test-Path $f) { Get-Content $f })   # always a list, even for a one-line file
$Distro = if ($lines.Count) { "$($lines[0])".Trim() } else { '' }
$Ours = $lines -contains 'installed-by-zimaker'
$D = if ($Distro) { @('-d', $Distro) } else { @() }

$a = if ($Quiet) { 'y' } else { Read-Host "Also remove its libraries and job logs inside WSL (~/.zimaker, about 150 MB)? [Y/n]" }
if ($a -notmatch '^[nN]') {
  & wsl.exe @D -e sh -c 'rm -rf "$HOME/.zimaker"' 2>$null
  Write-Host "Removed ~/.zimaker in WSL."
}

# The Linux distribution, and WSL itself: only when the user says so (never with -Quiet); No is the default.
function Wsl-On { try { & wsl.exe --status *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false } }
function Get-DistroNames {   # all of them, Docker's included ("wsl -l -v": skip the translated header line)
  $out = try { @(& wsl.exe -l -v 2>$null) } catch { @() }
  if ($LASTEXITCODE -ne 0) { return @() }
  $out | Select-Object -Skip 1 | ForEach-Object { ($_ -replace "`0", '').Trim() } | Where-Object { $_ } |
    ForEach-Object { ($_.TrimStart('*').Trim() -split '\s+')[0] }
}

if (-not $Quiet -and $Distro -and (@(Get-DistroNames) -contains $Distro)) {
  Write-Host ""
  if ($Ours) {
    Write-Host "ZIMaker installed the Linux distribution '$Distro' in WSL when it was set up."
    $go = (Read-Host "Remove '$Distro' too? This deletes everything in it, including any files saved there. [y/N]") -match '^[yY]'
  } else {
    Write-Host "ZIMaker ran in '$Distro', a Linux distribution you had before ZIMaker. You probably want to keep it:"
    Write-Host "removing it deletes everything in it."
    $go = "$(Read-Host "To remove it anyway, type its name ($Distro); or just press Enter to keep it")".Trim() -ceq $Distro
  }
  if ($go) {
    & wsl.exe --unregister $Distro
    if ($LASTEXITCODE -eq 0) { Write-Host "Removed the Linux distribution '$Distro'." } else { Write-Host "Couldn't remove '$Distro' (see above)." -ForegroundColor Red }
  } else {
    Write-Host "Kept '$Distro'."
  }
}

if (-not $Quiet -and (Wsl-On)) {
  $left = @(Get-DistroNames)
  if ($left.Count) {
    Write-Host "WSL stays installed: other Linux distributions still use it ($($left -join ', '))."
  } else {
    Write-Host ""
    Write-Host "No Linux distributions are left in WSL."
    Write-Host "Removing WSL itself also affects any other program that needs it, such as Docker Desktop."
    if ((Read-Host "Remove WSL from Windows too? (needs administrator approval, then a restart) [y/N]") -match '^[yY]') {
      # Remove the WSL app (older Windows has no "wsl --uninstall"), then turn off its Windows feature.
      # The Virtual Machine Platform feature is left on: other programs (emulators, for example) can use it too.
      $elevated = 'wsl.exe --uninstall; dism.exe /online /disable-feature /featurename:Microsoft-Windows-Subsystem-Linux /norestart'
      $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($elevated))
      try {
        Start-Process powershell.exe -ArgumentList '-NoProfile', '-EncodedCommand', $enc -Verb RunAs -Wait
        Write-Host "WSL has been removed. Restart the PC to finish." -ForegroundColor Yellow
      } catch { Write-Host "Removing WSL was cancelled or failed: $_" -ForegroundColor Red }
    } else {
      Write-Host "Kept WSL."
    }
  }
}

if ($Dir -eq $Installed -and (Test-Path (Join-Path $Dir 'ZIMaker.vbs'))) {
  # the folder is in use while this window is open; remove it once the window (our parent cmd.exe) closes
  $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId
  $cmd = "Wait-Process -Id $parent -ErrorAction SilentlyContinue; Start-Sleep 1; Remove-Item -LiteralPath '$Dir' -Recurse -Force"
  Start-Process powershell.exe -ArgumentList '-NoProfile', '-WindowStyle', 'Hidden', '-Command', $cmd -WindowStyle Hidden -WorkingDirectory $env:TEMP
  Write-Host "The program folder ($Dir) will be removed when you close this window."
} else {
  Write-Host "Program files left in place ($Dir is not the installed copy)."
}
Write-Host "ZIMaker has been uninstalled. ZIM files you made were not touched." -ForegroundColor Green
