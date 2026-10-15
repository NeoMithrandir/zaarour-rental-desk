"""When to read the boards, and reading them in the background.

The desk opens straight away on what it already has. A board is read again only when its last good
read is older than its freshness window (`scan.freshness_minutes` in settings); boards read recently
are reused. While the desk is open it checks again every `scan.auto_refresh_minutes`, still only for
boards that have gone stale. Every read is logged in the scan_runs table.
"""
import threading
import time
from datetime import datetime, timedelta

from . import db
from .sources import http

DEFAULT_FRESHNESS = {"kijiji": 180, "craigslist": 180, "logisquebec": 360, "livrent": 1440}


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
                    "last_error": last["error"] if last and not last["ok"] else None})
    return out


def due(conn, settings, force=False, now=None):
    return [b["source"] for b in board_status(conn, settings, now) if force or not b["fresh"]]


def run(conn, settings, force=False, log=print, fetcher=None, only=None):
    """Read every stale board (or every board with force) and store new ads. Returns counts."""
    from .feed import SOURCES, checker_for, process  # feed imports this module
    from collections import Counter
    fetcher = fetcher or http.default_fetcher(settings)
    known = lambda lead_id: conn.execute("SELECT 1 FROM leads WHERE id=?", (lead_id,)).fetchone() is not None
    total = Counter()
    boards = [s for s in due(conn, settings, force) if not only or s in only]
    skipped = [s for s in settings.get("sources", []) if s not in boards]
    if skipped:
        log("Recently read, reusing: " + ", ".join(skipped))
    checker = checker_for(conn, settings)
    for name in boards:
        module = SOURCES.get(name)
        if not module:
            log(f"  ! Unknown source in settings: {name}")
            continue
        log(f"Reading {name}...")
        started = db.now()
        req0, nm0 = getattr(fetcher, "requests", 0), getattr(fetcher, "not_modified", 0)
        ok, error, leads = 1, None, []
        try:
            leads = module.collect(settings, log=log, fetcher=fetcher, known=known)
        except http.NotModified:
            log(f"  {name}: no change since last check")
        except Exception as e:  # one board failing never stops the others
            ok, error = 0, str(e)
            log(f"  ! {name} failed: {e}")
        counts = process(conn, leads, checker) if leads else Counter()
        total.update(counts)
        conn.execute("INSERT INTO scan_runs (source, started_at, finished_at, ok, ads, new, requests, not_modified, error) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (name, started, db.now(), ok, len(leads), counts.get("new", 0),
                      getattr(fetcher, "requests", 0) - req0, getattr(fetcher, "not_modified", 0) - nm0, error))
        conn.commit()
    if hasattr(fetcher, "save"):
        fetcher.save()
    total["boards_read"] = len(boards)
    return total


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
                counts = run(conn, settings, force=force, log=self._log)
                self.status["last"] = {"at": db.now(), "new": counts.get("new", 0), "review": counts.get("review", 0),
                                       "boards": counts.get("boards_read", 0)}
                if after:
                    after(conn, settings)
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
