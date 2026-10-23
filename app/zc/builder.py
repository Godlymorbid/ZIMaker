"""Write prepared volumes into a ZIM file and validate it."""
import datetime
import html
import os
import posixpath
import re
import struct
import subprocess
import urllib.parse
import zlib

from . import common as C
from . import deps

CSS = """
body{font-family:Georgia,serif;max-width:46em;margin:0 auto;padding:1em;line-height:1.6;color:#222;background:#fdfcf8}
body.scan{max-width:60em}
nav.pg{display:flex;justify-content:space-between;gap:1em;padding:.5em 0;border-bottom:1px solid #ccc;font-family:sans-serif;font-size:.9em}
nav.pg.bottom{border-top:1px solid #ccc;border-bottom:none;margin-top:2em}
a{color:#7a1f1f}
h1{font-size:1.4em}
.hint{color:#666}
.pic{color:#7a1f1f;font-size:.85em}
.note{font-size:.85em;color:#666;font-style:italic}
ul.toc{padding-left:1.2em}
ul.toc li.d1{margin-left:1.5em}
ul.toc li.d2{margin-left:3em}
img.scan{display:block;width:100%;height:auto;margin:.5em 0;box-shadow:0 1px 4px #0003;background:#fff}
details.ocrtext{margin-top:1em}
details.ocrtext summary{cursor:pointer;font-family:sans-serif;font-size:.9em;color:#555}
"""
NAV_STYLE = ("font-family:sans-serif;font-size:14px;display:flex;justify-content:space-between;gap:1em;"
             "padding:6px 0;margin:0 0 8px;border-bottom:1px solid #ccc")


def _icon(size=48):
    """A small book icon as PNG (stdlib only)."""
    rows = b""
    for y in range(size):
        row = b"\x00"
        for x in range(size):
            inner = 12 <= x < 36 and 8 <= y < 40
            line = inner and y in (14, 19, 24, 29, 34) and 15 <= x < 33
            rgb = (122, 31, 31) if not inner else ((90, 60, 40) if line else (245, 238, 220))
            row += bytes(rgb)
        rows += row

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b""))


def _doc(title, inner, css_href, cls=""):
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title)}</title><link rel="stylesheet" href="{css_href}"></head>'
            f'<body class="{cls}">{inner}</body></html>')


def _rel(target, from_path):
    return posixpath.relpath(target, posixpath.dirname(from_path) or ".")


VOID = "area|base|br|col|embed|hr|img|input|link|meta|source|track|wbr|image|path|rect|circle|line|polygon|polyline|ellipse|use|stop"


def _xhtml_to_html(raw):
    raw = re.sub(r"^\s*<\?xml[^>]*\?>", "", raw)
    # <div/> is legal XHTML but opens a never-closed element in HTML
    raw = re.sub(rf"<(?!(?:{VOID})\b)([a-zA-Z][\w:-]*)(\s[^<>]*?)?\s*/>", r"<\1\2></\1>", raw)
    if not re.search(r"<meta[^>]+charset", raw, re.I):
        raw = re.sub(r"(<head[^>]*>)", r'\1<meta charset="utf-8">', raw, count=1, flags=re.I)
    return raw


def build(log, volumes, out_path, title=None):
    """volumes: list of (vdir, volume dict). Writes out_path; returns check result string."""
    C.use_lib()
    from libzim.writer import Creator, FileProvider, Hint, Item, StringProvider
    from PIL import Image

    class It(Item):
        def __init__(self, path, title, mime, content=None, file=None, front=False, compress=True):
            super().__init__()
            self.p, self.t, self.m, self.c, self.f, self.fr, self.cz = path, title, mime, content, file, front, compress

        def get_path(self):
            return self.p

        def get_title(self):
            return self.t

        def get_mimetype(self):
            return self.m

        def get_contentprovider(self):
            return FileProvider(self.f) if self.f else StringProvider(self.c)

        def get_hints(self):
            return {Hint.FRONT_ARTICLE: self.fr, Hint.COMPRESS: self.cz}

    multi = len(volumes) > 1
    v0 = volumes[0][1]
    title = (title or "").strip() or (v0["title"] if not multi else f"{v0['title']} (+{len(volumes) - 1} more)")
    lang = v0.get("language") or "eng"
    any_images = any((v.get("kind") == "pages" and any(p.get("image") for p in v["pages"]))
                     or (v.get("kind") == "epub" and not v.get("drop_images"))
                     or (v.get("kind") == "site" and any(f["mime"].startswith("image/") for f in v["files"]))
                     for _, v in volumes)
    if os.path.exists(out_path):
        os.remove(out_path)

    with Creator(out_path).config_indexing(True, lang) as c:
        c.set_mainpath("index" if multi else ("v1/" + v0["main"] if v0["kind"] == "site" else "v1/index"))
        c.add_illustration(48, _icon())
        meta = {
            "Title": C.clip(title, 30),
            "Name": C.slug(title, 50),
            "Language": lang,
            "Creator": C.clip(v0.get("creator") or "Unknown", 200),
            "Publisher": C.clip(v0.get("publisher") or "ZIM Creator", 200),
            "Date": datetime.date.today().isoformat(),
            "Description": C.clip(title if len(title) <= 80 else title, 80),
            "LongDescription": C.clip(f"{title}. Converted with ZIM Creator from: "
                                      + "; ".join(v.get("source", "") for _, v in volumes), 4000),
            "Tags": "zim-creator;_videos:no" + ("" if any_images else ";_pictures:no"),
        }
        for k, v in meta.items():
            if v:
                c.add_metadata(k, v)
        c.add_item(It("style.css", "", "text/css", CSS))
        rows = []
        for k, (vdir, vol) in enumerate(volumes, 1):
            pre = f"v{k}/"
            if vol["kind"] == "pages":
                n = _emit_pages(c, It, Image, vdir, vol, pre, multi)
            elif vol["kind"] == "site":
                n = _emit_site(c, It, vdir, vol, pre, multi)
            else:
                n = _emit_epub(c, It, vdir, vol, pre, multi)
            rows.append((k, vol["title"], n))
            log(f"added {vol['title']} ({n} pages)")
        if multi:
            items = "".join(f'<li><a href="v{k}/index">{html.escape(t)}</a> <span class="hint">({n} pages)</span></li>'
                            for k, t, n in rows)
            body = (f"<h1>{html.escape(title)}</h1><p>Search with the search bar, or open one of these below.</p>"
                    f'<ul class="toc">{items}</ul>')
            c.add_item(It("index", title, "text/html", _doc(title, body, "style.css"), front=True))
    return check(log, out_path)


def _emit_pages(c, It, Image, vdir, vol, pre, multi):
    pages = vol["pages"]
    css = "../style.css"
    toc = []
    for i, p in enumerate(pages):
        path = f"{pre}p{i + 1}"
        t = f"{vol['title']}, {p['label']}" + (f" — {p['head']}" if p.get("head") else "")
        prv = f'<a href="p{i}">← {html.escape(pages[i - 1]["label"])}</a>' if i else "<span></span>"
        nxt = (f'<a href="p{i + 2}">{html.escape(pages[i + 1]["label"])} →</a>' if i + 1 < len(pages)
               else "<span></span>")
        bar = f'{prv}<a href="index">Contents</a>{nxt}'
        body = p.get("body") or '<p class="note">(no text on this page)</p>'
        img = p.get("image")
        if img and os.path.exists(os.path.join(vdir, img)):
            mime = {".gif": "image/gif", ".svg": "image/svg+xml"}.get(os.path.splitext(img)[1].lower(), "image/webp")
            size = ""
            if mime != "image/svg+xml":
                with Image.open(os.path.join(vdir, img)) as im:
                    size = f' width="{im.size[0]}" height="{im.size[1]}"'
            ipath = f"{pre}{img}"
            c.add_item(It(ipath, "", mime, file=os.path.join(vdir, img), compress=mime == "image/svg+xml"))
            content = (f'<img class="scan" src="{img}"{size} alt="{html.escape(p["label"])}">'
                       + (f'<details class="ocrtext"><summary>Text of this page</summary>{p["body"]}</details>'
                          if p.get("body") else ""))
            cls = "scan"
        else:
            content, cls, img = body, "", None
        inner = f'<nav class="pg">{bar}</nav><h1>{html.escape(t)}</h1>{content}<nav class="pg bottom">{bar}</nav>'
        c.add_item(It(path, t, "text/html", _doc(t, inner, css, cls), front=True))
        toc.append(f'<li><a href="p{i + 1}">{html.escape(p["label"])}</a>'
                   + (f' <span class="hint">{html.escape(p["head"])}</span>' if p.get("head") else "")
                   + (' <span class="pic">· illustrated</span>' if img and not vol.get("gallery") else "") + "</li>")
    back = '<nav class="pg"><a href="../index">All</a></nav>' if multi else ""
    info = " · ".join(x for x in (vol.get("creator"), vol.get("date")) if x)
    unit = "pictures" if vol.get("gallery") else "parts" if vol.get("plain") else "pages"
    note = f"{len(pages)} {unit}."
    if vol.get("ocr"):
        note += " Text comes from OCR and may contain errors."
    if any(p.get("image") for p in pages) and not vol.get("gallery"):
        note += " Pages marked “illustrated” show the original page image."
    body = (f"{back}<h1>{html.escape(vol['title'])}</h1>"
            + (f"<p>{html.escape(info)}</p>" if info else "")
            + f'<p class="note">{note}</p><ul class="toc">{"".join(toc)}</ul>')
    c.add_item(It(f"{pre}index", vol["title"], "text/html", _doc(vol["title"], body, css), front=True))
    return len(pages)


def _emit_site(c, It, vdir, vol, pre, multi):
    """A website (crawled, or a saved site from a ZIP): every file at its own path, plus a page list."""
    pages, done = [], set()
    for f in vol["files"]:
        src = os.path.join(vdir, f["disk"])
        if not os.path.exists(src) or f["path"] in done or f["path"] == "index":
            continue
        done.add(f["path"])
        is_html = f["mime"] in ("text/html", "application/xhtml+xml")
        compress = not f["mime"].startswith(("image/", "font/", "video/", "audio/")) or f["mime"] == "image/svg+xml"
        c.add_item(It(pre + f["path"], f.get("title") or "", "text/html" if is_html else f["mime"],
                      file=src, front=is_html, compress=compress))
        if is_html:
            pages.append(f)
    index_path = f"{pre}index"
    pages.sort(key=lambda f: (f["path"] != vol["main"], f["path"]))
    items = "".join(f'<li><a href="{html.escape(_rel(pre + f["path"], index_path))}">'
                    f'{html.escape(f.get("title") or f["path"])}</a></li>' for f in pages)
    back = '<nav class="pg"><a href="../index">All</a></nav>' if multi else ""
    body = (f"{back}<h1>{html.escape(vol['title'])}</h1>"
            + f'<p><a href="{html.escape(_rel(pre + vol["main"], index_path))}">Open the home page →</a></p>'
            + f'<p class="note">Saved from {html.escape(vol.get("source", ""))} · {len(pages)} pages. '
            + "Links to pages that weren't saved still point to the live site.</p>"
            + f'<ul class="toc">{items}</ul>')
    c.add_item(It(index_path, f"{vol['title']}: all pages", "text/html", _doc(vol["title"], body, "../style.css"), front=True))
    return len(pages)


LINK_ATTR_RE = re.compile(r"""(\s(?:src|href|xlink:href)\s*=\s*)(["'])(.*?)\2""", re.I | re.S)


def _fix_links(raw, doc, have, by_name):
    """Repair or drop references to files that aren't in the book (common in real-world ebooks)."""
    def sub(m):
        url = html.unescape(m.group(3)).strip()
        if not url or url.startswith("#") or re.match(r"[a-z][a-z0-9+.-]*:", url, re.I):
            return m.group(0)
        path, _, frag = url.partition("#")
        target = posixpath.normpath(posixpath.join(posixpath.dirname(doc), urllib.parse.unquote(path)))
        if target in have:
            return m.group(0)
        alt = by_name.get(posixpath.basename(target))
        if alt:  # same file name elsewhere in the book: point there
            new = _rel(alt, doc) + (f"#{frag}" if frag else "")
            return f"{m.group(1)}{m.group(2)}{html.escape(new, quote=True)}{m.group(2)}"
        return ""  # nothing to point at: drop the reference rather than leave a broken link
    return LINK_ATTR_RE.sub(sub, raw)


def _emit_epub(c, It, vdir, vol, pre, multi):
    book = os.path.join(vdir, "book")
    spine = vol["spine"]
    pos = {h: i for i, h in enumerate(spine)}
    index_path = f"{pre}index"
    have = {it["path"] for it in vol["items"] if it["media"] != "application/x-dtbncx+xml"}
    names = {}
    for p in have:
        names.setdefault(posixpath.basename(p), []).append(p)
    by_name = {n: ps[0] for n, ps in names.items() if len(ps) == 1}
    for it in vol["items"]:
        src = os.path.join(book, it["path"])
        zpath = pre + it["path"]
        if it["media"] in ("application/xhtml+xml", "text/html"):
            raw = open(src, encoding="utf-8", errors="replace").read()
            if vol.get("drop_images"):
                raw = re.sub(r"<img\b[^>]*>", "", raw, flags=re.I)
                raw = re.sub(r"<svg\b.*?</svg>", "", raw, flags=re.I | re.S)
            raw = _fix_links(_xhtml_to_html(raw), it["path"], have, by_name)
            i = pos.get(it["path"])
            if i is not None:
                links = []
                links.append(f'<a href="{_rel(pre + spine[i - 1], zpath)}">← Previous</a>' if i else "<span></span>")
                links.append(f'<a href="{_rel(index_path, zpath)}">Contents</a>')
                links.append(f'<a href="{_rel(pre + spine[i + 1], zpath)}">Next →</a>' if i + 1 < len(spine)
                             else "<span></span>")
                bar = f'<div style="{NAV_STYLE}">{"".join(links)}</div>'
                raw = re.sub(r"(<body[^>]*>)", r"\1" + bar.replace("\\", "\\\\"), raw, count=1, flags=re.I)
                raw = re.sub(r"(</body>)", bar.replace("border-bottom", "border-top").replace("\\", "\\\\") + r"\1",
                             raw, count=1, flags=re.I)
            t = vol["titles"].get(it["path"], "") if i is not None else ""
            c.add_item(It(zpath, f"{vol['title']}: {t}" if t else "", "text/html", raw, front=i is not None))
        elif it["media"] == "application/x-dtbncx+xml":
            continue
        else:
            compress = not it["media"].startswith(("image/", "font/", "audio/", "video/"))
            c.add_item(It(zpath, "", it["media"] or "application/octet-stream", file=src, compress=compress))
    entries = "".join(
        f'<li class="d{min(e.get("depth", 0), 2)}"><a href="{html.escape(_rel(pre + e["href"].split("#")[0], index_path) + ("#" + e["href"].split("#", 1)[1] if "#" in e["href"] else ""))}">'
        f'{html.escape(e["label"])}</a></li>' for e in vol["toc"] if e["href"].split("#")[0] in have)
    back = '<nav class="pg"><a href="../index">All books</a></nav>' if multi else ""
    info = " · ".join(x for x in (vol.get("creator"), vol.get("date")) if x)
    body = (f"{back}<h1>{html.escape(vol['title'])}</h1>" + (f"<p>{html.escape(info)}</p>" if info else "")
            + (f'<p><a href="{html.escape(_rel(pre + spine[0], index_path))}">Start reading →</a></p>' if spine else "")
            + f'<ul class="toc">{entries}</ul>')
    c.add_item(It(index_path, vol["title"], "text/html", _doc(vol["title"], body, "../style.css"), front=True))
    return len(spine)


def check(log, path):
    """Validate with zimcheck when available, else libzim's own integrity check."""
    zc = deps.zimcheck_path()
    if zc:
        out = subprocess.run([zc, "-A", path], capture_output=True, text=True).stdout
        errors = [ln for ln in out.splitlines() if "[ERROR]" in ln]
        if "Overall Test Status: Pass" in out:
            log("validation passed (zimcheck)")
            return "pass"
        log("validation problems: " + "; ".join(errors[:5]))
        return "fail"
    from libzim.reader import Archive
    ok = Archive(path).check()
    log("validation " + ("passed (libzim)" if ok else "FAILED (libzim)"))
    return "pass" if ok else "fail"
