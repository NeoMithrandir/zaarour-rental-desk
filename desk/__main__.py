"""python3 -m desk [morning|serve|fetch|learn|recheck|export-rentals]"""
import argparse
import csv
import sys

from . import db, feed, learn, lease_import, leases, web


def main(argv=None):
    p = argparse.ArgumentParser(prog="desk", description="Zaarour rentals desk")
    sub = p.add_subparsers(dest="cmd")
    f = sub.add_parser("fetch", help="read the boards whose last read is stale, run the checks, and stop")
    f.add_argument("--force", action="store_true", help="read every board even if it was read recently")
    s = sub.add_parser("serve", help="open the review screen; stale boards are read in the background")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--no-scan", action="store_true", help="don't read any board, just show what the desk has")
    m = sub.add_parser("morning", help="open the review screen at once and read stale boards behind it (what Stefan runs)")
    m.add_argument("--port", type=int, default=8765)
    sub.add_parser("learn", help="write the learnings (rentals-desk/learnings/) from Stefan's decisions and outcomes")
    sub.add_parser("recheck", help="re-run the checks after editing brokers or loading a Centris export")
    i = sub.add_parser("import-leases", help="import lease files (default: everything in data/import/inbox)")
    i.add_argument("paths", nargs="*")
    i.add_argument("--dry-run", action="store_true", help="validate and report without saving")
    e = sub.add_parser("export-leases", aliases=["export-rentals"], help="write the leases in the renewal tracker to a CSV")
    e.add_argument("path", nargs="?", default="data/leases.csv")
    a = p.parse_args(argv)

    if a.cmd == "fetch":
        feed.run_daily(force=a.force)
    elif a.cmd == "serve":
        web.serve(a.port, not a.no_browser, scan_on_start=not a.no_scan)
    elif a.cmd == "morning":
        lease_import.import_inbox()
        web.serve(a.port, scan_on_start=True)
    elif a.cmd == "learn":
        print(f"Learnings written: {learn.write(db.connect())}")
    elif a.cmd == "recheck":
        print(dict(feed.recheck_all(db.connect())))
    elif a.cmd == "import-leases":
        t = lease_import.import_inbox(paths=a.paths or None, dry_run=a.dry_run)
        return 1 if t["rejected"] else 0
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


if __name__ == "__main__":
    sys.exit(main())
