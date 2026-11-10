"""Edit a ZIM: write a changed copy (ZIM files can't be changed in place). The original is never touched.

Options (all optional):
  title, description, language, icon (an image file)  -> new details
  media: drop video and audio                         -> smaller
  images: "keep" | "webp" (recompress) | "none" (text only)
  quality: small | balanced | sharp (for "webp")
  clean: pages without their scripts, like ZIMaker's own website saves
  remove: page paths, or folders ending in "/", to leave out

A removed page is replaced by a short "removed from this copy" page, so links to it still lead somewhere. Pictures
and media that are left out are taken out of the pages that showed them.
"""
import datetime
import html
import io
import multiprocessing
import os
import posixpath
import re
import shutil
import urllib.parse
from concurrent.futures import ProcessPoolExecutor

from . import builder, merge, settings
from . import common as C

MEDIA = ("video/", "audio/")
RECOMPRESS = ("image/jpeg", "image/png", "image/bmp", "image/tiff")
SCAN_LIMIT = 300_000   # entries looked at for the overview of sections (a full Wikipedia has millions)


def _kind(mime):
    if mime.startswith("text/html"):
        return "pages"
    if mime.startswith("image/"):
        return "images"
    if mime.startswith(MEDIA):
        return "media"
    return "other"


def _entries(a):
    old_ns = not a.has_new_namespace_scheme
    for i in range(a.entry_count if not old_ns else a.all_entry_count):
        e = a._get_entry_by_id(i)
        if old_ns and e.path.startswith(merge.SKIP_OLD_NS):
            continue
        yield e


def overview(path):
    """What the Edit panel shows: details, what's inside, and folders that could be removed."""
    C.use_lib()
    z = merge.inspect(path)   # refuses zimit ZIMs
    a = z["archive"]
    counts = {"pages": 0, "images": 0, "media": 0, "other": 0}
    folders = {}
    partial = False
    for n, e in enumerate(_entries(a)):
        if n >= SCAN_LIMIT:
            partial = True
            break
        if e.is_redirect:
            continue
        mime = e.get_item().mimetype
        k = _kind(mime)
        counts[k] += 1
        if k != "pages":
            continue
        segs = e.path.split("/")[:-1]
        for d in range(1, min(len(segs), 3) + 1):
            f = "/".join(segs[:d]) + "/"
            folders[f] = folders.get(f, 0) + 1
    # folders worth offering: not one that holds (nearly) everything, and not one level that adds nothing
    total = counts["pages"]
    useful = sorted(((f, n) for f, n in folders.items() if n < total or total < 2), key=lambda x: (-x[1], x[0]))
    return {"title": z["title"], "description": z["description"], "language": z["language"],
            "creator": z["creator"], "size": z["size"], "counts": counts, "partial": partial,
            "search": bool(a.has_fulltext_index), "folders": [{"path": f, "pages": n} for f, n in useful[:150]]}


def clean_options(o):
    o = o if isinstance(o, dict) else {}
    lang = str(o.get("language") or "").strip().lower().replace(" ", "")
    remove = [str(x).strip().lstrip("/") for x in (o.get("remove") or []) if str(x).strip().strip("/")][:5000]
    return {"title": " ".join(str(o.get("title") or "").split())[:200],
            "description": " ".join(str(o.get("description") or "").split())[:500],
            "language": lang if re.fullmatch(r"[a-z]{3}(,[a-z]{3})*", lang) else "",
            "icon": str(o.get("icon") or ""),
            "media": bool(o.get("media")),
            "images": o.get("images") if o.get("images") in ("keep", "webp", "none") else "keep",
            "quality": o.get("quality") if o.get("quality") in settings.QUALITY else "balanced",
            "clean": bool(o.get("clean")),
            "remove": remove}


def _icon_png(data):
    """Any picture -> a 48x48 PNG (the size Kiwix shows), or None if it isn't one."""
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGBA")
            im.thumbnail((48, 48))
            canvas = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
            canvas.paste(im, ((48 - im.width) // 2, (48 - im.height) // 2))
            out = io.BytesIO()
            canvas.save(out, "PNG")
            return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


def _shrink(job):
    """(path, data, mime, side, quality) -> (path, data, mime): WebP if that is clearly smaller."""
    path, data, mime, side, quality = job
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            if getattr(im, "is_animated", False) and im.n_frames > 1:
                return path, data, mime
            im.load()
            im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P", "PA") or "transparency" in im.info else "RGB")
            if max(im.size) > side:
                im.thumbnail((side, side))
            out = io.BytesIO()
            im.save(out, "WEBP", quality=quality, method=4)
            new = out.getvalue()
        if len(new) < len(data) * 0.9:
            return path, new, "image/webp"
    except Exception:  # noqa: BLE001  not a picture PIL can read: keep it as it is
        pass
    return path, data, mime


IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
SOURCE_TAG = re.compile(r"<source\b[^>]*>", re.I)
AV_RE = re.compile(r"<(video|audio)\b[^>]*>.*?</\1\s*>", re.I | re.S)
LINK_RE = re.compile(r"""(\s(?:href|src|poster)\s*=\s*)(["'])(.*?)\2""", re.I | re.S)
SRCSET_RE = re.compile(r"""\s(?:srcset|data-srcset)\s*=\s*(["']).*?\1""", re.I | re.S)


def _target(url, page):
    """A link in a page -> the ZIM path it points to (None for outside links)."""
    url = html.unescape(url).strip()
    if not url or url.startswith(("#", "/")) or re.match(r"[a-z][a-z0-9+.-]*:", url, re.I):
        return None
    path = url.split("#")[0].split("?")[0]
    return posixpath.normpath(posixpath.join(posixpath.dirname(page), urllib.parse.unquote(path)))


def _fix_page(raw, page, o, gone):
    """One page: without scripts (if asked), without pictures and media that were left out, and with links to
    left-out files turned into plain text."""
    if o["clean"]:
        from . import web
        raw = web.static_page(raw)
    if o["media"]:
        raw = AV_RE.sub(lambda m: f'<p style="color:#777;font-style:italic">({m.group(1)} removed from this copy)</p>', raw)
    if o["images"] == "none":
        raw = IMG_TAG.sub("", raw)
        raw = SOURCE_TAG.sub(lambda m: "" if re.search(r"srcset", m.group(0), re.I) else m.group(0), raw)
        raw = re.sub(r"<picture\b[^>]*>|</picture\s*>", "", raw, flags=re.I)
    if gone:
        def tag(m):   # <img> or <source> pointing at something left out: drop it
            t = m.group(0)
            s = re.search(r"""\ssrc\s*=\s*(["'])(.*?)\1""", t, re.I | re.S)
            return "" if s and _target(s.group(2), page) in gone else t
        raw = IMG_TAG.sub(tag, raw)
        raw = SOURCE_TAG.sub(tag, raw)

        def link(m):
            return "" if _target(m.group(3), page) in gone else m.group(0)
        raw = LINK_RE.sub(link, raw)
        if o["images"] == "none":
            raw = SRCSET_RE.sub("", raw)   # alternatives in other sizes are left out too
    return raw


def _remover(o):
    """A quick test for "is this path among the removed pages and folders?" (asked for every entry)."""
    exact = {r.rstrip("/") for r in o["remove"]}
    folders = tuple(r for r in o["remove"] if r.endswith("/"))
    if not exact:
        return lambda path: False
    return lambda path: path in exact or path.startswith(folders)


def edit(log, src, out_path, opts):
    """Write a changed copy of the ZIM at src to out_path. Returns (title, check result)."""
    C.use_lib()
    o = clean_options(opts)
    z = merge.inspect(src)
    a = z["archive"]
    need = z["size"] * 1.1 + 200e6
    free = shutil.disk_usage(os.path.dirname(out_path)).free
    if free < need:
        raise RuntimeError(f"Not enough free space to edit this ZIM: about {need / 1e9:.1f} GB is needed while working, "
                           f"and {free / 1e9:.1f} GB is free.")
    title = o["title"] or z["title"]
    lang = o["language"] or z["language"] or "eng"
    merge.roomy_cache()
    removed = _remover(o)

    # 1. what is left out: removed pages become placeholders; removed files, media and pictures go entirely
    #    (nothing to look for when only details change, pictures are recompressed or pages cleaned)
    gone, total = set(), z["count"]
    if o["remove"] or o["media"] or o["images"] == "none":
        log.stage("Looking through the ZIM")
        total = 0
        for e in _entries(a):
            total += 1
            if e.is_redirect:
                continue
            mime = e.get_item().mimetype
            k = _kind(mime)
            if (removed(e.path) and k != "pages") or (o["media"] and k == "media") \
                    or (o["images"] == "none" and k == "images"):
                gone.add(e.path)
    if o["remove"]:
        log(f"leaving out: {', '.join(o['remove'][:10])}" + (f" and {len(o['remove']) - 10} more" if len(o["remove"]) > 10 else ""))
    if gone:
        log(f"{len(gone)} files left out (pictures, audio/video or files in removed folders)")

    if os.path.exists(out_path):
        os.remove(out_path)
    # pictures are recompressed in other processes, started fresh ("spawn"): forking this one while libzim's
    # writer threads run could hang
    pool = (ProcessPoolExecutor(max(1, min(8, (os.cpu_count() or 2) - 1)), mp_context=multiprocessing.get_context("spawn"))
            if o["images"] == "webp" else None)
    try:
        _write(log, a, z, o, out_path, title, lang, gone, total, pool, removed)
    finally:
        if pool:
            pool.shutdown(cancel_futures=True)
    old, new = z["size"], os.path.getsize(out_path)
    log(f"new copy: {new / 1e6:.1f} MB (was {old / 1e6:.1f} MB)")
    return title, builder.check(log, out_path)


def _write(log, a, z, o, out_path, title, lang, gone, total, pool, removed):
    from libzim.writer import Creator, Hint, Item
    side, q = settings.QUALITY[o["quality"]][2], settings.QUALITY[o["quality"]][3]
    done = kept_redirects = shrunk = saved_bytes = placeholders = 0

    class It(Item):
        def __init__(self, path, title, mime, content, front=False, compress=True):
            super().__init__()
            self.p, self.t, self.m, self.c, self.fr, self.cz = path, title, mime, content, front, compress

        def get_path(self):
            return self.p

        def get_title(self):
            return self.t

        def get_mimetype(self):
            return self.m

        def get_contentprovider(self):
            return merge.provider(self.c)

        def get_hints(self):
            return {Hint.FRONT_ARTICLE: self.fr, Hint.COMPRESS: self.cz}

    with Creator(out_path).config_indexing(bool(a.has_fulltext_index), lang.split(",")[0]) \
            .config_nbworkers(C.ZIM_WORKERS) as c:
        main = merge._main_path(a)
        if main:
            c.set_mainpath(main)
        icon = None
        if o["icon"]:
            try:
                icon = _icon_png(open(o["icon"], "rb").read())
            except OSError:
                icon = None
            if not icon:
                log.warn("the new icon isn't a picture ZIMaker can read: kept the old one")
        icon = icon or (_icon_png(z["icon"]) if z["icon"] else None) or builder._icon()
        try:
            c.add_illustration(content=icon, width=48)
        except TypeError:
            c.add_illustration(48, icon)
        tags = [t for t in merge._meta(a, "Tags").split(";") if t]
        if o["media"]:
            tags = [t for t in tags if not t.startswith("_videos:")] + ["_videos:no"]
        if o["images"] == "none":
            tags = [t for t in tags if not t.startswith("_pictures:")] + ["_pictures:no"]
        meta = {k: merge._meta(a, k) for k in ("Creator", "Publisher", "Source", "Flavour", "License", "Relation",
                                                "Scraper", "LongDescription")}
        meta.update({
            "Title": C.clip(title, 30),
            # its own Name: Kiwix treats ZIMs with the same Name as versions of one book (and hides older ones)
            "Name": C.slug(merge._meta(a, "Name") or title, 42) + "_edited",
            "Language": lang,
            "Description": C.clip(o["description"] or z["description"] or title, 80),
            "Date": datetime.date.today().isoformat(),
            "Tags": ";".join(dict.fromkeys(tags)),
        })
        for k, v in meta.items():
            if v:
                c.add_metadata(k, v)

        batch = []

        def flush():
            nonlocal shrunk, saved_bytes
            results = pool.map(_shrink, [b[:5] for b in batch], chunksize=8)
            for b, (path, data, mime) in zip(batch, results):
                if mime != b[2]:
                    shrunk += 1
                    saved_bytes += len(b[1]) - len(data)
                c.add_item(It(path, b[5], mime, data, compress=False))
            batch.clear()

        for e in _entries(a):
            done += 1
            if done % 2000 == 0:
                log.stage("Writing the new copy", done // 1000, total // 1000)
            path = e.path
            if e.is_redirect:
                try:
                    target = e.get_redirect_entry().path
                except Exception:  # noqa: BLE001
                    continue
                if target in gone:
                    continue
                c.add_redirection(path, e.title if e.title != path else "", target, {Hint.FRONT_ARTICLE: False})
                kept_redirects += 1
                continue
            if path in gone:
                continue
            it = e.get_item()
            mime = it.mimetype
            t = it.title if it.title != path else ""
            k = _kind(mime)
            if k == "pages" and removed(path):
                placeholders += 1
                body = (f"<h1>{html.escape(t or posixpath.basename(path))}</h1>"
                        "<p>This page was removed from this copy of the ZIM.</p>")
                c.add_item(It(path, "", "text/html", builder._doc(t or "Removed", body, "data:,").encode()))
                continue
            if k == "pages" and (o["clean"] or o["media"] or o["images"] == "none" or gone):
                raw = bytes(it.content).decode("utf-8", "replace")
                c.add_item(It(path, t, mime, _fix_page(raw, path, o, gone).encode("utf-8"), front=bool(t)))
                continue
            if o["images"] == "webp" and mime in RECOMPRESS:
                batch.append((path, bytes(it.content), mime, side, q, t, mime))
                if len(batch) >= 64:
                    flush()
                continue
            compress = not mime.startswith(("image/", "video/", "audio/", "font/")) or mime == "image/svg+xml"
            c.add_item(It(path, t, mime, merge.content_of(it, compress), front=k == "pages" and bool(t),
                          compress=compress))
        if batch:
            flush()
        if o["images"] == "webp":
            log(f"{shrunk} pictures recompressed as WebP, {saved_bytes / 1e6:.0f} MB smaller")
        if placeholders:
            log(f"{placeholders} pages removed (each replaced by a short note, so links to it still work)")
        log.stage("Writing the search index" if a.has_fulltext_index else "Finishing the ZIM")
