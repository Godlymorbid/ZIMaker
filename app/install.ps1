# ZIMaker installer (per user, no admin needed). Run via "Install ZIMaker.cmd".
param(
  [string]$Source = (Split-Path -Parent $PSScriptRoot),
  [switch]$Quiet   # no questions (used for automated installs and the in-app updater)
)
$ErrorActionPreference = 'Stop'
$Dest = Join-Path $env:LOCALAPPDATA 'Programs\ZIMaker'
$Source = (Resolve-Path $Source).Path.TrimEnd('\')

function Step($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host ""; Write-Host $m -ForegroundColor Red; exit 1 }
function To-Wsl($p) { '/mnt/' + $p.Substring(0, 1).ToLower() + ($p.Substring(2) -replace '\\', '/') }
function Ask($q) { if ($Quiet) { return $true }; $a = Read-Host "$q [Y/n]"; return ($a -notmatch '^[nN]') }

Write-Host "ZIMaker installer" -ForegroundColor Yellow
Write-Host "Turns websites, archive.org books, ebooks, documents and pictures into ZIM files for Kiwix."

# 1. WSL with a Linux distribution. Two separate steps, because they need different rights:
#    turning WSL on needs an administrator (and a restart); Ubuntu must be installed as *this* user,
#    since WSL distributions belong to the Windows account that installs them.
Step "Checking WSL (Windows Subsystem for Linux)"
$env:WSL_UTF8 = '1'   # plain text from wsl.exe instead of UTF-16
function Linux-Works { try { $o = & wsl.exe -e sh -c "echo zc-ok" 2>$null; return (($o -join '') -match 'zc-ok') } catch { return $false } }
function Wsl-On { try { & wsl.exe --status *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false } }

if (-not (Linux-Works)) {
  if (-not (Wsl-On)) {
    Write-Host "ZIMaker runs its converter in WSL, which isn't turned on on this PC yet."
    if (-not (Ask "Turn on WSL now? (needs administrator approval, then a restart)")) {
      Fail "WSL is required. You can turn it on later with:  wsl --install"
    }
    # Turn on WSL only (no distribution: one installed from an administrator window would belong to the
    # administrator). Older Windows versions don't know --no-distribution; turn the features on directly then.
    $elevated = 'wsl.exe --install --no-distribution; $c = $LASTEXITCODE; if ($c -ne 0 -and $c -ne 3010) { ' +
                'dism.exe /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart; ' +
                'dism.exe /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart }'
    $enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($elevated))
    try { Start-Process powershell.exe -ArgumentList '-NoProfile', '-EncodedCommand', $enc -Verb RunAs -Wait }
    catch { Fail "Turning on WSL was cancelled or failed: $_" }
    if (-not (Wsl-On)) {
      Write-Host ""
      Write-Host "Next:" -ForegroundColor Yellow
      Write-Host " 1. Restart the PC."
      Write-Host " 2. Run 'Install ZIMaker' again. It installs Ubuntu and then ZIMaker."
      exit 2
    }
  }

  # WSL is on but has no working Linux: install Ubuntu for this user, right here in this window.
  Write-Host "WSL is on, but there's no Linux installed for your account yet."
  if (-not (Ask "Install Ubuntu now? (about 500 MB download, no administrator needed)")) {
    Fail "ZIMaker needs a Linux distribution in WSL. You can install one later with:  wsl --install -d Ubuntu"
  }
  Write-Host "Installing Ubuntu. When it asks, pick a username and password for Linux (they don't have to match Windows)."
  Write-Host "When you see a Linux prompt (ending in `$), type  exit  and press Enter to continue." -ForegroundColor Yellow
  & wsl.exe --install -d Ubuntu --web-download
  if ($LASTEXITCODE -ne 0) { & wsl.exe --install -d Ubuntu }   # older WSL without --web-download
  & wsl.exe --set-default Ubuntu *> $null
  if (-not (Linux-Works)) {
    Fail ("Ubuntu didn't finish installing.`n" +
          "Open PowerShell and run:  wsl --install -d Ubuntu`n" +
          "pick a Linux username and password, then run 'Install ZIMaker' again.")
  }
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
  $s.Arguments = '"' + (Join-Path $Dest 'ZIMaker.vbs') + '"'
  $s.WorkingDirectory = $Dest
  $s.IconLocation = Join-Path $Dest 'app\icon.ico'
  $s.Description = 'Turn websites, books, documents and pictures into ZIM files for Kiwix'
  $s.Save()
  Write-Host "  $lnk"
}
New-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) 'ZIMaker.lnk')
New-Shortcut (Join-Path ([Environment]::GetFolderPath('Programs')) 'ZIMaker.lnk')

# 6. Remove the old install from before the rename ("ZIM Creator"), if there is one
$old = Join-Path $env:LOCALAPPDATA 'Programs\ZIM Creator'
foreach ($folder in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
  $lnk = Join-Path $folder 'ZIM Creator.lnk'
  if (Test-Path $lnk) {
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    if ($s.Arguments -like "*$old*") { Remove-Item $lnk -Force; Write-Host "  removed old shortcut $lnk" }
  }
}
if ((Test-Path $old) -and (Test-Path (Join-Path $old 'ZIM Creator.vbs'))) {
  Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
  Write-Host "  removed old install $old"
}

Write-Host ""
Write-Host "ZIMaker is installed." -ForegroundColor Green
Write-Host "Open it from the desktop icon or the Start menu. To remove it, run 'Uninstall ZIMaker' in $Dest."
if (-not $Quiet -and (Ask "Open ZIMaker now?")) { Start-Process wscript.exe -ArgumentList ('"' + (Join-Path $Dest 'ZIMaker.vbs') + '"') }
exit 0
