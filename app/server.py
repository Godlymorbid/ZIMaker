"""ZIMaker local web app. Start with 'Start ZIMaker.cmd' (Windows) or: python3 -I app/server.py

Serves the UI on http://127.0.0.1:8765 and runs one conversion job at a time in a child process.
"""
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)
from zc import common as C  # noqa: E402

PORT = 8765
SETUP = {"state": "starting", "message": "", "zimcheck": False}
PROCS = {}
QUEUE = []
LOCK = threading.Lock()
SEEN = {"t": time.time() + 90}  # last time the window polled; grace period while it first opens
STARTED = time.time()           # lets the window notice when an updated server has taken over
IDLE_EXIT = 150                 # default seconds without a window (and no job) before the app stops itself (Settings
                                # can change it); not less: a minimised window may only poll once a minute
SERVER = {}


def busy():
    with LOCK:
        return bool(QUEUE) or any(p.poll() is None for p in PROCS.values())


def watchdog():
    """Stop when the window has been closed for the time set in Settings and no job is running or queued
    (never, with "Keep running")."""
    from zc import settings
    while True:
        time.sleep(5)
        try:
            try:
                limit = settings.load()["stop_after"]
            except Exception:  # noqa: BLE001
                limit = IDLE_EXIT
            if limit and time.time() - SEEN["t"] > limit and not busy() and SETUP["state"] != "installing":
                print("Window closed and nothing running: stopping ZIMaker.", flush=True)
                SERVER["srv"].shutdown()
                return
        except Exception as e:  # noqa: BLE001  never let the auto-stop die quietly
            print(f"auto-stop check failed: {e}", flush=True)


def setup_thread():
    from zc import deps
    try:
        SETUP.update(state="installing", message="Setting up (first run downloads ~150 MB of libraries)…")
        st = deps.ensure(lambda m: (SETUP.__setitem__("message", m), print(m, flush=True)))
        SETUP.update(state="ready", message="", zimcheck=st["zimcheck"])
    except Exception as e:  # noqa: BLE001
        SETUP.update(state="error", message=f"Setup failed: {e}")


# ---- ZIM viewer: opened archives, a remembered library, search
ZIMS = {}
LIBRARY = os.path.join(C.HOME, "library.json")


def _library_load():
    try:
        return json.load(open(LIBRARY, encoding="utf-8"))
    except (OSError, ValueError):
        return []


def zim_open(path):
    path = (path or "").strip().strip('"')
    if not path:
        raise RuntimeError("Enter the path of a .zim file.")
    wp = path if path.startswith("/") else C.to_wsl(path)
    if not wp.lower().endswith(".zim") or not os.path.isfile(wp):
        raise RuntimeError(f"No ZIM file at {path}")
    C.use_lib()
    from libzim.reader import Archive
    try:
        a = Archive(wp)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"This isn't a ZIM file ZIMaker can open ({e}).") from e
    zid = hashlib.sha1(os.path.realpath(wp).encode()).hexdigest()[:12]
    ZIMS[zid] = {"archive": a, "path": wp}
    try:
        title = a.get_metadata("Title").decode("utf-8", "replace")
    except (KeyError, RuntimeError):
        title = os.path.splitext(os.path.basename(wp))[0]
    ZIMS[zid]["title"] = title
    shown = path if not wp.startswith(C.UPLOADS) else os.path.basename(wp)
    # uploaded copies aren't kept in the library, and nothing is in private mode
    if not wp.startswith(C.UPLOADS) and not private():
        lib = [x for x in _library_load() if x.get("path") != shown]
        lib.insert(0, {"path": shown, "title": title, "t": time.time()})
        json.dump(lib[:100], open(LIBRARY, "w", encoding="utf-8"))
    return {"id": zid, "title": title, "path": shown, "articles": a.article_count,
            "size": os.path.getsize(wp), "search": bool(a.has_fulltext_index)}


def private():
    from zc import settings
    return settings.load()["private"]


def clear_history():
    """Forget what was made and opened: job folders (with their logs and web addresses), uploaded copies, the
    list of opened ZIMs. Jobs still running or queued are kept. The ZIM files themselves stay where they were saved."""
    with LOCK:
        keep = set(QUEUE) | {j for j, p in PROCS.items() if p.poll() is None}
    in_use = set()
    n = 0
    for jid in (os.listdir(C.JOBS) if os.path.isdir(C.JOBS) else []):
        d = os.path.join(C.JOBS, jid)
        if jid in keep:
            try:
                in_use |= {s.get("path", "") for s in json.load(open(os.path.join(d, "job.json"), encoding="utf-8"))["sources"]}
            except (OSError, ValueError, KeyError):
                pass
            continue
        shutil.rmtree(d, ignore_errors=True)
        n += 1
    for name in (os.listdir(C.UPLOADS) if os.path.isdir(C.UPLOADS) else []):
        d = os.path.join(C.UPLOADS, name)
        if not any(p.startswith(d + os.sep) for p in in_use) and not any(z["path"].startswith(d + os.sep) for z in list(ZIMS.values())):
            shutil.rmtree(d, ignore_errors=True)
    if os.path.exists(LIBRARY):
        os.remove(LIBRARY)
    shutil.rmtree(os.path.join(C.HOME, "peek"), ignore_errors=True)   # "Check links" scratch space
    return n


def proxy_test(p):
    """Fetch the address the outside world sees, through proxy p (from the Settings window, not saved yet)."""
    p = C.clean_proxy(p)
    t = time.time()
    try:
        req = urllib.request.Request("https://check.torproject.org/api/ip", headers=C.UA)
        with C.proxy_opener(p).open(req, timeout=40) as r:
            d = json.load(r)
        return {"ok": True, "ip": d.get("IP", ""), "tor": bool(d.get("IsTor")), "seconds": round(time.time() - t, 1)}
    except Exception as e:  # noqa: BLE001
        why = str(getattr(e, "reason", "") or e)
        out = {"ok": False, "error": why}
        if C.IS_WSL and p["type"] != "off" and p["host"].strip("[]") in ("127.0.0.1", "localhost", "::1") and \
                ("refused" in why.lower() or "timed out" in why.lower()):
            out["wsl_hint"] = True  # a proxy on Windows' own 127.0.0.1 can't be reached from WSL's default network
        return out


def node_test(kind, p):
    """Is the I2P router or Hyphanet node (from the Settings window, not saved yet) answering? I2P: also fetch a
    well-known I2P site through it, to show the router has joined the network."""
    p = C.clean_i2p(p) if kind == "i2p" else C.clean_ipfs(p) if kind == "ipfs" else C.clean_hyphanet(p)
    gw = {"host": p.get("host", "127.0.0.1"), "port": p["port"]}
    t = time.time()
    try:
        if kind == "ipfs":   # the empty file's CID: answered at once, without fetching anything from the network
            if p["on"]:
                opener, url = C.proxy_opener(C.clean_proxy({}), ipfs=gw), f"http://bafkqaaa.ipfs.localhost:{p['port']}/"
            else:            # the public gateway, through the proxy like every website
                opener, url = C.proxy_opener(C.clean_proxy(dict(C.PROXY))), f"https://bafkqaaa.ipfs.{p['gateway']}/"
        elif kind == "i2p":
            opener = C.proxy_opener(C.clean_proxy({}), i2p=gw)
            url = "http://i2p-projekt.i2p/"
        else:   # the gateway's own start page, directly: answers when the node is running (no network fetch)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            url = f"http://{'[::1]' if gw['host'] in ('::1', '[::1]') else gw['host']}:{gw['port']}/"
        req = urllib.request.Request(url, headers=C.UA)
        with opener.open(req, timeout=90 if kind == "i2p" else 40 if kind == "ipfs" else 15) as r:
            r.read(2000)
        return {"ok": True, "seconds": round(time.time() - t, 1)}
    except Exception as e:  # noqa: BLE001
        why = str(getattr(e, "reason", "") or e)
        out = {"ok": False, "error": why}
        if kind == "ipfs" and not p["on"] and not isinstance(e, urllib.error.HTTPError):
            # <cid>.ipfs.<gateway> names blocked? Jobs then use gateway/ipfs/<cid>/ instead: test that too
            for g in (p["gateway"],):
                try:
                    with opener.open(urllib.request.Request(f"https://{g}/ipfs/bafkqaaa", headers=C.UA), timeout=40) as r:
                        r.read(2000)
                    return {"ok": True, "seconds": round(time.time() - t, 1),
                            "note": f"only as {g}/ipfs/… (this network blocks {p['gateway']}'s IPFS subdomains), "
                                    "so links that start at a site's root may not work"}
                except Exception:  # noqa: BLE001
                    pass
            out["error"] = (f"couldn't reach the public gateway {p['gateway']} (it may be down or blocked on this "
                            "network). Try another public gateway, or your own IPFS program")
        if "refused" in why.lower() and (kind != "ipfs" or p["on"]):
            out["error"] = (f"nothing answers at {gw['host']}:{p['port']}. Is "
                            + {"i2p": "your I2P router", "ipfs": "IPFS Desktop (or Kubo)"}.get(kind, "Hyphanet")
                            + " running, on that port?")
        if isinstance(e, urllib.error.HTTPError) and kind == "i2p":
            # the router answered (its own error page: site not found yet, router still starting)
            out = {"ok": False, "error": f"the router answered, but couldn't reach the test site yet (HTTP {e.code}). "
                                         "A new router needs a few minutes to join the network.", "router": True}
        if C.IS_WSL and (kind != "ipfs" or p["on"]) and gw["host"].strip("[]") in ("127.0.0.1", "localhost", "::1") and \
                ("refused" in why.lower() or "timed out" in why.lower()):
            out["wsl_hint"] = True
        return out


def forget(path):
    json.dump([x for x in _library_load() if x.get("path") != path], open(LIBRARY, "w", encoding="utf-8"))


def library():
    """ZIMs opened before plus ZIMs ZIMaker made, newest first (only ones that still exist)."""
    seen, out = set(), []
    entries = [(x["path"], x.get("title", "")) for x in _library_load()]
    if os.path.isdir(C.JOBS):
        for jid in sorted(os.listdir(C.JOBS), reverse=True)[:200]:
            try:
                res = json.load(open(os.path.join(C.JOBS, jid, "result.json"), encoding="utf-8"))
            except (OSError, ValueError):
                continue
            entries += [(f["path"], "") for f in res.get("files", [])]
    for path, title in entries:
        if path in seen:
            continue
        seen.add(path)
        try:
            wp = path if path.startswith("/") else C.to_wsl(path)
            st = os.stat(wp)
        except (OSError, subprocess.CalledProcessError):
            continue
        out.append({"path": path, "title": title or os.path.splitext(os.path.basename(wp))[0],
                    "size": st.st_size, "mtime": st.st_mtime})
    out.sort(key=lambda x: -x["mtime"])
    return out[:60]


def zim_search(zid, q, kind):
    z = ZIMS.get(zid)
    if not z:
        return {"error": "This ZIM isn't open any more."}
    q = q.strip()
    if not q:
        return {"items": []}
    C.use_lib()
    a = z["archive"]
    # libzim 4 renamed getResults/getEstimatedMatches to snake_case, and full-text results became objects with .path
    def results(s, n):
        return [r if isinstance(r, str) else r.path for r in (getattr(s, "get_results", None) or s.getResults)(0, n)]
    try:
        if kind == "suggest":
            from libzim.suggestion import SuggestionSearcher
            paths, total = results(SuggestionSearcher(a).suggest(q), 12), None
        else:
            if not a.has_fulltext_index:
                return {"error": "This ZIM has no full-text index; try the title suggestions instead.", "items": []}
            from libzim.search import Query, Searcher
            s = Searcher(a).search(Query().set_query(q))
            total, paths = (getattr(s, "get_estimated_matches", None) or s.getEstimatedMatches)(), results(s, 50)
    except Exception as e:  # noqa: BLE001
        return {"error": f"Search failed ({e})", "items": []}
    items = []
    for p in paths:
        try:
            items.append({"path": p, "title": a.get_entry_by_path(p).title or p})
        except KeyError:
            pass
    return {"items": items, "total": total}


def classify(line):
    """('ia', item id) | ('lib', url) | ('web', url) | (None, error message) for one line of the links box."""
    from zc import ia, library
    s = line.strip()
    key = C.hyphanet_key(s)   # USK@…, freenet:…, or a link to the node's gateway: a Hyphanet freesite
    if key:
        return "web", C.hyphanet_url(key)
    ref = C.ipfs_ref(s)       # ipfs://, ipns://, a CID, or a link to any IPFS gateway
    if ref:
        url = C.ipfs_url(ref)
        return ("web", url) if url else (None, C.IPFS_NEEDS_NODE)
    if re.match(r"https?://", s, re.I):
        host = urllib.parse.urlsplit(s).netloc.lower()
        if host.endswith("archive.org") and ia.item_id(s):
            return "ia", ia.item_id(s)
        if library.detect(s):   # Gutenberg, Standard Ebooks, Wikisource, OpenStax, LibriVox, IIIF (or a blocked library)
            return "lib", s
        return "web", s
    # bare text: a domain like example.com/page is a website; anything else an archive.org item ID
    if re.fullmatch(r"[\w-]+(\.[\w-]+)*\.([A-Za-z]{2,24}|i2p)(/\S*)?", s, re.I) and not s.lower().startswith("archive.org"):
        # onion and I2P sites rarely use https
        url = ("http://" if C.is_onion("http://" + s) or C.is_i2p("http://" + s) else "https://") + s
        return ("lib" if library.detect(url) else "web"), url
    if s.lower().startswith("archive.org/") and ia.item_id("https://" + s):
        return "ia", ia.item_id("https://" + s)
    if ia.item_id(s):
        return "ia", ia.item_id(s)
    return None, "Not a web address or archive.org item"


def job_summary(jid):
    d = os.path.join(C.JOBS, jid)
    try:
        job = json.load(open(os.path.join(d, "job.json"), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    st = {}
    if os.path.exists(os.path.join(d, "state.json")):
        try:
            st = json.load(open(os.path.join(d, "state.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    res = None
    if os.path.exists(os.path.join(d, "result.json")):
        try:
            res = json.load(open(os.path.join(d, "result.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    status = st.get("status", "queued")
    p = PROCS.get(jid)
    if status == "running" and (p is None or p.poll() is not None) and res is None:
        status = "interrupted"
    if jid in QUEUE:
        status = "queued"
    log = ""
    lp = os.path.join(d, "log.txt")
    if os.path.exists(lp):
        with open(lp, "rb") as f:  # only the end: this runs for every job, every 2 seconds
            f.seek(max(0, os.path.getsize(lp) - 48_000))
            lines = f.read().decode("utf-8", "replace").splitlines(keepends=True)
        log = "".join(ln for ln in lines[-200:] if not ln.startswith("Traceback") and not ln.startswith("  File"))
    return {"id": jid, "created": job.get("created", 0), "label": job.get("label", ""), "status": status,
            "stage": st.get("stage", "Waiting…"), "done": st.get("done"), "total": st.get("total"),
            "result": res, "log": log}


def new_job(body):
    """Queue a job from the window's options (or the watched folder's). Returns its id; ValueError: what's wrong."""
    sources = []
    for line in body.get("links", "").splitlines():
        if line.strip():
            kind, val = classify(line)
            if kind == "ia":
                sources.append({"type": "ia", "id": val})
            elif kind == "web":
                sources.append({"type": "web", "url": val, "name": val})
            elif kind == "lib":
                sources.append({"type": "lib", "url": val, "name": val})
            else:
                raise ValueError(f"{line.strip()}: {val}")
    for f in body.get("files", []):
        # an uploaded copy: exactly one level inside the uploads folder (the runner removes that folder afterwards)
        path = os.path.realpath(str(f.get("path", "")) if isinstance(f, dict) else "")
        if os.path.dirname(os.path.dirname(path)) != os.path.realpath(C.UPLOADS) or not os.path.isfile(path):
            raise ValueError("bad file")
        sources.append({"type": "file", "path": path, "name": f.get("name")})
    for p in body.get("paths", []):
        try:
            wp = C.to_wsl(str(p))
        except Exception:  # noqa: BLE001  wslpath rejects some odd strings
            raise ValueError(f"File not found: {p}") from None
        if not os.path.exists(wp):
            raise ValueError(f"File not found: {p}")
        sources.append({"type": "file", "path": wp, "name": os.path.basename(wp)})
    if not sources:
        raise ValueError("Add at least one web address or file.")
    out_dir = str(body.get("output_dir") or "").strip()
    if not out_dir:
        raise ValueError("Choose an output folder.")
    try:
        max_pages = max(1, min(int(body.get("max_pages") or 300), 50000))
    except (TypeError, ValueError):
        raise ValueError("The page limit must be a number.") from None
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = os.path.join(C.JOBS, jid)
    os.makedirs(d)
    label = ", ".join((s.get("name") or s.get("id")) for s in sources)
    from zc import settings
    st = settings.load()
    pname = body.get("profile") if body.get("profile") in st["profiles"] else st["active"]
    prof = st["profiles"][pname]
    job = {"sources": sources, "mode": body.get("mode", "pictures"), "combine": bool(body.get("combine")),
           "title": body.get("title", ""), "output_dir": out_dir, "created": time.time(),
           "label": C.clip(label, 120), "profile": pname, "neutral_names": st["neutral_names"],
           "quality": prof["quality"], "sensitivity": prof["sensitivity"],
           # from the watched folder, with "delete each file after its ZIM is made" on
           "delete_sources": bool(body.get("delete_sources")),
           "web": {"max_pages": max_pages,
                   "scope": body.get("scope") if body.get("scope") in ("section", "page") else "site",
                   "media": bool(prof.get("web_media")),
                   # the main screen's checkbox; the profile's setting if the window didn't send one
                   "scripts": bool(body["scripts"] if "scripts" in body else prof.get("web_scripts"))}}
    json.dump(job, open(os.path.join(d, "job.json"), "w", encoding="utf-8"))
    C.Log(d).stage("Waiting in queue", status="queued")
    with LOCK:
        QUEUE.append(jid)
    return jid


def rerun_job(jid):
    """Queue a copy of an earlier job with the same sources and options (Retry / Run again). Returns the new id."""
    try:
        old = json.load(open(os.path.join(C.JOBS, jid, "job.json"), encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("That job's details are gone (cleared from the history).") from None
    for s in old.get("sources", []):
        if s.get("type") == "file" and not os.path.exists(s.get("path", "")):
            uploaded = os.path.realpath(s.get("path", "")).startswith(os.path.realpath(C.UPLOADS) + os.sep)
            raise ValueError(f"{s.get('name') or 'A file'} isn't there any more"
                             + (" (uploaded copies are removed after a job): add the file again." if uploaded else "."))
    new = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = os.path.join(C.JOBS, new)
    os.makedirs(d)
    job = dict(old, created=time.time(), delete_sources=False)   # never deletes anything when run again by hand
    json.dump(job, open(os.path.join(d, "job.json"), "w", encoding="utf-8"))
    C.Log(d).stage("Waiting in queue", status="queued")
    with LOCK:
        QUEUE.append(new)
    return new


# ---- watched folder: new files that appear in it become ZIMs, with the active profile's options
WATCH = {"error": "", "folder": "", "sizes": {}}
WATCH_STATE = os.path.join(C.HOME, "watch.json")   # what was already there: hashes only, no file names
PARTIAL = (".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload", ".aria2", ".!ut")


WATCH_VERSION = 2   # v1 keys included the modified time: an old file merely touched (re-synced, say) looked new


def _file_key(path, st):
    return hashlib.sha1(f"{path}|{st.st_size}".encode()).hexdigest()[:20]


def watch_once():
    """One look at the watched folder; queues a job for each finished new file. Called every few seconds."""
    from zc import formats, settings
    st = settings.load()
    w = st["watch"]
    if not w["on"] or not w["folder"]:
        WATCH.update(error="", folder="")
        return []
    folder = C.to_wsl(w["folder"])
    if not os.path.isdir(folder):
        WATCH.update(error=f"The watched folder doesn't exist: {w['folder']}", folder="")
        return []
    WATCH.update(error="", folder=w["folder"])
    fid = hashlib.sha1(os.path.realpath(folder).encode()).hexdigest()[:20]
    try:
        state = json.load(open(WATCH_STATE, encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    names = [n for n in os.listdir(folder) if not n.startswith(".") and not n.lower().endswith(PARTIAL)]
    files = {}
    for n in names:
        p = os.path.join(folder, n)
        try:
            s = os.stat(p)
        except OSError:
            continue
        if os.path.isfile(p):
            files[p] = (n, s, _file_key(p, s))
    if state.get("folder") != fid or state.get("v") != WATCH_VERSION:
        # a newly watched folder: what's already in it stays as it is, only files added from now on are converted
        state = {"v": WATCH_VERSION, "folder": fid, "seen": sorted(k for _, _, k in files.values())}
        json.dump(state, open(WATCH_STATE, "w", encoding="utf-8"))
        return []
    before = set(state.get("seen", []))
    present = {k for _, _, k in files.values()}
    seen, queued = before & present, []   # forget files that are gone; never drop one that's still there
    for p, (n, s, k) in files.items():
        if k in seen:
            continue
        if WATCH["sizes"].get(p) != s.st_size:  # still downloading? wait until its size holds still for a look
            WATCH["sizes"][p] = s.st_size
            continue
        WATCH["sizes"].pop(p, None)
        seen.add(k)
        if formats.kind(n) == "unsupported" or n.lower().endswith(".zim"):
            continue
        prof = st["profiles"][st["active"]]
        try:
            queued.append(new_job({"paths": [p], "mode": prof["mode"], "combine": True, "output_dir": prof["output_dir"],
                                   "profile": st["active"], "delete_sources": w["delete"]}))
        except ValueError:
            pass
    if seen != before:
        state["seen"] = sorted(seen)
        json.dump(state, open(WATCH_STATE, "w", encoding="utf-8"))
    return queued


def watcher():
    while True:
        time.sleep(5)
        try:
            watch_once()
        except Exception as e:  # noqa: BLE001  keep watching
            WATCH["error"] = f"Watching the folder failed: {e}"


def scheduler():
    """Runs the queued jobs one at a time. A job is taken off the queue and started under the lock, so it's never in
    neither place (Cancel, Remove and Clear history see it either waiting or running)."""
    while True:
        time.sleep(1)
        with LOCK:
            running = any(p.poll() is None for p in list(PROCS.values()))
            if running or not QUEUE or SETUP["state"] != "ready":
                continue
            jid = QUEUE.pop(0)
            d = os.path.join(C.JOBS, jid)
            try:
                with open(os.path.join(d, "runner.out"), "w") as out:
                    PROCS[jid] = subprocess.Popen([sys.executable, "-I", os.path.join(APP, "zc", "runner.py"), d],
                                                  stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            except OSError as e:  # its folder was removed meanwhile, or it couldn't start: skip it, keep scheduling
                print(f"job {jid} couldn't start: {e}", flush=True)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # the browser keeps no copies of jobs, settings or ZIM pages
        super().end_headers()

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            body = {}
        return body if isinstance(body, dict) else {}

    def _guard(self, changes):
        """Only ZIMaker's own window may use it. Other web pages open in the same browser can also send requests to
        127.0.0.1, so: the Host must be this server (a renamed host is someone else's page), and anything that changes
        something must carry X-ZIMaker, a header other sites' pages can't add without permission this server never
        gives (it answers no preflight requests)."""
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").lower()
        origin = self.headers.get("Origin")
        ok_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if host not in ok_hosts or (origin and origin.lower() not in {"http://" + h for h in ok_hosts}) or \
                (changes and self.headers.get("X-ZIMaker") != "1"):
            self._json({"error": "Not allowed: this request didn't come from ZIMaker's own window."}, 403)
            return False
        return True

    def do_GET(self):
        if not self._guard(False):
            return
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            b = open(os.path.join(APP, "ui.html"), "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/api/ping":
            # the launcher checks this just before opening a window, and viewer windows send it to stay open:
            # either way someone is about to use the app, so don't stop
            SEEN["t"] = time.time()
            self._json({"ok": True, "started": STARTED})
        elif u.path == "/api/settings":
            from zc import settings
            self._json(settings.load())
        elif u.path == "/api/tools":
            from zc import tools
            self._json(tools.status())
        elif u.path == "/viewer":
            self._file(os.path.join(APP, "viewer.html"), "text/html; charset=utf-8")
        elif u.path == "/api/zim/library":
            self._json({"items": library()})
        elif re.fullmatch(r"/api/zim/\w+/info", u.path):
            # the viewer asks for the title here rather than carrying it in its address (which the browser remembers)
            z = ZIMS.get(u.path.split("/")[3])
            self._json({"title": z["title"], "private": private()} if z else {"error": "This ZIM isn't open any more."},
                       200 if z else 404)
        elif u.path.startswith("/api/zim/") and u.path.endswith("/search"):
            zid = u.path.split("/")[3]
            q = urllib.parse.parse_qs(u.query)
            self._json(zim_search(zid, (q.get("q") or [""])[0], (q.get("kind") or ["full"])[0]))
        elif u.path.startswith("/zim/"):
            parts = u.path.split("/", 3)
            self._send_zim(parts[2] if len(parts) > 2 else "", urllib.parse.unquote(parts[3]) if len(parts) > 3 else "")
        elif u.path == "/api/update":
            from zc import update
            force = urllib.parse.parse_qs(u.query).get("force") == ["1"]
            self._json(update.check(force))
        elif u.path == "/api/status":
            SEEN["t"] = time.time()
            ids = sorted(os.listdir(C.JOBS), reverse=True) if os.path.isdir(C.JOBS) else []
            jobs = [j for j in (job_summary(i) for i in ids[:30]) if j]
            self._json({"setup": SETUP, "jobs": jobs, "default_output": DEFAULTS.get("output", ""),
                        "windows": C.IS_WSL, "watch": {"folder": WATCH["folder"], "error": WATCH["error"]},
                        "downloads": DEFAULTS.get("downloads", "")})
        else:
            self._json({"error": "not found"}, 404)

    def _file(self, path, ctype):
        b = open(path, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _send_zim(self, zid, zpath):
        """Serve one entry of an opened ZIM. Scripts inside ZIM content are switched off (CSP sandbox), so a
        saved website can't reach ZIMaker's own controls; byte ranges let audio and video seek."""
        z = ZIMS.get(zid)
        if not z:
            return self._json({"error": "This ZIM isn't open any more. Open it again from ZIMaker."}, 404)
        a = z["archive"]
        try:
            if not zpath:
                e = a.main_entry
            else:
                e = a.get_entry_by_path(zpath)
            if e.is_redirect:
                e = e.get_redirect_entry()
                if e.path != zpath:
                    self.send_response(302)
                    self.send_header("Location", f"/zim/{zid}/" + urllib.parse.quote(e.path))
                    self.end_headers()
                    return
            item = e.get_item()
        except KeyError:
            b = b"<!DOCTYPE html><meta charset=utf-8><p style='font-family:sans-serif'>This page isn't in the ZIM file.</p>"
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        data = item.content
        total = item.size
        start, end, code = 0, total - 1, 200
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if m and total:
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)) if m.group(2) else total - 1, total - 1)
            else:
                start = max(0, total - int(m.group(2) or 0))
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{total}")
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", item.mimetype or "application/octet-stream")
        self.send_header("Content-Length", str(end - start + 1 if total else 0))
        self.send_header("Accept-Ranges", "bytes")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        # no scripts (sandbox), and nothing loaded from the internet: a remote picture or font would tell the
        # network what is being read (links still open, in the browser, when clicked)
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline' data: blob:; "
                                                    "sandbox allow-same-origin allow-popups allow-popups-to-escape-sandbox")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if total:
            self.wfile.write(bytes(data[start:end + 1]))

    def do_PUT(self):
        if not self._guard(True):
            return
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/upload":
            return self._json({"error": "not found"}, 404)
        from zc import formats
        name = os.path.basename(urllib.parse.parse_qs(u.query).get("name", ["upload"])[0])
        if formats.kind(name) == "unsupported" and not name.lower().endswith(".zim"):
            return self._json({"error": formats.why_unsupported(name)}, 400)
        d = os.path.join(C.UPLOADS, uuid.uuid4().hex[:12])
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        left = int(self.headers.get("Content-Length") or 0)
        with open(path, "wb") as f:
            while left > 0:
                chunk = self.rfile.read(min(left, 1 << 20))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        self._json({"path": path, "name": name, "size": os.path.getsize(path)})

    def do_POST(self):
        if not self._guard(True):
            return
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/inspect":
            from zc import ia, web
            out = []
            for line in self._body().get("text", "").splitlines():
                if not line.strip():
                    continue
                kind, val = classify(line)
                try:
                    if kind == "ia":
                        info = ia.inspect(val)
                        err = ("Borrow-only item: archive.org doesn't allow downloading it" if info["restricted"]
                               else "" if info["books"] or info.get("audio") else "No OCR text in this item")
                        if not info["books"] and info.get("audio") and not err:
                            info["title"] += " (audiobook)"
                        out.append({"input": line, "kind": "ia", "title": info["title"], "error": err,
                                    "books": [{"title": b["title"], "scans_gb": round(b["archive_size"] / 1e9, 2)}
                                              for b in info["books"]]})
                    elif kind == "lib":
                        from zc import library
                        out.append({"input": line, "kind": "lib", "title": library.describe(val), "error": ""})
                    elif kind == "web":
                        p = web.peek(val)
                        err = "" if p["html"] else "This address isn't a web page"
                        out.append({"input": line, "kind": "web", "title": p["title"], "error": err})
                    else:
                        out.append({"input": line, "error": val})
                except Exception as e:  # noqa: BLE001
                    msg = str(e)
                    if kind == "web" and ("urlopen" in msg or "timed out" in msg or "Errno" in msg):
                        msg = "Couldn't reach this website"
                    out.append({"input": line, "error": msg})
            return self._json({"items": out})
        if u.path == "/api/jobs":
            body = self._body()
            body.pop("delete_sources", None)   # deleting originals is only for the watched folder, never a web request
            try:
                return self._json({"id": new_job(body)})
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
            except Exception as e:  # noqa: BLE001
                return self._json({"error": f"Couldn't start the job: {e}"}, 400)
        m = re.fullmatch(r"/api/jobs/([\w-]+)/rerun", u.path)
        if m:
            try:
                return self._json({"id": rerun_job(m.group(1))})
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
        m = re.fullmatch(r"/api/jobs/([\w-]+)/(cancel|delete)", u.path)
        if m:
            jid, act = m.groups()
            with LOCK:
                if jid in QUEUE:
                    QUEUE.remove(jid)
            p = PROCS.get(jid)
            if p and p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
            d = os.path.join(C.JOBS, jid)
            if act == "cancel" and os.path.isdir(d):
                C.Log(d).stage("Cancelled", status="cancelled")
                shutil.rmtree(os.path.join(d, "work"), ignore_errors=True)
            if act == "delete":
                shutil.rmtree(d, ignore_errors=True)
            return self._json({"ok": True})
        if u.path == "/api/settings":
            from zc import settings
            return self._json(settings.save(self._body()))
        if u.path == "/api/zim/open":
            try:
                return self._json(zim_open(self._body().get("path", "")))
            except Exception as e:  # noqa: BLE001
                return self._json({"error": str(e)}, 400)
        if u.path == "/api/proxy/test":
            return self._json(proxy_test(self._body().get("proxy")))
        if u.path in ("/api/i2p/test", "/api/hyphanet/test", "/api/ipfs/test"):
            return self._json(node_test(u.path.split("/")[2], self._body().get("node")))
        if u.path == "/api/history/clear":
            return self._json({"ok": True, "removed": clear_history()})
        if u.path == "/api/zim/forget":
            forget(self._body().get("path", ""))
            return self._json({"ok": True})
        if u.path == "/api/update/apply":
            from zc import update
            if busy():
                return self._json({"busy": True})
            try:
                return self._json(update.apply())
            except Exception as e:  # noqa: BLE001
                return self._json({"error": f"The update couldn't be downloaded: {e}"}, 500)
        if u.path == "/api/quit":
            if busy() and not self._body().get("force"):
                return self._json({"busy": True})
            with LOCK:
                QUEUE.clear()
                procs = list(PROCS.items())
            for jid, p in procs:
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGTERM)
                    C.Log(os.path.join(C.JOBS, jid)).stage("Cancelled (ZIMaker was closed)", status="cancelled")
            self._json({"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.5), SERVER["srv"].shutdown()), daemon=True).start()
            return
        if u.path == "/api/open":
            # only ever a folder, or a ZIM file's folder: never anything that could be run or opened in a program
            p = str(self._body().get("path", "")).strip()
            try:
                wp = C.to_wsl(p) if p else ""
            except Exception:  # noqa: BLE001
                wp = ""
            if not wp or not (os.path.isdir(wp) or (wp.lower().endswith(".zim") and os.path.isfile(wp))):
                return self._json({"error": "That folder doesn't exist."}, 400)
            C.open_folder(p)
            return self._json({"ok": True})
        self._json({"error": "not found"}, 404)


DEFAULTS = {}


def main():
    os.makedirs(C.JOBS, exist_ok=True)
    os.makedirs(C.UPLOADS, exist_ok=True)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError:
        print(f"ZIMaker already seems to be running: open http://127.0.0.1:{PORT}")
        return
    SERVER["srv"] = srv
    DEFAULTS["output"] = C.default_output()
    # suggested folder to watch: the user's Downloads
    DEFAULTS["downloads"] = (C.windows_profile() + "\\Downloads") if C.IS_WSL and C.windows_profile() \
        else os.path.expanduser("~/Downloads")
    threading.Thread(target=setup_thread, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=watcher, daemon=True).start()
    if private():  # private mode: also catches anything left behind if ZIMaker didn't stop normally last time
        clear_history()
    # stopped from outside (WSL shutting down, the system, Ctrl+C): still clean up below
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=srv.shutdown, daemon=True).start())
    print(f"ZIMaker is running at http://127.0.0.1:{PORT}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if private():
            with LOCK:
                procs = list(PROCS.values())
            for p in procs:  # cancelled jobs: wait until they've exited, so their folders can go too
                try:
                    p.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    p.kill()
            clear_history()


if __name__ == "__main__":
    main()
