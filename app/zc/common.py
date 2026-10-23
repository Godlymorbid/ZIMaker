"""Shared helpers: paths, logging/progress, downloads, text cleanup, filenames."""
import html
import json
import ntpath
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
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
    if not shutil.which("curl"):
        _download_py(url, dest, log, label, size)
        open(dest + ".ok", "w").close()
        return dest
    for attempt in (1, 2):
        cmd = ["curl", "-sSL", "--fail", "--retry", "8", "--retry-delay", "10", "-C", "-", "-o", dest, url]
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


def windows_profile():
    """C:\\Users\\<name> of the Windows user, via WSL interop."""
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
