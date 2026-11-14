"""The daily lead feed (plan items 1.1 to 1.3): collect, check, store."""
import hashlib
from collections import Counter

from . import alerts, capture, db
from .checks import Checker, bucket
from .config import load_areas, load_settings
from .outreach import detect_lang
from .sources import appartogo, craigslist, kijiji, lespac, livrent, logisquebec

SOURCES = {"kijiji": kijiji, "craigslist": craigslist, "logisquebec": logisquebec, "livrent": livrent,
           "appartogo": appartogo, "lespac": lespac,
           "alerts": alerts}


def checker_for(conn, settings=None):
    return Checker(load_areas(), settings or load_settings(), db.known_posters(conn))


def process(conn, leads, checker):
    """Run the checks on raw leads and store them. Returns counts for the run summary."""
    # Several ads from one poster in one batch usually means a property manager or a broker.
    per_poster = Counter(l.get("poster_id") for l in leads if l.get("poster_id"))
    counts = Counter()
    for lead in leads:
        area, flags = checker.run(lead)
        n = per_poster.get(lead.get("poster_id"), 0)
        if n >= 3 and not any(f["code"] in ("broker", "property_manager") for f in flags):
            flags.append({"code": "property_manager", "level": "info", "text": f"Same poster has {n} ads today"})
        lead["area"], lead["flags"], lead["bucket"] = area, flags, bucket(flags)
        lead["phase"] = checker.phase.get(area)
        lead["lang"] = detect_lang(lead.get("title", "") + " " + lead.get("snippet", ""))
        new = db.upsert_lead(conn, lead, refresh=not lead.get("partial"))
        counts["new" if new else "already_seen"] += 1
        if new:
            counts[lead["bucket"]] += 1
    conn.commit()
    return counts


def run_daily(log=print, fetcher=None, force=False):
    """Read the boards whose last read is stale (all of them with force) and print a summary."""
    from . import scan
    settings = load_settings()
    conn = db.connect()
    counts = scan.run(conn, settings, force=force, log=log, fetcher=fetcher)
    log(f"Done: {counts['new']} new ads ({counts['review']} to review, {counts['broker']} likely broker, "
        f"{counts['hidden']} hidden as tenant ads or out of area); {counts['already_seen']} already seen.")
    return counts


def recheck_all(conn):
    """Re-run the checks on undecided leads, e.g. after adding brokers or loading a Centris export."""
    checker = checker_for(conn)
    rows = [db.lead_dict(r) for r in conn.execute("SELECT * FROM leads WHERE status='new'")]
    return process(conn, rows, checker)


def add_manual(conn, form):
    """A lead Stefan pastes in by hand or sends with the capture button while browsing a board himself
    (Facebook Marketplace and the other boards the desk does not read)."""
    url = (form.get("url") or "").strip()
    source = form.get("source") or capture.source_of(url) or "facebook"
    lead = {
        "id": capture.canonical_id(url, source) if url else
              "manual:" + hashlib.sha1((form.get("title", "") + form.get("address", "")).encode()).hexdigest()[:12],
        "source": source,
        "source_id": "",
        "url": url,
        "title": (form.get("title") or "").strip(),
        "snippet": (form.get("text") or "").strip(),
        "address": (form.get("address") or "").strip(),
        "price": int(form["price"]) if str(form.get("price") or "").strip().isdigit() else None,
        "poster_id": (form.get("poster") or "").strip(),
        "poster_type": "",
        "posted_at": (form.get("posted_at") or "").strip() or db.now(),
        "search": form.get("via") or "manual",
        "partial": True,
    }
    process(conn, [lead], checker_for(conn))
    return lead["id"]
