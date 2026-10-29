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
function Wsl-On { try { & wsl.exe --status *> $null; return ($LASTEXITCODE -eq 0) } catch { return $false } }

# The user's distributions, from "wsl -l -v" (its header line is translated, so skip it; the default has a *).
# Docker's and Rancher's internal ones can't run ZIMaker, so don't even start them.
function Get-Distros {
  $lines = try { @(& wsl.exe -l -v 2>$null) } catch { @() }
  if ($LASTEXITCODE -ne 0) { return @() }
  $list = @()
  foreach ($l in ($lines | Select-Object -Skip 1)) {
    $t = ($l -replace "`0", '').Trim()   # older wsl.exe ignores WSL_UTF8 and writes UTF-16
    if (-not $t) { continue }
    $name = ($t.TrimStart('*').Trim() -split '\s+')[0]
    if ($name -match '^(docker-desktop|rancher-desktop)') { continue }
    $list += [pscustomobject]@{ Name = $name; Default = $t.StartsWith('*') }
  }
  return $list
}

# Can ZIMaker run in this distribution? $null if it doesn't start (or is Alpine, whose libraries ZIMaker's don't fit).
$probe = '[ -f /etc/alpine-release ] && echo zc-alpine && exit 0; command -v apt-get >/dev/null 2>&1 && a=apt || a=noapt; ' +
         'python3 -c ''import sys; sys.exit(sys.version_info < (3, 9))'' >/dev/null 2>&1 && p=py || p=nopy; echo zc-$a-$p'
function Test-Distro($n) {
  $o = try { (& wsl.exe -d $n -e sh -c $probe 2>$null) -join '' } catch { '' }
  if ($o -match 'zc-(apt|noapt)-(py|nopy)') { return [pscustomobject]@{ Apt = ($Matches[1] -eq 'apt'); Python = ($Matches[2] -eq 'py') } }
  return $null
}

function Install-Ubuntu($name) {
  Write-Host "Installing $name. When it asks, pick a username and password for Linux (they don't have to match Windows)."
  Write-Host "When you see a Linux prompt (ending in `$), type  exit  and press Enter to continue." -ForegroundColor Yellow
  & wsl.exe --install -d $name --web-download
  if ($LASTEXITCODE -ne 0) { & wsl.exe --install -d $name }   # older WSL without --web-download
  $r = Test-Distro $name
  if (-not ($r -and $r.Python)) {
    Fail ("$name didn't finish installing.`n" +
          "Open PowerShell and run:  wsl --install -d $name`n" +
          "pick a Linux username and password, then run 'Install ZIMaker' again.")
  }
}

$DistroFile = Join-Path $Dest 'distro.txt'   # the distribution ZIMaker runs in; kept across updates
$Distro = ''
$Ours = $false   # ZIMaker installed this distribution itself (the uninstaller can then offer to remove it)
$all = @(Get-Distros)
if (-not $all.Count) {
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

  # WSL is on but has no Linux: install Ubuntu for this user, right here in this window.
  Write-Host "WSL is on, but there's no Linux installed for your account yet."
  if (-not (Ask "Install Ubuntu now? (about 500 MB download, no administrator needed)")) {
    Fail "ZIMaker needs a Linux distribution in WSL. You can install one later with:  wsl --install -d Ubuntu"
  }
  Install-Ubuntu 'Ubuntu'
  $Distro = 'Ubuntu'; $Ours = $true
}

# 2. Which distribution ZIMaker runs in. Keep the one chosen before while it still works (updates ask nothing).
$saved = if (Test-Path $DistroFile) { "$(Get-Content $DistroFile -TotalCount 1)".Trim() } else { '' }
if (-not $Distro -and $saved -and (@($all.Name) -contains $saved)) {
  $r = Test-Distro $saved
  if ($r -and $r.Python) { $Distro = $saved; $Ours = @(Get-Content $DistroFile) -contains 'installed-by-zimaker' }
}
if (-not $Distro) {
  Write-Host "Checking your Linux distributions in WSL..."
  $cands = @()
  foreach ($d in $all) {
    $r = Test-Distro $d.Name
    if (-not $r) { Write-Host "  $($d.Name): can't run ZIMaker"; continue }
    if (-not $r.Python -and -not $r.Apt) { Write-Host "  $($d.Name): has no Python 3.9 or newer"; continue }
    # best first: ready Debian/Ubuntu, then Debian/Ubuntu missing Python, then others; within those, the default, then Ubuntu
    $kind = if (-not $r.Apt) { 2 } elseif (-not $r.Python) { 1 } else { 0 }
    $pref = if ($d.Default) { 0 } elseif ($d.Name -match '^Ubuntu') { 1 } else { 2 }
    $note = @()
    if ($d.Default) { $note += 'your default' }
    if (-not $r.Python) { $note += 'ZIMaker will add Python to it' }
    if (-not $r.Apt) { $note += "the optional tools (video, 7-Zip, LibreOffice) need Ubuntu or Debian" }
    $cands += [pscustomobject]@{ Name = $d.Name; Python = $r.Python; Rank = $kind * 10 + $pref; Note = ($note -join '; ') }
  }
  $cands = @($cands | Sort-Object Rank, Name)
  # a separate Ubuntu can be added under a name that isn't taken yet
  $newName = @('Ubuntu', 'Ubuntu-24.04') | Where-Object { @($all.Name) -notcontains $_ } | Select-Object -First 1

  $pick = $null
  if (-not $cands.Count) {
    if ($Quiet) { Fail "None of your WSL Linux distributions can run ZIMaker. Run 'Install ZIMaker' to set one up." }
    if (-not $newName) { Fail "None of your WSL Linux distributions can run ZIMaker. Install Python 3 in your Ubuntu, then run 'Install ZIMaker' again." }
    Write-Host "None of your Linux distributions can run ZIMaker."
    if (-not (Ask "Install $newName for ZIMaker, next to them? (about 500 MB, no administrator needed; your default stays the same)")) {
      Fail "ZIMaker needs a Linux distribution it can run in. You can add one later with:  wsl --install -d Ubuntu"
    }
    $pick = 'new'
  } elseif ($Quiet) {
    $pick = $cands[0]
  } else {
    Write-Host ""
    Write-Host "ZIMaker can run in one of your existing Linux distributions:"
    for ($i = 0; $i -lt $cands.Count; $i++) {
      $n = $cands[$i].Note; Write-Host ("  [{0}] {1}{2}" -f ($i + 1), $cands[$i].Name, $(if ($n) { "  ($n)" } else { '' }))
    }
    if ($newName) { Write-Host "  [U] Install a separate $newName just for ZIMaker (about 500 MB; your default stays the same)" }
    Write-Host "It only adds its own folder (~/.zimaker) and doesn't change anything else in the one you pick."
    while (-not $pick) {
      $a = (Read-Host "Choose [1]").Trim()
      if (-not $a) { $a = '1' }
      if ($newName -and $a -match '^[uU]$') { $pick = 'new' }
      elseif ($a -match '^\d+$' -and [int]$a -ge 1 -and [int]$a -le $cands.Count) { $pick = $cands[[int]$a - 1] }
      else { Write-Host "Type a number from the list$(if ($newName) { ' or U' })." }
    }
  }

  if ($pick -eq 'new') {
    Install-Ubuntu $newName
    $Distro = $newName; $Ours = $true
  } else {
    $Distro = $pick.Name
    if (-not $pick.Python) {
      Write-Host "Adding Python to $Distro (its package manager, as root)..."
      & wsl.exe -d $Distro -u root -- sh -c "apt-get update && apt-get install -y python3"
      $r = Test-Distro $Distro
      if (-not ($r -and $r.Python)) { Fail "Couldn't install Python in $Distro. Open it and run:  sudo apt install python3  then run 'Install ZIMaker' again." }
    }
  }
}
$D = @('-d', $Distro)   # every wsl.exe call below runs in ZIMaker's distribution
Write-Host "WSL is ready. ZIMaker will run in: $Distro"

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
# line 1 (read by the launchers, uninstaller and updater): the distribution; line 2: whether ZIMaker installed it
Set-Content -LiteralPath $DistroFile -Value (@($Distro) + $(if ($Ours) { @('installed-by-zimaker') } else { @() })) -Encoding Ascii
Write-Host "Files copied."

# 4. Libraries inside WSL (~150 MB, first time only)
Step "Setting up the converter's libraries in WSL (first time: about 150 MB)"
& wsl.exe @D -e python3 -I "$(To-Wsl $Dest)/app/setup.py"
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
