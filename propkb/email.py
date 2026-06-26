"""propkb.email -- Gmail integration for the property KB (Phase 2).

HARD RULE: this module never autonomously sends correspondence to third parties.
It (a) READS mail via IMAP, (b) FILES relevant messages into the property KB,
(c) DRAFTS replies/new questions into Gmail Drafts for human review+send, and
(d) NOTIFIES the user (self email + ntfy push). Drafting != sending. The only
outbound messages are notifications to the user's own address, which the user
explicitly requested.

Auth: GMAIL_USER + GMAIL_PASS (app password, spaces stripped) from repo .env.
"""

from __future__ import annotations

import email as _email
import email.utils
import hashlib
import imaplib
import json
import os
import re
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

from . import settings, store

IMAP_HOST = "imap.gmail.com"
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465
DRAFTS_MAILBOX = "[Gmail]/Drafts"


# ---------- relevance ----------

# Generic geographic/street tokens that must NOT alone make a message relevant
# (else an Amazon order shipped to "Durham, NC" matches). Relevance needs a
# DISTINCTIVE identifier: PIN, REID, the street name, the zip, or the street number.
_STOPWORDS = {
    "durham", "nc", "north", "carolina", "rd", "road", "st", "street", "ave",
    "avenue", "dr", "drive", "ln", "lane", "ct", "court", "blvd", "usa", "us",
    "county", "the", "and",
}


def _relevance_terms(slug: str) -> list[str]:
    """Distinctive terms that mark a message as relevant to this property: PIN,
    REID, street name, zip, street number. Parsed from facts.yaml (no yaml dep).
    Generic city/state/street-type tokens are excluded so unrelated mail to the
    same city doesn't match."""
    terms = set()
    p = store.paths(slug)
    for w in re.split(r"[-_]", slug):
        if len(w) >= 4 and w.lower() not in _STOPWORDS:
            terms.add(w.lower())
    try:
        with open(p["facts"], "r", encoding="utf-8") as f:
            txt = f.read()
        for key in ("pin", "reid"):
            m = re.search(rf"{key}\s*:\s*\"?([^\n\"]+)", txt, re.I)
            if m and m.group(1).strip():
                terms.add(m.group(1).strip().lower())
        m = re.search(r"address\s*:\s*\"?([^\n\"]+)", txt, re.I)
        if m:
            val = m.group(1).strip()
            terms.add(val.lower())                       # full address string
            for tok in re.findall(r"[A-Za-z0-9]{4,}", val):  # street name, zip, street number
                t = tok.lower()
                if t not in _STOPWORDS:
                    terms.add(t)
    except OSError:
        pass
    return sorted(t for t in terms if t)


def is_relevant(slug: str, subject: str, body: str, sender: str) -> bool:
    hay = " ".join((subject or "", body or "", sender or "")).lower()
    return any(t in hay for t in _relevance_terms(slug))


# ---------- parsing ----------

def _body_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and \
               "attachment" not in str(part.get("Content-Disposition", "")):
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace")
                except Exception:  # noqa: BLE001
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", "replace")
    except Exception:  # noqa: BLE001
        return msg.get_payload() or ""


def _slug_text(s: str, n: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:n] or "msg"


def _save_attachments(msg, slug: str, stem: str) -> list[str]:
    """Extract every email attachment (PDFs, images, etc.) and save it into the
    property store under sources/emails/attachments/ with a standard,
    provenance-preserving slug name: ``<email-stem>__<filename-slug>.<ext>``.
    Returns the saved paths. PDFs land in the indexable corpus; images are kept
    for in-session vision/OCR. Nothing goes to Downloads."""
    saved: list[str] = []
    att_dir = os.path.join(store.paths(slug)["emails"], "attachments")
    os.makedirs(att_dir, exist_ok=True)
    for part in msg.walk():
        disp = str(part.get("Content-Disposition", "") or "")
        filename = part.get_filename()
        is_attach = "attachment" in disp.lower() or (filename and "inline" in disp.lower())
        if not (filename and (is_attach or part.get_content_maintype() in ("image", "application"))):
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:  # noqa: BLE001
            continue
        if not payload:
            continue
        base, ext = os.path.splitext(filename)
        ext = (ext or "." + (part.get_content_subtype() or "bin")).lower()
        name = f"{stem}__{_slug_text(base, 50)}{ext}"
        dest = os.path.join(att_dir, name)
        with open(dest, "wb") as f:
            f.write(payload)
        saved.append(dest)
    return saved


# ---------- dedup state ----------

def _state_path(slug: str) -> str:
    return os.path.join(store.paths(slug)["emails"], ".processed.json")


def _load_seen(slug: str) -> set:
    try:
        with open(_state_path(slug), "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError):
        return set()


def _save_seen(slug: str, seen: set) -> None:
    os.makedirs(os.path.dirname(_state_path(slug)), exist_ok=True)
    with open(_state_path(slug), "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f)


# ---------- read + file ----------

def sync(slug: str, days: int = 7, max_msgs: int = 50) -> list[dict]:
    """Pull recent mail, file property-relevant + unseen messages into
    sources/emails/ as .eml + parsed .md. Returns a list of filed hits.
    Pure read+file; does NOT update timeline/understanding (the /intake skill
    does that interpretation after this runs)."""
    user, pw = settings.gmail_user(), settings.gmail_pass()
    if not user or not pw:
        raise RuntimeError("GMAIL_USER / GMAIL_PASS not set in .env")
    store.create(slug, "")  # ensure folders exist
    seen = _load_seen(slug)
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%d-%b-%Y")
    hits: list[dict] = []

    M = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        M.login(user, pw)
        M.select("INBOX", readonly=True)
        typ, data = M.search(None, f'(SINCE "{since}")')
        ids = data[0].split() if data and data[0] else []
        for num in ids[-max_msgs:][::-1]:
            typ, raw = M.fetch(num, "(RFC822)")
            if typ != "OK" or not raw or not raw[0]:
                continue
            msg = _email.message_from_bytes(raw[0][1])
            mid = (msg.get("Message-ID") or
                   hashlib.sha1(raw[0][1]).hexdigest())
            if mid in seen:
                continue
            subject = str(_email.header.make_header(
                _email.header.decode_header(msg.get("Subject", ""))))
            sender = msg.get("From", "")
            body = _body_text(msg)
            if not is_relevant(slug, subject, body, sender):
                continue
            date_hdr = msg.get("Date", "")
            try:
                dt = email.utils.parsedate_to_datetime(date_hdr).date().isoformat()
            except Exception:  # noqa: BLE001
                dt = datetime.utcnow().date().isoformat()
            stem = f"{dt}_{_slug_text(sender,20)}_{_slug_text(subject)}"
            store.write_text(slug, raw[0][1].decode("utf-8", "replace"),
                             stem + ".eml", kind="emails")
            attachments = _save_attachments(msg, slug, stem)
            att_md = ""
            if attachments:
                att_md = "\n## Attachments\n" + "\n".join(
                    "- `sources/emails/attachments/%s`" % os.path.basename(a)
                    for a in attachments) + "\n"
            md = (f"# Email — {subject}\n\n"
                  f"- **From:** {sender}\n- **Date:** {date_hdr}\n"
                  f"- **Message-ID:** {mid}\n{att_md}\n---\n\n{body.strip()}\n")
            store.write_text(slug, md, stem + ".md", kind="emails")
            seen.add(mid)
            hits.append({"date": dt, "from": sender, "subject": subject,
                         "message_id": mid, "file": stem + ".md",
                         "attachments": [os.path.basename(a) for a in attachments]})
        _save_seen(slug, seen)
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass
    return hits


# ---------- draft (NEVER send to third parties) ----------

def draft(to: str, subject: str, body: str, in_reply_to: str | None = None) -> bool:
    """Append a message to Gmail Drafts for human review + manual send. Returns
    True on success. This does NOT send."""
    user, pw = settings.gmail_user(), settings.gmail_pass()
    if not user or not pw:
        raise RuntimeError("GMAIL_USER / GMAIL_PASS not set in .env")
    em = EmailMessage()
    em["From"] = user
    em["To"] = to
    em["Subject"] = subject
    if in_reply_to:
        em["In-Reply-To"] = in_reply_to
        em["References"] = in_reply_to
    em.set_content(body)
    M = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        M.login(user, pw)
        typ, _ = M.append(DRAFTS_MAILBOX, "(\\Draft)", None, em.as_bytes())
        return typ == "OK"
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass


# ---------- notify the USER (self email + ntfy) ----------

def notify(title: str, message: str) -> dict:
    """Notify the user only: an email to their own address + an ntfy push.
    Not third-party correspondence."""
    out = {"email": False, "ntfy": False}
    user, pw = settings.gmail_user(), settings.gmail_pass()
    if user and pw:
        try:
            em = EmailMessage()
            em["From"] = em["To"] = user
            em["Subject"] = "[propkb] " + title
            em.set_content(message)
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ctx) as s:
                s.login(user, pw)
                s.send_message(em)
            out["email"] = True
        except Exception:  # noqa: BLE001
            pass
    topic = settings.ntfy_topic()
    if topic:
        try:
            import urllib.request
            req = urllib.request.Request(
                "https://ntfy.sh/" + topic,
                data=message.encode("utf-8"),
                headers={"Title": title})
            urllib.request.urlopen(req, timeout=10)
            out["ntfy"] = True
        except Exception:  # noqa: BLE001
            pass
    return out
