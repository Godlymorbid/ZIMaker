"""Which files ZIM Creator accepts, and how each kind is converted.

Office/markup documents go through Pandoc to EPUB, MOBI/AZW through the 'mobi' library, then the EPUB
path; page-based files (PDF, XPS, CBZ) through PyMuPDF; images become a gallery; ZIPs are unpacked.
"""
import html
import json
import os
import re
import shutil
import subprocess
import zipfile

from . import common as C

PANDOC_FORMATS = {
    ".docx": "docx", ".odt": "odt", ".fodt": "fodt", ".rtf": "rtf", ".md": "markdown", ".markdown": "markdown",
    ".html": "html", ".htm": "html", ".xhtml": "html", ".fb2": "fb2", ".ipynb": "ipynb", ".rst": "rst",
    ".tex": "latex", ".latex": "latex", ".org": "org", ".textile": "textile", ".adoc": "asciidoc",
    ".asciidoc": "asciidoc", ".typ": "typst", ".pptx": "pptx", ".xlsx": "xlsx", ".csv": "csv", ".tsv": "tsv",
    ".wiki": "mediawiki", ".mediawiki": "mediawiki", ".dbk": "docbook", ".opml": "opml",
}
EBOOK = {".epub"}
MOBI = {".mobi", ".azw", ".azw3", ".prc", ".kf8"}
PAGED = {".pdf", ".xps", ".oxps", ".cbz"}
TEXT = {".txt", ".text"}
IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".svg"}
ARCHIVES = {".zip"}
UNSUPPORTED = {
    ".doc": "Old Word (.doc) files aren't supported. Open it in Word and save it as .docx.",
    ".xls": "Old Excel (.xls) files aren't supported. Open it in Excel and save it as .xlsx.",
    ".ppt": "Old PowerPoint (.ppt) files aren't supported. Open it in PowerPoint and save it as .pptx.",
    ".djvu": "DjVu files aren't supported. Convert it to PDF first.",
    ".cbr": "CBR (RAR) comics aren't supported. Convert it to CBZ first.",
    ".rar": "RAR archives aren't supported. Unpack it, or re-pack it as .zip.",
    ".7z": "7z archives aren't supported. Unpack it, or re-pack it as .zip.",
    ".pages": "Apple Pages files aren't supported. Export it as Word or PDF.",
    ".key": "Apple Keynote files aren't supported. Export it as PowerPoint or PDF.",
    ".numbers": "Apple Numbers files aren't supported. Export it as Excel or PDF.",
}
SUPPORTED = set(PANDOC_FORMATS) | EBOOK | MOBI | PAGED | TEXT | IMAGES | ARCHIVES


def ext(name):
    return os.path.splitext(name)[1].lower()


def kind(name):
    e = ext(name)
    if e in IMAGES:
        return "image"
    if e in ARCHIVES:
        return "zip"
    if e in SUPPORTED:
        return "doc"
    return "unsupported"


def why_unsupported(name):
    return UNSUPPORTED.get(ext(name), f"{ext(name) or 'This'} files aren't supported.")


# ---------------------------------------------------------------- documents

def prepare_document(log, path, name, mode, vdir):
    """One document -> one volume dict."""
    from . import epub, pdf
    e = ext(name)
    stem = os.path.splitext(os.path.basename(name))[0]
    if e in EBOOK:
        return epub.prepare(log, path, mode, vdir, name)
    if e in PAGED:
        return pdf.prepare(log, path, mode, vdir, name)
    if e in MOBI:
        return _mobi(log, path, name, mode, vdir)
    if e in TEXT:
        return _text(log, path, stem)
    if e in PANDOC_FORMATS:
        return _pandoc(log, path, name, PANDOC_FORMATS[e], mode, vdir)
    raise RuntimeError(why_unsupported(name))


def _pandoc(log, path, name, fmt, mode, vdir):
    from . import deps, epub
    exe = deps.pandoc_path()
    if not exe:
        raise RuntimeError("Pandoc isn't installed, so this kind of document can't be converted.")
    stem = os.path.splitext(os.path.basename(name))[0]
    out = os.path.join(vdir, "converted.epub")
    meta = os.path.join(vdir, "meta.json")
    with open(meta, "w", encoding="utf-8") as f:
        json.dump({"title": stem}, f)  # used only when the document has no title of its own
    cmd = [exe, path, "-f", fmt, "-t", "epub3", "-o", out, "--toc", "--toc-depth=2", "--split-level=2",
           f"--metadata-file={meta}", f"--resource-path={os.path.dirname(path)}"]
    log(f"[{name}] converting with Pandoc ({fmt})")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError("Pandoc couldn't convert this file: " + (r.stderr.strip().splitlines() or ["unknown error"])[-1])
    vol = epub.prepare(log, out, mode, vdir, name)
    vol["source"] = os.path.basename(name)
    return vol


def _mobi(log, path, name, mode, vdir):
    C.use_lib()
    import mobi
    from . import epub, pdf
    log(f"[{name}] unpacking Kindle book")
    tmp, out = mobi.extract(path)
    try:
        oe = ext(out)
        if oe == ".epub":
            vol = epub.prepare(log, out, mode, vdir, name)
        elif oe == ".pdf":
            vol = pdf.prepare(log, out, mode, vdir, name)
        else:  # old-style MOBI unpacks to one HTML file plus images
            vol = _pandoc(log, out, name, "html", mode, vdir)
        vol["source"] = os.path.basename(name)
        return vol
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


CHAPTER = re.compile(r"^\s*(chapter|book|part|section|act|canto|letter|volume)\b[ .:]*[\w.\- ]{0,40}$", re.I)


def _text(log, path, stem):
    raw = open(path, "rb").read()
    text = ""
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    text = text.replace("\r\n", "\n")
    # split at chapter-like headings; otherwise into parts of ~4000 words
    parts, cur, title = [], [], "Start"
    for line in text.split("\n"):
        if CHAPTER.match(line) and sum(len(x.split()) for x in cur) > 200:
            parts.append((title, "\n".join(cur)))
            cur, title = [], line.strip()
        else:
            cur.append(line)
    parts.append((title, "\n".join(cur)))
    if len(parts) == 1:
        words = text.split("\n\n")
        parts, chunk, n = [], [], 0
        for para in words:
            chunk.append(para)
            n += len(para.split())
            if n > 4000:
                parts.append((f"Part {len(parts) + 1}", "\n\n".join(chunk)))
                chunk, n = [], 0
        if chunk:
            parts.append((f"Part {len(parts) + 1}" if parts else stem, "\n\n".join(chunk)))
    pages = [{"label": t, "head": "", "body": "".join(f"<p>{html.escape(p.strip())}</p>"
                                                       for p in re.split(r"\n\s*\n", body) if p.strip()),
              "image": None} for t, body in parts]
    log(f"[{stem}] {len(pages)} parts")
    return {"kind": "pages", "title": stem, "creator": "", "publisher": "", "date": "", "language": "eng",
            "source": os.path.basename(path), "pages": pages, "plain": True}


# ---------------------------------------------------------------- images

def prepare_gallery(log, items, title, vdir):
    """items: [(path, display name)] -> one volume of pictures, one per page."""
    from . import images
    os.makedirs(os.path.join(vdir, "img"), exist_ok=True)
    pages = []
    for i, (path, name) in enumerate(sorted(items, key=lambda x: _natural(x[1]))):
        e = ext(name)
        rel = f"img/{i}{e if e in ('.gif', '.svg') else '.webp'}"
        try:
            if e in (".gif", ".svg"):
                shutil.copyfile(path, os.path.join(vdir, rel))  # keep animation / vector as-is
            else:
                images.save_photo(path, os.path.join(vdir, rel))
        except Exception as ex:  # noqa: BLE001
            log(f"skipped {name}: {ex}")
            continue
        pages.append({"label": os.path.splitext(os.path.basename(name))[0], "head": "", "body": "", "image": rel})
        if i % 20 == 0:
            log.stage(f"Adding pictures: {title}", i, len(items))
    log(f"[{title}] {len(pages)} pictures")
    return {"kind": "pages", "title": title, "creator": "", "publisher": "", "date": "", "language": "eng",
            "source": f"{len(pages)} pictures", "pages": pages, "gallery": True}


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# ---------------------------------------------------------------- zip files

def prepare_zip(log, path, name, mode, vdir):
    """A ZIP holding a saved website -> site; only pictures -> gallery (comic); otherwise each file.
    Returns [(vdir, volume), ...]."""
    stem = os.path.splitext(os.path.basename(name))[0]
    root = os.path.join(vdir, "unzipped")
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if not n.endswith("/") and "__MACOSX" not in n
                 and not os.path.basename(n).startswith(".")]
        for n in names:  # extract safely (no absolute paths or ..)
            target = os.path.normpath(os.path.join(root, n))
            if not target.startswith(root + os.sep):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(n) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    names = [n for n in names if os.path.exists(os.path.normpath(os.path.join(root, n)))]
    index = sorted((n for n in names if os.path.basename(n).lower() in ("index.html", "index.htm")),
                   key=lambda n: n.count("/"))
    if index:
        log(f"[{name}] looks like a saved website (starts at {index[0]})")
        files = []
        for n in names:
            if mode == "none" and kind(n) == "image":
                continue
            files.append({"path": n, "disk": os.path.join("unzipped", n),
                          "mime": _mime(n), "title": _html_title(os.path.join(root, n)) if _mime(n) == "text/html" else ""})
        return [(vdir, {"kind": "site", "title": _html_title(os.path.join(root, index[0])) or stem, "creator": "",
                        "publisher": "", "date": "", "language": "eng", "source": os.path.basename(name),
                        "main": index[0], "files": files})]
    if names and all(kind(n) == "image" for n in names):
        if mode == "none":
            raise RuntimeError("This ZIP only contains pictures, so there's nothing to keep in text-only mode.")
        return [(vdir, prepare_gallery(log, [(os.path.join(root, n), n) for n in names], stem, vdir))]
    vols, k = [], 0
    for n in sorted(names, key=_natural):
        kd = kind(n)
        if kd not in ("doc",):
            continue
        k += 1
        sub = os.path.join(vdir, f"z{k}")
        os.makedirs(sub, exist_ok=True)
        try:
            vols.append((sub, prepare_document(log, os.path.join(root, n), n, mode, sub)))
        except Exception as ex:  # noqa: BLE001
            log(f"skipped {n}: {ex}")
    pics = [(os.path.join(root, n), n) for n in names if kind(n) == "image"]
    if pics and mode != "none":
        pdir = os.path.join(vdir, "pics")
        vols.append((pdir, prepare_gallery(log, pics, f"{stem}: pictures", pdir)))
    if not vols:
        raise RuntimeError("Nothing in this ZIP could be converted.")
    return vols


def _mime(n):
    import mimetypes
    e = ext(n)
    if e in (".html", ".htm", ".xhtml"):
        return "text/html"
    return {".webp": "image/webp", ".svg": "image/svg+xml", ".js": "application/javascript",
            ".woff2": "font/woff2", ".woff": "font/woff"}.get(e) or mimetypes.guess_type(n)[0] or "application/octet-stream"


def _html_title(p):
    try:
        m = re.search(r"<title[^>]*>(.*?)</title>", open(p, encoding="utf-8", errors="replace").read(200000), re.I | re.S)
        return " ".join(html.unescape(m.group(1)).split()) if m else ""
    except OSError:
        return ""
