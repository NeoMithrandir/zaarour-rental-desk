"""The Mac's side of the online desk (cloud/, at rentals.zaarour.ca).

The team reviews ads, approves messages and keeps the lease tracker on the online desk. This Mac keeps doing what
only it can do: read the boards (with its permissions, cool-offs and caches), reveal and search phone numbers where
allowed, and write the learnings. After each read it pushes the new and refreshed ads up; it pulls the team's
decisions, drafts, leases and broker list back so the learnings and the checks see them.

Who owns what: the ad's facts come from here; the decisions and the leases belong to the online desk. A push never
overwrites a decision there, and a pull only brings back what the desk decided. Nothing here sends anything to a
landlord: messages still go out only when a person clicks the board's own Send.

settings.json:
  "cloud": {"url": "https://rentals.zaarour.ca", "access_client_id": "...", "access_client_secret": "...", "token": "..."}
access_client_id/secret: the Cloudflare Access service token that lets this Mac past Access.
token: the desk's AGENT_TOKEN secret (the same value as `npx wrangler secret put AGENT_TOKEN`).
"""
import json
import re
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from . import db
from .config import CONFIG_DIR, DATA_DIR

STATE = DATA_DIR / "cloud-state.json"
CHUNK = 200
SECRET = re.compile(r"(key|password|passwd|secret|token|cookie)", re.I)
LEAD_COLS = ["id", "source", "source_id", "url", "title", "snippet", "address", "area", "phase", "lat", "lng", "price",
             "bedrooms", "unit_type", "poster_id", "poster_type", "posted_at", "image", "search", "lang", "flags", "bucket",
             "detailed", "phone", "phone_via", "phone_checked_at", "first_seen", "status", "decided_at", "reject_reason",
             "message", "sent_at", "outcome", "outcome_at", "notes", "template", "approach"]
DECISIONS = ["status", "decided_at", "reject_reason", "message", "sent_at", "outcome", "outcome_at", "notes", "template", "approach"]


class CloudError(Exception):
    pass


def config(settings):
    return settings.get("cloud") or {}


def enabled(settings):
    c = config(settings)
    return bool(c.get("url") and c.get("token"))


def url(settings):
    return config(settings).get("url", "").rstrip("/") + "/"


def strip_secrets(obj):
    """settings.json without API keys, passwords, tokens or the cloud block: what the online desk may keep."""
    if isinstance(obj, dict):
        return {k: strip_secrets(v) for k, v in obj.items()
                if k != "cloud" and not (SECRET.search(k) and isinstance(v, str))}
    if isinstance(obj, list):
        return [strip_secrets(v) for v in obj]
    return obj


# ---------------------------------------------------------------- talking to the desk
def _headers(settings):
    c = config(settings)
    h = {"Authorization": f"Bearer {c.get('token', '')}", "Content-Type": "application/json", "Accept": "application/json",
         "User-Agent": "ZaarourDeskMac/1"}
    if c.get("access_client_id"):
        h["CF-Access-Client-Id"] = c["access_client_id"]
        h["CF-Access-Client-Secret"] = c.get("access_client_secret", "")
    return h


def _curl(method, full, headers, data, timeout):
    """The same request with curl, when this Python can't verify certificates (python.org installs without
    'Install Certificates.command'). The headers go through curl's config on stdin, so the secrets never show in ps."""
    cfg = "".join(f'header = "{k}: {v}"\n' for k, v in headers.items())
    cmd = ["curl", "-sS", "-X", method, "--max-time", str(timeout), "-K", "-", "-w", "\n%{http_code}", full]
    if data is not None:
        # The body goes in a temporary file next to the state file; stdin carries the config.
        tmp = DATA_DIR / "cloud-body.json"
        tmp.write_bytes(data)
        cmd[1:1] = ["--data-binary", f"@{tmp}"]
    try:
        r = subprocess.run(cmd, input=cfg.encode(), capture_output=True, timeout=timeout + 10)
    finally:
        if data is not None:
            (DATA_DIR / "cloud-body.json").unlink(missing_ok=True)
    if r.returncode:
        raise CloudError(f"couldn't reach the online desk: {r.stderr.decode(errors='replace').strip()}")
    body, _, code = r.stdout.decode("utf-8", errors="replace").rpartition("\n")
    return int(code or 0), body


def request(settings, method, path, body=None, timeout=60):
    full = url(settings) + path.lstrip("/")
    headers = _headers(settings)
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(full, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, text = r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        code, text = e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError) or "CERTIFICATE" in str(e.reason).upper():
            code, text = _curl(method, full, headers, data, timeout)
        else:
            raise CloudError(f"couldn't reach the online desk at {url(settings)}: {e.reason}")
    try:
        out = json.loads(text) if text else {}
    except ValueError:
        # Access answers with its sign-in page when the service token is missing or wrong.
        raise CloudError(f"the online desk answered {code} with a page, not data: check cloud.access_client_id and "
                         "access_client_secret in config/settings.json, and the Access policy for the Mac's service token")
    if code >= 400:
        raise CloudError(out.get("error") or f"HTTP {code}")
    return out


# ---------------------------------------------------------------- what has been sent and fetched
def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state):
    DATA_DIR.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _read(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def config_files():
    out = {}
    for name in ("areas", "approaches", "tal_rates"):
        text = _read(CONFIG_DIR / f"{name}.json")
        if text:
            out[name] = json.loads(text)
    brokers = _read(CONFIG_DIR / "known_brokers.csv")
    if brokers is not None:
        out["known_brokers"] = brokers
    return out


def reference(conn):
    """What the checks use that lives only on this Mac: the Centris export and the numbers found with brokers."""
    from .checks import Checker
    from .phone_search import found_brokers
    centris = Checker._load_centris(None, DATA_DIR / "centris_active_rentals.csv")
    return {"centris": centris, "found_brokers": found_brokers(conn)}


def scanner_status(conn, settings, scanner=None):
    from . import scan
    st = dict(scanner.status) if scanner else {"running": False, "lines": [], "last": None}
    return {"running": bool(st.get("running")), "lines": list(st.get("lines") or [])[-40:], "last": st.get("last"),
            "boards": scan.board_status(conn, settings), "at": db.now()}


def _rows(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


# ---------------------------------------------------------------- push
def push(conn, settings, scanner=None, log=print):
    """Send the ads the scanners added or refreshed since the last push, the scan log and the settings."""
    state = load_state()
    lo = state.get("pushed_changed_at") or ""
    hi = conn.execute("SELECT MAX(changed_at) FROM leads").fetchone()[0] or lo
    cols = ", ".join(c for c in LEAD_COLS)
    leads = _rows(conn, f"SELECT {cols} FROM leads WHERE changed_at > ? AND changed_at <= ? ORDER BY changed_at", (lo, hi))
    runs = _rows(conn, "SELECT * FROM scan_runs WHERE id > ? ORDER BY id", (state.get("pushed_scan_run", 0),))
    extra = {"settings": strip_secrets(_settings_file()), "config": config_files(), "reference": reference(conn),
             "status": scanner_status(conn, settings, scanner), "scan_runs": runs}
    parts = [leads[i:i + CHUNK] for i in range(0, len(leads), CHUNK)] or [[]]
    totals = {"inserted": 0, "updated": 0, "expired": 0}
    for n, part in enumerate(parts):
        body = {"leads": part, "final": n == len(parts) - 1, **(extra if n == 0 else {})}
        res = request(settings, "POST", "api/agent/ingest", body)
        for k in totals:
            totals[k] += res.get(k, 0)
    state.update(pushed_changed_at=hi, pushed_scan_run=max([r["id"] for r in runs], default=state.get("pushed_scan_run", 0)),
                 pushed_at=db.now())
    save_state(state)
    if leads or totals["expired"]:
        log(f"Online desk: {totals['inserted']} new ads sent, {totals['updated']} refreshed, {totals['expired']} set aside as old.")
    return totals


def _settings_file():
    """settings.json as written (the online desk adds the example's defaults itself)."""
    text = _read(CONFIG_DIR / "settings.json")
    return json.loads(text) if text else {}


# ---------------------------------------------------------------- pull
def _apply_leads(conn, rows):
    have = {r[0] for r in conn.execute(
        f"SELECT id FROM leads WHERE id IN ({','.join('?' * len(rows))})", [r['id'] for r in rows])} if rows else set()
    for r in rows:
        if r["id"] in have:
            conn.execute(f"UPDATE leads SET {', '.join(f'{c}=?' for c in DECISIONS)} WHERE id=?",
                         [r.get(c) for c in DECISIONS] + [r["id"]])
        else:  # an ad added on the online desk (by hand, the capture button)
            cols = [c for c in LEAD_COLS if c in r]
            conn.execute(f"INSERT OR IGNORE INTO leads ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [r[c] for c in cols])
            conn.execute("UPDATE leads SET changed_at=NULL WHERE id=?", (r["id"],))  # it came from there: nothing to send back


def pull(conn, settings, log=print):
    """Fetch what the team decided on the online desk: decisions, outcomes, drafts, leases, the broker list, events."""
    state = load_state()
    counts = {"leads": 0, "events": 0, "drafts": 0, "rentals": 0}
    for _ in range(50):  # pages of 1000 changed ads
        q = f"api/agent/pull?since={state.get('pull_since', '')}&incl={'1' if state.get('pull_incl') else '0'}&events_after={state.get('events_after', 0)}"
        res = request(settings, "GET", q, timeout=120)
        _apply_leads(conn, res.get("leads") or [])
        for d in res.get("drafts") or []:
            conn.execute("DELETE FROM drafts WHERE lead_id=?", (d["lead_id"],))
        for d in res.get("drafts") or []:
            conn.execute("INSERT OR REPLACE INTO drafts (lead_id, lang, text, template, model, created_at, approach, picked_by) "
                         "VALUES (?,?,?,?,?,?,?,?)", [d.get(c) for c in ("lead_id", "lang", "text", "template", "model", "created_at", "approach", "picked_by")])
        for r in res.get("rentals") or []:
            conn.execute("INSERT OR REPLACE INTO rentals (id, created_at, data, followup_date, lease_end, address, external_id, origin) "
                         "VALUES (?,?,?,?,?,?,?,?)", [r.get(c) for c in ("id", "created_at", "data", "followup_date", "lease_end", "address", "external_id", "origin")])
        events = res.get("events") or []
        for e in events:
            conn.execute("INSERT INTO events (lead_id, at, action, reason, template, edited, signals, approach) VALUES (?,?,?,?,?,?,?,?)",
                         [e.get(c) for c in ("lead_id", "at", "action", "reason", "template", "edited", "signals", "approach")])
        if "known_posters" in res:  # the whole list: a Broker reject adds one, its undo removes it
            conn.execute("DELETE FROM known_posters")
            conn.executemany("INSERT OR REPLACE INTO known_posters VALUES (?,?,?,?)",
                             [(p["source"], p["poster_id"], p["label"], p["added_at"]) for p in res["known_posters"]])
        conn.commit()
        counts["leads"] += len(res.get("leads") or [])
        counts["drafts"] += len(res.get("drafts") or [])
        counts["rentals"] += len(res.get("rentals") or [])
        counts["events"] += len(events)
        state.update(pull_since=res["cursor"], pull_incl=bool(res.get("incl")), pulled_at=db.now())
        if events:
            state["events_after"] = events[-1]["id"]
        save_state(state)
        if not res.get("more") and not res.get("events_more"):
            break
    if any(counts.values()):
        log(f"Online desk: {counts['leads']} decisions, {counts['events']} events, {counts['drafts']} drafts and "
            f"{counts['rentals']} leases brought back.")
    return counts


def sync(conn, settings, scanner=None, log=print):
    """Push, then pull, then refresh the learnings if they are a day old."""
    from . import learn
    push(conn, settings, scanner, log)
    pull(conn, settings, log)
    learn.maybe_write(conn, settings)


# ---------------------------------------------------------------- the one-time move
def _samples_text(settings):
    from . import drafts
    return "\n\n---\n\n".join(s["text"] for s in drafts.load_samples(settings))


def migrate(conn, settings, force=False, log=print):
    """Copy this Mac's whole desk to the online desk, once: ads with their decisions, events, drafts, leases, the
    broker list, the scan log, the style samples and the suggested wording."""
    from .profile import suggestions
    start = {"start": True, "force": force, "settings": strip_secrets(_settings_file()), "config": config_files(),
             "reference": reference(conn)}
    request(settings, "POST", "api/agent/migrate", start)
    tables = {
        "leads": f"SELECT {', '.join(LEAD_COLS)} FROM leads",
        "known_posters": "SELECT * FROM known_posters",
        "scan_runs": "SELECT * FROM scan_runs",
        "events": "SELECT * FROM events ORDER BY id",
        "drafts": "SELECT * FROM drafts",
        "rentals": "SELECT * FROM rentals",
    }
    for table, sql in tables.items():
        rows = _rows(conn, sql)
        for i in range(0, len(rows), CHUNK):
            request(settings, "POST", "api/agent/migrate", {"table": table, "rows": rows[i:i + CHUNK]}, timeout=120)
        log(f"  {table}: {len(rows)}")
    kv = [{"key": "samples", "value": _samples_text(settings)}]
    sugg = suggestions()
    if sugg:
        kv.append({"key": "suggestions", "value": sugg})
    office = _settings_file().get("office_email")
    if office:
        kv.append({"key": "desk_settings", "value": {"office_email": office}})
    request(settings, "POST", "api/agent/migrate", {"table": "kv", "rows": kv})
    done = request(settings, "POST", "api/agent/migrate", {"table": "done"})
    state = load_state()
    state.update(pull_since=done["cursor"], pull_incl=False, events_after=done["events_after"],
                 pushed_changed_at=conn.execute("SELECT MAX(changed_at) FROM leads").fetchone()[0] or "",
                 pushed_scan_run=conn.execute("SELECT COALESCE(MAX(id), 0) FROM scan_runs").fetchone()[0], migrated_at=db.now())
    save_state(state)
    return done.get("counts", {})


def import_leases(settings, records, dry_run=False):
    """Lease records for the online desk's tracker (python3 -m desk import-leases, in online mode)."""
    return request(settings, "POST", "api/agent/leases-import", {"records": records, "dry_run": dry_run}, timeout=120)


# ---------------------------------------------------------------- the agent: the Mac's background job
def agent(settings_loader, log=print, once=False):
    """Every minute: tell the desk how the boards stand and take a "Check boards" request. Every few minutes: push
    and pull. Every auto_refresh_minutes: read the boards whose last read is stale, as the open desk used to."""
    from . import learn, scan
    settings = settings_loader()
    if not enabled(settings):
        raise CloudError("The online desk isn't set up on this Mac: fill in cloud in config/settings.json (see cloud/README.md).")

    def after(conn, s):
        learn.maybe_write(conn, s)
        try:
            push(conn, s, scanner, log)
        except CloudError as e:
            log(f"! Couldn't send the new ads to the online desk: {e}")

    scanner = scan.Scanner(settings_loader)
    last_sync = last_auto = 0.0
    lock = threading.Lock()
    while True:
        settings = settings_loader()
        conn = db.connect()
        try:
            res = request(settings, "POST", "api/agent/poll", {"status": scanner_status(conn, settings, scanner)})
            if res.get("scan") and not scanner.status["running"]:
                force = bool(res["scan"].get("force"))
                log(f"{datetime.now():%H:%M} Check boards asked by {res['scan'].get('by') or 'the desk'}")
                if not scanner.start(force=force, after=after):
                    log("  Every board was read recently.")
                    push(conn, settings, scanner, log)  # the bar shows the boards as they stand
            minutes = int(settings.get("scan", {}).get("auto_refresh_minutes", 60))
            if minutes and time.time() - last_auto > minutes * 60 and not scanner.status["running"]:
                last_auto = time.time()
                scanner.start(after=after)
            if time.time() - last_sync > 300 and not scanner.status["running"]:
                with lock:
                    sync(conn, settings, scanner, log)
                last_sync = time.time()
        except CloudError as e:
            log(f"{datetime.now():%H:%M} ! {e}")
        finally:
            conn.close()
        if once:
            return
        time.sleep(10 if scanner.status["running"] else 60)  # while reading, the desk's bar follows along
