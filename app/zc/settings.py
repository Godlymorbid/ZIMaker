"""User settings: theme, automatic update checks, and named profiles of conversion defaults.

Stored in ~/.zimaker/settings.json, so they survive updates and reinstalls. Everything coming from the
window is validated here; unknown or bad values fall back to the defaults.
"""
import json
import os

from . import common as C

PATH = os.path.join(C.HOME, "settings.json")

QUALITY = {  # image quality presets: (scan width px, scan WebP quality, photo max side px, photo quality)
    "small": (1400, 55, 1600, 72),
    "balanced": (1800, 65, 2400, 82),
    "sharp": (2400, 80, 3200, 90),
}
SENSITIVITY = {"more": 8, "balanced": 15, "fewer": 30}  # picture detection: smallest picture cluster

CHOICES = {
    "mode": ("pictures", "all", "none"),
    "scope": ("page", "section", "site"),
    "quality": tuple(QUALITY),
    "sensitivity": tuple(SENSITIVITY),
}


def default_profile():
    return {"mode": "pictures", "combine": False, "output_dir": C.default_output(), "max_pages": 300,
            "scope": "site", "quality": "balanced", "sensitivity": "balanced"}


def defaults():
    return {"theme": "system", "check_updates": True, "active": "Default", "profiles": {"Default": default_profile()}}


def clean_profile(p):
    base = default_profile()
    p = p if isinstance(p, dict) else {}
    out = {}
    for k, v in base.items():
        x = p.get(k, v)
        if k in CHOICES:
            x = x if x in CHOICES[k] else v
        elif k == "combine":
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
    out = {"theme": s.get("theme") if s.get("theme") in ("system", "light", "dark") else d["theme"],
           "check_updates": bool(s.get("check_updates", True))}
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
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, PATH)
    return s


def apply_to_images(quality, sensitivity):
    """Set the image modules' tuning for this job (call before any worker processes start)."""
    from . import images
    q = QUALITY.get(quality, QUALITY["balanced"])
    blob = SENSITIVITY.get(sensitivity, SENSITIVITY["balanced"])
    images.SCAN_WIDTH, images.SCAN_QUALITY, images.PHOTO_SIDE, images.PHOTO_QUALITY = q
    images.BLOB_MIN = blob
    os.environ["ZIMAKER_TUNING"] = ",".join(str(x) for x in (*q, blob))
