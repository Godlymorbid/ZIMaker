"""PDF files: the text layer of every page, page images for illustrated pages (or all / none).

Works for scanned PDFs with an OCR layer (e.g. Internet Archive PDFs) and born-digital PDFs.
A scanned PDF without any text layer is kept as page images (there is no text to show).
"""
import os

from . import common as C

_DOCS = {}


def _doc(path):
    import pymupdf
    if path not in _DOCS:
        _DOCS[path] = pymupdf.open(path)
    return _DOCS[path]


def _page_job(a):
    C.use_lib()
    import pymupdf
    from PIL import Image
    from . import images
    path, i, mode, out = a
    page = _doc(path)[i]
    blocks = page.get_text("blocks")
    text = "\n\n".join(b[4].strip() for b in blocks if len(b) > 6 and b[6] == 0 and b[4].strip())
    W, H = page.rect.width, page.rect.height
    try:
        label = page.get_label() or ""
    except Exception:  # noqa: BLE001
        label = ""
    fig = mode == "all"
    if mode == "pictures":
        words = [tuple(w[:4]) for w in page.get_text("words")]
        z = 600 / W
        pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), colorspace=pymupdf.csGRAY, alpha=False)
        fig = images.is_picture(Image.frombytes("L", (pix.width, pix.height), pix.samples), W, H, words)
    has = False
    if fig:
        z = images.SCAN_WIDTH / W
        pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
        images.save_scan(Image.frombytes("RGB", (pix.width, pix.height), pix.samples), out)
        has = True
    return i, label, text, has


def prepare(log, path, mode, vdir, name=None):
    C.use_lib()
    import pymupdf
    from concurrent.futures import ProcessPoolExecutor
    os.makedirs(os.path.join(vdir, "img"), exist_ok=True)
    doc = pymupdf.open(path)
    md = doc.metadata or {}
    n = doc.page_count
    title = (md.get("title") or "").strip() or os.path.splitext(name or os.path.basename(path))[0]
    sample = range(0, n, max(1, n // 30))
    has_text = any(doc[i].get_text("text").strip() for i in sample)
    doc.close()
    if not has_text and mode != "all":
        log(f"[{title}] no text in this file (a scan without OCR, or a comic), so every page is kept as an image")
        mode = "all"
    log(f"[{title}] {n} pages")
    results, batch = {}, []

    def run(pool):
        for i, label, text, has in pool.map(_page_job, batch):
            results[i] = (label, text, has)
        batch.clear()

    with ProcessPoolExecutor(C.WORKERS) as pool:
        for i in range(n):
            batch.append((path, i, mode, os.path.join(vdir, "img", f"{i}.webp")))
            if len(batch) >= C.BATCH:
                run(pool)
                log.stage(f"Reading PDF: {title}", i + 1, n)
        run(pool)
    pages = []
    for i in range(n):
        label, text, has = results[i]
        pages.append({
            "label": f"p. {label}" if label else f"Page {i + 1}",
            "head": C.running_head(text),
            "body": C.ocr_html(text) if text else "",
            "image": f"img/{i}.webp" if has else None,
        })
    log(f"[{title}] {n} pages, {sum(1 for p in pages if p['image'])} with images")
    return {"kind": "pages", "title": title, "creator": (md.get("author") or "").strip(),
            "publisher": "", "date": "", "language": "eng", "source": os.path.basename(name or path),
            "pages": pages}
