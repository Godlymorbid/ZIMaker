"""Run one conversion job: python3 -I runner.py <job_dir>

job.json: {"sources": [{"type": "ia", "id": "..."} | {"type": "file", "path": "/...", "name": "x.pdf"}],
           "mode": "pictures" | "all" | "none", "combine": bool, "title": str, "output_dir": "C:\\..."}
Writes log.txt, state.json and result.json into the job folder.
"""
import json
import os
import shutil
import sys
import traceback
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zc import common as C  # noqa: E402

C.use_lib()
from zc import builder, deps, formats, ia, settings, web  # noqa: E402


def unique(path):
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path = f"{base} ({n}){ext}"
        n += 1
    return path


def main(job_dir):
    log = C.Log(job_dir)
    job = json.load(open(os.path.join(job_dir, "job.json"), encoding="utf-8"))
    result = {"files": [], "errors": []}
    try:
        log.stage("Checking setup")
        deps.ensure(log)
        out_dir = C.to_wsl(job["output_dir"])
        os.makedirs(out_dir, exist_ok=True)
        mode = job.get("mode", "pictures")
        work = os.path.join(job_dir, "work")
        settings.apply_to_images(job.get("quality", "balanced"), job.get("sensitivity", "balanced"))
        if job.get("profile"):
            log(f"Profile: {job['profile']} (image quality: {job.get('quality', 'balanced')}, "
                f"picture detection: {job.get('sensitivity', 'balanced')})")

        # 1. expand sources into volumes (an archive.org item can contain several books;
        #    loose pictures from the whole job become one gallery)
        plan, pictures = [], []
        for s in job["sources"]:
            if s["type"] == "ia":
                log(f"Looking up archive.org item {s['id']}")
                try:
                    info = ia.inspect(s["id"])
                except Exception as e:  # noqa: BLE001
                    result["errors"].append(f"{s['id']}: {e}")
                    log(result["errors"][-1])
                    continue
                if not info["books"]:
                    result["errors"].append(f"{s['id']}: no OCR'd book found in this item")
                    log(result["errors"][-1])
                    continue
                for b in info["books"]:
                    plan.append(("ia", info, b))
            elif s["type"] == "web":
                plan.append(("web", s, None))
            else:
                name = s.get("name") or os.path.basename(s["path"])
                kd = formats.kind(name)
                if kd == "image":
                    pictures.append((s["path"], name))
                elif kd == "unsupported":
                    result["errors"].append(f"{name}: {formats.why_unsupported(name)}")
                    log(result["errors"][-1])
                else:
                    plan.append((kd, s, None))
        if pictures:
            if mode == "none":
                result["errors"].append(f"{len(pictures)} picture file(s) skipped: text-only mode keeps no images")
                log(result["errors"][-1])
            else:
                plan.append(("gallery", pictures, None))
        if not plan:
            raise RuntimeError("Nothing to convert.")

        # 2. prepare each volume
        volumes = []
        for k, (kind, a, b) in enumerate(plan, 1):
            vdir = os.path.join(work, f"v{k}")
            os.makedirs(vdir, exist_ok=True)
            label = (b["title"] if kind == "ia" else a["url"] if kind == "web"
                     else f"{len(a)} pictures" if kind == "gallery" else a.get("name") or os.path.basename(a["path"]))
            log.stage(f"Preparing {k}/{len(plan)}: {label}")
            try:
                if kind == "ia":
                    volumes.append((vdir, ia.prepare(log, a, b, mode, vdir)))
                elif kind == "web":
                    volumes.append((vdir, web.prepare(log, a["url"], job.get("web") or {}, mode, vdir)))
                elif kind == "gallery":
                    t = os.path.splitext(os.path.basename(a[0][1]))[0] if len(a) == 1 else f"Pictures ({len(a)})"
                    volumes.append((vdir, formats.prepare_gallery(log, a, t, vdir)))
                elif kind == "zip":
                    volumes.extend(formats.prepare_zip(log, a["path"], a.get("name") or a["path"], mode, vdir))
                else:
                    volumes.append((vdir, formats.prepare_document(log, a["path"], a.get("name") or a["path"], mode, vdir)))
            except Exception as e:  # noqa: BLE001  keep going with the other books
                result["errors"].append(f"{label}: {e}")
                log(f"ERROR {label}: {e}")
                log(traceback.format_exc())

        if not volumes:
            raise RuntimeError("Nothing could be converted.")
        # tell same-titled books apart: by file type, or by file name when the type is the same too
        counts = Counter(v["title"] for _, v in volumes)
        dup = [v for _, v in volumes if counts[v["title"]] > 1]
        by_type = Counter((v["title"], os.path.splitext(v.get("source", ""))[1].lower()) for v in dup)
        for v in dup:
            src = v.get("source", "")
            ext = os.path.splitext(src)[1]
            if ext and by_type[(v["title"], ext.lower())] == 1:
                v["title"] = f"{v['title']} ({ext.lstrip('.').upper()})"
            elif src:
                v["title"] = f"{v['title']} ({src})"

        # 3. build
        suffix = " (text only)" if mode == "none" else ""
        groups = [volumes] if job.get("combine") or len(volumes) == 1 else [[v] for v in volumes]
        for g, group in enumerate(groups, 1):
            title = job.get("title", "").strip() if (len(groups) == 1) else ""
            name = title or group[0][1]["title"] + (f" (+{len(group) - 1} more)" if len(group) > 1 else "")
            tmp = os.path.join(job_dir, f"out{g}.zim")
            log.stage(f"Building ZIM {g}/{len(groups)}: {name}")
            status = builder.build(log, group, tmp, title or None)
            dest = unique(os.path.join(out_dir, C.safe_filename(name + suffix) + ".zim"))
            log.stage(f"Saving {os.path.basename(dest)}")
            shutil.move(tmp, dest)
            size = os.path.getsize(dest)
            result["files"].append({"path": C.to_win(dest), "size": size, "check": status})
            log(f"saved {C.to_win(dest)} ({size / 1e6:.1f} MB)")
        shutil.rmtree(work, ignore_errors=True)
        log.stage("Done" + (" (with some errors)" if result["errors"] else ""), status="done")
    except Exception as e:  # noqa: BLE001
        result["errors"].append(str(e))
        log(f"FAILED: {e}")
        log(traceback.format_exc())
        log.stage(f"Failed: {e}", status="failed")
    finally:
        with open(os.path.join(job_dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump(result, f)
        for s in job.get("sources", []):  # uploaded copies are no longer needed
            p = s.get("path", "")
            if p.startswith(C.UPLOADS + os.sep):
                shutil.rmtree(os.path.dirname(p), ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1])
