"""User settings: theme, automatic update checks, and named profiles of conversion defaults.

Stored in ~/.zimaker/settings.json, so they survive updates and reinstalls. Everything coming from the
window is validated here; unknown or bad values fall back to the defaults.
"""
import json
import os
import re

from . import common as C

PATH = os.path.join(C.HOME, "settings.json")

QUALITY = {  # image quality presets: (scan width px, scan WebP quality, photo max side px, photo quality)
    "small": (1400, 55, 1600, 72),
    "balanced": (1800, 65, 2400, 82),
    "sharp": (2400, 80, 3200, 90),
}
SENSITIVITY = {"more": 8, "balanced": 15, "fewer": 30}  # picture detection: smallest picture cluster
BROWSERS = ("auto", "edge", "chrome", "brave", "firefox", "default")
STOP_AFTER = (150, 600, 1800, 3600, 4 * 3600, 0)   # seconds without a window before ZIMaker stops itself; 0 = never

CHOICES = {
    "mode": ("pictures", "all", "none"),
    "scope": ("page", "section", "site"),
    "quality": tuple(QUALITY),
    "sensitivity": tuple(SENSITIVITY),
}


def default_profile():
    return {"mode": "pictures", "combine": False, "output_dir": C.default_output(), "max_pages": 300,
            "scope": "site", "web_media": False, "web_scripts": False, "quality": "balanced", "sensitivity": "balanced"}


def defaults():
    return {"theme": "system", "accent": "", "check_updates": True,
            "proxy": C.clean_proxy({}),   # every download goes through it (see common.net_init)
            "private": False,             # keep no job history or list of opened ZIMs
            "neutral_names": False,       # name new ZIM files zim-<date>-<n>.zim instead of after their title
            "browser": "auto",            # the app window: auto (Edge on Windows, a Chromium browser on Linux) or a named one
            "stop_after": 150,            # stop this long after the last window closed (and no job), 0 = keep running
            # watched folder: files that appear in it are made into ZIMs with the active profile's options
            "watch": {"on": False, "folder": "", "delete": False},
            "active": "Default", "profiles": {"Default": default_profile()}}


def clean_profile(p):
    base = default_profile()
    p = p if isinstance(p, dict) else {}
    out = {}
    for k, v in base.items():
        x = p.get(k, v)
        if k in CHOICES:
            x = x if x in CHOICES[k] else v
        elif k in ("combine", "web_media", "web_scripts"):
            x = bool(x)
        elif k == "max_pages":
            try:
                x = max(1, min(int(x), 50000))
            except (TypeError, ValueError):
                x = v
        elif k == "output_dir":
            x = str(x).strip()[:500] or v
        out[k] = x
    return out


def clean(s):
    d = defaults()
    s = s if isinstance(s, dict) else {}
    accent = str(s.get("accent") or "")
    out = {"theme": s.get("theme") if s.get("theme") in ("system", "light", "dark") else d["theme"],
           "accent": accent.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", accent) else "",  # "" = ZIMaker red
           "check_updates": bool(s.get("check_updates", True)),
           "proxy": C.clean_proxy(s.get("proxy")),
           "private": bool(s.get("private")), "neutral_names": bool(s.get("neutral_names")),
           "browser": s.get("browser") if s.get("browser") in BROWSERS else "auto",
           "stop_after": s.get("stop_after") if s.get("stop_after") in STOP_AFTER else 150}
    w = s.get("watch") if isinstance(s.get("watch"), dict) else {}
    out["watch"] = {"on": bool(w.get("on")), "folder": str(w.get("folder") or "").strip()[:500], "delete": bool(w.get("delete"))}
    profiles = {}
    for name, p in (s.get("profiles") or {}).items():
        name = " ".join(str(name).split())[:40]
        if name and name not in profiles and len(profiles) < 50:
            profiles[name] = clean_profile(p)
    if not profiles:
        profiles = d["profiles"]
    out["profiles"] = profiles
    out["active"] = s.get("active") if s.get("active") in profiles else next(iter(profiles))
    return out


def load():
    try:
        return clean(json.load(open(PATH, encoding="utf-8")))
    except (OSError, ValueError):
        return defaults()


def save(s):
    s = clean(s)
    os.makedirs(C.HOME, exist_ok=True)
    tmp = PATH + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)   # owner-only: it can hold a proxy password
    os.replace(tmp, PATH)
    C.net_init(s["proxy"])          # this process uses the new proxy straight away
    # the Windows launcher (a VBScript) can't read this file inside Linux: give it the browser choice next to itself
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                               "browser.txt"), "w", encoding="ascii") as f:
            f.write(s["browser"] + "\n")
    except OSError:
        pass
    return s


def apply_to_images(quality, sensitivity):
    """Set the image modules' tuning for this job (call before any worker processes start)."""
    from . import images
    q = QUALITY.get(quality, QUALITY["balanced"])
    blob = SENSITIVITY.get(sensitivity, SENSITIVITY["balanced"])
    images.SCAN_WIDTH, images.SCAN_QUALITY, images.PHOTO_SIDE, images.PHOTO_QUALITY = q
    images.BLOB_MIN = blob
    os.environ["ZIMAKER_TUNING"] = ",".join(str(x) for x in (*q, blob))
