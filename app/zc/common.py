"""Shared helpers: paths, logging/progress, downloads, text cleanup, filenames."""
import functools
import html
import http.client
import json
import ntpath
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

HOME = os.path.expanduser("~/.zimaker")
_OLD_HOME = os.path.expanduser("~/.zim-creator")  # before the rename to ZIMaker: keep libraries and job history
if not os.path.exists(HOME) and os.path.isdir(_OLD_HOME):
    try:
        os.rename(_OLD_HOME, HOME)
    except OSError:
        pass
LIB = os.path.join(HOME, "lib")            # Python wheels unpacked here (libzim, Pillow, numpy, PyMuPDF)
TOOLS = os.path.join(HOME, "zim-tools")    # openZIM zim-tools (zimcheck)
JOBS = os.path.join(HOME, "jobs")
UPLOADS = os.path.join(HOME, "uploads")
UA = {"User-Agent": "zimaker/1.0 (personal archiving tool)"}

# How many pages are processed at once. WSL often has only ~3 GB of RAM, so keep this modest.
WORKERS = max(1, min(3, (os.cpu_count() or 2) - 1))
BATCH = 24


def use_lib():
    if LIB not in sys.path:
        sys.path.insert(0, LIB)


class Log:
    """Writes log.txt and state.json into a job folder; the web UI reads both."""

    def __init__(self, job_dir):
        self.dir = job_dir
        os.makedirs(job_dir, exist_ok=True)

    def __call__(self, msg):
        line = time.strftime("%H:%M:%S  ") + str(msg)
        with open(os.path.join(self.dir, "log.txt"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line, flush=True)

    def stage(self, text, done=None, total=None, status="running"):
        tmp = os.path.join(self.dir, "state.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"status": status, "stage": text, "done": done, "total": total, "t": time.time()}, f)
        os.replace(tmp, os.path.join(self.dir, "state.json"))


# ---------------------------------------------------------------- proxy
# Every download (websites, archive.org, libraries, updates) goes through the proxy chosen in Settings, if any.
# With a proxy chosen, nothing connects directly: if the proxy is down or misconfigured, downloads fail instead
# of quietly going around it. Names are looked up by the proxy too (SOCKS: remote DNS; curl: socks5h).
PROXY = {}  # the proxy in use: {"type": "http" | "socks5", "host", "port", "user", "password"}; empty = none


def clean_proxy(p):
    p = p if isinstance(p, dict) else {}
    try:
        port = int(p.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    return {"type": p.get("type") if p.get("type") in ("off", "http", "socks5") else "off",
            "host": str(p.get("host") or "").strip()[:255], "port": port if 0 < port < 65536 else 0,
            "user": str(p.get("user") or "")[:255], "password": str(p.get("password") or "")[:255]}


def _proxy_problem(p):
    if not re.fullmatch(r"[A-Za-z0-9.\-]+|\[[0-9A-Fa-f:.]+\]", p["host"]):
        return "its address is missing or not valid"
    if not p["port"]:
        return "its port is missing"
    return None


def _recv(s, n):
    b = b""
    while len(b) < n:
        chunk = s.recv(n - len(b))
        if not chunk:
            raise OSError("the SOCKS proxy closed the connection")
        b += chunk
    return b


def _socks_connect(p, host, port, timeout):
    """A connection to host:port through a SOCKS5 proxy. The proxy looks up the name, so no DNS leaks."""
    s = socket.create_connection((p["host"].strip("[]"), p["port"]), timeout)
    try:
        login = bool(p["user"])
        s.sendall(b"\x05\x01" + (b"\x02" if login else b"\x00"))
        method = _recv(s, 2)[1]
        if method == 2 and login:
            u, pw = p["user"].encode()[:255], p["password"].encode()[:255]
            s.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(pw)]) + pw)
            if _recv(s, 2)[1] != 0:
                raise OSError("the SOCKS proxy didn't accept the username or password")
        elif method != 0:
            raise OSError("the SOCKS proxy wants a login (set a username and password in Settings)")
        name = host.strip("[]").encode("idna")
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name + struct.pack(">H", port))
        reply = _recv(s, 4)
        if reply[1] != 0:
            why = {2: "not allowed by the proxy", 3: "network unreachable", 4: "host unreachable",
                   5: "connection refused", 6: "timed out"}.get(reply[1], f"SOCKS error {reply[1]}")
            raise OSError(f"the proxy couldn't reach {host} ({why})")
        _recv(s, {1: 4, 4: 16}.get(reply[3]) or _recv(s, 1)[0])  # the address the proxy bound: not needed
        _recv(s, 2)
        return s
    except BaseException:
        s.close()
        raise


class _SocksHTTP(http.client.HTTPConnection):
    def __init__(self, *a, proxy, **kw):
        super().__init__(*a, **kw)
        self.proxy = proxy

    def connect(self):
        self.sock = _socks_connect(self.proxy, self.host, self.port, self.timeout)


class _SocksHTTPS(http.client.HTTPSConnection):
    def __init__(self, *a, proxy, **kw):
        super().__init__(*a, **kw)
        self.proxy = proxy

    def connect(self):
        sock = _socks_connect(self.proxy, self.host, self.port, self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _SocksHandler(urllib.request.HTTPHandler):
    def __init__(self, proxy):
        super().__init__()
        self.proxy = proxy

    def http_open(self, req):
        return self.do_open(lambda host, **kw: _SocksHTTP(host, proxy=self.proxy, **kw), req)


class _SocksHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, proxy):
        super().__init__()
        self.proxy = proxy

    def https_open(self, req):
        return self.do_open(lambda host, **kw: _SocksHTTPS(host, proxy=self.proxy, **kw), req, context=self._context)


class _Refuse(urllib.request.BaseHandler):
    """Runs before the normal handlers: refuses what would otherwise connect directly despite the proxy."""
    handler_order = 100

    def __init__(self, why=None):
        self.why = why

    def _refuse(self, req):
        raise urllib.error.URLError(f"the proxy can't be used: {self.why or 'it only carries web addresses'}. "
                                    "Check ⚙ Settings → Connection and privacy")

    def ftp_open(self, req):
        return self._refuse(req)

    def http_open(self, req):
        return self._refuse(req) if self.why else None

    https_open = http_open


def proxy_opener(p):
    """A urllib opener that sends everything through proxy p (and nothing around it)."""
    if p["type"] == "off":
        return urllib.request.build_opener()
    why = _proxy_problem(p)
    if why:
        return urllib.request.build_opener(_Refuse(why))
    if p["type"] == "http":
        login = (urllib.parse.quote(p["user"], safe="") + ":" + urllib.parse.quote(p["password"], safe="") + "@"
                 if p["user"] else "")
        url = f"http://{login}{p['host']}:{p['port']}"
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": url, "https": url}), _Refuse())
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _SocksHandler(p), _SocksHTTPSHandler(p),
                                       _Refuse())


def net_init(p=None):
    """Use the proxy from settings.json (or p) for every download this process makes."""
    if p is None:
        try:
            p = json.load(open(os.path.join(HOME, "settings.json"), encoding="utf-8")).get("proxy")
        except (OSError, ValueError, AttributeError):
            p = None
    p = clean_proxy(p)
    PROXY.clear()
    if p["type"] != "off":
        PROXY.update(p)
        os.environ.pop("no_proxy", None), os.environ.pop("NO_PROXY", None)  # urllib would go around the proxy for these
    urllib.request.install_opener(proxy_opener(p))


def _proxy_url():
    scheme = "socks5h" if PROXY["type"] == "socks5" else "http"
    login = (urllib.parse.quote(PROXY["user"], safe="") + ":" + urllib.parse.quote(PROXY["password"], safe="") + "@"
             if PROXY["user"] else "")
    return f"{scheme}://{login}{PROXY['host']}:{PROXY['port']}"


def proxy_env():
    """Environment for helper programs (Pandoc, LibreOffice) that may fetch pictures a document links to:
    the proxy, if one is set (None: the normal environment)."""
    if not PROXY:
        return None
    env = dict(os.environ)
    url = _proxy_url() if not _proxy_problem(PROXY) else "http://127.0.0.1:9"  # unusable proxy: fetch nothing
    for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        env[k] = url
    env.pop("no_proxy", None), env.pop("NO_PROXY", None)
    return env


# ---------------------------------------------------------------- network

def get_json(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.load(r)


def get_text(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _download_py(url, dest, log, label, size):
    """Resumable download without curl (for systems that don't have it)."""
    for attempt in range(8):
        have = os.path.getsize(dest) if os.path.exists(dest) else 0
        req = urllib.request.Request(url, headers=dict(UA, **({"Range": f"bytes={have}-"} if have else {})))
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(dest, "ab" if r.status == 206 else "wb") as f:
                last = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        return dest
                    f.write(chunk)
                    if log and size and time.time() - last > 5:
                        log.stage(f"Downloading {label}", f.tell() // 1_000_000, size // 1_000_000)
                        last = time.time()
        except urllib.error.HTTPError as e:
            if e.code == 416:  # already complete
                return dest
            if attempt == 7:
                raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 7:
                raise
        time.sleep(10)
    return dest


def download(url, dest, log=None, label="", size=0):
    """Resumable download (curl if available); a .ok marker records completion."""
    if os.path.exists(dest + ".ok"):
        return dest
    if PROXY and _proxy_problem(PROXY):
        raise RuntimeError(f"The proxy can't be used: {_proxy_problem(PROXY)}. Check ⚙ Settings → Connection and privacy.")
    if not shutil.which("curl"):
        _download_py(url, dest, log, label, size)
        open(dest + ".ok", "w").close()
        return dest
    for attempt in (1, 2):
        cmd = ["curl", "-sSL", "--fail", "--retry", "8", "--retry-delay", "10", "-C", "-", "-o", dest, url]
        if PROXY:  # same proxy as everything else; socks5h: the proxy looks up names
            cmd[1:1] = ["--proxy", _proxy_url()]
        p = subprocess.Popen(cmd)
        last = 0
        while p.poll() is None:
            time.sleep(2)
            if log and size and time.time() - last > 5:
                have = os.path.getsize(dest) if os.path.exists(dest) else 0
                log.stage(f"Downloading {label}", have // 1_000_000, size // 1_000_000)
                last = time.time()
        if p.returncode == 0:
            open(dest + ".ok", "w").close()
            return dest
        if attempt == 1 and os.path.exists(dest):
            os.remove(dest)  # a stale partial file can make resume fail; start over once
    raise RuntimeError(f"Download failed (curl exit {p.returncode}): {url}")


# ---------------------------------------------------------------- text

def ocr_html(t):
    """Plain OCR/PDF text -> simple paragraphs."""
    t = re.sub(r"(\w)-\n(?=[a-z])", r"\1", t)  # rejoin words hyphenated at line ends
    paras = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
    return "".join(f"<p>{html.escape(' '.join(p.split()))}</p>" for p in paras)


def running_head(t):
    """Leading run of capitalised words on a page (e.g. 'AKA HILLS—AKENSIDE'), minus page numbers."""
    first = next((ln.strip() for ln in t.splitlines() if ln.strip()), "")
    head = []
    for tok in first.split()[:12]:
        if any(ch.islower() for ch in tok):
            break
        head.append(tok)
    while head and re.fullmatch(r"[\d.,]+", head[-1]):
        head.pop()
    while head and re.fullmatch(r"[\d.,]+", head[0]):
        head.pop(0)
    if len(head) > 1 and len(head[-1]) == 1:
        head.pop()
    hw = " ".join(head)
    return hw if sum(ch.isalpha() for ch in hw) >= 3 else ""


def first(v, default=""):
    if isinstance(v, list):
        v = v[0] if v else default
    return str(v).strip() if v else default


def clip(s, n):
    """Shorten to at most n characters on a word boundary (ZIM metadata has hard limits)."""
    s = " ".join(str(s).split())
    if len(s) <= n:
        return s
    cut = s[:n - 1].rsplit(" ", 1)[0].rstrip(" ,;:-–—")
    return (cut or s[:n - 1]) + "…"


def slug(s, n=60):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    return (s[:n].strip("_") or "book")


def safe_filename(s, n=120):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s)
    s = " ".join(s.split()).strip(" .")
    return s[:n].strip(" .") or "book"


LANG3 = {"en": "eng", "english": "eng", "fr": "fra", "french": "fra", "de": "deu", "german": "deu",
         "es": "spa", "spanish": "spa", "it": "ita", "italian": "ita", "la": "lat", "latin": "lat",
         "pt": "por", "portuguese": "por", "nl": "nld", "dutch": "nld", "ru": "rus", "russian": "rus",
         "sv": "swe", "swedish": "swe", "da": "dan", "danish": "dan", "no": "nor", "norwegian": "nor",
         "pl": "pol", "polish": "pol", "el": "ell", "greek": "ell", "ja": "jpn", "japanese": "jpn",
         "zh": "zho", "chinese": "zho", "ar": "ara", "arabic": "ara", "hi": "hin", "hindi": "hin"}


def lang3(v):
    v = first(v).lower().split("-")[0].split("_")[0]
    if v in LANG3:
        return LANG3[v]
    return v if re.fullmatch(r"[a-z]{3}", v) else "eng"


# ---------------------------------------------------------------- paths
# On Windows the converter runs inside WSL and shows Windows paths; on Linux it runs natively.
# ZIMAKER_NATIVE=1 forces the native-Linux behaviour (for testing inside WSL).

IS_WSL = (os.environ.get("ZIMAKER_NATIVE") != "1"
          and (os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop") or "microsoft" in os.uname().release.lower()))


def to_wsl(p):
    """A path typed by the user -> a path this process can open."""
    p = p.strip().strip('"')
    if not p or p.startswith("/") or not IS_WSL:
        return os.path.expanduser(p)
    return subprocess.check_output(["wslpath", "-u", p], text=True).strip()


def to_win(p):
    """A path to show the user (Windows form when running under WSL)."""
    if not IS_WSL:
        return p
    try:
        return subprocess.check_output(["wslpath", "-w", p], text=True).strip()
    except Exception:
        return p


def default_output():
    if IS_WSL:
        prof = windows_profile()
        if prof:
            return prof + "\\Documents\\ZIMaker"
    docs = os.path.expanduser("~/Documents")
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "ZIMaker")


def open_folder(p):
    """Show a folder (or the folder holding a file) in the system file manager."""
    if IS_WSL:  # paths shown to the user are Windows paths here
        target = ntpath.dirname(p) if p.lower().endswith(".zim") else p
        subprocess.Popen(["explorer.exe", target], cwd="/mnt/c")
    else:
        target = os.path.dirname(p) if p.lower().endswith(".zim") else p
        subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@functools.lru_cache(maxsize=1)
def windows_profile():
    """C:\\Users\\<name> of the Windows user, via WSL interop (asked once; it's slow-ish)."""
    if not IS_WSL:
        return ""
    try:
        out = subprocess.check_output(["cmd.exe", "/c", "echo %USERPROFILE%"], cwd="/mnt/c",
                                      text=True, stderr=subprocess.DEVNULL).strip()
        if out and "%" not in out:
            return out
    except Exception:
        pass
    return ""


net_init()  # every process (server, jobs, setup) starts with the proxy chosen in Settings
