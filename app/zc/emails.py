"""Emails: a single message (.eml) or a mailbox (.mbox) -> one page per message, newest first.

Shows the sender, recipients, date and subject, the message text (the plain-text version, or the HTML
version with scripts and remote content removed), pictures that came as attachments, and a list of the
other attachments.
"""
import email
import email.utils
import html
import mailbox
import os
import re
from email import policy

from . import common as C

MAX_MESSAGES = 20000


def _clean_html(raw):
    """Keep an HTML email readable offline and safe: no scripts, forms, frames or remote resources."""
    raw = re.sub(r"(?is)<(script|style|iframe|object|embed|form)\b.*?</\1>", "", raw)
    raw = re.sub(r"(?is)<(script|iframe|object|embed|form|meta|link|base)\b[^>]*>", "", raw)
    raw = re.sub(r"""(?i)\son\w+\s*=\s*(".*?"|'.*?'|[^\s>]+)""", "", raw)          # onclick= etc.
    raw = re.sub(r"""(?i)(<img\b[^>]*?)\ssrc\s*=\s*(["'])(https?:|//).*?\2""", r"\1", raw)  # remote images (trackers)
    m = re.search(r"(?is)<body[^>]*>(.*)</body>", raw)
    return m.group(1) if m else raw


def _message_page(msg, vdir, n, mode):
    subject = str(msg.get("Subject") or "(no subject)").strip()
    sender = str(msg.get("From") or "")
    to = str(msg.get("To") or "")
    cc = str(msg.get("Cc") or "")
    date_s = str(msg.get("Date") or "")
    try:
        when = email.utils.parsedate_to_datetime(date_s)
    except (TypeError, ValueError, IndexError):
        when = None
    body_html, pics, files = "", [], []
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:  # noqa: BLE001
        part = None
    if part is not None:
        try:
            content = part.get_content()
        except Exception:  # noqa: BLE001
            content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
        if part.get_content_type() == "text/html":
            body_html = f'<div class="mail-html">{_clean_html(content)}</div>'
        else:
            body_html = "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>"
                                for p in re.split(r"\n\s*\n", content.replace("\r\n", "\n")) if p.strip())
    for k, att in enumerate(msg.iter_attachments()):
        fname = att.get_filename() or f"attachment-{k + 1}"
        data = att.get_payload(decode=True) or b""
        if att.get_content_maintype() == "image" and mode != "none" and data:
            ext = os.path.splitext(fname)[1].lower() or ".img"
            rel = f"att/{n}-{k}{ext}"
            os.makedirs(os.path.join(vdir, "att"), exist_ok=True)
            with open(os.path.join(vdir, rel), "wb") as f:
                f.write(data)
            pics.append((rel, fname))
        else:
            files.append(f"{html.escape(fname)} <span class=\"hint\">({len(data) / 1e3:.0f} KB)</span>")
    head = "".join(f"<div><b>{lab}:</b> {html.escape(val)}</div>" for lab, val in
                   (("From", sender), ("To", to), ("Cc", cc), ("Date", date_s)) if val)
    extra = ""
    if files:
        extra += "<p><b>Attachments:</b><br>" + "<br>".join(files) + "</p>"
    extra += "".join(f'<figure><img class="scan" src="{rel}" alt="{html.escape(fn)}"><figcaption class="hint">'
                     f"{html.escape(fn)}</figcaption></figure>" for rel, fn in pics)
    return {"label": subject[:120], "head": C.clip(sender, 60), "when": when.timestamp() if when else 0,
            "body": f'<div class="mail-head">{head}</div>{body_html or "<p class=note>(no text)</p>"}{extra}',
            "image": None, "files": [rel for rel, _ in pics]}


def prepare(log, path, name, mode, vdir):
    stem = os.path.splitext(os.path.basename(name))[0]
    pages = []
    if name.lower().endswith(".mbox"):
        box = mailbox.mbox(path, factory=lambda f: email.message_from_binary_file(f, policy=policy.default),
                           create=False)
        for n, msg in enumerate(box):
            if n >= MAX_MESSAGES:
                log(f"[{name}] stopped after {MAX_MESSAGES} messages")
                break
            try:
                pages.append(_message_page(msg, vdir, n, mode))
            except Exception as e:  # noqa: BLE001
                log(f"skipped a message: {e}")
            if n % 200 == 0:
                log.stage(f"Reading mailbox: {name}", n, None)
    else:
        with open(path, "rb") as f:
            pages.append(_message_page(email.message_from_binary_file(f, policy=policy.default), vdir, 0, mode))
    if not pages:
        raise RuntimeError("No email messages found in this file.")
    pages.sort(key=lambda p: -p["when"])  # newest first
    log(f"[{name}] {len(pages)} message{'s' if len(pages) != 1 else ''}")
    title = pages[0]["label"] if len(pages) == 1 else stem
    return {"kind": "pages", "title": title, "creator": "", "publisher": "", "date": "", "language": "eng",
            "source": os.path.basename(name), "pages": pages, "plain": True, "emails": True}
