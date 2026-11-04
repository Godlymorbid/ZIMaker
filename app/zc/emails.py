"""Emails: a single message (.eml) or a mailbox (.mbox) -> one page per message, newest first.

Shows the sender, recipients, date and subject, the message text (the plain-text version, or the HTML
version with scripts and remote content removed), pictures that came as attachments, and a list of the
other attachments.
"""
import email
import email.utils
import html
import mailbox
import mimetypes
import os
import re
from email import policy
from html.parser import HTMLParser

from . import common as C

MAX_MESSAGES = 20000


# HTML email is rebuilt from an allow-list, not cleaned with patterns: only these tags and attributes survive, so
# nothing that runs (scripts, event handlers, javascript: links) or phones home (remote pictures, CSS url()) can
# slip through in a form a pattern didn't foresee. Kiwix runs scripts, so this matters.
KEEP_TAGS = {"a", "abbr", "address", "b", "big", "blockquote", "br", "caption", "center", "cite", "code", "col",
             "colgroup", "dd", "del", "div", "dl", "dt", "em", "figcaption", "figure", "font", "h1", "h2", "h3", "h4",
             "h5", "h6", "hr", "i", "img", "ins", "li", "ol", "p", "pre", "q", "s", "small", "span", "strike", "strong",
             "sub", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "tt", "u", "ul"}
DROP_WITH_CONTENT = {"script", "style", "iframe", "object", "embed", "form", "noscript", "template", "svg", "math",
                     "head", "title", "textarea", "select", "button", "frame", "frameset", "applet", "audio", "video",
                     "canvas"}
KEEP_ATTRS = {"alt", "title", "width", "height", "align", "valign", "colspan", "rowspan", "border", "cellpadding",
              "cellspacing", "bgcolor", "color", "face", "size", "dir", "lang", "style", "href", "src"}
VOID_TAGS = {"br", "col", "hr", "img"}


class _Cleaner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs, closed=False):
        if tag in DROP_WITH_CONTENT:
            # tags without a closing tag (embed, frame, <iframe/>) mustn't start skipping that never ends
            self.skip += not closed and tag not in ("embed", "frame")
            return
        if self.skip or tag not in KEEP_TAGS:
            return
        kept = []
        for k, v in attrs:
            v = v or ""
            if k not in KEEP_ATTRS:
                continue
            if k == "href" and not re.match(r"(?i)\s*(https?:|mailto:|#)", v):
                continue   # javascript:, data:, file:, ...
            if k == "src" and not (tag == "img" and re.match(r"(?i)\s*data:image/(png|jpe?g|gif|webp);", v)):
                continue   # remote pictures are trackers; embedded ones are fine
            if k == "style" and re.search(r"(?i)url\s*\(|expression|javascript|@import|behavio", v):
                continue
            kept.append(f' {k}="{html.escape(v, quote=True)}"')
        if tag == "img" and not any(a.startswith(" src=") for a in kept):
            return   # a picture that isn't here: nothing to show
        if tag == "a":
            kept.append(' rel="noopener noreferrer"')
        self.out.append(f"<{tag}{''.join(kept)}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs, closed=True)

    def handle_endtag(self, tag):
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
        elif not self.skip and tag in KEEP_TAGS and tag not in VOID_TAGS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))


def _clean_html(raw):
    """Keep an HTML email readable offline and safe: only plain formatting, links and embedded pictures."""
    c = _Cleaner()
    c.feed(raw)
    c.close()
    return "".join(c.out)


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
            ext = os.path.splitext(fname)[1].lower()   # from the sender: only if it looks like an extension
            if not re.fullmatch(r"\.[a-z0-9]{1,5}", ext):
                ext = mimetypes.guess_extension(att.get_content_type()) or ".img"
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
    extra += "".join(f'<figure><img class="scan" src="{html.escape(rel)}" alt="{html.escape(fn)}"><figcaption class="hint">'
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
                log.warn(f"[{name}] stopped after {MAX_MESSAGES} messages; the rest aren't in the ZIM")
                break
            try:
                pages.append(_message_page(msg, vdir, n, mode))
            except Exception as e:  # noqa: BLE001
                log.warn(f"skipped a message: {e}")
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
