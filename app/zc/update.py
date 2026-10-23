"""Updates from GitHub.

check(): compares every installed file with the repository (by git blob hash), so any pushed change counts
         as an update without anyone having to bump a version number. The repo listing is cached for 6 hours.
apply(): downloads the latest code and runs the platform's installer in the background; the installer stops
         this server, installs, and starts the new one. The open window reloads itself.
The repository is set in app/update.json.
"""
import base64
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import time
import zipfile

from . import common as C

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.dirname(APP)  # the folder this copy of ZIMaker runs from
CACHE = os.path.join(C.HOME, "update-check.json")
API = "https://api.github.com/repos/{repo}"
MAX_AGE = 6 * 3600


def config():
    try:
        return json.load(open(os.path.join(APP, "update.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _blob_sha(path):
    data = open(path, "rb").read()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _remote(force):
    cfg = config()
    try:
        cached = json.load(open(CACHE, encoding="utf-8"))
        if not force and time.time() - cached["t"] < MAX_AGE and cached.get("repo") == cfg.get("repo"):
            return cached
    except (OSError, ValueError, KeyError):
        pass
    base = API.format(repo=cfg["repo"])
    tree = C.get_json(f"{base}/git/trees/{cfg['branch']}?recursive=1", timeout=20)
    commit = C.get_json(f"{base}/commits/{cfg['branch']}", timeout=20)
    data = {"t": time.time(), "repo": cfg["repo"],
            "files": {e["path"]: e["sha"] for e in tree.get("tree", []) if e.get("type") == "blob"},
            "date": commit["commit"]["committer"]["date"][:10],
            "message": commit["commit"]["message"].splitlines()[0][:120]}
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, CACHE)
    return data


def check(force=False):
    cfg = config()
    if not cfg.get("repo"):
        return {"available": False, "error": "No update source configured."}
    try:
        remote = _remote(force)
    except Exception as e:  # noqa: BLE001  offline, GitHub busy, rate limit: just say we couldn't check
        return {"available": False, "error": f"Couldn't check for updates ({e.__class__.__name__})."}
    changed = [p for p, sha in remote["files"].items()
               if not os.path.isfile(os.path.join(DEST, p)) or _blob_sha(os.path.join(DEST, p)) != sha]
    return {"available": bool(changed), "changed": changed[:20], "count": len(changed),
            "date": remote["date"], "message": remote["message"], "repo": cfg["repo"]}


def apply():
    """Download the latest code and start the installer in the background. Returns immediately."""
    cfg = config()
    url = f"https://github.com/{cfg['repo']}/archive/refs/heads/{cfg['branch']}.zip"
    # Windows: stage next to the install (a Windows folder the installer can read); Linux: in ~/.zimaker
    staging = os.path.join(os.path.dirname(DEST), "ZIMaker-update") if C.IS_WSL else os.path.join(C.HOME, "update")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    zpath = os.path.join(staging, "latest.zip")
    C.download(url, zpath)
    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            target = os.path.normpath(os.path.join(staging, n))
            if not target.startswith(staging + os.sep) or n.endswith("/"):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(n) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    for x in (zpath, zpath + ".ok", CACHE):
        if os.path.exists(x):
            os.remove(x)
    tops = [d for d in os.listdir(staging) if os.path.isdir(os.path.join(staging, d))]
    if len(tops) != 1 or not os.path.isfile(os.path.join(staging, tops[0], "app", "server.py")):
        raise RuntimeError("The download doesn't look like ZIMaker.")
    src = os.path.join(staging, tops[0])

    if C.IS_WSL:
        # A small wrapper runs the downloaded installer, then cleans up and restarts ZIMaker itself, so this works
        # whatever version is downloaded. It's passed base64-encoded, so no quoting can go wrong on the way.
        def lit(p):
            return "'" + p.replace("'", "''") + "'"
        wsrc = C.to_win(src)
        script = "\n".join([
            "$ErrorActionPreference = 'Continue'",
            "Start-Transcript -Path (Join-Path $env:TEMP 'ZIMaker-update.log') -Force | Out-Null",
            f"& {lit(wsrc + chr(92) + 'app' + chr(92) + 'install.ps1')} -Source {lit(wsrc)} -Quiet",
            f"Remove-Item -LiteralPath {lit(C.to_win(staging))} -Recurse -Force -ErrorAction SilentlyContinue",
            "$d = Join-Path $env:LOCALAPPDATA 'Programs\\ZIMaker'",
            "$w = '/mnt/' + $d.Substring(0, 1).ToLower() + ($d.Substring(2) -replace '\\\\', '/')",
            "Start-Process -WindowStyle Hidden wsl.exe -ArgumentList '-e', 'python3', '-I', ('\"' + $w + '/app/server.py\"')",
            "Stop-Transcript | Out-Null",
        ])
        encoded = base64.b64encode(script.encode("utf-16-le")).decode()
        launch = ("Start-Process powershell.exe -WindowStyle Hidden -ArgumentList "
                  f"'-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','{encoded}'")
        subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", launch], cwd="/mnt/c",
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        script = (f"sleep 1; sh {shlex.quote(os.path.join(src, 'install.sh'))} > {shlex.quote(os.path.join(C.HOME, 'update.log'))} 2>&1; "
                  f"rm -rf {shlex.quote(staging)}; "
                  f"nohup python3 -I {shlex.quote(os.path.join(data, 'zimaker', 'app', 'server.py'))} "
                  f">> {shlex.quote(os.path.join(C.HOME, 'server.log'))} 2>&1 &")
        subprocess.Popen(["sh", "-c", script], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True}
