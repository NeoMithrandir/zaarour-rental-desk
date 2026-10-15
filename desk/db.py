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
CREATE TABLE IF NOT EXISTS rentals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    data TEXT,
    followup_date TEXT,
    lease_end TEXT,
    address TEXT
);
"""

LEAD_FIELDS = ["id", "source", "source_id", "url", "title", "snippet", "address", "area", "phase", "lat", "lng",
               "price", "bedrooms", "unit_type", "poster_id", "poster_type", "posted_at", "image",
               "search", "lang", "flags", "bucket"]


def now():
    return datetime.now().isoformat(timespec="seconds")


def connect(path=None):
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(path or DB_PATH))
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
    return conn


def upsert_lead(conn, lead):
    """Insert a new lead; return True if it was new. Existing leads keep Stefan's decision."""
    row = {k: lead.get(k) for k in LEAD_FIELDS}
    row["flags"] = json.dumps(lead.get("flags", []), ensure_ascii=False)
    cur = conn.execute("SELECT id FROM leads WHERE id=?", (row["id"],))
    if cur.fetchone():
        # Refresh the facts (price drops, edits) but never the decision.
        sets = ", ".join(f"{k}=?" for k in LEAD_FIELDS if k != "id")
        conn.execute(f"UPDATE leads SET {sets} WHERE id=?", [row[k] for k in LEAD_FIELDS if k != "id"] + [row["id"]])
        return False
    row["first_seen"] = now()
    cols = LEAD_FIELDS + ["first_seen"]
    conn.execute(f"INSERT INTO leads ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
    return True


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
