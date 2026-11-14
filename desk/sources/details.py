"""Opening ads for their full text, on boards whose search pages carry little (Craigslist, LogisQuébec).

After a board's search pages are read, the desk opens the ads it has no full text for yet: new ads first, newest
first, at most `details.max_per_run` per board per read, through the same polite fetcher (robots.txt, spacing,
back-off, and any limit from the board's permission). An ad opened once is never opened again.

Phone numbers and email addresses are blanked out of the text before it is stored: Stefan replies through the
board's own form. The checks see the original text once, in memory, so a known broker's number still flags the ad.
"""
import re

from .http import HostCooling

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_DIGIT = r"(?:\d|zero|one|two|three|four|five|six|seven|eight|nine|z[ée]ro|un|deux|trois|quatre|cinq|sept|huit|neuf)"
# Ten digits or more in a row, allowing spaces, dots, dashes and brackets, and digits spelled out to dodge filters
# ("514Two66One355").
PHONE_RE = re.compile(rf"(?<![\w]){_DIGIT}(?:[\s.\-()]{{0,3}}{_DIGIT}){{9,}}(?![\w])", re.I)
DETAIL_FIELDS = ("snippet", "posted_at", "bedrooms", "unit_type")


def redact(text):
    text = EMAIL_RE.sub("[email removed]", text or "")
    text = PHONE_RE.sub("[phone removed]", text)
    # Glued spellings like 514Two66One355 have no separators for PHONE_RE's word boundaries to find.
    def glued(m):
        digits = re.findall(_DIGIT, m.group(0), re.I)
        return "[phone removed]" if len(digits) >= 10 and re.search(r"\d", m.group(0)) else m.group(0)
    return re.sub(r"\b[0-9A-Za-zéÉ]{10,}\b", glued, text)


def enrich(conn, source, leads, parse_ad, settings, fetcher, log=print):
    """Add the full text, posting date and unit details to leads from this board that don't have them yet."""
    cfg = settings.get("details") or {}
    if source not in cfg.get("boards", []):
        return 0
    have = {r[0] for r in conn.execute("SELECT id FROM leads WHERE source=? AND detailed=1", (source,))}
    known = {r[0] for r in conn.execute("SELECT id FROM leads WHERE source=?", (source,))}
    for lead in leads:
        if lead["id"] in have:
            lead["summary_only"] = True  # keep the full text already stored
    todo = [l for l in leads if l["id"] not in have and l.get("url")]
    todo.sort(key=lambda l: l["id"] in known)  # new ads first, otherwise in page order (newest first)
    done = failed = 0
    for lead in todo[:int(cfg.get("max_per_run", 60))]:
        try:
            extra = parse_ad(fetcher(lead["url"], conditional=False))
        except HostCooling as e:
            log(f"  ! {source} ads: {e}")
            break
        except Exception as e:  # a removed ad or a page change: skip it, keep going
            failed += 1
            if failed <= 3:
                log(f"  ! {source} ad {lead['source_id']}: {e}")
            continue
        text = (extra.pop("text", "") or "").strip()
        if text:
            lead["check_text"] = text
            lead["snippet"] = redact(text)[:4000]
        for k, v in extra.items():
            if v and not lead.get(k):
                lead[k] = v
        lead["detailed"] = 1
        done += 1
    left = len(todo) - done - failed
    log(f"  {source}: opened {done} ads for their full text" + (f", {left} left for later reads" if left > 0 else ""))
    return done
