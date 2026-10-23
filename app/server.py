"""ZIMaker local web app. Start with 'Start ZIMaker.cmd' (Windows) or: python3 -I app/server.py

Serves the UI on http://localhost:8765 and runs one conversion job at a time in a child process.
"""
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
IDLE_EXIT = 150                 # seconds without a window (and no job) before the app stops itself;
                                # a minimised window may only poll once a minute
SERVER = {}


def busy():
    return bool(QUEUE) or any(p.poll() is None for p in PROCS.values())


def watchdog():
    """Stop when the window has been closed and no job is running or queued."""
    while True:
        time.sleep(5)
        if time.time() - SEEN["t"] > IDLE_EXIT and not busy() and SETUP["state"] != "installing":
            print("Window closed and nothing running: stopping ZIMaker.", flush=True)
            SERVER["srv"].shutdown()
            return


def setup_thread():
    from zc import deps
    try:
        SETUP.update(state="installing", message="Setting up (first run downloads ~150 MB of libraries)…")
        st = deps.ensure(lambda m: (SETUP.__setitem__("message", m), print(m, flush=True)))
        SETUP.update(state="ready", message="", zimcheck=st["zimcheck"])
    except Exception as e:  # noqa: BLE001
        SETUP.update(state="error", message=f"Setup failed: {e}")


def classify(line):
    """('ia', item id) | ('web', url) | (None, error message) for one line of the links box."""
    from zc import ia
    s = line.strip()
    if re.match(r"https?://", s, re.I):
        host = urllib.parse.urlsplit(s).netloc.lower()
        if host.endswith("archive.org") and ia.item_id(s):
            return "ia", ia.item_id(s)
        return "web", s
    # bare text: a domain like example.com/page is a website; anything else an archive.org item ID
    if re.fullmatch(r"[\w-]+(\.[\w-]+)*\.[A-Za-z]{2,24}(/\S*)?", s) and not s.lower().startswith("archive.org"):
        return "web", "https://" + s
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
        with open(lp, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        log = "".join(ln for ln in lines[-200:] if not ln.startswith("Traceback") and not ln.startswith("  File"))
    return {"id": jid, "created": job.get("created", 0), "label": job.get("label", ""), "status": status,
            "stage": st.get("stage", "Waiting…"), "done": st.get("done"), "total": st.get("total"),
            "result": res, "log": log}


def scheduler():
    while True:
        time.sleep(1)
        with LOCK:
            busy = any(p.poll() is None for p in PROCS.values())
            if busy or not QUEUE or SETUP["state"] != "ready":
                continue
            jid = QUEUE.pop(0)
        d = os.path.join(C.JOBS, jid)
        out = open(os.path.join(d, "runner.out"), "w")
        PROCS[jid] = subprocess.Popen([sys.executable, "-I", os.path.join(APP, "zc", "runner.py"), d],
                                      stdout=out, stderr=subprocess.STDOUT, start_new_session=True)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path in ("/", "/index.html"):
            b = open(os.path.join(APP, "ui.html"), "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif u.path == "/api/ping":
            self._json({"ok": True})
        elif u.path == "/api/status":
            SEEN["t"] = time.time()
            ids = sorted(os.listdir(C.JOBS), reverse=True) if os.path.isdir(C.JOBS) else []
            jobs = [j for j in (job_summary(i) for i in ids[:30]) if j]
            self._json({"setup": SETUP, "jobs": jobs, "default_output": DEFAULTS.get("output", ""),
                        "windows": C.IS_WSL})
        else:
            self._json({"error": "not found"}, 404)

    def do_PUT(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/upload":
            return self._json({"error": "not found"}, 404)
        from zc import formats
        name = os.path.basename(urllib.parse.parse_qs(u.query).get("name", ["upload"])[0])
        if formats.kind(name) == "unsupported":
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
                               else "" if info["books"] else "No OCR text in this item")
                        out.append({"input": line, "kind": "ia", "title": info["title"], "error": err,
                                    "books": [{"title": b["title"], "scans_gb": round(b["archive_size"] / 1e9, 2)}
                                              for b in info["books"]]})
                    elif kind == "web":
                        p = web.peek(val)
                        err = ("This site's robots.txt asks tools like this not to download it" if not p["allowed"]
                               else "" if p["html"] else "This address isn't a web page")
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
            sources = []
            for line in body.get("links", "").splitlines():
                if line.strip():
                    kind, val = classify(line)
                    if kind == "ia":
                        sources.append({"type": "ia", "id": val})
                    elif kind == "web":
                        sources.append({"type": "web", "url": val, "name": val})
                    else:
                        return self._json({"error": f"{line.strip()}: {val}"}, 400)
            for f in body.get("files", []):
                if not os.path.abspath(f["path"]).startswith(C.UPLOADS):
                    return self._json({"error": "bad file"}, 400)
                sources.append({"type": "file", "path": f["path"], "name": f.get("name")})
            for p in body.get("paths", []):
                wp = C.to_wsl(p)
                if not os.path.exists(wp):
                    return self._json({"error": f"File not found: {p}"}, 400)
                sources.append({"type": "file", "path": wp, "name": os.path.basename(wp)})
            if not sources:
                return self._json({"error": "Add at least one web address or file."}, 400)
            out_dir = (body.get("output_dir") or "").strip()
            if not out_dir:
                return self._json({"error": "Choose an output folder."}, 400)
            jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
            d = os.path.join(C.JOBS, jid)
            os.makedirs(d)
            label = ", ".join((s.get("name") or s.get("id")) for s in sources)
            job = {"sources": sources, "mode": body.get("mode", "pictures"), "combine": bool(body.get("combine")),
                   "title": body.get("title", ""), "output_dir": out_dir, "created": time.time(),
                   "label": C.clip(label, 120),
                   "web": {"max_pages": int(body.get("max_pages") or 300),
                           "scope": body.get("scope") if body.get("scope") in ("section", "page") else "site"}}
            json.dump(job, open(os.path.join(d, "job.json"), "w", encoding="utf-8"))
            C.Log(d).stage("Waiting in queue", status="queued")
            with LOCK:
                QUEUE.append(jid)
            return self._json({"id": jid})
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
        if u.path == "/api/quit":
            if busy() and not self._body().get("force"):
                return self._json({"busy": True})
            with LOCK:
                QUEUE.clear()
            for jid, p in PROCS.items():
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGTERM)
                    C.Log(os.path.join(C.JOBS, jid)).stage("Cancelled (ZIMaker was closed)", status="cancelled")
            self._json({"ok": True})
            threading.Thread(target=lambda: (time.sleep(0.5), SERVER["srv"].shutdown()), daemon=True).start()
            return
        if u.path == "/api/open":
            C.open_folder(self._body().get("path", ""))
            return self._json({"ok": True})
        self._json({"error": "not found"}, 404)


DEFAULTS = {}


def main():
    os.makedirs(C.JOBS, exist_ok=True)
    os.makedirs(C.UPLOADS, exist_ok=True)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except OSError:
        print(f"ZIMaker already seems to be running: open http://localhost:{PORT}")
        return
    SERVER["srv"] = srv
    DEFAULTS["output"] = C.default_output()
    threading.Thread(target=setup_thread, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    print(f"ZIMaker is running at http://localhost:{PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
