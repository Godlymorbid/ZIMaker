"""Audio and video files, converted with FFmpeg into formats every Kiwix reader can play.

Video -> WebM (VP9 + Opus), the format openZIM's own tools use. Audio -> MP3 (MP3 input is kept as-is).
Each file gets a page with a player, its length and tags; videos get a preview frame, audio its cover art,
and subtitle files with the same name (.srt / .vtt) are attached to the video.
"""
import html
import json
import os
import re
import shutil
import subprocess

from . import common as C
from . import tools

# per profile image-quality preset: (max video height, VP9 quality (lower = better), audio kbps)
PRESETS = {"small": (480, 40, 96), "balanced": (720, 34, 128), "sharp": (1080, 31, 192)}
VIDEO_EXT = {".mp4", ".m4v", ".mkv", ".mov", ".avi", ".webm", ".wmv", ".flv", ".mpg", ".mpeg", ".m2ts", ".mts",
             ".ts", ".3gp", ".3g2", ".ogv", ".vob", ".asf", ".divx", ".f4v"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".wma", ".aif", ".aiff", ".alac",
             ".amr", ".ape", ".mka", ".m4b", ".ac3", ".dts", ".mid", ".midi", ".au", ".caf"}
SUBTITLE_EXT = {".srt", ".vtt"}


def probe(path):
    ffprobe = tools.find("ffmpeg", "ffprobe") or "ffprobe"
    r = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError("FFmpeg couldn't read this file (it may be damaged or not really audio/video).")
    return json.loads(r.stdout or "{}")


def _duration(info):
    try:
        return float(info.get("format", {}).get("duration") or 0)
    except ValueError:
        return 0.0


def _hms(sec):
    sec = int(round(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _run(log, cmd, total, label):
    """Run FFmpeg, reporting progress from its -progress output."""
    p = subprocess.Popen(cmd + ["-progress", "pipe:1", "-nostats", "-loglevel", "error"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in p.stdout:
        m = re.match(r"out_time_us=(\d+)", line)
        if m and total:
            log.stage(f"Converting {label}", min(int(int(m.group(1)) / 1e6), int(total)), int(total))
    err = p.stderr.read()
    if p.wait() != 0:
        raise RuntimeError("FFmpeg couldn't convert this file: " + (err.strip().splitlines() or ["unknown error"])[-1][:300])


def _srt_to_vtt(src, dst):
    raw = open(src, "rb").read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text.lstrip().startswith("WEBVTT"):
        text = "WEBVTT\n\n" + re.sub(r"(\d\d:\d\d:\d\d),(\d\d\d)", r"\1.\2", text.replace("\r\n", "\n"))
    open(dst, "w", encoding="utf-8").write(text)


def prepare(log, items, title, vdir, quality="balanced", subtitles=()):
    """items: [(path, display name)]; subtitles: [(path, name)] matched to videos by file name."""
    ff = tools.need("ffmpeg", "Audio and video")
    max_h, crf, kbps = PRESETS.get(quality, PRESETS["balanced"])
    os.makedirs(os.path.join(vdir, "media"), exist_ok=True)
    subs = {}
    for p, n in subtitles:
        stem = os.path.splitext(os.path.basename(n))[0]
        base, _, lang = stem.rpartition(".")  # movie.en.srt -> movie (English)
        key = base if base and len(lang) in (2, 3) else stem
        subs.setdefault(key.lower(), []).append((p, n, lang if base and len(lang) in (2, 3) else ""))
    pages = []
    for i, (path, name) in enumerate(sorted(items, key=lambda x: x[1].lower())):
        stem = os.path.splitext(os.path.basename(name))[0]
        ext = os.path.splitext(name)[1].lower()
        try:
            info = probe(path)
        except Exception as e:  # noqa: BLE001
            log(f"skipped {name}: {e}")
            continue
        streams = info.get("streams", [])
        video = [s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")]
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        dur = _duration(info)
        tags = {k.lower(): v for k, v in (info.get("format", {}).get("tags") or {}).items()}
        label = tags.get("title") or stem
        meta = []
        for k, lab in (("artist", "Artist"), ("album", "Album"), ("date", "Date"), ("genre", "Genre"), ("comment", "Notes")):
            if tags.get(k):
                meta.append(f"<b>{lab}:</b> {html.escape(str(tags[k]))[:300]}")
        if dur:
            meta.insert(0, f"<b>Length:</b> {_hms(dur)}")
        base = f"media/{i}"
        log(f"[{name}] {'video' if video else 'audio'}, {_hms(dur) if dur else 'unknown length'}")
        if video:
            out = os.path.join(vdir, base + ".webm")
            h = min(max_h, int(video[0].get("height") or max_h))
            cmd = [ff, "-y", "-i", path, "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libvpx-vp9", "-b:v", "0",
                   "-crf", str(crf), "-vf", f"scale=-2:{h - h % 2}", "-deadline", "realtime", "-cpu-used", "8",
                   "-row-mt", "1", "-c:a", "libopus", "-b:a", f"{min(kbps, 128)}k", "-ac", "2", "-sn", out]
            _run(log, cmd, dur, name)
            poster = base + ".jpg"
            subprocess.run([ff, "-y", "-ss", str(min(5.0, dur / 10 if dur else 0)), "-i", path, "-frames:v", "1",
                            "-vf", "scale=960:-2", "-loglevel", "error", os.path.join(vdir, poster)],
                           capture_output=True, timeout=120)
            tracks = []
            for k, (sp, sn, lang) in enumerate(subs.get(stem.lower(), [])):
                vtt = f"{base}-{k}.vtt"
                try:
                    _srt_to_vtt(sp, os.path.join(vdir, vtt))
                    tracks.append({"src": vtt, "lang": lang, "label": lang.upper() if lang else "Subtitles"})
                except OSError:
                    pass
            media = {"file": base + ".webm", "mime": "video/webm", "kind": "video",
                     "poster": poster if os.path.exists(os.path.join(vdir, poster)) else None, "tracks": tracks}
        elif audio:
            if ext == ".mp3":
                shutil.copyfile(path, os.path.join(vdir, base + ".mp3"))  # already plays everywhere
            else:
                _run(log, [ff, "-y", "-i", path, "-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", f"{kbps}k",
                           os.path.join(vdir, base + ".mp3")], dur, name)
            cover = base + ".jpg"
            subprocess.run([ff, "-y", "-i", path, "-an", "-map", "0:v:0?", "-frames:v", "1", "-vf", "scale='min(800,iw)':-2",
                            "-loglevel", "error", os.path.join(vdir, cover)], capture_output=True, timeout=120)
            media = {"file": base + ".mp3", "mime": "audio/mpeg", "kind": "audio",
                     "poster": cover if os.path.exists(os.path.join(vdir, cover)) and os.path.getsize(os.path.join(vdir, cover)) else None,
                     "tracks": []}
        else:
            log(f"skipped {name}: no audio or video in it")
            continue
        pages.append({"label": label, "head": "", "body": "<p>" + "<br>".join(meta) + "</p>" if meta else "",
                      "image": None, "media": media})
    if not pages:
        raise RuntimeError("None of the audio/video files could be converted.")
    kinds = {p["media"]["kind"] for p in pages}
    log(f"[{title}] {len(pages)} {'videos' if kinds == {'video'} else 'audio files' if kinds == {'audio'} else 'audio and video files'}")
    return {"kind": "pages", "title": title, "creator": "", "publisher": "", "date": "", "language": "eng",
            "source": f"{len(pages)} media files", "pages": pages, "gallery": True, "media": True}
