"""python3 -m desk [morning|serve|fetch|phones|learn|recheck|import-leases|export-leases|cloud]"""
import argparse
import csv
import sys
import webbrowser

from . import cloud, db, feed, learn, lease_import, leases, web
from .config import load_settings


def _online(a):
    """With the online desk set up, the review screen is rentals.zaarour.ca: open that instead of a second desk here."""
    settings = load_settings()
    if not cloud.enabled(settings) or getattr(a, "local", False):
        if cloud.enabled(settings):
            print("Warning: this Mac's own desk is not the team's. Decisions made here stay on this Mac.")
        return False
    print(f"The desk is online: opening {cloud.url(settings)}")
    print("This Mac reads the boards and sends new ads there (python3 -m desk cloud agent; launcher/agent.command).")
    if not getattr(a, "no_browser", False):
        webbrowser.open(cloud.url(settings))
    return True


def main(argv=None):
    p = argparse.ArgumentParser(prog="desk", description="Zaarour rentals desk")
    sub = p.add_subparsers(dest="cmd")
    f = sub.add_parser("fetch", help="read the boards whose last read is stale, run the checks, and stop")
    f.add_argument("--force", action="store_true", help="read every board even if it was read recently")
    s = sub.add_parser("serve", help="open the review screen; stale boards are read in the background")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--no-scan", action="store_true", help="don't read any board, just show what the desk has")
    s.add_argument("--local", action="store_true", help="run this Mac's own desk even when the online desk is set up")
    m = sub.add_parser("morning", help="open the review screen at once and read stale boards behind it (what Stefan runs)")
    m.add_argument("--port", type=int, default=8765)
    m.add_argument("--local", action="store_true", help="run this Mac's own desk even when the online desk is set up")
    sub.add_parser("phones", help="reveal landlords' numbers now where allowed, then search them for brokers")
    sub.add_parser("learn", help="write the learnings (rentals-desk/learnings/) from Stefan's decisions and outcomes")
    sub.add_parser("recheck", help="re-run the checks after editing brokers or loading a Centris export")
    i = sub.add_parser("import-leases", help="import lease files (default: everything in data/import/inbox)")
    i.add_argument("paths", nargs="*")
    i.add_argument("--dry-run", action="store_true", help="validate and report without saving")
    e = sub.add_parser("export-leases", aliases=["export-rentals"], help="write the leases in the renewal tracker to a CSV")
    e.add_argument("path", nargs="?", default="data/leases.csv")
    c = sub.add_parser("cloud", help="the online desk: migrate (once), push, pull, sync, or agent (the Mac's background job)")
    c.add_argument("action", choices=["migrate", "push", "pull", "sync", "agent", "status"])
    c.add_argument("--force", action="store_true", help="migrate: replace what the online desk has with this Mac's copy")
    a = p.parse_args(argv)

    if a.cmd == "fetch":
        feed.run_daily(force=a.force)
        settings = load_settings()
        if cloud.enabled(settings):
            cloud.sync(db.connect(), settings)
    elif a.cmd == "serve":
        if not _online(a):
            web.serve(a.port, not a.no_browser, scan_on_start=not a.no_scan)
    elif a.cmd == "morning":
        settings = load_settings()
        if cloud.enabled(settings) and not a.local:
            lease_import.import_inbox(importer=lambda records, dry_run: cloud.import_leases(settings, records, dry_run))
            _online(a)
        else:
            lease_import.import_inbox()
            web.serve(a.port, scan_on_start=True)
    elif a.cmd == "phones":
        from .config import load_settings
        from .sources import http, phones
        settings = load_settings()
        fetcher = http.default_fetcher(settings)
        from . import phone_search
        conn = db.connect()
        print(f"Numbers revealed: {phones.reveal(conn, settings, fetcher)}")
        print(f"Numbers found with a broker: {phone_search.run(conn, settings)}"
              + (f" (search off: {phone_search.problem(settings)})" if phone_search.problem(settings) else ""))
        fetcher.save()
    elif a.cmd == "learn":
        print(f"Learnings written: {learn.write(db.connect())}")
    elif a.cmd == "recheck":
        print(dict(feed.recheck_all(db.connect())))
    elif a.cmd == "import-leases":
        settings = load_settings()
        importer = (lambda records, dry_run: cloud.import_leases(settings, records, dry_run)) if cloud.enabled(settings) else None
        t = lease_import.import_inbox(paths=a.paths or None, dry_run=a.dry_run, importer=importer)
        return 1 if t["rejected"] else 0
    elif a.cmd == "cloud":
        return _cloud(a)
    elif a.cmd in ("export-leases", "export-rentals"):
        rows = leases.export_rows(db.connect())
        with open(a.path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["id", "created_at", "followup_date"] + leases.FIELD_KEYS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"{len(rows)} leases written to {a.path}")
    else:
        p.print_help()
        return 1
    return 0


def _cloud(a):
    settings = load_settings()
    if not cloud.enabled(settings):
        print("The online desk isn't set up on this Mac yet: fill in \"cloud\" in config/settings.json (see cloud/README.md).")
        return 1
    conn = db.connect()
    try:
        if a.action == "migrate":
            print(f"Moving this Mac's desk to {cloud.url(settings)} ...")
            counts = cloud.migrate(conn, settings, force=a.force)
            print("Done. The online desk now has: " + ", ".join(f"{n} {t}" for t, n in counts.items()))
        elif a.action == "push":
            cloud.push(conn, settings)
        elif a.action == "pull":
            cloud.pull(conn, settings)
        elif a.action == "sync":
            cloud.sync(conn, settings)
        elif a.action == "status":
            st = cloud.load_state()
            print(f"Online desk: {cloud.url(settings)}")
            for k in ("migrated_at", "pushed_at", "pulled_at"):
                print(f"  {k.replace('_', ' ')}: {st.get(k) or 'never'}")
        elif a.action == "agent":
            print(f"Reading the boards for {cloud.url(settings)} (Ctrl+C to stop)")
            cloud.agent(load_settings)
    except cloud.CloudError as e:
        print(f"The online desk: {e}")
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
