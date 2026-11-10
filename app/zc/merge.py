"""Combine several ZIM files into one: each goes into its own folder, behind a new front page.

Links inside a ZIM are relative, so they keep working when every entry of that ZIM moves into the same folder.
Shortcuts (redirects) are carried over, and the new ZIM gets one search index across everything.
"""
import datetime
import hashlib
import html
import os
import shutil

from . import builder
from . import common as C

# ZIMs made by zimit/warc2zim replay a recorded website with code that expects to sit at the top of the ZIM:
# moved into a folder, their pages break
REPLAY_SCRAPERS = ("warc2zim", "zimit")
SKIP_OLD_NS = ("M/", "X/", "Z/")   # old-style ZIMs: metadata and search indexes (the new ZIM makes its own)


BIG = 8 << 20   # items this big (videos, mostly) are handed to the new ZIM in pieces, never held whole in memory


def roomy_cache():
    """Reading every entry of a ZIM in path order jumps between its compressed clusters; libzim remembers only
    16 MB of opened clusters, so they'd be opened again and again. Give it room (where this libzim can)."""
    try:
        import libzim.reader as r
        if r.get_cluster_cache_max_size() < 512 << 20:
            r.set_cluster_cache_max_size(512 << 20)
    except Exception:  # noqa: BLE001  older libzim: no such setting
        pass


def provider(content):
    """What libzim reads an item's content from: bytes as they are; an item of another ZIM in 4 MB pieces."""
    from libzim.writer import Blob, ContentProvider, StringProvider
    if isinstance(content, (bytes, str)):
        return StringProvider(content)

    class Pieces(ContentProvider):
        def __init__(self, item):
            super().__init__()
            self.item, self.size = item, item.size

        def get_size(self):
            return self.size

        def gen_blob(self):
            view = self.item.content
            for o in range(0, self.size, 4 << 20):
                yield Blob(bytes(view[o:o + (4 << 20)]))
    return Pieces(content)


def content_of(item, compress):
    """An item's content for the copy: big uncompressed ones stay in the source ZIM until they're written."""
    return item if item.size > BIG and not compress else bytes(item.content)


def _meta(a, key):
    try:
        return bytes(a.get_metadata(key)).decode("utf-8", "replace").strip()
    except (KeyError, RuntimeError):
        return ""


def _main_path(a):
    """The path of a ZIM's start page (its main entry is usually a shortcut to it)."""
    if not a.has_main_entry:
        return None
    e = a.main_entry
    for _ in range(10):
        if not e.is_redirect:
            return e.path
        e = e.get_redirect_entry()
    return None


def _icon(a):
    try:
        sizes = sorted(a.get_illustration_sizes())
    except Exception:  # noqa: BLE001
        return None
    if not sizes:
        return None
    size = 48 if 48 in sizes else sizes[0]
    try:
        return bytes(a.get_illustration_item(size).content)
    except Exception:  # noqa: BLE001
        return None


def inspect(path):
    """What merging would do with one ZIM: its title etc., or a reason it can't be combined."""
    from libzim.reader import Archive
    try:
        a = Archive(path)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"{os.path.basename(path)} isn't a ZIM file ZIMaker can open ({e})") from e
    scraper = _meta(a, "Scraper")
    if any(s in scraper.lower() for s in REPLAY_SCRAPERS):
        raise RuntimeError(f"{os.path.basename(path)} was made by {scraper.split()[0]}, which replays a recorded "
                           "website with code that only works at the top of a ZIM. Combined into another ZIM, its "
                           "pages would break, so it can't be combined (open it on its own in Kiwix).")
    return {"archive": a, "title": _meta(a, "Title") or os.path.splitext(os.path.basename(path))[0],
            "language": _meta(a, "Language"), "creator": _meta(a, "Creator"),
            "description": _meta(a, "Description"), "main": _main_path(a), "icon": _icon(a),
            "count": a.entry_count if a.has_new_namespace_scheme else a.all_entry_count, "size": os.path.getsize(path)}


def merge(log, paths, out_path, title=""):
    """paths: ZIM files (WSL paths). Writes out_path; returns (title, check result: "pass", "fail", "unchecked")."""
    C.use_lib()
    from libzim.writer import Creator, Hint, Item
    roomy_cache()

    seen, zims = set(), []
    for p in paths:
        real = os.path.realpath(p)
        if real in seen:
            log(f"{os.path.basename(p)} is listed twice: added once")
            continue
        seen.add(real)
        zims.append(inspect(p))   # refuses zimit ZIMs before anything is written
    if len(zims) < 2:
        raise RuntimeError("Choose at least two different ZIM files to combine.")

    need = sum(z["size"] for z in zims) * 1.1 + 200e6   # the new ZIM is about as big as the originals together
    free = shutil.disk_usage(os.path.dirname(out_path)).free
    if free < need:
        raise RuntimeError(f"Not enough free space to combine these: about {need / 1e9:.1f} GB is needed while "
                           f"working, and {free / 1e9:.1f} GB is free.")

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
            return provider(self.c)

        def get_hints(self):
            return {Hint.FRONT_ARTICLE: self.fr, Hint.COMPRESS: self.cz}

    title = (title or "").strip() or (zims[0]["title"] + (f" (+{len(zims) - 1} more)"))
    langs = []
    for z in zims:
        for code in z["language"].split(","):
            code = code.strip()
            if code and code not in langs:
                langs.append(code)
    creators = {z["creator"] for z in zims if z["creator"]}
    # a folder per ZIM, named after it (readable addresses in Kiwix), never twice the same
    folders, used = [], set()
    for z in zims:
        base = C.slug(z["title"], 40) or "zim"
        name, n = base, 2
        while name in used:
            name, n = f"{base}-{n}", n + 1
        used.add(name)
        folders.append(name)

    if os.path.exists(out_path):
        os.remove(out_path)
    total = sum(z["count"] for z in zims)
    done = 0
    with Creator(out_path).config_indexing(True, (langs[0] if langs else "eng").split("-")[0][:3]) \
            .config_nbworkers(C.ZIM_WORKERS) as c:
        c.set_mainpath("index")
        try:
            c.add_illustration(content=builder._icon(), width=48)
        except TypeError:
            c.add_illustration(48, builder._icon())
        meta = {
            "Title": C.clip(title, 30),
            "Name": C.slug(title, 42) + "_" + hashlib.sha1(
                (title + "|" + "|".join(z["title"] for z in zims)).encode()).hexdigest()[:6],
            "Language": ",".join(langs) or "eng",
            "Creator": C.clip(creators.pop() if len(creators) == 1 else "Various", 200),
            "Publisher": "ZIMaker",
            "Date": datetime.date.today().isoformat(),
            "Description": C.clip(f"{len(zims)} ZIMs combined: " + ", ".join(z["title"] for z in zims), 80),
            "LongDescription": C.clip(f"{title}. Combined with ZIMaker from: "
                                      + "; ".join(z["title"] for z in zims), 4000),
            "Tags": "zimaker",
        }
        for k, v in meta.items():
            if v:
                c.add_metadata(k, v)

        cards = []
        for k, (z, folder) in enumerate(zip(zims, folders), 1):
            a, pre = z["archive"], folder + "/"
            old_ns = not a.has_new_namespace_scheme
            log.stage(f"Combining {k}/{len(zims)}: {z['title']}", done // 1000, total // 1000)
            n_items = n_redirects = 0
            for i in range(z["count"]):   # new-style ZIMs: their content entries come first
                e = a._get_entry_by_id(i)
                path = e.path
                if old_ns and path.startswith(SKIP_OLD_NS):
                    continue
                if e.is_redirect:
                    try:
                        target = e.get_redirect_entry().path
                    except Exception:  # noqa: BLE001  a shortcut to nothing: leave it out
                        continue
                    t = e.title if e.title != path else ""
                    c.add_redirection(pre + path, t, pre + target, {Hint.FRONT_ARTICLE: False})
                    n_redirects += 1
                else:
                    it = e.get_item()
                    mime = it.mimetype
                    html_page = mime.startswith("text/html")
                    t = it.title if it.title != path else ""
                    compress = not mime.startswith(("image/", "video/", "audio/", "font/")) or mime == "image/svg+xml"
                    c.add_item(It(pre + path, t, mime, content_of(it, compress), front=html_page and bool(t),
                                  compress=compress))
                    n_items += 1
                done += 1
                if done % 2000 == 0:
                    log.stage(f"Combining {k}/{len(zims)}: {z['title']}", done // 1000, total // 1000)
            icon = ""
            if z["icon"]:
                c.add_item(It(f"_zimaker/icon{k}.png", "", "image/png", z["icon"], compress=False))
                icon = f'<img src="_zimaker/icon{k}.png" width="48" height="48" alt="" style="flex:none">'
            start = pre + z["main"] if z["main"] else None
            link = builder._href(start) if start else None
            head = f'<a href="{link}">{html.escape(z["title"])}</a>' if link else html.escape(z["title"])
            desc = (f'<div class="hint">{html.escape(z["description"])}</div>'
                    if z["description"] and z["description"] != z["title"] else "")
            cards.append(f'<li style="display:flex;gap:12px;align-items:center;margin:10px 0">{icon}'
                         f'<div><b>{head}</b>{desc}</div></li>')
            log(f"added {z['title']} ({n_items} files, {n_redirects} shortcuts) in {folder}/")
        body = (f"<h1>{html.escape(title)}</h1><p>{len(zims)} ZIMs in one. Search with the search bar, or open "
                f'one below.</p><ul style="list-style:none;padding:0">{"".join(cards)}</ul>')
        c.add_item(It("index", title, "text/html", builder._doc(title, body, "_zimaker/style.css").encode(), front=True))
        c.add_item(It("_zimaker/style.css", "", "text/css", builder.CSS.encode()))
        log.stage("Writing the search index (this can take a while for big ZIMs)")
    return title, builder.check(log, out_path)
