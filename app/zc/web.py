"""Websites: crawl a site (or one section of it) and keep its pages, styles, scripts, fonts and images.

Pages are saved as the site serves them; only links are rewritten. Links to pages that weren't saved
(beyond the page limit or out of scope) are left pointing at the live site, so the ZIM has no broken
internal links. Respects robots.txt. Sites that build their pages with JavaScript won't work well.
"""
import hashlib
import html
import mimetypes
import os
import posixpath
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from . import common as C

UA = "ZIMCreator/1.0 (offline archiving tool; respects robots.txt)"
HTML_TYPES = ("text/html", "application/xhtml+xml")
MAX_FILE = 50_000_000          # skip single files bigger than this
MAX_TOTAL = 4_000_000_000      # stop fetching assets past this many bytes
MAX_ASSETS = 20000
MEDIA_EXT = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg|ico|bmp|tiff?)$", re.I)
AV_EXT = re.compile(r"\.(mp4|webm|ogv|m4v|mov|avi|mkv|mp3|m4a|aac|ogg|oga|opus|wav|flac|m3u8|ts)$", re.I)  # never saved
NOT_PAGE_EXT = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg|ico|bmp|tiff?|mp4|webm|ogv|m4v|mov|mp3|m4a|ogg|oga|opus|wav|flac|"
                          r"pdf|zip|gz|tgz|rar|7z|exe|msi|dmg|iso|apk|css|js|json|xml|rss|atom|woff2?|ttf|otf|eot)$", re.I)
SKIP = ("mailto:", "javascript:", "data:", "tel:", "about:", "blob:", "sms:", "#")

TAG_RE = re.compile(r"<(a|area|link|img|script|source|video|audio|iframe|frame|embed|track|input|object|image|use|meta|body|td|table|base)\b[^>]*>", re.I)
ATTR_RE = re.compile(r"""(\s)(href|src|poster|data-src|data-original|background|data|xlink:href|content|srcset|data-srcset)(\s*=\s*)(?:(["'])(.*?)\4|([^\s"'>]+))""", re.I | re.S)
CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+?)\1\s*\)|@import\s+(['"])([^'"]+)\3""", re.I)
STYLE_BLOCK_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.I | re.S)
STYLE_ATTR_RE = re.compile(r"""(\sstyle\s*=\s*)(["'])(.*?)\2""", re.I | re.S)


def normalize(url):
    url, _ = urllib.parse.urldefrag(url.strip())
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https"):
        return None
    netloc = p.netloc.lower()
    if (p.scheme == "http" and netloc.endswith(":80")) or (p.scheme == "https" and netloc.endswith(":443")):
        netloc = netloc.rsplit(":", 1)[0]
    return urllib.parse.urlunsplit((p.scheme, netloc, p.path or "/", p.query, ""))


def zpath_for(url):
    """Where a URL is stored inside the volume: host/path (query strings become a short hash)."""
    p = urllib.parse.urlsplit(url)
    path = urllib.parse.unquote(p.path) or "/"
    if path.endswith("/"):
        path += "index.html"
    if p.query:
        root, ext = posixpath.splitext(path)
        path = f"{root}__{hashlib.sha1(p.query.encode()).hexdigest()[:10]}{ext}"
    segs = []
    for s in path.split("/"):
        if s in ("", ".", ".."):
            continue
        s = re.sub(r'[<>:"\\|?*\x00-\x1f#%]', "_", s)
        segs.append(s if len(s) <= 120 else s[:100] + hashlib.sha1(s.encode()).hexdigest()[:10])
    return p.netloc.replace(":", "_") + "/" + "/".join(segs or ["index.html"])


def _rel(target, from_path):
    rel = posixpath.relpath(target, posixpath.dirname(from_path) or ".")
    return urllib.parse.quote(rel, safe="/-._~!$&'()*+,;=:@")


class Site:
    def __init__(self, log, start, max_pages, scope, mode, vdir):
        self.log, self.mode, self.vdir = log, mode, vdir
        self.start = normalize(start)
        if not self.start:
            raise RuntimeError("Not a web address (it must start with http:// or https://)")
        s = urllib.parse.urlsplit(self.start)
        host = s.netloc
        self.hosts = {host, host[4:] if host.startswith("www.") else "www." + host}
        self.prefix = s.path[: s.path.rfind("/") + 1] if scope == "section" else "/"
        self.max_pages = max_pages
        self.store = os.path.join(vdir, "site")
        os.makedirs(self.store, exist_ok=True)
        self.saved = {}       # url -> {"z", "disk", "mime", "title", "kind"}
        self.by_z = {}        # zim path -> first url saved there
        self.raw = {}         # url -> (base_url, text) for html/css still to rewrite
        self.total = 0
        self.failed = 0
        self.robots = urllib.robotparser.RobotFileParser()
        try:
            txt = self._get(f"{s.scheme}://{host}/robots.txt", small=True)[3].decode("utf-8", "replace")
            self.robots.parse(txt.splitlines())
        except Exception:  # noqa: BLE001  no robots.txt: everything allowed
            self.robots.parse([])
        self.delay = self.robots.crawl_delay(UA) or 0

    # ---------------------------------------------------------------- fetching
    def _get(self, url, small=False):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(req, timeout=20 if small else 45) as r:
            n = int(r.headers.get("Content-Length") or 0)
            if n > MAX_FILE:
                raise RuntimeError("too large")
            data = r.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise RuntimeError("too large")
            return r.geturl(), r.headers.get_content_type(), r.headers.get_content_charset(), data

    def fetch(self, url):
        if self.delay:
            time.sleep(min(self.delay, 10))
        try:
            return url, self._get(url)
        except Exception as e:  # noqa: BLE001
            return url, e

    def in_scope(self, url):
        p = urllib.parse.urlsplit(url)
        return (p.netloc in self.hosts and urllib.parse.unquote(p.path).startswith(self.prefix)
                and self.robots.can_fetch(UA, url))

    def _save(self, url, data, mime, title="", kind="asset"):
        """Store a file; returns False if another address already saved the same thing (e.g. /x/ and /x/index.html)."""
        z = zpath_for(url)
        if z in self.by_z:
            self.saved[url] = self.saved[self.by_z[z]]
            return False
        self.by_z[z] = url
        disk = hashlib.sha1(url.encode()).hexdigest()
        with open(os.path.join(self.store, disk), "wb") as f:
            f.write(data)
        self.saved[url] = {"z": z, "disk": disk, "mime": mime, "title": title, "kind": kind}
        self.total += len(data)
        return True

    # ---------------------------------------------------------------- link handling
    @staticmethod
    def _abs(base, ref):
        ref = html.unescape(ref.strip())
        if not ref or ref.lower().startswith(SKIP):
            return None, ""
        full = urllib.parse.urljoin(base, ref)
        frag = urllib.parse.urldefrag(full)[1]
        return normalize(full), frag

    def _links(self, text, base, rewrite=None, page_z=None):
        """Find (pass 1) or rewrite (pass 2) every link in an HTML document."""
        found = {"page": set(), "asset": set()}
        m = re.search(r"<base\b[^>]*href\s*=\s*[\"']?([^\"'\s>]+)", text, re.I)
        if m:
            base = urllib.parse.urljoin(base, html.unescape(m.group(1)))

        def target(ref, kind):
            url, frag = self._abs(base, ref)
            if not url:
                return None
            if rewrite is None:
                found[kind].add(url)
                return None
            info = self.saved.get(url)
            if info:
                return _rel(info["z"], page_z) + (f"#{frag}" if frag else "")
            return url + (f"#{frag}" if frag else "")  # not saved: keep the live link

        def css(block):
            def sub(m):
                ref = m.group(2) or m.group(4)
                new = target(ref, "asset")
                if new is None:
                    return m.group(0)
                return f'url("{new}")' if m.group(2) is not None else f'@import "{new}"'
            return CSS_URL_RE.sub(sub, block)

        def tag_sub(tm):
            tag = tm.group(0)
            name = tm.group(1).lower()
            if name == "base":
                return "" if rewrite else tag
            if name == "meta":
                low = tag.lower()
                if "content-security-policy" in low:
                    return "" if rewrite else tag
                if "refresh" not in low:
                    return tag
            rel = (re.search(r"""\srel\s*=\s*["']?([^"'>]+)""", tag, re.I) or [None, ""])[1].lower()

            def attr_sub(am):
                attr = am.group(2).lower()
                val = am.group(5) if am.group(5) is not None else am.group(6)
                q = am.group(4) or '"'
                if name == "meta":
                    if attr != "content":
                        return am.group(0)
                    mm = re.match(r"(\s*\d+\s*;\s*url\s*=\s*)(.+)", val, re.I)
                    if not mm:
                        return am.group(0)
                    new = target(mm.group(2), "page")
                    return am.group(0) if new is None else f'{am.group(1)}{attr}{am.group(3)}{q}{mm.group(1)}{new}{q}'
                if attr == "content":
                    return am.group(0)
                if attr in ("srcset", "data-srcset"):
                    parts = []
                    for item in val.split(","):
                        bits = item.strip().split()
                        if not bits:
                            continue
                        new = target(bits[0], "asset")
                        parts.append(" ".join([new or bits[0]] + bits[1:]))
                    return am.group(0) if rewrite is None else f'{am.group(1)}{attr}{am.group(3)}{q}{", ".join(parts)}{q}'
                if name in ("a", "area", "iframe", "frame") and attr == "href" or name in ("iframe", "frame") and attr == "src":
                    kind = "page"
                elif name == "link" and attr == "href" and not re.search(r"stylesheet|icon|preload|prefetch|modulepreload|manifest", rel):
                    kind = "page"
                else:
                    kind = "asset"
                new = target(val, kind)
                if new is None:
                    return am.group(0)
                return f'{am.group(1)}{attr}{am.group(3)}{q}{html.escape(new, quote=True)}{q}'

            tag = ATTR_RE.sub(attr_sub, tag)
            if rewrite:
                tag = re.sub(r"""\s(integrity|crossorigin)\s*=\s*(["']).*?\2""", "", tag, flags=re.I)
            return tag

        out = TAG_RE.sub(tag_sub, text)
        out = STYLE_BLOCK_RE.sub(lambda m: m.group(1) + css(m.group(2)) + m.group(3), out)
        out = STYLE_ATTR_RE.sub(lambda m: m.group(1) + m.group(2) + css(m.group(3)) + m.group(2), out)
        if rewrite and self.mode == "none":
            out = re.sub(r"<(img|video|audio|source|picture|embed)\b[^>]*>(</(video|audio|picture)>)?", "", out, flags=re.I)
        return found if rewrite is None else out

    def _css_links(self, text, base, rewrite=False, css_z=None):
        found = set()

        def sub(m):
            ref = m.group(2) or m.group(4)
            url, frag = self._abs(base, ref)
            if not url:
                return m.group(0)
            if not rewrite:
                found.add(url)
                return m.group(0)
            info = self.saved.get(url)
            new = _rel(info["z"], css_z) if info else url
            new += f"#{frag}" if frag else ""
            return f'url("{new}")' if m.group(2) is not None else f'@import "{new}"'

        out = CSS_URL_RE.sub(sub, text)
        return out if rewrite else found

    # ---------------------------------------------------------------- crawl
    @staticmethod
    def _decode(data, charset):
        if not charset:
            m = re.search(rb"""<meta[^>]+charset\s*=\s*["']?([\w-]+)""", data[:4096], re.I)
            charset = m.group(1).decode() if m else "utf-8"
        try:
            return data.decode(charset, "replace")
        except LookupError:
            return data.decode("utf-8", "replace")

    def crawl(self):
        log = self.log
        pages, queued, assets = deque([self.start]), {self.start}, set()
        n_pages = 0
        with ThreadPoolExecutor(1 if self.delay else 4) as pool:
            while pages and n_pages < self.max_pages:
                batch = [pages.popleft() for _ in range(min(8, len(pages)))]
                for url, res in pool.map(self.fetch, batch):
                    if isinstance(res, Exception):
                        self.failed += 1
                        continue
                    final, mime, charset, data = res
                    if mime in HTML_TYPES:
                        if n_pages >= self.max_pages:
                            continue
                        text = self._decode(data, charset)
                        t = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
                        title = " ".join(html.unescape(t.group(1)).split()) if t else ""
                        if not self._save(url, b"", "text/html", title, "page"):
                            continue
                        self.raw[url] = (final, text)
                        n_pages += 1
                        found = self._links(text, final)
                        for u in found["page"]:
                            if u not in queued and self.in_scope(u):
                                if AV_EXT.search(urllib.parse.urlsplit(u).path):
                                    continue
                                if NOT_PAGE_EXT.search(urllib.parse.urlsplit(u).path):
                                    assets.add(u)
                                else:
                                    queued.add(u)
                                    pages.append(u)
                        assets |= found["asset"]
                    elif mime.split("/")[0] not in ("video", "audio"):
                        self._save(url, data, mime)
                log.stage(f"Saving website: {n_pages} pages", n_pages, self.max_pages)
            if n_pages >= self.max_pages and pages:
                log(f"Reached the page limit ({self.max_pages}); {len(pages)} more pages were found but not saved")

            # assets (any host: sites often use CDNs), including what stylesheets refer to
            todo = deque(u for u in assets if u not in self.saved)
            seen = set(todo)
            n = 0
            while todo and n < MAX_ASSETS and self.total < MAX_TOTAL:
                batch = [todo.popleft() for _ in range(min(16, len(todo)))]
                batch = [u for u in batch if not AV_EXT.search(urllib.parse.urlsplit(u).path)]
                if self.mode == "none":
                    batch = [u for u in batch if not MEDIA_EXT.search(urllib.parse.urlsplit(u).path)]
                for url, res in pool.map(self.fetch, batch):
                    n += 1
                    if isinstance(res, Exception):
                        self.failed += 1
                        continue
                    final, mime, charset, data = res
                    kind = mime.split("/")[0]
                    if kind in ("video", "audio") or (self.mode == "none" and kind == "image"):
                        continue
                    if mime == "text/css" or url.lower().split("?")[0].endswith(".css"):
                        text = self._decode(data, charset)
                        if not self._save(url, b"", "text/css"):
                            continue
                        self.raw[url] = (final, text)
                        for u in self._css_links(text, final):
                            if u not in seen and u not in self.saved:
                                seen.add(u)
                                todo.append(u)
                    else:
                        self._save(url, data, mime or "application/octet-stream")
                log.stage(f"Saving website files: {n} of {len(seen)}", n, len(seen))
        return n_pages

    def rewrite(self):
        for url, (base, text) in self.raw.items():
            info = self.saved[url]
            if info["mime"] == "text/css":
                out = self._css_links(text, base, rewrite=True, css_z=info["z"])
            else:
                out = self._links(text, base, rewrite=True, page_z=info["z"])
                out = re.sub(r"<meta[^>]+charset\s*=\s*[\"']?[\w-]+[\"']?[^>]*>", '<meta charset="utf-8">', out, count=1, flags=re.I)
                if not re.search(r"<meta[^>]+charset", out, re.I):
                    out = re.sub(r"(<head[^>]*>)", r'\1<meta charset="utf-8">', out, count=1, flags=re.I)
            with open(os.path.join(self.store, info["disk"]), "w", encoding="utf-8") as f:
                f.write(out)


def peek(url):
    """Quick look for the 'Check links' button: title and whether robots.txt allows it."""
    s = Site(lambda *_: None, url, 1, "site", "pictures", os.path.join(C.HOME, "peek"))
    final, mime, charset, data = s._get(s.start, small=True)
    text = Site._decode(data, charset)
    t = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    return {"title": " ".join(html.unescape(t.group(1)).split()) if t else s.start,
            "allowed": s.robots.can_fetch(UA, s.start), "html": mime in HTML_TYPES}


def prepare(log, url, opts, mode, vdir):
    max_pages = max(1, min(int(opts.get("max_pages") or 300), 50000))
    scope = opts.get("scope", "site")
    site = Site(log, url, max_pages, scope, mode, vdir)
    if not site.robots.can_fetch(UA, site.start):
        raise RuntimeError("This site's robots.txt asks automated tools not to download it, so ZIM Creator won't.")
    log(f"[{site.start}] saving up to {max_pages} pages" + (f" under {site.prefix}" if scope == "section" else ""))
    n = site.crawl()
    if not n:
        raise RuntimeError("No pages could be downloaded from this address.")
    site.rewrite()
    log(f"[{site.start}] {n} pages and {len(site.saved) - n} other files ({site.total / 1e6:.0f} MB)"
        + (f"; {site.failed} could not be downloaded" if site.failed else ""))
    start = site.saved[site.start]
    unique = {i["z"]: i for i in site.saved.values()}
    files = [{"path": i["z"], "disk": os.path.join("site", i["disk"]), "mime": i["mime"], "title": i["title"]}
             for i in unique.values()]
    host = urllib.parse.urlsplit(site.start).netloc
    title = start["title"] if start["title"] and not re.match(r"https?://", start["title"]) else host
    return {"kind": "site", "title": title, "creator": host, "publisher": host,
            "date": time.strftime("%Y-%m-%d"), "language": "eng", "source": site.start,
            "main": start["z"], "files": files}


mimetypes.add_type("image/webp", ".webp")
