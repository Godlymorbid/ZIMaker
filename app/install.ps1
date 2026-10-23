# ZIM Creator installer (per user, no admin needed). Run via "Install ZIM Creator.cmd".
param(
  [string]$Source = (Split-Path -Parent $PSScriptRoot),
  [switch]$Quiet   # no questions (used for automated installs)
)
$ErrorActionPreference = 'Stop'
$Dest = Join-Path $env:LOCALAPPDATA 'Programs\ZIM Creator'
$Source = (Resolve-Path $Source).Path.TrimEnd('\')

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host ""; Write-Host $m -ForegroundColor Red; exit 1 }
function To-Wsl($p) { '/mnt/' + $p.Substring(0, 1).ToLower() + ($p.Substring(2) -replace '\\', '/') }
function Ask($q) { if ($Quiet) { return $true }; $a = Read-Host "$q [Y/n]"; return ($a -notmatch '^[nN]') }

Write-Host "ZIM Creator installer" -ForegroundColor Yellow
Write-Host "Turns archive.org books, EPUBs and PDFs into ZIM files for Kiwix."

# 1. WSL with a Linux distribution
Step "Checking WSL (Windows Subsystem for Linux)"
$ok = $false
try { $out = & wsl.exe -e sh -c "echo zc-ok" 2>$null; $ok = (($out -join '') -match 'zc-ok') } catch {}
if (-not $ok) {
  Write-Host "ZIM Creator runs its converter in WSL, which isn't set up on this PC yet."
  if (Ask "Install WSL with Ubuntu now? (needs administrator approval, then a restart)") {
    try { Start-Process wsl.exe -ArgumentList '--install', '-d', 'Ubuntu' -Verb RunAs -Wait } catch { Fail "WSL install was cancelled or failed: $_" }
    Write-Host ""
    Write-Host "Next:" -ForegroundColor Yellow
    Write-Host " 1. Restart the PC."
    Write-Host " 2. Open 'Ubuntu' from the Start menu once and pick a username and password."
    Write-Host " 3. Run 'Install ZIM Creator' again."
    exit 2
  }
  Fail "WSL is required. You can install it later with:  wsl --install -d Ubuntu"
}
Write-Host "WSL is ready."

# 2. Python 3.9+ in the default distribution
$py = (& wsl.exe -e sh -c "command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; print(sys.version_info >= (3, 9))'" 2>$null) -join ''
if ($py -notmatch 'True') {
  Fail ("Your default WSL Linux has no Python 3.9 or newer.`n" +
        "If it's Ubuntu, open Ubuntu and run:  sudo apt install python3`n" +
        "Or install Ubuntu (wsl --install -d Ubuntu) and make it the default (wsl --set-default Ubuntu).")
}
Write-Host "Python is ready."

# 3. Stop a running copy (upgrade), then copy the files
Step "Installing to $Dest"
try { Invoke-WebRequest -UseBasicParsing -Method POST -Body '{"force": true}' 'http://localhost:8765/api/quit' -TimeoutSec 3 | Out-Null; Start-Sleep 2 } catch {}
if ($Source -ne $Dest) {
  New-Item -ItemType Directory -Force $Dest | Out-Null
  foreach ($item in Get-ChildItem $Source -Force) {
    if ($item.Name -in @('__pycache__', 'dist')) { continue }
    Copy-Item $item.FullName -Destination $Dest -Recurse -Force
  }
  Get-ChildItem $Dest -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
}
Get-ChildItem $Dest -Recurse -File | Unblock-File   # files from a downloaded zip are marked as "from the internet"
Write-Host "Files copied."

# 4. Libraries inside WSL (~150 MB, first time only)
Step "Setting up the converter's libraries in WSL (first time: about 150 MB)"
& wsl.exe -e python3 -I "$(To-Wsl $Dest)/app/setup.py"
if ($LASTEXITCODE -ne 0) { Fail "Setting up the libraries failed (see above). Check your internet connection and run the installer again." }

# 5. Shortcuts
Step "Creating shortcuts"
function New-Shortcut($lnk) {
  $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
  $s.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
  $s.Arguments = '"' + (Join-Path $Dest 'ZIM Creator.vbs') + '"'
  $s.WorkingDirectory = $Dest
  $s.IconLocation = Join-Path $Dest 'app\icon.ico'
  $s.Description = 'Turn archive.org books, EPUBs and PDFs into ZIM files'
  $s.Save()
  Write-Host "  $lnk"
}
New-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) 'ZIM Creator.lnk')
New-Shortcut (Join-Path ([Environment]::GetFolderPath('Programs')) 'ZIM Creator.lnk')

Write-Host ""
Write-Host "ZIM Creator is installed." -ForegroundColor Green
Write-Host "Open it from the desktop icon or the Start menu. To remove it, run 'Uninstall ZIM Creator' in $Dest."
if (-not $Quiet -and (Ask "Open ZIM Creator now?")) { Start-Process wscript.exe -ArgumentList ('"' + (Join-Path $Dest 'ZIM Creator.vbs') + '"') }
exit 0
