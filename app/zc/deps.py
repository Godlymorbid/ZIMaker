"""First-run setup: fetch the Python libraries and openZIM tools into ~/.zimaker (no sudo needed).

Wheels come from PyPI and are unpacked straight into LIB; zim-tools comes from download.openzim.org.
"""
import glob
import importlib
import json
import os
import platform
import re
import sys
import tarfile
import urllib.request
import zipfile

from . import common as C

PACKAGES = {"libzim": "libzim", "PIL": "pillow", "numpy": "numpy", "pymupdf": "pymupdf", "mobi": "mobi"}
# Versions tested with ZIMaker. libzim 4.0 changed its API the day it came out (Oct 2026) and broke every build on
# fresh installs, so new installs get a tested version instead of whatever is newest. (Code works with 3.13 and 4.0.)
PINS = {"libzim": "4.0.0"}
PANDOC = os.path.join(C.HOME, "pandoc")  # Pandoc converts DOCX, ODT, RTF, Markdown, PPTX, XLSX, ... to EPUB


def _wheel_url(project):
    ver = sys.version_info[:2]
    tag = f"cp{ver[0]}{ver[1]}"
    arch = platform.machine()  # x86_64 on Intel/AMD PCs, aarch64 on ARM (Snapdragon) PCs
    pin = PINS.get(project)
    d = C.get_json(f"https://pypi.org/pypi/{project}/{pin}/json" if pin else f"https://pypi.org/pypi/{project}/json")
    for f in d["urls"]:
        n = f["filename"]
        if not n.endswith(".whl"):
            continue
        if n.endswith("-none-any.whl") and "py3" in n:  # pure-Python wheel: works everywhere
            return f["url"], n
        if arch not in n or "manylinux" not in n:
            continue
        m = re.search(r"-cp3(\d+)-abi3-", n)
        if f"{tag}-{tag}" in n or (m and int(m.group(1)) <= ver[1]):
            return f["url"], n
    raise RuntimeError(f"No Linux {arch} wheel of {project} for Python {ver[0]}.{ver[1]}")


def _install(project, log):
    url, name = _wheel_url(project)
    log(f"Installing {project} ({name})")
    os.makedirs(C.LIB, exist_ok=True)
    path = os.path.join(C.LIB, name)
    urllib.request.urlretrieve(url, path)
    with zipfile.ZipFile(path) as z:
        z.extractall(C.LIB)
    os.remove(path)


def _importable(module):
    C.use_lib()
    importlib.invalidate_caches()
    try:
        importlib.import_module(module)
        return True, None
    except ModuleNotFoundError as e:
        return False, e.name
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def zimcheck_path():
    for pattern in (os.path.join(C.TOOLS, "*", "zimcheck"), os.path.expanduser("~/zimtools/*/zimcheck")):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


def _install_zimtools(log):
    page = C.get_text("https://download.openzim.org/release/zim-tools/")
    arch = {"x86_64": "x86_64", "aarch64": "aarch64"}.get(platform.machine(), platform.machine())
    names = sorted(set(re.findall(rf"zim-tools_linux-{arch}-[0-9.]+\.tar\.gz", page)),
                   key=lambda s: [int(x) for x in re.findall(r"\d+", s.split("-")[-1])])
    if not names:
        raise RuntimeError("could not find a zim-tools release")
    log(f"Installing openZIM {names[-1]} (for zimcheck validation)")
    os.makedirs(C.TOOLS, exist_ok=True)
    dest = os.path.join(C.TOOLS, names[-1])
    C.download("https://download.openzim.org/release/zim-tools/" + names[-1], dest)
    with tarfile.open(dest) as t:
        t.extractall(C.TOOLS)
    os.remove(dest)
    os.remove(dest + ".ok")


def enable_heif(log=print):
    """Let Pillow open iPhone photos (HEIC/HEIF) and AVIF: installs pillow-heif from PyPI the first time."""
    ok, _ = _importable("pillow_heif")
    if not ok:
        try:
            _install("pillow-heif", log)
        except Exception as e:  # noqa: BLE001
            log(f"couldn't add HEIC/AVIF support ({e})")
            return False
        ok, _ = _importable("pillow_heif")
    if ok:
        import pillow_heif
        pillow_heif.register_heif_opener()
        if hasattr(pillow_heif, "register_avif_opener"):
            try:
                pillow_heif.register_avif_opener()
            except Exception:  # noqa: BLE001  newer Pillow reads AVIF itself
                pass
    return ok


def pandoc_path():
    hits = sorted(glob.glob(os.path.join(PANDOC, "*", "bin", "pandoc")))
    return hits[-1] if hits else None


def _install_pandoc(log):
    rel = C.get_json("https://api.github.com/repos/jgm/pandoc/releases/latest")
    arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    asset = next((a for a in rel["assets"] if a["name"].endswith(f"-linux-{arch}.tar.gz")), None)
    if not asset:
        raise RuntimeError(f"no Pandoc build for {arch}")
    log(f"Installing Pandoc {rel['tag_name']} (document converter)")
    os.makedirs(PANDOC, exist_ok=True)
    dest = os.path.join(PANDOC, asset["name"])
    C.download(asset["browser_download_url"], dest)
    with tarfile.open(dest) as t:
        t.extractall(PANDOC)
    os.remove(dest)
    os.remove(dest + ".ok")


def ensure(log=print):
    """Install anything missing. Returns a status dict."""
    for module, project in PACKAGES.items():
        ok, missing = _importable(module)
        target = project
        for _ in range(4):  # the package itself, then any dependency it reports missing
            if ok:
                break
            _install(target, log)
            ok, missing = _importable(module)
            dep = (missing or "").split(".")[0]
            target = dep if dep and dep != module and re.fullmatch(r"[A-Za-z0-9_-]+", dep) else project
        if not ok:
            raise RuntimeError(f"Could not set up {project}: {missing}")
    if not pandoc_path():
        try:
            _install_pandoc(log)
        except Exception as e:  # noqa: BLE001  only needed for office/markup documents
            log(f"Pandoc not available ({e}); DOCX/ODT/RTF/Markdown and similar files can't be converted")
    if not zimcheck_path():
        try:
            _install_zimtools(log)
        except Exception as e:  # noqa: BLE001  validation is optional
            log(f"zimcheck not available ({e}); files will be checked with libzim only")
    return {"ok": True, "zimcheck": bool(zimcheck_path()), "pandoc": bool(pandoc_path())}


if __name__ == "__main__":
    print(json.dumps(ensure()))
