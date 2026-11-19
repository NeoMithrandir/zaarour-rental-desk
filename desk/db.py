"""SQLite storage: leads seen, Stefan's decisions, and leases in the renewal tracker (table "rentals")."""
import json
import sqlite3
from datetime import datetime

from .config import DB_PATH, DATA_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    source TEXT, source_id TEXT, url TEXT, title TEXT, snippet TEXT,
    address TEXT, area TEXT, phase INTEGER, lat REAL, lng REAL, price INTEGER, bedrooms TEXT, unit_type TEXT,
    poster_id TEXT, poster_type TEXT, posted_at TEXT, image TEXT, search TEXT,
    lang TEXT, flags TEXT, bucket TEXT,
    first_seen TEXT, status TEXT DEFAULT 'new',
    decided_at TEXT, reject_reason TEXT, message TEXT, sent_at TEXT,
    outcome TEXT, outcome_at TEXT, notes TEXT
);
CREATE INDEX IF NOT EXISTS leads_status ON leads(status, bucket);
CREATE TABLE IF NOT EXISTS known_posters (
    source TEXT, poster_id TEXT, label TEXT, added_at TEXT,
    PRIMARY KEY (source, poster_id)
);
CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT, started_at TEXT, finished_at TEXT, ok INTEGER,
    ads INTEGER, new INTEGER, requests INTEGER, not_modified INTEGER, error TEXT
);
CREATE INDEX IF NOT EXISTS scan_runs_source ON scan_runs(source, finished_at);
CREATE TABLE IF NOT EXISTS reveal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    board TEXT, at TEXT, pages INTEGER, numbers INTEGER, stop_until TEXT, note TEXT, lead_id TEXT
);
CREATE INDEX IF NOT EXISTS reveal_log_board ON reveal_log(board, at);
CREATE TABLE IF NOT EXISTS phone_searches (
    number TEXT PRIMARY KEY, searched_at TEXT, verdict TEXT, label TEXT, url TEXT, title TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id TEXT, at TEXT, action TEXT, reason TEXT, template TEXT, edited INTEGER, signals TEXT
);
CREATE INDEX IF NOT EXISTS events_lead ON events(lead_id, at);
CREATE TABLE IF NOT EXISTS drafts (
    lead_id TEXT, lang TEXT, text TEXT, template TEXT, model TEXT, created_at TEXT,
    PRIMARY KEY (lead_id, lang)
);
CREATE TABLE IF NOT EXISTS rentals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    data TEXT,
    followup_date TEXT,
    lease_end TEXT,
    address TEXT
);
"""

# The ad's facts (what the scanners read). Writing any of them stamps changed_at; the decisions don't.
FACT_COLUMNS = ["source", "source_id", "url", "title", "snippet", "address", "area", "phase", "lat", "lng", "price", "bedrooms",
                "unit_type", "poster_id", "poster_type", "posted_at", "image", "search", "lang", "flags", "bucket", "detailed",
                "phone", "phone_via", "phone_checked_at"]
_STAMP = "strftime('%Y-%m-%dT%H:%M:%f', 'now')"
CHANGED_TRIGGERS = f"""
CREATE TRIGGER IF NOT EXISTS leads_added AFTER INSERT ON leads
BEGIN UPDATE leads SET changed_at = {_STAMP} WHERE id = NEW.id; END;
CREATE TRIGGER IF NOT EXISTS leads_refreshed AFTER UPDATE OF {", ".join(FACT_COLUMNS)} ON leads
BEGIN UPDATE leads SET changed_at = {_STAMP} WHERE id = NEW.id; END;
"""

LEAD_FIELDS = ["id", "source", "source_id", "url", "title", "snippet", "address", "area", "phase", "lat", "lng",
               "price", "bedrooms", "unit_type", "poster_id", "poster_type", "posted_at", "image",
               "search", "lang", "flags", "bucket", "detailed"]
# What only an opened ad has (sources/details.py): a later search-page read must not overwrite it.
DETAIL_FIELDS = ["snippet", "posted_at", "bedrooms", "unit_type", "detailed"]


def now():
    return datetime.now().isoformat(timespec="seconds")


def connect(path=None):
    DATA_DIR.mkdir(exist_ok=True)
    # The review screen and a background scan share the file: wait for each other instead of failing.
    conn = sqlite3.connect(str(path or DB_PATH), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}
    if "phase" not in cols:  # databases created before areas had phases
        conn.execute("ALTER TABLE leads ADD COLUMN phase INTEGER")
    rcols = {r["name"] for r in conn.execute("PRAGMA table_info(rentals)")}
    for col in ("external_id", "origin"):  # leases imported from outside the desk (lease_import.py)
        if col not in rcols:
            conn.execute(f"ALTER TABLE rentals ADD COLUMN {col} TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS rentals_external_id ON rentals(external_id) WHERE external_id IS NOT NULL")
    if "detailed" not in cols:  # 1 once the ad itself was opened for its full text
        conn.execute("ALTER TABLE leads ADD COLUMN detailed INTEGER")
    if "template" not in cols:  # which message template an approved lead got (for the learnings)
        conn.execute("ALTER TABLE leads ADD COLUMN template TEXT")
    # Which way of opening the message used (approaches.py): on the draft, the approved lead and each event.
    for table, col in (("leads", "approach"), ("drafts", "approach"), ("drafts", "picked_by"), ("events", "approach")):
        if col not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
    # Landlord phone numbers, only on boards whose permission covers contact details (sources/phones.py).
    for col in ("phone", "phone_via", "phone_checked_at"):
        if col not in {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {col} TEXT")
    # When the scanners last added or refreshed an ad, so the online desk gets only what changed (cloud.py).
    if "changed_at" not in {r["name"] for r in conn.execute("PRAGMA table_info(leads)")}:
        conn.execute("ALTER TABLE leads ADD COLUMN changed_at TEXT")
    conn.executescript(CHANGED_TRIGGERS)
    return conn


def upsert_lead(conn, lead, refresh=True):
    """Insert a new lead; return True if it was new. Existing leads keep Stefan's decision.

    refresh=False leaves an existing lead untouched: a captured or alert copy of an ad the desk already
    read carries less detail than the scanned one."""
    row = {k: lead.get(k) for k in LEAD_FIELDS}
    row["flags"] = json.dumps(lead.get("flags", []), ensure_ascii=False)
    cur = conn.execute("SELECT id FROM leads WHERE id=?", (row["id"],))
    if cur.fetchone():
        if not refresh:
            return False
        # Refresh the facts (price drops, edits) but never the decision.
        fields = [k for k in LEAD_FIELDS if k != "id" and not (lead.get("summary_only") and k in DETAIL_FIELDS)]
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE leads SET {sets} WHERE id=?", [row[k] for k in fields] + [row["id"]])
        return False
    row["first_seen"] = now()
    cols = LEAD_FIELDS + ["first_seen"]
    conn.execute(f"INSERT INTO leads ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
    return True


def sync_phases(conn, areas):
    """Re-tag stored ads when an area changes phase in config/areas.json, so sorting, the phase filter and the
    "Phase 2" tag follow the new split for ads already on file. Returns how many ads changed."""
    changed = 0
    for name, a in areas.items():
        n = int(a.get("phase", 1))
        rows = conn.execute("SELECT id, flags FROM leads WHERE area=? AND phase IS NOT ?", (name, n)).fetchall()
        for r in rows:
            flags = [f for f in json.loads(r["flags"] or "[]") if f.get("code") != "phase2"]
            if n > 1:
                flags.append({"code": "phase2", "level": "info", "text": "Phase 2 area"})
            conn.execute("UPDATE leads SET phase=?, flags=? WHERE id=?", (n, json.dumps(flags, ensure_ascii=False), r["id"]))
        changed += len(rows)
    conn.commit()
    return changed


def lead_dict(r):
    d = dict(r)
    d["flags"] = json.loads(d.get("flags") or "[]")
    return d


def known_posters(conn):
    return {r["poster_id"]: r["label"] for r in conn.execute("SELECT poster_id, label FROM known_posters")}


def mark_poster(conn, source, poster_id, label):
    if poster_id:
        conn.execute("INSERT OR REPLACE INTO known_posters VALUES (?,?,?,?)", (source, poster_id, label, now()))


def unmark_poster(conn, source, poster_id, label):
    """Forget a learned poster, but only the entry a given action created (undo of a Broker reject)."""
    if poster_id:
        conn.execute("DELETE FROM known_posters WHERE source=? AND poster_id=? AND label=?", (source, poster_id, label))


def log_event(conn, lead, action, reason=None, template=None, edited=None, approach=None):
    """Append one decision or outcome to the learning log, with the listing's signals as they were then."""
    d = dict(lead)
    signals = {k: d.get(k) for k in ("source", "area", "phase", "price", "bedrooms", "unit_type", "poster_type",
                                       "lang", "posted_at", "bucket", "first_seen")}
    flags = d.get("flags")
    if isinstance(flags, str):
        flags = json.loads(flags or "[]")
    signals["flags"] = sorted({f["code"] for f in flags or []})
    conn.execute("INSERT INTO events (lead_id, at, action, reason, template, edited, signals, approach) VALUES (?,?,?,?,?,?,?,?)",
                 (d.get("id"), now(), action, reason, template, None if edited is None else int(bool(edited)),
                  json.dumps(signals, ensure_ascii=False), approach))
