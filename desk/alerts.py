"""Saved-search email alerts: the legitimate way to hear about new ads on boards whose terms don't allow
automated reading (Kijiji, Craigslist, Rentals.ca, PadMapper/Zumper, LesPAC, Louer.ca, Appartogo...).

Stefan saves a search on the board and turns on its email alerts. The desk then reads those emails, the
way a mail app does, and turns each ad link into a lead:
- from a folder: save or drag alert emails (.eml) into data/inbox/; or
- from a mailbox folder over IMAP, read-only (settings `alerts.imap`). Messages are fetched with PEEK,
  so they stay unread, and nothing is moved or deleted. The password is read from the macOS keychain.

It reads only what the email contains. It does not open the ad pages.
"""
import email
import email.policy
import html as htmllib
import imaplib
import re
import subprocess
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

from .capture import canonical_id, source_of
from .config import DATA_DIR
from .sources.http import money

ANCHOR_RE = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
PRICE_RE = re.compile(r"\$\s?\d[\d\s,.]{2,7}|\d[\d\s,.]{2,7}\s?\$")
UNIT_RE = re.compile(r"\b\d\s?(1/2|½)|\b\d\s?(bed|chambre|cc\b|br\b)|studio|bachelor|appartement|apartment|condo|logement|maison|house",
                     re.I)
# Ad pages, per board. Links that match none of these (logos, settings, unsubscribe) are skipped,
# except on boards without a known pattern, where the link text has to look like an ad.
AD_PATH = {
    "kijiji": re.compile(r"^/v-[^/]+/.+/\d{6,}$"),
    "craigslist": re.compile(r"/d/.+/\d+\.html$"),
    "facebook": re.compile(r"^/marketplace/item/\d+"),
    "padmapper": re.compile(r"^/(apartments|buildings|apartment-buildings|rentals|address)/"),
    "logisquebec": re.compile(r"-l\d+$"),
    "livrent": re.compile(r"/building/"),
}
SKIP_TEXT = re.compile(r"unsubscribe|désabonn|desabonn|preferences|préférences|privacy|confidentialit|help|aide|view all|voir tout",
                       re.I)


def _unwrap(url):
    """Alert emails often wrap links in a click tracker; use the real address inside it when it is there."""
    url = htmllib.unescape(url)
    for _ in range(3):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        inner = next((v[0] for k, v in q.items() for x in v if x.startswith("http")), None)
        if not inner:
            break
        url = inner
    return url


def _strip(s):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def parse_message(msg, received=None):
    """Leads from one alert email (an email.message.Message)."""
    sender_source = source_of("https://" + (email.utils.parseaddr(msg.get("From", ""))[1].split("@")[-1]))
    html_part = msg.get_body(preferencelist=("html",)) if hasattr(msg, "get_body") else None
    text_part = msg.get_body(preferencelist=("plain",)) if hasattr(msg, "get_body") else None
    pairs = []  # (url, link text, text around it)
    if html_part is not None:
        body = html_part.get_content()
        for m in ANCHOR_RE.finditer(body):
            # The ad's details usually follow its link: look there first, then just before it.
            after = _strip(body[m.end(): m.end() + 300].split("<a ", 1)[0])
            pairs.append((m.group(1), _strip(m.group(2)), after + " | " + _strip(body[max(0, m.start() - 200): m.start()])))
    elif text_part is not None:
        body = text_part.get_content()
        for m in URL_RE.finditer(body):
            before = body[max(0, m.start() - 160): m.start()].strip().splitlines()
            pairs.append((m.group(0), before[-1].strip() if before else "", body[max(0, m.start() - 300): m.end() + 300]))
    leads = {}
    for raw, text, around in pairs:
        url = _unwrap(raw)
        src = source_of(url) or sender_source
        if not src or SKIP_TEXT.search(text or ""):
            continue
        path = urllib.parse.urlsplit(url).path.rstrip("/")
        pattern = AD_PATH.get(src)
        if pattern is not None and source_of(url):
            if not pattern.search(path):
                continue
        elif not (PRICE_RE.search(text or "") or UNIT_RE.search(text or "")):
            continue
        lid = canonical_id(url, src)
        price_m = PRICE_RE.search(text or "") or PRICE_RE.search(around or "")
        lead = leads.get(lid) or {
            "id": lid, "source": src, "source_id": lid.split(":", 1)[1], "url": url, "title": "", "snippet": "",
            "address": "", "lat": None, "lng": None, "price": money(price_m.group(0)) if price_m else None,
            "bedrooms": "", "unit_type": "", "poster_id": "", "poster_type": "", "posted_at": received or "",
            "image": "", "search": f"Email alert ({msg.get('Subject', '')[:60]})", "partial": True,
        }
        if text and len(text) > len(lead["title"]) and not text.startswith("http"):
            lead["title"] = text[:200]
        if not lead["snippet"]:
            lead["snippet"] = (around or "")[:400]
        leads[lid] = lead
    return [l for l in leads.values() if l["title"] or l["price"]]


def _received(msg):
    try:
        return email.utils.parsedate_to_datetime(msg.get("Date")).replace(tzinfo=None).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return ""


def from_folder(folder):
    out = []
    for f in sorted(Path(folder).glob("*.eml")):
        msg = email.message_from_bytes(f.read_bytes(), policy=email.policy.default)
        out += parse_message(msg, _received(msg))
    return out


def _password(cfg):
    if cfg.get("keychain_service"):
        r = subprocess.run(["security", "find-generic-password", "-s", cfg["keychain_service"], "-a", cfg["user"], "-w"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    raise RuntimeError("no mailbox password: add it to the keychain (see README, Email alerts)")


def from_imap(cfg, days=3):
    box = imaplib.IMAP4_SSL(cfg["host"], int(cfg.get("port", 993)))
    try:
        box.login(cfg["user"], _password(cfg))
        box.select(f'"{cfg.get("folder", "INBOX")}"', readonly=True)  # read-only: nothing is marked, moved or deleted
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        _typ, data = box.search(None, "SINCE", since)
        out = []
        for num in (data[0] or b"").split():
            _typ, parts = box.fetch(num, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            out += parse_message(msg, _received(msg))
        return out
    finally:
        try:
            box.logout()
        except Exception:
            pass


def collect(settings, log=print, fetcher=None, known=None):
    cfg = settings.get("alerts", {})
    leads = []
    folder = Path(cfg.get("folder") or DATA_DIR / "inbox")
    if folder.exists():
        found = from_folder(folder)
        log(f"  Alert emails in {folder.name}/: {len(found)} ads")
        leads += found
    if (cfg.get("imap") or {}).get("host"):
        found = from_imap(cfg["imap"], int(cfg.get("days", 3)))
        log(f"  Alert emails in the mailbox: {len(found)} ads")
        leads += found
    if not leads and not folder.exists() and not (cfg.get("imap") or {}).get("host"):
        log("  No alert inbox set up yet (see README, Email alerts)")
    return leads
