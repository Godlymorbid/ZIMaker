"""Online libraries with their own download routes, so a plain link to a book gives a proper book:

  Project Gutenberg   gutenberg.org/ebooks/1342          the EPUB (with illustrations)
  Standard Ebooks     standardebooks.org/ebooks/...      their carefully made EPUB
  Wikisource          en.wikisource.org/wiki/Title       the work as EPUB, via Wikisource's own export
  OpenStax            openstax.org/details/books/...     the textbook's PDF
  LibriVox            librivox.org/<book>/               the audiobook (MP3s, from archive.org) + its Gutenberg text
  IIIF                any .../manifest(.json) link       page scans (and text, where the library has it) from the
                                                         many libraries that publish IIIF: Wellcome, Bodleian,
                                                         Stanford, Princeton, e-codices, ...

Everything downloads through common (so the proxy from Settings applies). The finished files go through the same
converters as files a user drops in (epub, pdf, media), so they look the same.
"""
import html
import json
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET

from . import common as C

# Libraries whose sites stop automated tools with a "checking your browser" (Cloudflare) page. ZIMaker doesn't try
# to get around that: it says so, and how to get the book anyway.
BLOCKED = {
    "loc.gov": "the Library of Congress", "gallica.bnf.fr": "Gallica", "biodiversitylibrary.org": "the Biodiversity Heritage Library",
    "hathitrust.org": "HathiTrust",
}


def _host(url):
    return urllib.parse.urlsplit(url).netloc.lower().split(":")[0]


def _on(host, domain):
    return host == domain or host.endswith("." + domain)


def detect(url):
    """What kind of library link this is ('gutenberg', 'standardebooks', ...), or None for an ordinary website."""
    host, path = _host(url), urllib.parse.urlsplit(url).path
    if _on(host, "gutenberg.org") and re.search(r"/(?:ebooks|files|cache/epub|etext)/(\d+)", path):
        return "gutenberg"
    if _on(host, "standardebooks.org") and re.match(r"/ebooks/[^/]+/[^/]+", path):
        return "standardebooks"
    if _on(host, "wikisource.org") and path.startswith("/wiki/") and len(path) > 6:
        return "wikisource"
    if _on(host, "openstax.org") and re.match(r"/(?:details/)?books/[^/]+", path):
        return "openstax"
    if _on(host, "librivox.org") and path.strip("/") and not path.startswith(("/search", "/api", "/pages", "/category", "/reader")):
        return "librivox"
    if re.search(r"/manifest(?:\.json)?/?$", path, re.I) or re.search(r"/iiif/.*manifest", path, re.I):
        return "iiif"
    if any(_on(host, d) for d in BLOCKED):
        return "blocked"
    return None


def describe(url):
    """A short description for the 'Check links' button (no network)."""
    kind = detect(url)
    if kind == "blocked":
        raise RuntimeError(_blocked_message(url))
    names = {"gutenberg": "Project Gutenberg book", "standardebooks": "Standard Ebooks book", "wikisource": "Wikisource work",
             "openstax": "OpenStax textbook (PDF)", "librivox": "LibriVox audiobook", "iiif": "IIIF book or manuscript (page scans)"}
    return names.get(kind, "")


def _blocked_message(url):
    lib = next(v for d, v in BLOCKED.items() if _on(_host(url), d))
    return (f"{lib[0].upper() + lib[1:]} blocks automated downloads (its site checks for a real browser), so ZIMaker can't "
            "fetch from it. Download the book in your browser (most items have a PDF or image download) and add the file "
            "to ZIMaker instead. Many of the same books are also on archive.org, which ZIMaker can fetch directly.")


def _fetch(url, dest):
    """Download to dest (resumable, through the proxy); returns dest."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    return C.download(url, dest)


def _document(log, path, name, mode, vdir):
    from . import formats
    return formats.prepare_document(log, path, name, mode, vdir)


# ---------------------------------------------------------------- ebooks

def _gutenberg_id(url):
    return re.search(r"/(?:ebooks|files|cache/epub|etext)/(\d+)", urllib.parse.urlsplit(url).path).group(1)


def gutenberg(log, n, mode, vdir):
    """Project Gutenberg book number n -> a volume (their EPUB; with illustrations unless text-only)."""
    dl = os.path.join(vdir, "download")
    tries = ([f"https://www.gutenberg.org/ebooks/{n}.epub.noimages"] if mode == "none" else
             [f"https://www.gutenberg.org/ebooks/{n}.epub3.images", f"https://www.gutenberg.org/ebooks/{n}.epub.images"])
    tries.append(f"https://www.gutenberg.org/ebooks/{n}.epub.noimages")
    log(f"[Project Gutenberg #{n}] downloading the EPUB")
    last = None
    for url in tries:
        try:
            path = _fetch(url, os.path.join(dl, f"pg{n}.epub"))
            break
        except Exception as e:  # noqa: BLE001
            last = e
            for x in (os.path.join(dl, f"pg{n}.epub"), os.path.join(dl, f"pg{n}.epub.ok")):
                if os.path.exists(x):
                    os.remove(x)
    else:
        raise RuntimeError(f"Project Gutenberg book {n} couldn't be downloaded (does it exist?): {last}")
    vol = _document(log, path, f"pg{n}.epub", mode, vdir)
    vol["source"] = f"gutenberg.org/ebooks/{n}"
    return vol


def standardebooks(log, url, mode, vdir):
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    parts = path.split("/")[2:]          # /ebooks/author/title[/translator...]
    page = C.get_text("https://standardebooks.org/ebooks/" + "/".join(parts))
    links = [h for h in re.findall(r'href="([^"]+\.epub)"', page) if not h.endswith((".kepub.epub", "_advanced.epub"))]
    if not links:
        raise RuntimeError("Couldn't find the EPUB download on this Standard Ebooks page.")
    log(f"[Standard Ebooks] downloading {os.path.basename(links[0])}")
    epub = _fetch(urllib.parse.urljoin("https://standardebooks.org/", html.unescape(links[0])) + "?source=download",
                  os.path.join(vdir, "download", os.path.basename(links[0])))
    vol = _document(log, epub, os.path.basename(links[0]), mode, vdir)
    vol["source"] = "standardebooks.org" + path
    return vol


def wikisource(log, url, mode, vdir):
    host = _host(url)
    lang = host.split(".")[0] if host.count(".") >= 2 and host.split(".")[0] not in ("www", "m") else "en"
    title = urllib.parse.unquote(urllib.parse.urlsplit(url).path[len("/wiki/"):])
    log(f"[Wikisource] exporting “{title.replace('_', ' ')}” as EPUB")
    q = urllib.parse.urlencode({"format": "epub", "lang": lang, "page": title})
    epub = _fetch(f"https://ws-export.wmcloud.org/?{q}", os.path.join(vdir, "download", "wikisource.epub"))
    vol = _document(log, epub, "wikisource.epub", mode, vdir)
    vol["source"] = f"{host}/wiki/{title}"
    return vol


def openstax(log, url, mode, vdir):
    slug = re.match(r"/(?:details/)?books/([^/?#]+)", urllib.parse.urlsplit(url).path).group(1)
    q = urllib.parse.urlencode({"type": "books.Book", "slug": slug, "fields": "title,high_resolution_pdf_url"})
    items = C.get_json(f"https://openstax.org/apps/cms/api/v2/pages/?{q}").get("items") or []
    pdf = items and items[0].get("high_resolution_pdf_url")
    if not pdf:
        raise RuntimeError(f"OpenStax has no PDF for “{slug}” (is the link to a book's page?).")
    log(f"[OpenStax] downloading “{items[0].get('title') or slug}” (PDF)")
    path = _fetch(pdf, os.path.join(vdir, "download", f"{slug}.pdf"))
    vol = _document(log, path, f"{slug}.pdf", mode, vdir)
    if items[0].get("title"):
        vol["title"] = items[0]["title"]
    vol["source"] = f"openstax.org/details/books/{slug}"
    return vol


# ---------------------------------------------------------------- audiobooks (LibriVox / archive.org audio)

def audio_files(meta, quality="balanced"):
    """The MP3 chapters of an archive.org audio item, in order: [(file name, chapter title)]."""
    files = meta.get("files", [])
    mp3 = [f for f in files if f.get("name", "").lower().endswith(".mp3")]
    small = [f for f in mp3 if f["name"].lower().endswith("_64kb.mp3")]
    full = [f for f in mp3 if not f["name"].lower().endswith("_64kb.mp3")]
    chosen = (full or small) if quality == "sharp" else (small or full)  # LibriVox: 64 kbps is plenty for speech

    def order(f):
        t = re.match(r"\d+", str(f.get("track") or ""))
        return (int(t.group(0)) if t else 10 ** 6, f["name"])
    return [(f["name"], " ".join(str(f.get("title") or os.path.splitext(f["name"])[0]).split())) for f in sorted(chosen, key=order)]


def archive_audio(log, ident, meta, vdir, quality):
    """An archive.org audio item (e.g. a LibriVox recording) -> an audio volume, one page per chapter."""
    from . import ia, media
    meta = meta or C.get_json(f"https://archive.org/metadata/{urllib.parse.quote(ident)}")
    md = meta.get("metadata", {})
    chapters = audio_files(meta, quality)
    if not chapters:
        raise RuntimeError(f"{ident}: no MP3 audio in this archive.org item")
    title = C.first(md.get("title"), ident)
    log(f"[{title}] downloading {len(chapters)} audio chapters")
    items = []
    for i, (name, chap) in enumerate(chapters, 1):
        log.stage(f"Downloading audio: {title}", i - 1, len(chapters))
        dest = os.path.join(vdir, "download", f"{i:03d}.mp3")
        _fetch(ia._url(ident, name), dest)
        items.append((dest, f"{i:03d} {C.safe_filename(chap, 100)}.mp3"))
    vol = media.prepare(log, items, title, vdir, quality)
    for p, (_, chap) in zip(vol["pages"], chapters):
        p["label"] = chap   # the chapter titles archive.org lists, not the files' own tags
    vol.update({"title": f"{title} (audiobook)", "creator": C.first(md.get("creator")), "date": C.first(md.get("date")),
                "language": C.lang3(md.get("language")), "source": f"archive.org/details/{ident}"})
    return vol


def librivox(log, url, mode, vdir, quality):
    """A LibriVox book page -> the audiobook, plus the book's text from Project Gutenberg when LibriVox links it."""
    page = C.get_text(url)
    idents = re.findall(r"archive\.org/download/([\w.-]+)/\1_64kb_mp3\.zip", page) or \
        [i for i in re.findall(r"archive\.org/(?:details|download)/([\w.-]+)", page) if not i.lower().startswith("librivoxplainvanilla")]
    if not idents:
        raise RuntimeError("Couldn't find this audiobook's files (is the link to a LibriVox book page?).")
    out = []
    if mode != "none":  # text-only: no audio, so its details aren't even fetched
        meta = C.get_json(f"https://archive.org/metadata/{urllib.parse.quote(idents[0])}")
        os.makedirs(vdir + "-audio", exist_ok=True)
        out.append((vdir + "-audio", archive_audio(log, idents[0], meta, vdir + "-audio", quality)))
    book = re.search(r"gutenberg\.org/(?:ebooks|etext|files|cache/epub)/(\d+)", page)
    if book:
        os.makedirs(vdir + "-text", exist_ok=True)
        try:
            out.append((vdir + "-text", gutenberg(log, book.group(1), mode, vdir + "-text")))
        except Exception as e:  # noqa: BLE001  the audio is still worth having
            log(f"[LibriVox] the book's text couldn't be added: {e}")
    if not out:
        raise RuntimeError("Text-only mode keeps no audio, and LibriVox doesn't link a text for this book.")
    return out


# ---------------------------------------------------------------- IIIF

def _label(v):
    """IIIF labels: a string, a {lang: [strings]} map (v3), or a list of {'@value'} (v2)."""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        if "@value" in v:
            return str(v["@value"])
        vals = v.get("en") or v.get("none") or next(iter(v.values()), [])
        return " ".join(map(str, vals)) if isinstance(vals, list) else str(vals)
    if isinstance(v, list) and v:
        return _label(next((x for x in v if isinstance(x, dict) and x.get("@language", "").startswith("en")), v[0]))
    return ""


def _meta(m, *keys):
    for item in m.get("metadata") or []:
        if _label(item.get("label")).strip().lower() in keys:
            return re.sub(r"<[^>]+>", "", _label(item.get("value"))).strip()
    return ""


def _list(v):
    """IIIF fields may hold one thing or a list of them."""
    return v if isinstance(v, list) else [v] if v else []


def _id(v):
    """The address of a IIIF thing: a plain string, or an object with id / @id."""
    if isinstance(v, str):
        return v
    return (v.get("id") or v.get("@id") or "") if isinstance(v, dict) else ""


def _service(res):
    """The image service base address of an image resource ('' if none). Some give '.../info.json'."""
    svc = next((s for s in _list(res.get("service")) if _id(s)), None)
    base = _id(svc) if svc else ""
    return re.sub(r"/info\.json$", "", base).rstrip("/")


def _canvases(m):
    """[(label, image service, full image address, text links)] for a v2 or v3 manifest. A canvas that can't be
    read is skipped rather than failing the whole book."""
    out = []
    if "items" in m:  # v3
        for c in _list(m["items"]):
            try:
                body = _list(c["items"][0]["items"][0]["body"])[0]
                if body.get("type") == "Choice":  # alternatives (e.g. colour / infrared): the first
                    body = _list(body.get("items"))[0]
                out.append((_label(c.get("label")), _service(body), _id(body),
                            _list(c.get("seeAlso")) + _list(c.get("rendering"))))
            except (KeyError, IndexError, TypeError, AttributeError):
                continue
    else:             # v2
        for s in _list(m.get("sequences"))[:1]:
            for c in _list(s.get("canvases")):
                try:
                    res = _list(c["images"])[0]["resource"]
                    if res.get("@type") == "oa:Choice":
                        res = res.get("default") or _list(res.get("item"))[0]
                    out.append((_label(c.get("label")), _service(res), _id(res),
                                _list(c.get("seeAlso")) + _list(c.get("rendering"))))
                except (KeyError, IndexError, TypeError, AttributeError):
                    continue
    return out


def _text_from(links):
    """Page text from a canvas's ALTO or plain-text link, if it has one."""
    for link in links:
        if isinstance(link, str):
            link = {"@id": link}
        if not isinstance(link, dict):
            continue
        url = _id(link)
        fmt = str(link.get("format") or "") + " " + str(link.get("profile") or "") + " " + url
        try:
            if re.search(r"alto", fmt, re.I):
                root = ET.fromstring(C.get_text(url, timeout=60).encode())
                lines = []
                for line in root.iter():
                    if line.tag.endswith("TextLine"):
                        lines.append(" ".join(s.get("CONTENT", "") for s in line if s.tag.endswith("String")))
                return "\n".join(lines)
            if re.search(r"text/plain|\.txt\b", fmt, re.I):
                return C.get_text(url, timeout=60)
        except Exception:  # noqa: BLE001  text is a bonus; the scan is the page
            continue
    return ""


class NotLibrary(Exception):
    """The link looked like a library's (e.g. ends in /manifest) but isn't one: crawl it as a website instead."""


def _iiif_page(args):
    """One IIIF page: its text, and its scan saved as WebP. Returns the page dict."""
    from PIL import Image
    from . import images
    i, (label, svc, full, links), mode, vdir, log = args
    page = {"label": label, "text": _text_from(links)}
    if mode == "none":
        return page
    # the scan at ZIMaker's width; servers that won't scale (or upscale) get asked for their own size; then the
    # plain image address
    tries = [f"{svc}/full/{images.SCAN_WIDTH},/0/default.jpg", f"{svc}/full/max/0/default.jpg",
             f"{svc}/full/full/0/default.jpg"] if svc else []
    tries += [full] if full else []
    tmp = os.path.join(vdir, "download", f"{i}.jpg")
    for src in tries:
        try:
            _fetch(src, tmp)
            images.save_scan(Image.open(tmp), os.path.join(vdir, "img", f"{i}.webp"))
            page["image"] = f"img/{i}.webp"
            break
        except Exception:  # noqa: BLE001  try the next form of the address
            pass
        finally:
            for x in (tmp, tmp + ".ok"):
                if os.path.exists(x):
                    os.remove(x)
    if "image" not in page:
        log.warn(f"page {i} ({label or 'no label'}) couldn't be downloaded")
    return page


def iiif(log, url, mode, vdir):
    """A IIIF manifest -> a volume of page scans (and text where the library provides it)."""
    C.use_lib()
    from concurrent.futures import ThreadPoolExecutor
    from . import ia
    m = C.get_json_small(url, limit=50_000_000, timeout=60)
    if not isinstance(m, dict) or not ("sequences" in m or "items" in m) or "iiif.io" not in json.dumps(m.get("@context", "")):
        raise NotLibrary(url)
    title = C.clip(_label(m.get("label")) or "Untitled", 200)
    canvases = _canvases(m)
    if not canvases:
        raise RuntimeError("This IIIF manifest has no pages ZIMaker can read.")
    log(f"[{title}] {len(canvases)} pages from {_host(url)}")
    if mode == "pictures":
        log(f"[{title}] page scans have no word positions here, so every page is kept as an image")
    os.makedirs(os.path.join(vdir, "img"), exist_ok=True)
    pages = []
    # four pages at a time: much faster than one by one, and still gentle on the library's servers
    with ThreadPoolExecutor(4) as pool:
        for page in pool.map(_iiif_page, [(i, c, mode, vdir, log) for i, c in enumerate(canvases, 1)]):
            pages.append(page)
            log.stage(f"Downloading pages: {title}", len(pages), len(canvases))
    if mode == "none" and not any(p["text"].strip() for p in pages):
        raise RuntimeError("This book has no text, only page scans, so text-only mode has nothing to keep.")
    info = {"creator": _meta(m, "author", "creator", "contributor") or _meta(m, "publisher"),
            "date": _meta(m, "date", "date of publication", "created"), "language": C.lang3(_meta(m, "language"))}
    vol = ia.finish_pages_volume(pages, title, info, url)
    for p, raw in zip(vol["pages"], pages):
        if raw["label"]:
            p["label"] = raw["label"]   # the library's own page names (e.g. "f. 3r"), not "Page 3"
    vol["ocr"] = any(p["text"].strip() for p in pages)
    log(f"[{title}] {len(pages)} pages, {sum(1 for p in pages if p.get('image'))} with images")
    return vol


# ---------------------------------------------------------------- entry point

def prepare(log, url, mode, vdir, quality="balanced"):
    """A library link -> [(vdir, volume)] (an audiobook may come with its book's text, so it can be two)."""
    kind = detect(url)
    if kind == "blocked":
        raise RuntimeError(_blocked_message(url))
    if kind == "gutenberg":
        return [(vdir, gutenberg(log, _gutenberg_id(url), mode, vdir))]
    if kind == "standardebooks":
        return [(vdir, standardebooks(log, url, mode, vdir))]
    if kind == "wikisource":
        return [(vdir, wikisource(log, url, mode, vdir))]
    if kind == "openstax":
        return [(vdir, openstax(log, url, mode, vdir))]
    if kind == "librivox":
        return librivox(log, url, mode, vdir, quality)
    if kind == "iiif":
        return [(vdir, iiif(log, url, mode, vdir))]
    raise RuntimeError("Not a library link ZIMaker knows.")
