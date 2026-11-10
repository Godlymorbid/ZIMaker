"""Shared helpers: paths, logging/progress, downloads, text cleanup, filenames."""
import functools
import html
import http.client
import ipaddress
import json
import ntpath
import os
import re
import select
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

# In a Docker container (ZIMAKER_DOCKER=1, set by the Dockerfile) there is no desktop: no window to open, no file
# manager, nothing to install into. Settings and jobs live in a volume (ZIMAKER_HOME), and the libraries are part of
# the image (ZIMAKER_RUNTIME), so a new image brings new libraries and the volume keeps only the user's own things.
DOCKER = os.environ.get("ZIMAKER_DOCKER") == "1"
HOME = os.environ.get("ZIMAKER_HOME") or os.path.expanduser("~/.zimaker")
_OLD_HOME = os.path.expanduser("~/.zim-creator")  # before the rename to ZIMaker: keep libraries and job history
if not os.path.exists(HOME) and os.path.isdir(_OLD_HOME) and not os.environ.get("ZIMAKER_HOME"):
    try:
        os.rename(_OLD_HOME, HOME)
    except OSError:
        pass
RUNTIME = os.environ.get("ZIMAKER_RUNTIME") or HOME   # where the libraries and helper programs are unpacked
LIB = os.path.join(RUNTIME, "lib")         # Python wheels unpacked here (libzim, Pillow, numpy, PyMuPDF)
TOOLS = os.path.join(RUNTIME, "zim-tools")  # openZIM zim-tools (zimcheck)
JOBS = os.path.join(HOME, "jobs")
UPLOADS = os.path.join(HOME, "uploads")
UA = {"User-Agent": "zimaker/1.0 (personal archiving tool)"}

# How many pages are processed at once. WSL often has only ~3 GB of RAM, so keep this modest.
WORKERS = max(1, min(6, (os.cpu_count() or 2) - 1))   # processes for page images (each needs up to ~100 MB)
# threads libzim uses to compress and index while writing a ZIM (its own default is 4, whatever the computer has)
ZIM_WORKERS = max(4, min(os.cpu_count() or 4, 10))
BATCH = 24


def use_lib():
    if LIB not in sys.path:
        sys.path.insert(0, LIB)


class Log:
    """Writes log.txt and state.json into a job folder; the web UI reads both."""

    def __init__(self, job_dir):
        self.dir = job_dir
        self.warnings = []   # something was left out (a page, a message, a file): the ZIM isn't the whole original
        os.makedirs(job_dir, exist_ok=True)

    def __call__(self, msg):
        line = time.strftime("%H:%M:%S  ") + str(msg)
        with open(os.path.join(self.dir, "log.txt"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
        print(line, flush=True)

    def warn(self, msg):
        """Log something that was left out; the job then never deletes its original (watched folder)."""
        self.warnings.append(str(msg))
        self(f"WARNING: {msg}")

    def stage(self, text, done=None, total=None, status="running"):
        tmp = os.path.join(self.dir, "state.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"status": status, "stage": text, "done": done, "total": total, "t": time.time()}, f)
        os.replace(tmp, os.path.join(self.dir, "state.json"))


# ---------------------------------------------------------------- proxy
# Every download (websites, archive.org, libraries, updates) goes through the proxy chosen in Settings, if any.
# With a proxy chosen, nothing connects directly: if the proxy is down or misconfigured, downloads fail instead
# of quietly going around it. Names are looked up by the proxy too (SOCKS: remote DNS; curl: socks5h).
PROXY = {}  # the proxy in use: {"type": "http" | "socks5", "host", "port", "user", "password"}; empty = none


def clean_proxy(p):
    p = p if isinstance(p, dict) else {}
    try:
        port = int(p.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    return {"type": p.get("type") if p.get("type") in ("off", "http", "socks5") else "off",
            "host": str(p.get("host") or "").strip()[:255], "port": port if 0 < port < 65536 else 0,
            "user": str(p.get("user") or "")[:255], "password": str(p.get("password") or "")[:255]}


def _proxy_problem(p):
    if not re.fullmatch(r"[A-Za-z0-9.\-]+|\[[0-9A-Fa-f:.]+\]", p["host"]):
        return "its address is missing or not valid"
    if not p["port"]:
        return "its port is missing"
    return None


def _recv(s, n):
    b = b""
    while len(b) < n:
        chunk = s.recv(n - len(b))
        if not chunk:
            raise OSError("the SOCKS proxy closed the connection")
        b += chunk
    return b


def _socks_connect(p, host, port, timeout):
    """A connection to host:port through a SOCKS5 proxy. The proxy looks up the name, so no DNS leaks."""
    s = socket.create_connection((p["host"].strip("[]"), p["port"]), timeout)
    try:
        login = bool(p["user"])
        s.sendall(b"\x05\x01" + (b"\x02" if login else b"\x00"))
        ver, method = _recv(s, 2)
        if ver != 5:
            raise OSError("that address isn't a SOCKS5 proxy")
        if method == 2 and login:
            u, pw = p["user"].encode()[:255], p["password"].encode()[:255]
            s.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(pw)]) + pw)
            if _recv(s, 2)[1] != 0:
                raise OSError("the SOCKS proxy didn't accept the username or password")
        elif method != 0:
            raise OSError("the SOCKS proxy wants a login (set a username and password in Settings)")
        h = host.strip("[]")
        try:  # an address: sent as one; a name: sent as a name, for the proxy to look up (no local DNS)
            ip = ipaddress.ip_address(h)
            dest = (b"\x01" if ip.version == 4 else b"\x04") + ip.packed
        except ValueError:
            name = h.encode("idna")
            if len(name) > 255:
                raise OSError(f"the web address's name is too long: {h[:60]}…") from None
            dest = b"\x03" + bytes([len(name)]) + name
        s.sendall(b"\x05\x01\x00" + dest + struct.pack(">H", port))
        reply = _recv(s, 4)
        if reply[0] != 5:
            raise OSError("the SOCKS proxy sent an invalid reply")
        if reply[1] != 0:
            why = {2: "not allowed by the proxy", 3: "network unreachable", 4: "host unreachable",
                   5: "connection refused", 6: "timed out"}.get(reply[1], f"SOCKS error {reply[1]}")
            raise OSError(f"the proxy couldn't reach {host} ({why})")
        _recv(s, {1: 4, 4: 16}.get(reply[3]) or _recv(s, 1)[0])  # the address the proxy bound: not needed
        _recv(s, 2)
        return s
    except BaseException:
        s.close()
        raise


# ---------------------------------------------------------------- connections kept open (website crawls)
# A crawl asks one site for hundreds of files. Opening a connection for each costs a TCP and TLS handshake every
# time (through Tor, a new stream as well), so a crawl keeps its connections open and uses them again.
# Which way a request goes is still decided by the handlers below, exactly as without this: a kept connection is
# only looked for at the point where that handler would otherwise open a new one, and only among connections the
# same handler opened to the same place (same proxy, same tunnel). So nothing can go around the proxy, and a
# connection made for one network (Tor, I2P, a local gateway) is never used for another.
_KEEP = threading.local()   # .pool: the KeepAlive this thread's requests may use (None: a new connection each time)


def _has_data(sock):
    """Something to read on a connection nobody is using: the server closed it (or sent something unasked)."""
    try:
        if getattr(sock, "pending", None) and sock.pending():
            return True
        return bool(select.select([sock], [], [], 0)[0])
    except (OSError, ValueError):
        return True


class KeepAlive:
    """The idle connections of one crawl. close() when the crawl is over."""

    def __init__(self, per_place=6, most=32, idle=30):
        self.per_place, self.most, self.max_idle = per_place, most, idle
        self.lock = threading.Lock()
        self.idle = {}        # (handler, host, tunnel host) -> [(connection, when it was put back)]
        self.closed = False
        self.opened = self.reused = 0

    def use(self):
        """with pool.use(): this thread's requests use the pool's connections (each thread says so itself)."""
        return _Using(self)

    def take(self, key):
        while True:
            with self.lock:
                waiting = self.idle.get(key)
                if not waiting:
                    return None
                h, t = waiting.pop()
            if h.sock is not None and time.monotonic() - t < self.max_idle and not _has_data(h.sock):
                return h
            h.close()

    def give(self, key, h):
        drop = [h]
        with self.lock:
            if not self.closed and h.sock is not None and len(self.idle.get(key, ())) < self.per_place:
                drop = []
                now = time.monotonic()
                if sum(map(len, self.idle.values())) >= self.most:   # too many open: the stale ones, else the oldest
                    for k in list(self.idle):
                        keep = [(c, t) for c, t in self.idle[k] if now - t < self.max_idle]
                        drop += [c for c, t in self.idle[k] if now - t >= self.max_idle]
                        if keep:
                            self.idle[k] = keep
                        else:
                            del self.idle[k]
                    if sum(map(len, self.idle.values())) >= self.most:
                        k = min(self.idle, key=lambda k: self.idle[k][0][1])
                        drop.append(self.idle[k].pop(0)[0])
                        if not self.idle[k]:
                            del self.idle[k]
                self.idle.setdefault(key, []).append((h, now))
        for c in drop:
            c.close()

    def close(self):
        with self.lock:
            self.closed = True
            conns = [c for v in self.idle.values() for c, _ in v]
            self.idle.clear()
        for c in conns:
            c.close()


class _Using:
    def __init__(self, pool):
        self.pool = pool

    def __enter__(self):
        self.before = getattr(_KEEP, "pool", None)
        _KEEP.pool = self.pool

    def __exit__(self, *exc):
        _KEEP.pool = self.before


class _KeepResponse(http.client.HTTPResponse):
    """An answer that hands its connection back for the next request once it has been read to its end.
    Closed before that (an error page nobody reads, a file that turned out too large): the connection is closed
    too, since what is left of the answer would be mistaken for the next one."""
    _keep = None
    _by_caller = False

    def close(self):
        self._by_caller = True
        super().close()

    def _close_conn(self):
        super()._close_conn()
        keep, self._keep = self._keep, None
        if keep:
            pool, key, h = keep
            if not self._by_caller and not self.will_close and (self.chunked or self.length == 0):
                pool.give(key, h)
            else:
                h.close()


class _Keep:
    """For urllib's HTTP(S) handlers: where they would open a connection, use one this crawl already has open to
    the same place, if any. Requests outside a crawl (no pool) are untouched."""

    def do_open(self, http_class, req, **args):
        pool = getattr(_KEEP, "pool", None)
        if pool is None or req.data is not None or not req.host:
            return super().do_open(http_class, req, **args)
        host = req.host
        key = (self, host, req._tunnel_host)   # this handler's own connections only: see the note above
        headers = dict(req.unredirected_hdrs)
        headers.update({k: v for k, v in req.headers.items() if k not in headers})
        headers["Connection"] = "keep-alive"
        headers = {name.title(): val for name, val in headers.items()}
        tunnel = {}
        if req._tunnel_host and "Proxy-Authorization" in headers:
            tunnel["Proxy-Authorization"] = headers.pop("Proxy-Authorization")
        timed = isinstance(req.timeout, (int, float))
        h = pool.take(key)
        while True:
            reused, sent = h is not None, False
            try:
                if reused:
                    if timed:
                        h.timeout = req.timeout
                        h.sock.settimeout(req.timeout)
                else:
                    h = http_class(host, timeout=req.timeout, **args)
                    h.response_class = _KeepResponse
                    if req._tunnel_host:
                        h.set_tunnel(req._tunnel_host, headers=tunnel)
                h.request(req.get_method(), req.selector, None, headers)
                sent = True
                r = h.getresponse()
                break
            except BaseException as e:
                if h is not None:
                    h.close()
                # a connection that sat idle may have been closed by the server in the meantime: once more, on a
                # new one (only a request that can safely be asked twice gets here: no data is ever sent)
                if reused and isinstance(e, (OSError, http.client.HTTPException)) and not isinstance(e, TimeoutError):
                    h = None
                    continue
                if not sent and isinstance(e, OSError):   # couldn't connect or send: reported as urllib does
                    raise urllib.error.URLError(e) from e
                raise
        if reused:
            pool.reused += 1
        else:
            pool.opened += 1
        if not r.will_close and h.sock is not None:
            r._keep = (pool, key, h)
        r.url = req.get_full_url()
        r.msg = r.reason
        return r


class _KeepHTTP(_Keep, urllib.request.HTTPHandler):
    pass


class _KeepHTTPS(_Keep, urllib.request.HTTPSHandler):
    pass


class _SocksHTTP(http.client.HTTPConnection):
    def __init__(self, *a, proxy, **kw):
        super().__init__(*a, **kw)
        self.proxy = proxy

    def connect(self):
        self.sock = _socks_connect(self.proxy, self.host, self.port, self.timeout)


class _SocksHTTPS(http.client.HTTPSConnection):
    def __init__(self, *a, proxy, **kw):
        super().__init__(*a, **kw)
        self.proxy = proxy

    def connect(self):
        sock = _socks_connect(self.proxy, self.host, self.port, self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _SocksHandler(_Keep, urllib.request.HTTPHandler):
    def __init__(self, proxy):
        super().__init__()
        self.proxy = proxy

    def http_open(self, req):
        return self.do_open(lambda host, **kw: _SocksHTTP(host, proxy=self.proxy, **kw), req)


class _SocksHTTPSHandler(_Keep, urllib.request.HTTPSHandler):
    def __init__(self, proxy):
        super().__init__()
        self.proxy = proxy

    def https_open(self, req):
        return self.do_open(lambda host, **kw: _SocksHTTPS(host, proxy=self.proxy, **kw), req, context=self._context)


class _Refuse(urllib.request.BaseHandler):
    """Runs before the normal handlers: refuses what would otherwise connect directly despite the proxy."""
    handler_order = 100

    def __init__(self, why=None):
        self.why = why

    def _refuse(self, req):
        raise urllib.error.URLError(f"the proxy can't be used: {self.why or 'it only carries web addresses'}. "
                                    "Check ⚙ Settings → Connection and privacy")

    def ftp_open(self, req):
        return self._refuse(req)

    def http_open(self, req):
        return self._refuse(req) if self.why else None

    https_open = http_open


ONION_NEEDS_TOR = ("This is a Tor onion site, which can only be reached through Tor. In ⚙ Settings → Connection and "
                   "privacy, choose Tor Browser or Tor service (Tor must be running), then try again.")


def _host_of(url_or_host):
    """The host name of a URL or of a "host" / "host:port" string (urllib's req.host has the port)."""
    try:
        return urllib.parse.urlsplit(url_or_host if "//" in url_or_host else "//" + url_or_host).hostname or ""
    except ValueError:
        return ""


def is_onion(url_or_host):
    return _host_of(url_or_host).rstrip(".").endswith(".onion")


# I2P sites (.i2p): only through the user's I2P router (Java I2P or i2pd), whose HTTP proxy looks the name up
# inside I2P. Only .i2p addresses go there; everything else keeps using the proxy above (or none).
I2P = {}  # the router in use: {"host", "port"}; empty = I2P off
I2P_NEEDS_ROUTER = ("This is an I2P site, which can only be reached through an I2P router. Install and start I2P or "
                    "i2pd, turn on ⚙ Settings → Connection and privacy → I2P sites, then try again.")


def clean_i2p(p):
    p = p if isinstance(p, dict) else {}
    try:
        port = int(p.get("port") or 4444)
    except (TypeError, ValueError):
        port = 4444
    return {"on": bool(p.get("on")), "host": str(p.get("host") or "127.0.0.1").strip()[:255],
            "port": port if 0 < port < 65536 else 4444}


def is_i2p(url_or_host):
    return _host_of(url_or_host).rstrip(".").endswith(".i2p")


def i2p_ok():
    return bool(I2P) and not _proxy_problem(I2P)


# Hyphanet (formerly Freenet): its content is read through the user's own node, whose web gateway (FProxy, normally
# http://127.0.0.1:8888) fetches a key like USK@…/site/5/ from the network. The gateway runs on this computer, so it
# is reached directly, never through the proxy above (which couldn't reach a local address anyway).
HYPHANET = {}  # the node's gateway: {"host", "port"}; empty = off
HYPHANET_NEEDS_NODE = ("This is a Hyphanet (Freenet) key, which can only be read through a Hyphanet node. Install and "
                       "start Hyphanet, turn on ⚙ Settings → Connection and privacy → Hyphanet, then try again.")
LOOPBACK = ("127.0.0.1", "localhost", "::1", "[::1]")
KEY_RE = re.compile(r"(USK|SSK|CHK|KSK)@\S+")


def clean_hyphanet(p):
    p = p if isinstance(p, dict) else {}
    try:
        port = int(p.get("port") or 8888)
    except (TypeError, ValueError):
        port = 8888
    host = str(p.get("host") or "127.0.0.1").strip().lower()
    return {"on": bool(p.get("on")), "host": host if host in LOOPBACK else "127.0.0.1",   # only this computer's node
            "port": port if 0 < port < 65536 else 8888}


def hyphanet_key(s):
    """'USK@…/site/5/', 'freenet:USK@…', 'hyphanet:…' or a gateway link (http://127.0.0.1:8888/USK@…) -> the key
    ('USK@…/site/5/'), else None."""
    s = s.strip()
    s = re.sub(r"^(?:freenet|hyphanet|hyph):/*", "", s, flags=re.I)
    m = re.match(r"https?://([^/]+)/+(.*)$", s, re.I)
    if m:
        if (_host_of(m.group(1)) or "").lower() not in ("127.0.0.1", "localhost", "::1"):
            return None
        s = re.sub(r"^(?:freenet|hyphanet|hyph):/*", "", urllib.parse.unquote(m.group(2)), flags=re.I)
    s = s.lstrip("/")
    return s if KEY_RE.fullmatch(s) else None


def hyphanet_url(key):
    g = HYPHANET or {"host": "127.0.0.1", "port": 8888}
    host = "[::1]" if g["host"] in ("::1", "[::1]") else g["host"]
    return f"http://{host}:{g['port']}/" + urllib.parse.quote(key, safe="/@~,-._!$()*+;=")


def is_hyphanet(url):
    """A gateway link: this computer's address with a Hyphanet key as its path."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return (p.hostname or "") in ("127.0.0.1", "localhost", "::1") and \
        bool(KEY_RE.match(urllib.parse.unquote(p.path).lstrip("/")))


def hyphanet_ok():
    return bool(HYPHANET)


def hyphanet_root(url):
    """The freesite a gateway link belongs to: /USK@key/name/ (every edition), /SSK@key/name-5/, /CHK@key/."""
    segs = urllib.parse.unquote(urllib.parse.urlsplit(url).path).lstrip("/").split("/")
    n = 1 if segs[0].startswith(("CHK@", "KSK@")) else 2
    return "/" + "/".join(segs[:n]) + "/"


class _Hyphanet(urllib.request.BaseHandler):
    """Runs before the proxy handlers: Hyphanet keys go straight to the local node's gateway, and only when
    Hyphanet is turned on. The gateway shows a "confirm" page instead of files over its size limit, so the
    request (not the address saved) asks for a larger limit."""
    handler_order = 84

    def __init__(self, gateway=None):
        self.gateway = gateway
        self._http = _KeepHTTP()

    def http_open(self, req):
        if not is_hyphanet(req.full_url):
            return None
        g = self.gateway
        if not g:
            raise urllib.error.URLError(HYPHANET_NEEDS_NODE)
        if (urllib.parse.urlsplit(req.full_url).port or 80) != g["port"]:
            raise urllib.error.URLError(f"this Hyphanet link is for a gateway at {req.host}, but Hyphanet in ⚙ Settings "
                                        f"is set to port {g['port']}")
        req.selector += ("&" if "?" in req.selector else "?") + "max-size=500000000"
        return self._http.http_open(req)

    def https_open(self, req):
        if is_hyphanet(req.full_url):
            raise urllib.error.URLError("Hyphanet's gateway uses http://, not https://")
        return None


# IPFS: content addressed by what it is (a CID), not by a server. Read through the user's own IPFS program (IPFS
# Desktop or Kubo: a gateway on this computer, reached directly) or else, if allowed, a public gateway (through the
# proxy, like any website; that gateway sees what is fetched). Always as a "subdomain" address (<cid>.ipfs.<gateway>),
# where a site's links that start with / still work, unlike gateway/ipfs/<cid>/.
IPFS = {}  # {"on": local node, "port", "public": fall back to a public gateway, "gateway": its host}; empty = never set
IPFS_NEEDS_NODE = ("This is an IPFS address. Start IPFS Desktop (or Kubo) and turn on ⚙ Settings → Connection and "
                   "privacy → IPFS, or allow a public gateway there, then try again.")
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def clean_ipfs(p):
    p = p if isinstance(p, dict) else {}
    try:
        port = int(p.get("port") or 8080)
    except (TypeError, ValueError):
        port = 8080
    gw = str(p.get("gateway") or "dweb.link").strip().lower()
    gw = re.sub(r"^https?://|/.*$", "", gw)
    return {"on": bool(p.get("on")), "port": port if 0 < port < 65536 else 8080,
            "public": bool(p.get("public", True)),
            "gateway": gw if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", gw) else "dweb.link"}


def _varints(b, n):
    out, i = [], 0
    for _ in range(n):
        v = shift = 0
        while True:
            if i >= len(b):
                raise ValueError("short")
            v |= (b[i] & 0x7F) << shift
            shift += 7
            i += 1
            if not b[i - 1] & 0x80:
                break
        out.append(v)
    return out, i


def _cid_bytes(s):
    """A CID in any common text form -> its binary CIDv1, or None if it isn't one."""
    import base64
    try:
        if re.fullmatch(r"Qm[1-9A-HJ-NP-Za-km-z]{44}", s):   # CIDv0: a base58 sha2-256 multihash
            n = 0
            for ch in s:
                n = n * 58 + B58.index(ch)
            mh = n.to_bytes(34, "big")
            return b"\x01\x70" + mh if mh[:2] == b"\x12\x20" else None
        base, rest = s[0], s[1:]
        if base in "bB":
            raw = base64.b32decode(rest.upper() + "=" * (-len(rest) % 8))
        elif base == "k":
            n = int(rest, 36)
            raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        elif base == "z":
            n = 0
            for ch in rest:
                n = n * 58 + B58.index(ch)
            raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        elif base in "fF":
            raw = bytes.fromhex(rest)
        else:
            return None
        (version, _codec, _hash, length), i = _varints(raw, 4)
        return raw if version == 1 and len(raw) - i == length and 0 < length <= 64 else None
    except (ValueError, IndexError):
        return None


def _b32(raw):
    import base64
    return "b" + base64.b32encode(raw).decode().lower().rstrip("=")


def _b36(raw):
    n, s = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 36)
        s = "0123456789abcdefghijklmnopqrstuvwxyz"[r] + s
    return "k" + s


def _ipfs_label(ns, ident):
    """The subdomain label for a CID (base32) or an IPNS name (a key in base36, or a DNSLink domain with its dots
    and dashes written the gateway way: en.wikipedia-on-ipfs.org -> en-wikipedia--on--ipfs-org)."""
    if ns == "ipns" and "." in ident:
        if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", ident.lower()):
            return None
        return ident.lower().replace("-", "--").replace(".", "-")
    if ns == "ipns" and re.fullmatch(r"[a-z0-9]+(--?[a-z0-9]+)*", ident) and "-" in ident:
        return ident   # already written the gateway way
    raw = _cid_bytes(ident)
    if not raw:
        return None
    return _b36(raw) if ns == "ipns" else _b32(raw)


def ipfs_ref(s):
    """ipfs://…, ipns://…, /ipfs/…, a bare CID, or a link to any IPFS gateway (gateway/ipfs/<cid>/… or
    <cid>.ipfs.gateway/…) -> (namespace, subdomain label, path), else None."""
    s = s.strip()
    m = (re.match(r"(ipfs|ipns)://([^/?#]+)(.*)$", s, re.I) or re.match(r"/(ipfs|ipns)/([^/?#]+)(.*)$", s, re.I)
         or re.match(r"https?://[^/]+/(ipfs|ipns)/([^/?#]+)(.*)$", s, re.I))
    if m:
        ns, ident, path = m.group(1).lower(), m.group(2), m.group(3)
    else:
        m = re.match(r"https?://([a-z0-9-]+)\.(ipfs|ipns)\.[^/]+(.*)$", s, re.I)
        if m:
            ident, ns, path = m.group(1), m.group(2).lower(), m.group(3)
        else:
            m = re.fullmatch(r"(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{50,})(/\S*)?", s)
            if not m:
                return None
            ns, ident, path = "ipfs", m.group(1), m.group(2) or ""
    label = _ipfs_label(ns, ident)
    if not label or len(label) > 63:
        return None
    if not path or path[0] == "#":
        path = "/"
    elif path[0] != "/":
        path = "/" + path
    return ns, label, path.split("#")[0]


def ipfs_url(ref):
    """The address to fetch: through this computer's IPFS program if it's turned on, else the public gateway
    (None when neither is allowed)."""
    ns, label, path = ref
    cfg = IPFS or clean_ipfs({})
    if cfg["on"]:
        return f"http://{label}.{ns}.localhost:{cfg['port']}{path}"
    if cfg["public"]:
        return f"https://{label}.{ns}.{cfg['gateway']}{path}"
    return None


def ipfs_path_urls(url):
    """For a public subdomain address that couldn't be reached (some networks block *.ipfs.* names): the same
    content as gateway/ipfs/<cid>/… on the same gateway. (Not ipfs.io: it now only serves browsers.)"""
    p = urllib.parse.urlsplit(url)
    m = re.fullmatch(r"([a-z0-9-]+)\.(ipfs|ipns)\.(.+)", p.hostname or "")
    if not m:
        return []
    label, ns, gw = m.groups()
    if ns == "ipns" and "-" in label:   # a DNSLink name written the gateway way: back to its dots
        label = label.replace("--", "\0").replace("-", ".").replace("\0", "-")
    rest = (p.path or "/") + ("?" + p.query if p.query else "")
    return [f"https://{gw}/{ns}/{label}{rest}"]


def is_ipfs(url):
    """'local' (this computer's IPFS gateway), 'public' (a public gateway) or ''."""
    try:
        host = urllib.parse.urlsplit(url if "//" in url else "//" + url).hostname or ""
    except ValueError:
        return ""
    m = re.fullmatch(r"[a-z0-9-]+\.(?:ipfs|ipns)\.(.+)", host)
    if not m:
        return ""
    return "local" if m.group(1) == "localhost" else "public"


class _IPFSLocal(urllib.request.BaseHandler):
    """Runs before the proxy handlers: <cid>.ipfs.localhost goes straight to this computer's IPFS gateway (with the
    name as the Host header, which is how the gateway knows what is asked for), never to DNS or the proxy."""
    handler_order = 83

    def __init__(self, node=None):
        self.node = node
        self._http = _KeepHTTP()

    def http_open(self, req):
        if is_ipfs(req.host or "") != "local":
            return None
        if not self.node:
            raise urllib.error.URLError(IPFS_NEEDS_NODE)
        port = self.node["port"]
        return self.do_open(lambda host, **kw: http.client.HTTPConnection("127.0.0.1", port, **kw), req)

    def do_open(self, conn, req):
        return self._http.do_open(conn, req)

    def https_open(self, req):
        if is_ipfs(req.host or "") == "local":
            raise urllib.error.URLError("this computer's IPFS gateway uses http://, not https://")
        return None


def is_darknet(url):
    """Onion, I2P, Hyphanet or IPFS: slow networks, so longer timeouts, fewer requests at once and retries."""
    return is_onion(url) or is_i2p(url) or is_hyphanet(url) or bool(is_ipfs(url))


class _I2P(urllib.request.BaseHandler):
    """Runs before the proxy handlers: .i2p addresses go to the I2P router's HTTP proxy and nowhere else
    (refused, before any DNS lookup, when I2P is off)."""
    handler_order = 85

    def __init__(self, router=None):
        self.router = router
        self._http, self._https = _KeepHTTP(), _KeepHTTPS(context=ssl.create_default_context())

    def _open(self, req):
        if not is_i2p(req.host or ""):
            return None
        r = self.router
        if not r or _proxy_problem(r):
            raise urllib.error.URLError(I2P_NEEDS_ROUTER)
        req.set_proxy(f"{r['host']}:{r['port']}", "http")   # http: absolute URL to the router; https: CONNECT
        if req.type == "https":
            return self._https.https_open(req)
        return self._http.http_open(req)

    http_open = https_open = _open


def onion_ok():
    """Onion sites only through a SOCKS5 proxy: it looks the name up inside Tor. Anything else would send the
    .onion name to an ordinary DNS server or proxy, telling them which onion site was asked for."""
    return bool(PROXY) and PROXY["type"] == "socks5" and not _proxy_problem(PROXY)


class _NoOnion(urllib.request.BaseHandler):
    """Without a SOCKS5 proxy, refuse .onion addresses before any connection (or DNS lookup) is made."""
    handler_order = 90

    def _check(self, req):
        if is_onion(req.host or ""):
            raise urllib.error.URLError(ONION_NEEDS_TOR)
        return None

    http_open = https_open = _check


class _WebOnly(urllib.request.BaseHandler):
    """ZIMaker only ever downloads http/https addresses. Others come from the web too (a link in a page or a IIIF
    manifest), and file:// would read the computer's own files (urllib also looks its host name up locally)."""
    handler_order = 80

    def _refuse(self, req):
        raise urllib.error.URLError(f"ZIMaker only downloads web addresses (http/https), not {req.type}: links")

    file_open = ftp_open = data_open = _refuse


def proxy_opener(p, i2p=None, hyphanet=None, ipfs=None):
    """A urllib opener that sends everything through proxy p (and nothing around it); .i2p addresses through
    the I2P router i2p, Hyphanet keys and <cid>.ipfs.localhost to the local gateways hyphanet and ipfs
    ({"host"/"port"}, or None: refused)."""
    i2 = [_I2P(i2p), _Hyphanet(hyphanet), _IPFSLocal(ipfs)]
    if p["type"] == "off":
        return urllib.request.build_opener(_KeepHTTP(), _KeepHTTPS(), _WebOnly(), *i2, _NoOnion())
    why = _proxy_problem(p)
    if why:
        return urllib.request.build_opener(_WebOnly(), *i2, _Refuse(why))
    if p["type"] == "http":
        login = (urllib.parse.quote(p["user"], safe="") + ":" + urllib.parse.quote(p["password"], safe="") + "@"
                 if p["user"] else "")
        url = f"http://{login}{p['host']}:{p['port']}"
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": url, "https": url}), _KeepHTTP(),
                                           _KeepHTTPS(), _WebOnly(), *i2, _Refuse(), _NoOnion())
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _SocksHandler(p), _SocksHTTPSHandler(p),
                                       _WebOnly(), *i2, _Refuse())


def net_init(p=None, i2p=None, hyphanet=None, ipfs=None):
    """Use the proxy, I2P router and Hyphanet gateway from settings.json (or the arguments) for every download
    this process makes."""
    if p is None or i2p is None or hyphanet is None or ipfs is None:
        try:
            s = json.load(open(os.path.join(HOME, "settings.json"), encoding="utf-8"))
            p = s.get("proxy") if p is None else p
            i2p = s.get("i2p") if i2p is None else i2p
            hyphanet = s.get("hyphanet") if hyphanet is None else hyphanet
            ipfs = s.get("ipfs") if ipfs is None else ipfs
        except (OSError, ValueError, AttributeError):
            pass
    p, i2p, hyphanet, ipfs = clean_proxy(p), clean_i2p(i2p), clean_hyphanet(hyphanet), clean_ipfs(ipfs)
    PROXY.clear()
    if p["type"] != "off":
        PROXY.update(p)
        os.environ.pop("no_proxy", None), os.environ.pop("NO_PROXY", None)  # urllib would go around the proxy for these
    I2P.clear()
    if i2p["on"]:
        I2P.update(host=i2p["host"], port=i2p["port"])
    HYPHANET.clear()
    if hyphanet["on"]:
        HYPHANET.update(host=hyphanet["host"], port=hyphanet["port"])
    IPFS.clear()
    IPFS.update(ipfs)
    urllib.request.install_opener(proxy_opener(p, dict(I2P) or None, dict(HYPHANET) or None,
                                               {"port": ipfs["port"]} if ipfs["on"] else None))


def _proxy_url():
    scheme = "socks5h" if PROXY["type"] == "socks5" else "http"
    login = (urllib.parse.quote(PROXY["user"], safe="") + ":" + urllib.parse.quote(PROXY["password"], safe="") + "@"
             if PROXY["user"] else "")
    return f"{scheme}://{login}{PROXY['host']}:{PROXY['port']}"


def proxy_env():
    """Environment for helper programs (Pandoc, LibreOffice) that may fetch pictures a document links to:
    the proxy, if one is set (None: the normal environment)."""
    if not PROXY:
        return None
    env = dict(os.environ)
    url = _proxy_url() if not _proxy_problem(PROXY) else "http://127.0.0.1:9"  # unusable proxy: fetch nothing
    for k in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        env[k] = url
    env.pop("no_proxy", None), env.pop("NO_PROXY", None)
    return env


# ---------------------------------------------------------------- network

def get_json(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return json.load(r)


def get_json_small(url, limit=5_000_000, timeout=30):
    """The JSON at url, if it answers with JSON of at most `limit` bytes; else None (it's something else)."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            if "json" not in (r.headers.get("Content-Type") or "").lower():
                return None
            data = r.read(limit + 1)
        return json.loads(data) if len(data) <= limit else None
    except Exception:  # noqa: BLE001
        return None


def get_text(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _download_py(url, dest, log, label, size):
    """Resumable download without curl (for systems that don't have it)."""
    for attempt in range(8):
        have = os.path.getsize(dest) if os.path.exists(dest) else 0
        req = urllib.request.Request(url, headers=dict(UA, **({"Range": f"bytes={have}-"} if have else {})))
        try:
            with urllib.request.urlopen(req, timeout=60) as r, open(dest, "ab" if r.status == 206 else "wb") as f:
                last = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        return dest
                    f.write(chunk)
                    if log and size and time.time() - last > 5:
                        log.stage(f"Downloading {label}", f.tell() // 1_000_000, size // 1_000_000)
                        last = time.time()
        except urllib.error.HTTPError as e:
            if e.code == 416:  # already complete
                return dest
            if attempt == 7 or not (e.code in (408, 425, 429) or e.code >= 500):
                raise   # not found, forbidden...: trying again won't change it
        except urllib.error.URLError as e:
            if attempt == 7 or isinstance(e.reason, ssl.SSLCertVerificationError) or "can't be used" in str(e.reason) \
                    or "only downloads web" in str(e.reason) or "onion site" in str(e.reason) \
                    or "I2P site, which" in str(e.reason) or "Hyphanet" in str(e.reason) \
                    or "IPFS address" in str(e.reason):
                raise   # a bad certificate or a refused address won't fix itself
        except (TimeoutError, OSError):
            if attempt == 7:
                raise
        time.sleep(10)
    return dest


def download(url, dest, log=None, label="", size=0):
    """Resumable download (curl if available); a .ok marker records completion."""
    if os.path.exists(dest + ".ok"):
        return dest
    if urllib.parse.urlsplit(url).scheme.lower() not in ("http", "https"):
        raise RuntimeError(f"ZIMaker only downloads web addresses (http/https): {url[:80]}")
    if PROXY and _proxy_problem(PROXY):
        raise RuntimeError(f"The proxy can't be used: {_proxy_problem(PROXY)}. Check ⚙ Settings → Connection and privacy.")
    if is_onion(url) and not onion_ok():
        raise RuntimeError(ONION_NEEDS_TOR)
    if is_i2p(url) and not i2p_ok():
        raise RuntimeError(I2P_NEEDS_ROUTER)
    if is_hyphanet(url) and not hyphanet_ok():
        raise RuntimeError(HYPHANET_NEEDS_NODE)
    if is_ipfs(url) == "local" and not IPFS.get("on"):
        raise RuntimeError(IPFS_NEEDS_NODE)
    # onion and I2P: Python's code (it never resolves names locally, and sends .i2p only to the router)
    if not shutil.which("curl") or is_darknet(url):
        _download_py(url, dest, log, label, size)
        open(dest + ".ok", "w").close()
        return dest
    had = os.path.getsize(dest) if os.path.exists(dest) else 0
    restarted = False
    for attempt in range(1, 41):
        # -q: ignore ~/.curlrc; only http(s), also after redirects
        # give up on a server that doesn't answer in 30 s or sends under 1 KB/s for a minute (curl's own limits
        # are 5 minutes and none); the retries then resume where it stopped
        cmd = ["curl", "-q", "-sSL", "--fail", "--proto", "=http,https", "--proto-redir", "=http,https",
               "--connect-timeout", "30", "--speed-limit", "1000", "--speed-time", "60",
               "--retry", "8", "--retry-delay", "10", "-C", "-", "-o", dest, url]
        if attempt > 1:
            # some servers break off HTTP/2 downloads part-way ("stream was not closed cleanly", seen with
            # download.openzim.org): later tries use plain HTTP/1.1
            cmd[2:2] = ["--http1.1"]
        if PROXY:  # same proxy as everything else; socks5h: the proxy looks up names
            cmd[2:2] = ["--proxy", _proxy_url()]
        p = subprocess.Popen(cmd)
        last = 0
        while True:
            try:
                p.wait(timeout=2)   # returns as soon as curl is done (small files take a fraction of a second)
                break
            except subprocess.TimeoutExpired:
                pass
            if log and size and time.time() - last > 5:
                have = os.path.getsize(dest) if os.path.exists(dest) else 0
                log.stage(f"Downloading {label}", have // 1_000_000, size // 1_000_000)
                last = time.time()
        if p.returncode == 0:
            open(dest + ".ok", "w").close()
            return dest
        if p.returncode == 22:
            break            # the server said no (not found, forbidden...): asking again won't change it
        have = os.path.getsize(dest) if os.path.exists(dest) else 0
        if p.returncode in (18, 56, 92) and have > had:
            # the server broke the transfer off part-way (curl doesn't retry that itself), but more arrived than
            # last time: carry on from there (-C -) for as long as each try gets further
            had = have
            continue
        if restarted:
            break
        restarted = True
        if os.path.exists(dest):
            os.remove(dest)  # a stale partial file can make resume fail; start over once
        had = 0
    raise RuntimeError(f"Download failed (curl exit {p.returncode}): {url}")


# ---------------------------------------------------------------- text

def decode_text(raw):
    """Bytes of a text file -> str: byte-order marks first (UTF-8, UTF-16, UTF-32), then UTF-16 without one (every
    other byte zero: what Windows PowerShell and older Notepad often write), then UTF-8, then Windows' Western."""
    for bom, enc in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
                     (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if raw.startswith(bom):
            return raw.decode(enc, "replace")
    head = raw[:4000]
    if len(head) >= 4 and head[1::2].count(0) > 0.4 * len(head[1::2]) and head[0::2].count(0) < 0.1 * len(head[0::2]):
        return raw.decode("utf-16-le", "replace")
    if len(head) >= 4 and head[0::2].count(0) > 0.4 * len(head[0::2]) and head[1::2].count(0) < 0.1 * len(head[1::2]):
        return raw.decode("utf-16-be", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def ocr_html(t):
    """Plain OCR/PDF text -> simple paragraphs."""
    t = re.sub(r"(\w)-\n(?=[a-z])", r"\1", t)  # rejoin words hyphenated at line ends
    paras = [p for p in re.split(r"\n\s*\n", t) if p.strip()]
    return "".join(f"<p>{html.escape(' '.join(p.split()))}</p>" for p in paras)


def running_head(t):
    """Leading run of capitalised words on a page (e.g. 'AKA HILLS—AKENSIDE'), minus page numbers."""
    first = next((ln.strip() for ln in t.splitlines() if ln.strip()), "")
    head = []
    for tok in first.split()[:12]:
        if any(ch.islower() for ch in tok):
            break
        head.append(tok)
    while head and re.fullmatch(r"[\d.,]+", head[-1]):
        head.pop()
    while head and re.fullmatch(r"[\d.,]+", head[0]):
        head.pop(0)
    if len(head) > 1 and len(head[-1]) == 1:
        head.pop()
    hw = " ".join(head)
    return hw if sum(ch.isalpha() for ch in hw) >= 3 else ""


def first(v, default=""):
    if isinstance(v, list):
        v = v[0] if v else default
    return str(v).strip() if v else default


def clip(s, n):
    """Shorten to at most n characters on a word boundary (ZIM metadata has hard limits)."""
    s = " ".join(str(s).split())
    if len(s) <= n:
        return s
    cut = s[:n - 1].rsplit(" ", 1)[0].rstrip(" ,;:-–—")
    return (cut or s[:n - 1]) + "…"


def slug(s, n=60):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").lower()
    return (s[:n].strip("_") or "book")


def safe_filename(s, n=120):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s)
    s = " ".join(s.split()).strip(" .")
    return s[:n].strip(" .") or "book"


LANG3 = {"en": "eng", "english": "eng", "fr": "fra", "french": "fra", "de": "deu", "german": "deu",
         "es": "spa", "spanish": "spa", "it": "ita", "italian": "ita", "la": "lat", "latin": "lat",
         "pt": "por", "portuguese": "por", "nl": "nld", "dutch": "nld", "ru": "rus", "russian": "rus",
         "sv": "swe", "swedish": "swe", "da": "dan", "danish": "dan", "no": "nor", "norwegian": "nor",
         "pl": "pol", "polish": "pol", "el": "ell", "greek": "ell", "ja": "jpn", "japanese": "jpn",
         "zh": "zho", "chinese": "zho", "ar": "ara", "arabic": "ara", "hi": "hin", "hindi": "hin"}


def lang3(v):
    v = first(v).lower().split("-")[0].split("_")[0]
    if v in LANG3:
        return LANG3[v]
    return v if re.fullmatch(r"[a-z]{3}", v) else "eng"


# ---------------------------------------------------------------- paths
# On Windows the converter runs inside WSL and shows Windows paths; on Linux it runs natively.
# ZIMAKER_NATIVE=1 forces the native-Linux behaviour (for testing inside WSL).

# (Docker Desktop on Windows runs containers on WSL's kernel, so a container would look like WSL: it never is.)
IS_WSL = (os.environ.get("ZIMAKER_NATIVE") != "1" and not DOCKER
          and (os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop") or "microsoft" in os.uname().release.lower()))


@functools.lru_cache(maxsize=1)
def _mount_root():
    """Where WSL mounts Windows drives ('/mnt/' normally; wsl.conf can change it). Asked once."""
    try:
        c = subprocess.check_output(["wslpath", "-u", "C:\\"], text=True).strip()   # e.g. /mnt/c
        return c[:-1] if c.lower().endswith("c") else "/mnt/"
    except Exception:  # noqa: BLE001
        return "/mnt/"


def to_wsl(p):
    """A path typed by the user -> a path this process can open."""
    p = p.strip().strip('"')
    if not p or p.startswith("/") or not IS_WSL:
        return os.path.expanduser(p)
    m = re.match(r"([A-Za-z]):[\\/]?(.*)$", p)
    if m:  # C:\Users\... : worked out here (wslpath is a program start each time, and this is called often)
        return _mount_root() + m.group(1).lower() + "/" + m.group(2).replace("\\", "/")
    return subprocess.check_output(["wslpath", "-u", p], text=True).strip()


def to_win(p):
    """A path to show the user (Windows form when running under WSL)."""
    if not IS_WSL:
        return p
    try:
        return subprocess.check_output(["wslpath", "-w", p], text=True).strip()
    except Exception:
        return p


def default_output():
    if DOCKER:   # the folder the container's /output volume is mounted on
        return os.environ.get("ZIMAKER_OUTPUT") or "/output"
    if IS_WSL:
        docs = windows_folders()[1] or (windows_profile() + "\\Documents" if windows_profile() else "")
        if docs:
            return docs + "\\ZIMaker"
    docs = os.path.expanduser("~/Documents")
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "ZIMaker")


def open_folder(p):
    """Show a folder (or the folder holding a file) in the system file manager."""
    if DOCKER:   # no desktop in a container (the window hides its "Open folder" buttons)
        return
    if IS_WSL:  # paths shown to the user are Windows paths here
        target = ntpath.dirname(p) if p.lower().endswith(".zim") else p
        subprocess.Popen(["explorer.exe", target], cwd="/mnt/c")
    else:
        target = os.path.dirname(p) if p.lower().endswith(".zim") else p
        subprocess.Popen(["xdg-open", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@functools.lru_cache(maxsize=1)
def windows_folders():
    """(user folder, Documents folder) of the Windows user, asked once through PowerShell in UTF-8: cmd.exe answers in
    an old code page, which garbles names like Jörg, and Documents may have been moved (OneDrive)."""
    if not IS_WSL:
        return "", ""
    try:
        out = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding = [Text.Encoding]::UTF8; [Environment]::GetFolderPath('UserProfile'); "
             "[Environment]::GetFolderPath('MyDocuments')"],
            cwd="/mnt/c", stderr=subprocess.DEVNULL, timeout=30).decode("utf-8", "replace").splitlines()
        lines = [ln.strip() for ln in out if ln.strip()]
        if len(lines) >= 2 and re.match(r"[A-Za-z]:\\", lines[0]):
            return lines[0], lines[1] if re.match(r"[A-Za-z]:\\", lines[1]) else ""
    except Exception:  # noqa: BLE001
        pass
    return "", ""


@functools.lru_cache(maxsize=1)
def windows_profile():
    """C:\\Users\\<name> of the Windows user, via WSL interop (asked once; it's slow-ish)."""
    if not IS_WSL:
        return ""
    if windows_folders()[0]:
        return windows_folders()[0]
    try:  # older fallback
        out = subprocess.check_output(["cmd.exe", "/c", "echo %USERPROFILE%"], cwd="/mnt/c",
                                      stderr=subprocess.DEVNULL).decode("utf-8", "replace").strip()
        if out and "%" not in out and "\ufffd" not in out:
            return out
    except Exception:  # noqa: BLE001
        pass
    return ""


net_init()  # every process (server, jobs, setup) starts with the proxy chosen in Settings
