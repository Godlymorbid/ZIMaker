"""Internet Archive books: OCR text for every page, page scans for illustrated pages (or all / none).

Uses the item's derived files: <book>_hocr_searchtext.txt.gz + _hocr_pageindex.json.gz (text per page),
_page_numbers.json (printed page numbers, which pages to show), _hocr.html (word positions, for picture
detection) and _jp2.zip / _jp2.tar (page scans). An item can hold several books (e.g. a multi-volume set).
"""
import gzip
import html
import io
import json
import os
import re
import tarfile
import urllib.error
import urllib.parse
import zipfile
from concurrent.futures import ProcessPoolExecutor

from . import common as C

ID_RE = re.compile(r"archive\.org/(?:details|download|stream|embed|metadata)/([^/?#\s]+)")
LEAF_RE = re.compile(r"_(\d{4})\.(?:jp2|jpg|jpeg|tif|tiff|png)$", re.I)
WORD_RE = re.compile(r'class="ocrx_word"[^>]*title="bbox (\d+) (\d+) (\d+) (\d+)')
PAGE_RE = re.compile(r'class="ocr_page"[^>]*?bbox 0 0 (\d+) (\d+)')


def item_id(text):
    text = text.strip()
    m = ID_RE.search(text)
    if m:
        return urllib.parse.unquote(m.group(1))
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", text):
        return text
    return None


def _url(ident, name):
    return f"https://archive.org/download/{urllib.parse.quote(ident)}/{urllib.parse.quote(name)}"


def inspect(ident, timeout=25):
    """Item metadata + the books it contains."""
    try:
        meta = C.get_json(f"https://archive.org/metadata/{urllib.parse.quote(ident)}", timeout=timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError("Couldn't reach archive.org — it may be down or busy. Try again in a while.") from e
    if not meta or "metadata" not in meta:
        raise ValueError(f"archive.org item not found: {ident}")
    md = meta["metadata"]
    files = {f["name"]: f for f in meta.get("files", [])}
    restricted = str(md.get("access-restricted-item", "")).lower() == "true" or bool(meta.get("is_dark"))
    books = []
    for name in sorted(files):
        if not name.endswith("_hocr_searchtext.txt.gz"):
            continue
        pre = name[: -len("_hocr_searchtext.txt.gz")]
        arch = next((pre + s for s in ("_jp2.zip", "_jp2.tar") if pre + s in files), None)
        books.append({
            "prefix": pre,
            "archive": arch,
            "archive_size": int(files[arch].get("size") or 0) if arch else 0,
            "has_index": pre + "_hocr_pageindex.json.gz" in files,
            "has_hocr": pre + "_hocr.html" in files,
            "hocr_size": int(files.get(pre + "_hocr.html", {}).get("size") or 0),
            "has_pnums": pre + "_page_numbers.json" in files,
        })
    title = C.first(md.get("title"), ident)
    for b in books:
        b["title"] = title if len(books) == 1 else _book_title(b["prefix"], title)
    return {
        "id": ident,
        "title": title,
        "creator": C.first(md.get("creator")),
        "publisher": C.first(md.get("publisher")),
        "date": C.first(md.get("date")),
        "language": C.lang3(md.get("language")),
        "restricted": restricted,
        "books": [b for b in books if b["has_index"]],
    }


def _book_title(prefix, item_title):
    t = re.sub(r"[_]+", " ", prefix).strip()
    return t or item_title


def _hocr_pages(path):
    """Yield one hOCR page (as text) at a time; skips the HTML header."""
    buf = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if 'class="ocr_page"' in line:
                if any('class="ocr_page"' in b for b in buf):
                    yield "".join(buf)
                buf = []
            buf.append(line)
    if any('class="ocr_page"' in b for b in buf):
        yield "".join(buf)


def _archive_index(path):
    """{leaf: ref} where ref lets a worker read that page's image bytes."""
    out = {}
    if path.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                m = LEAF_RE.search(n)
                if m:
                    out[int(m.group(1))] = ("zip", path, n)
    else:
        with tarfile.open(path) as t:
            for ti in t:
                m = LEAF_RE.search(ti.name)
                if m and ti.isfile():
                    out[int(m.group(1))] = ("tar", path, (ti.offset_data, ti.size))
    return out


def _read(ref):
    kind, path, where = ref
    if kind == "zip":
        with zipfile.ZipFile(path) as z:
            return z.read(where)
    off, size = where
    with open(path, "rb") as f:
        f.seek(off)
        return f.read(size)


def _page_job(a):
    """Worker: decide/save the scan for one page. Returns (leaf, has_scan)."""
    C.use_lib()
    from PIL import Image
    from . import images
    ref, leaf, W, H, words, mode, out = a
    data = _read(ref)
    if mode == "pictures":
        im = Image.open(io.BytesIO(data))
        if getattr(im, "format", "") == "JPEG2000":
            im.reduce = 1  # half resolution decodes much faster and is plenty for detection
        else:
            im.draft("L", (im.width // 2, im.height // 2))
        if not W or not H:
            W, H = Image.open(io.BytesIO(data)).size
        if not images.is_picture(im, W, H, words):
            return leaf, False
    images.save_scan(Image.open(io.BytesIO(data)), out)
    return leaf, True


def _list_leaves(ident, archive):
    """Leaf numbers in an image archive, from archive.org's listing (no download needed)."""
    try:
        page = html.unescape(urllib.parse.unquote(C.get_text(_url(ident, archive) + "/", timeout=120)))
        return sorted({int(m.group(1)) for m in re.finditer(r"_(\d{4})\.(?:jp2|jpg|jpeg|tif|tiff|png)\b", page, re.I)})
    except Exception:  # noqa: BLE001
        return []


def prepare(log, info, book, mode, vdir):
    """Fetch one book into vdir. mode: 'pictures' | 'all' | 'none'. Returns the volume dict."""
    ident, pre = info["id"], book["prefix"]
    os.makedirs(os.path.join(vdir, "img"), exist_ok=True)
    if info["restricted"]:
        raise RuntimeError("This archive.org item is borrow-only (lending library); its files can't be downloaded.")
    log(f"[{book['title']}] fetching OCR text")
    log.stage(f"Fetching text: {book['title']}")
    st = C.download(_url(ident, pre + "_hocr_searchtext.txt.gz"), os.path.join(vdir, "searchtext.gz"))
    pi = C.download(_url(ident, pre + "_hocr_pageindex.json.gz"), os.path.join(vdir, "pageindex.gz"))
    text = gzip.open(st, "rt", encoding="utf-8", errors="replace").read()
    index = json.load(gzip.open(pi))
    pnums = {}
    if book["has_pnums"]:
        pn = C.download(_url(ident, pre + "_page_numbers.json"), os.path.join(vdir, "page_numbers.json"))
        pnums = {p["leafNum"]: (p.get("pageNumber") or "") for p in json.load(open(pn))["pages"]}

    need_images = mode in ("pictures", "all") and book["archive"]
    if mode in ("pictures", "all") and not book["archive"]:
        log(f"[{book['title']}] no page scans in this item; making a text-only volume")
    refs = {}
    if need_images:
        if mode == "pictures" and not book["has_hocr"]:
            log(f"[{book['title']}] no word positions (hOCR) available; including every page as an image")
            mode = "all"
        ap = os.path.join(vdir, "scans" + (".zip" if book["archive"].endswith(".zip") else ".tar"))
        log(f"[{book['title']}] downloading page scans ({book['archive_size'] / 1e9:.1f} GB)")
        C.download(_url(ident, book["archive"]), ap, log, f"scans: {book['title']}", book["archive_size"])
        refs = _archive_index(ap)
        leaves = sorted(refs)
    else:
        leaves = _list_leaves(ident, book["archive"]) if book["archive"] else []
    if not leaves:
        leaves = sorted(pnums) if pnums and len(pnums) == len(index) else list(range(len(index)))
    if len(leaves) != len(index):
        log(f"[{book['title']}] note: {len(leaves)} images vs {len(index)} OCR pages; matching in order")

    hocr = None
    if need_images and mode == "pictures":
        log.stage(f"Fetching word positions: {book['title']}")
        hocr = C.download(_url(ident, pre + "_hocr.html"), os.path.join(vdir, "hocr.html"),
                          log, f"word positions: {book['title']}", book["hocr_size"])
    page_iter = _hocr_pages(hocr) if hocr else iter(())

    pages, batch, results = [], [], {}

    def run(pool):
        for leaf, has in pool.map(_page_job, batch):
            results[leaf] = has
        batch.clear()

    total = min(len(leaves), len(index))
    with ProcessPoolExecutor(C.WORKERS) as pool:
        for i in range(total):
            leaf = leaves[i]
            pg = next(page_iter, "") if hocr else ""
            if pnums and leaf not in pnums:
                continue  # not a display page (colour card, etc.)
            t = text[index[i][0]:index[i][1]]
            pages.append({"leaf": leaf, "label": pnums.get(leaf, ""), "text": t})
            if need_images and leaf in refs:
                m = PAGE_RE.search(pg) if pg else None
                W, H = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
                words = [tuple(map(int, w)) for w in WORD_RE.findall(pg)] if pg else []
                batch.append((refs[leaf], leaf, W, H, words, mode, os.path.join(vdir, "img", f"{leaf}.webp")))
                if len(batch) >= C.BATCH:
                    run(pool)
                    log.stage(f"{'Finding pictures' if mode == 'pictures' else 'Converting pages'}: {book['title']}",
                              len(pages), total)
        run(pool)

    for p in pages:
        if results.get(p["leaf"]):
            p["image"] = f"img/{p['leaf']}.webp"
    for f in ("scans.zip", "scans.tar", "hocr.html"):
        for x in (f, f + ".ok"):
            if os.path.exists(os.path.join(vdir, x)):
                os.remove(os.path.join(vdir, x))
    n_img = sum(1 for p in pages if p.get("image"))
    log(f"[{book['title']}] {len(pages)} pages, {n_img} with images")
    return finish_pages_volume(pages, book["title"], info, f"archive.org/details/{ident}")


def finish_pages_volume(pages, title, info, source):
    out = []
    for n, p in enumerate(pages, 1):
        out.append({
            "label": f"p. {p['label']}" if p.get("label") else f"Page {n}",
            "head": C.running_head(p["text"]),
            "body": C.ocr_html(p["text"]),
            "image": p.get("image"),
        })
    return {"kind": "pages", "title": title, "creator": info.get("creator", ""),
            "publisher": info.get("publisher", ""), "date": info.get("date", ""),
            "language": info.get("language", "eng"), "source": source, "pages": out, "ocr": True}
