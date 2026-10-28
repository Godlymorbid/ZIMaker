"""Which files ZIMaker accepts, and how each kind is converted.

  Office/markup documents       Pandoc -> EPUB -> EPUB path (old office formats go through LibreOffice first)
  MOBI/AZW                      'mobi' library -> EPUB path
  PDF, XPS, CBZ                 PyMuPDF (page by page)
  Pictures                      one gallery per job
  Audio and video               FFmpeg (see media.py)
  Saved web pages (MHTML)       a one-page website (see web.py)
  Emails (EML, MBOX)            one page per message (see emails.py)
  Archives (ZIP, TAR, 7Z, RAR)  unpacked: a saved website, a comic, or each file converted
  Windows Help (CHM)            unpacked with 7-Zip and kept as a browsable site
"""
import html
import json
import os
import re
import shutil
import subprocess
import tarfile
import zipfile

from . import common as C
from . import media, tools

PANDOC_FORMATS = {
    ".docx": "docx", ".odt": "odt", ".fodt": "fodt", ".rtf": "rtf", ".md": "markdown", ".markdown": "markdown",
    ".html": "html", ".htm": "html", ".xhtml": "html", ".fb2": "fb2", ".ipynb": "ipynb", ".rst": "rst",
    ".tex": "latex", ".latex": "latex", ".org": "org", ".textile": "textile", ".adoc": "asciidoc",
    ".asciidoc": "asciidoc", ".typ": "typst", ".pptx": "pptx", ".xlsx": "xlsx", ".csv": "csv", ".tsv": "tsv",
    ".wiki": "mediawiki", ".mediawiki": "mediawiki", ".dbk": "docbook", ".opml": "opml", ".t2t": "t2t",
    ".muse": "muse", ".pod": "pod", ".creole": "creole", ".dj": "djot", ".djot": "djot",
}
# older or less common office formats: LibreOffice converts them to something Pandoc / PyMuPDF reads
OFFICE_TEXT = {".doc", ".dot", ".docm", ".dotx", ".dotm", ".wpd", ".wps", ".sxw", ".abw", ".lwp", ".hwp"}
OFFICE_SHEET = {".xls", ".xlsm", ".xlsb", ".xlt", ".xltx", ".ods", ".fods", ".sxc"}
OFFICE_SLIDES = {".ppt", ".pps", ".ppsx", ".pptm", ".pot", ".potx", ".odp", ".fodp", ".sxi"}
OFFICE_DRAW = {".odg", ".fodg", ".vsd", ".vsdx", ".pub", ".sxd"}
OFFICE = OFFICE_TEXT | OFFICE_SHEET | OFFICE_SLIDES | OFFICE_DRAW
EBOOK = {".epub"}
MOBI = {".mobi", ".azw", ".azw3", ".prc", ".kf8"}
PAGED = {".pdf", ".xps", ".oxps", ".cbz"}
TEXT = {".txt", ".text", ".log", ".nfo"}
IMAGES = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".svg", ".avif",
          ".heic", ".heif", ".ico", ".tga", ".psd", ".jp2", ".j2k", ".pcx", ".ppm", ".pgm", ".pbm"}
MHTML = {".mhtml", ".mht"}
EMAIL = {".eml", ".mbox"}
CHM = {".chm"}
NATIVE_ARCHIVES = {".zip", ".tar", ".tgz", ".tar.gz", ".txz", ".tar.xz", ".tbz", ".tbz2", ".tar.bz2"}
TOOL_ARCHIVES = {".7z", ".rar", ".cbr", ".cb7"}
ARCHIVES = NATIVE_ARCHIVES | TOOL_ARCHIVES | CHM
UNSUPPORTED = {
    ".djvu": "DjVu files aren't supported. Convert it to PDF first (many free converters can).",
    ".pages": "Apple Pages files aren't supported. Export it as Word or PDF.",
    ".key": "Apple Keynote files aren't supported. Export it as PowerPoint or PDF.",
    ".numbers": "Apple Numbers files aren't supported. Export it as Excel or PDF.",
    ".warc": "Web archive (WARC) files aren't supported. openZIM's warc2zim tool converts them to ZIM.",
    ".zim": "That's already a ZIM file. Use “Open a ZIM file” to look inside it.",
}
DOCS = set(PANDOC_FORMATS) | OFFICE | EBOOK | MOBI | PAGED | TEXT | MHTML | EMAIL
SUPPORTED = DOCS | IMAGES | ARCHIVES | media.VIDEO_EXT | media.AUDIO_EXT | media.SUBTITLE_EXT


def ext(name):
    n = name.lower()
    for compound in (".tar.gz", ".tar.xz", ".tar.bz2"):
        if n.endswith(compound):
            return compound
    return os.path.splitext(n)[1]


def kind(name):
    e = ext(name)
    if e in IMAGES:
        return "image"
    if e in media.VIDEO_EXT or e in media.AUDIO_EXT:
        return "media"
    if e in media.SUBTITLE_EXT:
        return "subtitle"
    if e in ARCHIVES:
        return "archive"
    if e in DOCS:
        return "doc"
    return "unsupported"


def why_unsupported(name):
    return UNSUPPORTED.get(ext(name), f"{ext(name) or 'This'} files aren't supported.")


# ---------------------------------------------------------------- documents

def prepare_document(log, path, name, mode, vdir):
    """One document -> one volume dict."""
    from . import emails, epub, pdf, web
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
    if e in OFFICE:
        return _office(log, path, name, mode, vdir)
    if e in MHTML:
        return web.from_mhtml(log, path, name, mode, vdir)
    if e in EMAIL:
        return emails.prepare(log, path, name, mode, vdir)
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


def _office(log, path, name, mode, vdir):
    """Old/other office formats -> DOCX / XLSX (then Pandoc) or PDF (slides, drawings) with LibreOffice."""
    from . import pdf
    soffice = tools.need("libreoffice", "This kind of office file")
    e = ext(name)
    target = "docx" if e in OFFICE_TEXT else "xlsx" if e in OFFICE_SHEET else "pdf"
    out_dir = os.path.join(vdir, "office")
    os.makedirs(out_dir, exist_ok=True)
    log(f"[{name}] converting with LibreOffice")
    r = subprocess.run([soffice, f"-env:UserInstallation=file://{os.path.join(vdir, 'lo-profile')}", "--headless",
                        "--convert-to", target, "--outdir", out_dir, path],
                       capture_output=True, text=True, timeout=1800)
    made = [f for f in os.listdir(out_dir) if f.lower().endswith("." + target)]
    if not made:
        raise RuntimeError("LibreOffice couldn't convert this file: "
                           + ((r.stderr or r.stdout).strip().splitlines() or ["unknown error"])[-1][:300])
    out = os.path.join(out_dir, made[0])
    vol = pdf.prepare(log, out, mode, vdir, name) if target == "pdf" else _pandoc(log, out, name, target, mode, vdir)
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


def _decode(raw):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _text(log, path, stem):
    text = _decode(open(path, "rb").read()).replace("\r\n", "\n")
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
    from . import deps, images
    if any(ext(n) in (".heic", ".heif", ".avif") for _, n in items):
        deps.enable_heif(log)  # iPhone photos (and AVIF where Pillow lacks it)
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
    if not pages:
        raise RuntimeError("None of the pictures could be read.")
    log(f"[{title}] {len(pages)} pictures")
    return {"kind": "pages", "title": title, "creator": "", "publisher": "", "date": "", "language": "eng",
            "source": f"{len(pages)} pictures", "pages": pages, "gallery": True}


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# ---------------------------------------------------------------- archives

def _extract(log, path, name, root):
    """Unpack any supported archive into root (safely: nothing may land outside it)."""
    e = ext(name)
    os.makedirs(root, exist_ok=True)
    if e == ".zip":
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                target = os.path.normpath(os.path.join(root, n))
                if n.endswith("/") or not target.startswith(root + os.sep):
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(n) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    elif e in NATIVE_ARCHIVES:
        with tarfile.open(path) as t:
            t.extractall(root, filter="data")  # the 'data' filter refuses links and paths outside root
    else:
        tool = tools.need("7zip", "This kind of archive")
        if os.path.basename(tool) == "unar":
            cmd = [tool, "-q", "-f", "-D", "-o", root, path]
        else:
            cmd = [tool, "x", "-y", "-bd", f"-o{root}", path]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        unar = tools.find("7zip", "unar")
        if r.returncode != 0 and unar and os.path.basename(tool) != "unar":  # e.g. RAR with a 7-Zip built without it
            r = subprocess.run([unar, "-q", "-f", "-D", "-o", root, path], capture_output=True, text=True, timeout=3600)
        if r.returncode != 0:
            raise RuntimeError("This archive couldn't be unpacked: "
                               + ((r.stderr or r.stdout).strip().splitlines() or ["unknown error"])[-1][:300])
        # 7-Zip may still have written something outside root if the archive was malicious; it doesn't by
        # default (it strips absolute paths and ..), but drop anything that isn't a regular file inside root
    names = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            full = os.path.join(dirpath, f)
            if os.path.islink(full) or not os.path.realpath(full).startswith(os.path.realpath(root) + os.sep):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if "__MACOSX" in rel or os.path.basename(rel).startswith("."):
                continue
            names.append(rel)
    return names


def prepare_archive(log, path, name, mode, vdir, quality="balanced"):
    """An archive holding a saved website -> site; only pictures -> gallery (comic); a CHM -> site;
    otherwise each file is converted. Returns [(vdir, volume), ...]."""
    stem = os.path.splitext(os.path.basename(name))[0]
    if stem.lower().endswith(".tar"):
        stem = stem[:-4]
    root = os.path.join(vdir, "unpacked")
    log(f"[{name}] unpacking")
    names = _extract(log, path, name, root)
    if not names:
        raise RuntimeError("This archive is empty.")

    if ext(name) in CHM:
        return [(vdir, _chm_site(log, root, names, stem, mode))]
    index = sorted((n for n in names if os.path.basename(n).lower() in ("index.html", "index.htm")),
                   key=lambda n: n.count("/"))
    if index:
        log(f"[{name}] looks like a saved website (starts at {index[0]})")
        return [(vdir, _site(root, names, index[0], _html_title(os.path.join(root, index[0])) or stem, name, mode))]
    if all(kind(n) == "image" for n in names):
        if mode == "none":
            raise RuntimeError("This archive only contains pictures, so there's nothing to keep in text-only mode.")
        return [(vdir, prepare_gallery(log, [(os.path.join(root, n), n) for n in names], stem, vdir))]

    vols, k = [], 0
    for n in sorted(names, key=_natural):
        if kind(n) != "doc":
            continue
        k += 1
        sub = os.path.join(vdir, f"z{k}")
        os.makedirs(sub, exist_ok=True)
        try:
            vols.append((sub, prepare_document(log, os.path.join(root, n), n, mode, sub)))
        except Exception as ex:  # noqa: BLE001
            log(f"skipped {n}: {ex}")
    for n in names:  # archives inside the archive
        if kind(n) == "archive":
            k += 1
            sub = os.path.join(vdir, f"z{k}")
            try:
                vols.extend(prepare_archive(log, os.path.join(root, n), n, mode, sub, quality))
            except Exception as ex:  # noqa: BLE001
                log(f"skipped {n}: {ex}")
    pics = [(os.path.join(root, n), n) for n in names if kind(n) == "image"]
    if pics and mode != "none":
        pdir = os.path.join(vdir, "pics")
        vols.append((pdir, prepare_gallery(log, pics, f"{stem}: pictures", pdir)))
    av = [(os.path.join(root, n), n) for n in names if kind(n) == "media"]
    if av and mode != "none":
        mdir = os.path.join(vdir, "media")
        try:
            subs = [(os.path.join(root, n), n) for n in names if kind(n) == "subtitle"]
            vols.append((mdir, media.prepare(log, av, f"{stem}: audio and video", mdir, quality, subs)))
        except Exception as ex:  # noqa: BLE001
            log(f"skipped the audio/video files: {ex}")
    if not vols:
        raise RuntimeError("Nothing in this archive could be converted.")
    return vols


def _site(root, names, main, title, source, mode):
    files = []
    for n in names:
        k = kind(n)
        if mode == "none" and k in ("image", "media"):
            continue
        m = _mime(n)
        files.append({"path": n, "disk": os.path.join("unpacked", n), "mime": m,
                      "title": _html_title(os.path.join(root, n)) if m == "text/html" else ""})
    return {"kind": "site", "title": title, "creator": "", "publisher": "", "date": "", "language": "eng",
            "source": os.path.basename(source), "main": main, "files": files}


def _chm_site(log, root, names, stem, mode):
    """A Windows Help file: its pages, with the start page from the project file or the contents (.hhc)."""
    lower = {n.lower(): n for n in names}
    main, title = None, stem
    for n in names:
        if n.lower().endswith(".hhp"):
            hhp = _decode(open(os.path.join(root, n), "rb").read())
            m = re.search(r"^Default topic=(.+)$", hhp, re.M | re.I)
            if m:
                main = lower.get(m.group(1).strip().replace("\\", "/").lower())
            m = re.search(r"^Title=(.+)$", hhp, re.M | re.I)
            if m:
                title = m.group(1).strip()
    if not main:
        for n in names:
            if n.lower().endswith(".hhc"):
                m = re.search(r'name="Local"\s+value="([^"]+)"', _decode(open(os.path.join(root, n), "rb").read()), re.I)
                if m:
                    main = lower.get(m.group(1).split("#")[0].replace("\\", "/").lower())
                    break
    pages = sorted((n for n in names if n.lower().endswith((".htm", ".html"))), key=lambda n: (n.count("/"), n))
    main = main or next((p for p in pages if os.path.basename(p).lower().startswith(("index", "default", "main"))),
                        pages[0] if pages else None)
    if not main:
        raise RuntimeError("This help file has no pages.")
    log(f"[{stem}] help file with {len(pages)} pages, starting at {main}")
    return _site(root, [n for n in names if not n.startswith("#") and not n.startswith("$")], main,
                 title or _html_title(os.path.join(root, main)) or stem, stem + ".chm", mode)


def _mime(n):
    import mimetypes
    e = ext(n)
    if e in (".html", ".htm", ".xhtml"):
        return "text/html"
    return {".webp": "image/webp", ".svg": "image/svg+xml", ".js": "application/javascript",
            ".woff2": "font/woff2", ".woff": "font/woff", ".vtt": "text/vtt", ".webm": "video/webm",
            ".mp4": "video/mp4", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".opus": "audio/ogg",
            ".m4a": "audio/mp4"}.get(e) or mimetypes.guess_type(n)[0] or "application/octet-stream"


def _html_title(p):
    try:
        m = re.search(r"<title[^>]*>(.*?)</title>", open(p, encoding="utf-8", errors="replace").read(200000), re.I | re.S)
        return " ".join(html.unescape(m.group(1)).split()) if m else ""
    except OSError:
        return ""
