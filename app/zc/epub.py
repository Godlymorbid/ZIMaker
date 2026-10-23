"""EPUB files: every content document, stylesheet and image is kept as-is (so links keep working);
the builder adds a contents page and previous/next navigation. 'none' mode drops the images.
"""
import html
import os
import posixpath
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

from . import common as C

NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container", "opf": "http://www.idpf.org/2007/opf",
      "dc": "http://purl.org/dc/elements/1.1/", "ncx": "http://www.daisy.org/z3986/2005/ncx/"}
HTML_TYPES = ("application/xhtml+xml", "text/html")


def _norm(base_dir, href):
    href = urllib.parse.unquote(href.split("#")[0])
    p = posixpath.normpath(posixpath.join(base_dir, href)) if href else base_dir
    while p.startswith("../"):
        p = p[3:]
    return p.lstrip("/")


def _toc_from_nav(text, nav_dir):
    m = re.search(r'<nav[^>]*epub:type="[^"]*toc[^"]*"[^>]*>(.*?)</nav>', text, re.S | re.I)
    if not m:
        return []
    out, depth = [], 0
    for tok in re.finditer(r"<ol\b|</ol>|<a\b[^>]*href=\"([^\"]*)\"[^>]*>(.*?)</a>", m.group(1), re.S | re.I):
        s = tok.group(0).lower()
        if s.startswith("<ol"):
            depth += 1
        elif s.startswith("</ol"):
            depth -= 1
        else:
            label = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", tok.group(2))).split())
            frag = tok.group(1).split("#", 1)
            href = _norm(nav_dir, frag[0]) + ("#" + frag[1] if len(frag) > 1 else "")
            if label:
                out.append({"label": label, "href": href, "depth": max(0, depth - 1)})
    return out


def _toc_from_ncx(text, ncx_dir):
    out, depth = [], 0
    for tok in re.finditer(r"<navPoint\b|</navPoint>|<text>(.*?)</text>|<content[^>]*src=\"([^\"]*)\"", text, re.S):
        s = tok.group(0)
        if s.startswith("<navPoint"):
            depth += 1
        elif s.startswith("</navPoint"):
            depth -= 1
        elif s.startswith("<text>"):
            out.append({"label": " ".join(html.unescape(tok.group(1)).split()), "href": "", "depth": depth - 1})
        elif out and not out[-1]["href"]:
            frag = tok.group(2).split("#", 1)
            out[-1]["href"] = _norm(ncx_dir, frag[0]) + ("#" + frag[1] if len(frag) > 1 else "")
    return [e for e in out if e["label"] and e["href"]]


def _doc_title(raw):
    for pat in (r"<title[^>]*>(.*?)</title>", r"<h[1-3][^>]*>(.*?)</h[1-3]>"):
        m = re.search(pat, raw, re.S | re.I)
        if m:
            t = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(1))).split())
            if t:
                return t
    return ""


def prepare(log, path, mode, vdir, name=None):
    z = zipfile.ZipFile(path)
    container = ET.fromstring(z.read("META-INF/container.xml"))
    opf_path = container.find(".//c:rootfile", NS).get("full-path")
    opf_dir = posixpath.dirname(opf_path)
    opf = ET.fromstring(z.read(opf_path))
    md = opf.find("opf:metadata", NS)

    def dc(tag):
        el = md.find(f"dc:{tag}", NS) if md is not None else None
        return (el.text or "").strip() if el is not None and el.text else ""

    title = dc("title") or os.path.splitext(name or os.path.basename(path))[0]
    manifest = {}
    for it in opf.find("opf:manifest", NS):
        manifest[it.get("id")] = {"href": _norm(opf_dir, it.get("href", "")), "media": it.get("media-type", ""),
                                  "props": it.get("properties", "")}
    spine = [manifest[r.get("idref")]["href"] for r in opf.find("opf:spine", NS)
             if r.get("idref") in manifest and manifest[r.get("idref")]["media"] in HTML_TYPES]

    book = os.path.join(vdir, "book")
    items = []
    for it in manifest.values():
        if mode == "none" and it["media"].startswith("image/"):
            continue
        try:
            data = z.read(it["href"])
        except KeyError:
            continue
        dest = os.path.join(book, it["href"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        items.append({"path": it["href"], "media": it["media"]})

    toc = []
    nav = next((it for it in manifest.values() if "nav" in it["props"].split()), None)
    if nav:
        try:
            toc = _toc_from_nav(z.read(nav["href"]).decode("utf-8", "replace"), posixpath.dirname(nav["href"]))
        except KeyError:
            pass
    if not toc:
        ncx = next((it for it in manifest.values() if it["media"] == "application/x-dtbncx+xml"), None)
        if ncx:
            try:
                toc = _toc_from_ncx(z.read(ncx["href"]).decode("utf-8", "replace"), posixpath.dirname(ncx["href"]))
            except KeyError:
                pass
    titles = {}
    for n, href in enumerate(spine, 1):
        try:
            titles[href] = _doc_title(z.read(href).decode("utf-8", "replace")) or f"Section {n}"
        except KeyError:
            titles[href] = f"Section {n}"
    if not toc:
        toc = [{"label": titles[h], "href": h, "depth": 0} for h in spine]
    log(f"[{title}] {len(spine)} sections, {sum(1 for i in items if i['media'].startswith('image/'))} images")
    return {"kind": "epub", "title": title, "creator": dc("creator"), "publisher": dc("publisher"),
            "date": dc("date"), "language": C.lang3(dc("language")), "source": os.path.basename(name or path),
            "spine": spine, "titles": titles, "items": items, "toc": toc, "drop_images": mode == "none"}
