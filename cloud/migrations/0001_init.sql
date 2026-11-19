-- The desk's data on Cloudflare D1: the same tables as the Mac's data/desk.sqlite3 (desk/db.py), plus who did what
-- (decided_by, by_user), a sync stamp for the Mac (sync_at, UTC) and a small key-value table for settings and text.
-- Times shown to people (first_seen, decided_at, sent_at...) stay in Montreal local time, as on the Mac.

CREATE TABLE IF NOT EXISTS leads (
    id TEXT PRIMARY KEY,
    source TEXT, source_id TEXT, url TEXT, title TEXT, snippet TEXT,
    address TEXT, area TEXT, phase INTEGER, lat REAL, lng REAL, price INTEGER, bedrooms TEXT, unit_type TEXT,
    poster_id TEXT, poster_type TEXT, posted_at TEXT, image TEXT, search TEXT,
    lang TEXT, flags TEXT, bucket TEXT,
    first_seen TEXT, status TEXT DEFAULT 'new',
    decided_at TEXT, reject_reason TEXT, message TEXT, sent_at TEXT,
    outcome TEXT, outcome_at TEXT, notes TEXT,
    detailed INTEGER, template TEXT, approach TEXT,
    phone TEXT, phone_via TEXT, phone_checked_at TEXT,
    decided_by TEXT,
    sync_at TEXT
);
CREATE INDEX IF NOT EXISTS leads_status ON leads(status, bucket);
CREATE INDEX IF NOT EXISTS leads_sync ON leads(sync_at);
CREATE INDEX IF NOT EXISTS leads_poster ON leads(source, poster_id);

CREATE TABLE IF NOT EXISTS known_posters (
    source TEXT, poster_id TEXT, label TEXT, added_at TEXT,
    PRIMARY KEY (source, poster_id)
);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT, started_at TEXT, finished_at TEXT, ok INTEGER,
    ads INTEGER, new INTEGER, requests INTEGER, not_modified INTEGER, error TEXT,
    mac_id INTEGER UNIQUE
);
CREATE INDEX IF NOT EXISTS scan_runs_source ON scan_runs(source, finished_at);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id TEXT, at TEXT, action TEXT, reason TEXT, template TEXT, edited INTEGER, signals TEXT, approach TEXT,
    by_user TEXT
);
CREATE INDEX IF NOT EXISTS events_lead ON events(lead_id, at);

CREATE TABLE IF NOT EXISTS drafts (
    lead_id TEXT, lang TEXT, text TEXT, template TEXT, model TEXT, created_at TEXT, approach TEXT, picked_by TEXT,
    sync_at TEXT,
    PRIMARY KEY (lead_id, lang)
);
CREATE INDEX IF NOT EXISTS drafts_sync ON drafts(sync_at);

CREATE TABLE IF NOT EXISTS rentals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT,
    data TEXT,
    followup_date TEXT,
    lease_end TEXT,
    address TEXT,
    external_id TEXT,
    origin TEXT,
    created_by TEXT,
    sync_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS rentals_external_id ON rentals(external_id) WHERE external_id IS NOT NULL;

-- Settings pushed from the Mac (secrets stripped), config files, message templates, style samples, each person's
-- contact details (profile:<email>), the Mac's scanner status and a pending "read now" request.
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT,
    updated_by TEXT
);
