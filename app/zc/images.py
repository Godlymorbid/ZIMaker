"""Page images: 'does this page have a picture?' detection, and saving scans as compact WebP.

Picture detection looks for dark ink on the page that is NOT covered by any OCR'd word, after
removing long straight lines (table rules, borders). If what is left forms a sizeable connected
cluster, the page has an illustration, map, diagram or plate. Thresholds were calibrated by hand
on scanned encyclopaedia pages.
"""
import os

import numpy as np
from PIL import Image, ImageOps

SMALL = 600       # analysis width (px)
RUN = 33          # straight runs at least this long are rules/borders, not pictures
CELL = 12         # grid cell size for clustering (px)
BUSY = 10         # uncovered-ink pixels for a cell to count as busy
BLOB_MIN = 15     # cells in the largest cluster for a page to count as illustrated
INK_PLATE = 0.10  # or: this share of the page is uncovered ink (photo plates, full-page maps)

SCAN_WIDTH = 1800
SCAN_QUALITY = 65


def _long_runs(m, axis):
    a = m.astype(np.int32)
    if axis == 0:
        a = a.T
    c = np.cumsum(np.pad(a, ((0, 0), (1, 0))), axis=1)
    full = (c[:, RUN:] - c[:, :-RUN]) == RUN
    f = np.pad(full.astype(np.int32), ((0, 0), (RUN - 1, RUN - 1)))
    c2 = np.cumsum(np.pad(f, ((0, 0), (1, 0))), axis=1)
    cover = ((c2[:, RUN:] - c2[:, :-RUN]) > 0)[:, :a.shape[1]]
    return cover.T if axis == 0 else cover


def score(img, W, H, words):
    """img: page image (any size); W, H: coordinate space of words; words: [(x0, y0, x1, y1), ...]."""
    g = img.convert("L")
    h = max(1, round(SMALL * g.height / g.width))
    # normalise paper tone first, so grey or yellowed paper doesn't read as ink
    g = ImageOps.autocontrast(g.resize((SMALL, h)), cutoff=1)
    a = np.asarray(g)
    m = a < 140
    sx, sy = SMALL / W, h / H
    for x0, y0, x1, y1 in words:
        m[max(0, int(y0 * sy) - 3):int(y1 * sy) + 4, max(0, int(x0 * sx) - 3):int(x1 * sx) + 4] = False
    mx, my = int(SMALL * 0.04), int(h * 0.03)
    m[:my] = False
    m[h - my:] = False
    m[:, :mx] = False
    m[:, SMALL - mx:] = False
    ink = float(m.mean())
    m &= ~(_long_runs(m, 1) | _long_runs(m, 0))
    ch, cw = h // CELL, SMALL // CELL
    if ch == 0 or cw == 0:
        return {"ink": ink, "blob": 0}
    cells = m[:ch * CELL, :cw * CELL].reshape(ch, CELL, cw, CELL).sum(axis=(1, 3)) >= BUSY
    seen = np.zeros_like(cells)
    best = 0
    for cy, cx in zip(*np.nonzero(cells)):
        if seen[cy, cx]:
            continue
        stack, n = [(cy, cx)], 0
        seen[cy, cx] = True
        while stack:
            y0, x0 = stack.pop()
            n += 1
            for y1 in range(max(0, y0 - 1), min(ch, y0 + 2)):
                for x1 in range(max(0, x0 - 1), min(cw, x0 + 2)):
                    if cells[y1, x1] and not seen[y1, x1]:
                        seen[y1, x1] = True
                        stack.append((y1, x1))
        best = max(best, n)
    return {"ink": round(ink, 4), "blob": best}


def is_picture(img, W, H, words):
    s = score(img, W, H, words)
    return s["blob"] >= BLOB_MIN or s["ink"] >= INK_PLATE


def save_photo(src, out, max_side=2400, quality=82):
    """A picture for a gallery: upright, at most max_side px, colour kept, WebP."""
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)  # phone photos store their rotation separately
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        alpha = "A" in im.getbands() or (im.mode == "P" and "transparency" in im.info)
        im = im.convert("RGBA" if alpha else "RGB")
        im.save(out + ".tmp", "WEBP", quality=quality, method=4)
    os.replace(out + ".tmp", out)


def save_scan(img, out, width=SCAN_WIDTH, quality=SCAN_QUALITY):
    """Resize, drop colour unless the page really has it (cream paper is not colour), save WebP."""
    img = img.convert("RGB")
    if img.width > width:
        img = img.resize((width, round(width * img.height / img.width)), Image.LANCZOS)
    sat = img.resize((300, 400)).convert("HSV").split()[1]
    if sum(sat.histogram()[110:]) / (300 * 400) < 0.02:
        img = ImageOps.autocontrast(img.convert("L"), cutoff=0.5)
    img.save(out + ".tmp", "WEBP", quality=quality, method=6)
    os.replace(out + ".tmp", out)
    return img.size
