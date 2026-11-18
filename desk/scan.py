"""When to read the boards, and reading them in the background.

The desk opens straight away on what it already has. A board is read again only when its last good
read is older than its freshness window (`scan.freshness_minutes` in settings); boards read recently
are reused. While the desk is open it checks again every `scan.auto_refresh_minutes`, still only for
boards that have gone stale. Every read is logged in the scan_runs table.
"""
import threading
import time
from datetime import date, datetime, timedelta

from . import db
from .sources import details, http, phones

DEFAULT_FRESHNESS = {"kijiji": 180, "craigslist": 180, "logisquebec": 360, "livrent": 1440, "appartogo": 360,
                     "lespac": 180}
# Boards the desk reads by itself. Each needs the board's written permission on file (settings `permissions`)
# before it reads anything; the alert inbox only reads Stefan's own email, so it needs none.
SCANNERS = ("kijiji", "craigslist", "logisquebec", "livrent", "appartogo", "lespac")
HOSTS = {"kijiji": ["www.kijiji.ca"], "craigslist": ["montreal.craigslist.org", "www.craigslist.org"],
         "logisquebec": ["www.logisquebec.com"], "livrent": ["liv.rent"],
         "appartogo": ["appartogo.com"], "lespac": ["www.lespac.com"]}


def permission(settings, source, today=None):
    """Why the desk may not read this board yet, or None when it may.

    A scanner runs only when its board's entry under `permissions` has `scan_enabled` on, names the written
    permission in `letter`, and has not passed `expires`. Turning the flag on is Nesi's step, once the letter is in.
    """
    if source not in SCANNERS:
        return None
    p = (settings.get("permissions") or {}).get(source) or {}
    if not p.get("scan_enabled"):
        return "waiting for written permission"
    if not str(p.get("letter") or "").strip():
        return "scan_enabled is on but no permission letter is filed (permissions.%s.letter)" % source
    expires = str(p.get("expires") or "").strip()
    if expires and date.fromisoformat(expires) < (today or date.today()):
        return f"permission expired on {expires}"
    return None


def letter_pending(settings, source):
    """True while a board is read on someone's statement and its letter itself is not filed yet
    (`letter_pending` in its permissions entry; `letter` then holds the statement)."""
    p = (settings.get("permissions") or {}).get(source) or {}
    return source in SCANNERS and bool(p.get("letter_pending"))


def requests_last_hour(conn, source, now=None):
    """Requests to a board in the last hour: its scanner's pages and the ad pages opened for phone numbers."""
    since = ((now or datetime.now()) - timedelta(hours=1)).isoformat(timespec="seconds")
    r = conn.execute("SELECT COALESCE(SUM(requests), 0) FROM scan_runs WHERE source=? AND finished_at>?",
                     (source, since)).fetchone()
    p = conn.execute("SELECT COALESCE(SUM(pages), 0) FROM reveal_log WHERE board=? AND at>?", (source, since)).fetchone()
    return r[0] + p[0]


class Budget:
    """The fetcher, capped at the number of requests a board's permission allows this hour."""

    def __init__(self, fetcher, left, source):
        self.fetcher, self.left, self.source = fetcher, left, source

    def __call__(self, url, *a, **kw):
        if self.left <= 0:
            raise http.HostCooling(f"{self.source}: hourly request limit from its permission reached")
        self.left -= 1
        return self.fetcher(url, *a, **kw)

    def __getattr__(self, name):
        return getattr(self.fetcher, name)


def freshness(settings, source):
    return int((settings.get("scan", {}).get("freshness_minutes") or {}).get(source, DEFAULT_FRESHNESS.get(source, 240)))


def last_ok(conn, source):
    r = conn.execute("SELECT finished_at FROM scan_runs WHERE source=? AND ok=1 ORDER BY finished_at DESC LIMIT 1",
                     (source,)).fetchone()
    return r["finished_at"] if r else None


def board_status(conn, settings, now=None):
    """Per board: when it was last read, whether that is still fresh, and the last error if the last try failed."""
    now = now or datetime.now()
    out = []
    for source in settings.get("sources", []):
        ok_at = last_ok(conn, source)
        last = conn.execute("SELECT * FROM scan_runs WHERE source=? ORDER BY id DESC LIMIT 1", (source,)).fetchone()
        fresh = bool(ok_at) and datetime.fromisoformat(ok_at) > now - timedelta(minutes=freshness(settings, source))
        out.append({"source": source, "last_ok": ok_at, "fresh": fresh, "window_minutes": freshness(settings, source),
                    "last_error": last["error"] if last and not last["ok"] else None,
                    "waiting": permission(settings, source, now.date()),
                    "letter_pending": letter_pending(settings, source),
                    "phones": phones.allowed(settings, source)})
    return out


def due(conn, settings, force=False, now=None):
    return [b["source"] for b in board_status(conn, settings, now) if not b["waiting"] and (force or not b["fresh"])]


def run(conn, settings, force=False, log=print, fetcher=None, only=None, reveal=True):
    """Read every stale board (or every board with force) and store new ads. Returns counts, with the boards
    read in `.boards`. With reveal, then look up landlords' numbers on those boards where allowed (phones.py)."""
    from .feed import SOURCES, checker_for, process  # feed imports this module
    from collections import Counter
    fetcher = fetcher or http.default_fetcher(settings)
    known = lambda lead_id: conn.execute("SELECT 1 FROM leads WHERE id=?", (lead_id,)).fetchone() is not None
    total = Counter()
    boards = [s for s in due(conn, settings, force) if not only or s in only]
    waiting = [s for s in settings.get("sources", []) if permission(settings, s)]
    skipped = [s for s in settings.get("sources", []) if s not in boards and s not in waiting]
    if waiting:
        log("Not read until written permission is on file: " + ", ".join(waiting))
    if skipped:
        log("Recently read, reusing: " + ", ".join(skipped))
    checker = checker_for(conn, settings)
    for name in boards:
        module = SOURCES.get(name)
        if not module:
            log(f"  ! Unknown source in settings: {name}")
            continue
        terms = (settings.get("permissions") or {}).get(name) or {}
        board_fetcher = fetcher
        if terms.get("max_requests_per_hour"):
            left = int(terms["max_requests_per_hour"]) - requests_last_hour(conn, name)
            if left <= 0:
                log(f"  {name}: hourly request limit from its permission already used, next read later")
                continue
            board_fetcher = Budget(fetcher, left, name)
        if terms.get("min_interval_seconds") and hasattr(fetcher, "host_min_interval"):
            for host in HOSTS.get(name, []):
                fetcher.host_min_interval[host] = float(terms["min_interval_seconds"])
        log(f"Reading {name}...")
        started = db.now()
        req0, nm0 = getattr(fetcher, "requests", 0), getattr(fetcher, "not_modified", 0)
        ok, error, leads = 1, None, []
        try:
            leads = module.collect(settings, log=log, fetcher=board_fetcher, known=known)
            if leads and hasattr(module, "parse_ad"):
                details.enrich(conn, name, leads, module.parse_ad, settings, board_fetcher, log)
        except http.NotModified:
            log(f"  {name}: no change since last check")
        except Exception as e:  # one board failing never stops the others
            ok, error = 0, str(e)
            log(f"  ! {name} failed: {e}")
        for lead in leads:
            # Numbers and emails are blanked out of what is stored; the checks (and, where the board's permission
            # covers contact details, sources/phones.py) see the original once, in memory.
            lead.setdefault("check_text", lead.get("snippet") or "")
            lead["check_title"] = lead.get("title") or ""
            lead["snippet"], lead["title"] = details.redact(lead.get("snippet")), details.redact(lead.get("title"))
        counts = process(conn, leads, checker) if leads else Counter()
        if leads:
            phones.keep_printed(conn, name, leads, settings)
        total.update(counts)
        conn.execute("INSERT INTO scan_runs (source, started_at, finished_at, ok, ads, new, requests, not_modified, error) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (name, started, db.now(), ok, len(leads), counts.get("new", 0),
                      getattr(fetcher, "requests", 0) - req0, getattr(fetcher, "not_modified", 0) - nm0, error))
        conn.commit()
    if hasattr(fetcher, "save"):
        fetcher.save()
    total["boards_read"] = len(boards)
    total.boards = boards
    if reveal:
        reveal_phones(conn, settings, fetcher, log, boards)
    return total


def reveal_phones(conn, settings, fetcher, log, boards):
    """Landlords' numbers behind show-number buttons, on the boards just read (never after an alerts-only read)."""
    if not any(b in SCANNERS for b in boards) or not hasattr(fetcher, "robots"):
        return 0
    from . import phone_search
    try:
        return phones.reveal(conn, settings, fetcher, log, only=boards)
    except Exception as e:  # never lose the read over it
        log(f"  ! Phone numbers: {e}")
        return 0
    finally:
        if hasattr(fetcher, "save"):
            fetcher.save()
        try:  # then look the numbers up: a broker's number is an extra broker flag
            phone_search.run(conn, settings, log)
        except Exception as e:
            log(f"  ! Number search: {e}")


class Scanner:
    """One background reader for the running desk. Only one read runs at a time."""

    def __init__(self, settings_loader, connect=db.connect):
        self.settings_loader, self.connect = settings_loader, connect
        self.lock = threading.Lock()
        self.status = {"running": False, "lines": [], "last": None, "started_at": None}

    def _log(self, line):
        lines = self.status["lines"]
        lines.append(line.strip())
        del lines[:-40]

    def start(self, force=False, after=None):
        """Start a background read of stale boards; False if one is already running or nothing is due."""
        settings = self.settings_loader()
        conn = self.connect()
        try:
            if not force and not due(conn, settings):
                return False
        finally:
            conn.close()
        if not self.lock.acquire(blocking=False):
            return False
        self.status.update(running=True, lines=[], started_at=db.now())

        def work():
            conn = self.connect()
            try:
                counts = run(conn, settings, force=force, log=self._log, reveal=False)
                self.status["last"] = {"at": db.now(), "new": counts.get("new", 0), "review": counts.get("review", 0),
                                       "boards": counts.get("boards_read", 0)}
                if after:
                    after(conn, settings)
                # Slow on purpose (ads well apart): the new ads are shown first, but the next read waits for it,
                # so the two never talk to a board at once or keep separate cool-offs.
                self.status["running"] = False
                reveal_phones(conn, settings, http.default_fetcher(), self._log, counts.boards)
            except Exception as e:  # never take the review screen down with it
                self._log(f"! Reading stopped: {e}")
            finally:
                conn.close()
                self.status["running"] = False
                self.lock.release()

        threading.Thread(target=work, name="desk-scan", daemon=True).start()
        return True

    def loop(self, minutes):
        """While the desk is open, check for stale boards every few minutes."""
        def tick():
            while True:
                time.sleep(max(60, minutes * 60))
                try:
                    self.start()
                except Exception:
                    pass
        if minutes:
            threading.Thread(target=tick, name="desk-scan-loop", daemon=True).start()
