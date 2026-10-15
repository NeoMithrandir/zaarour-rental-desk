"""python3 -m desk [fetch|serve|morning|recheck|export]"""
import argparse
import csv
import sys

from . import db, feed, lease_import, leases, web


def main(argv=None):
    p = argparse.ArgumentParser(prog="desk", description="Zaarour rentals desk")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("fetch", help="collect today's new Kijiji ads and run the checks")
    s = sub.add_parser("serve", help="open the review screen")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-browser", action="store_true")
    m = sub.add_parser("morning", help="fetch, then open the review screen (what Stefan runs)")
    m.add_argument("--port", type=int, default=8765)
    sub.add_parser("recheck", help="re-run the checks after editing brokers or loading a Centris export")
    i = sub.add_parser("import-leases", help="import lease files (default: everything in data/import/inbox)")
    i.add_argument("paths", nargs="*")
    i.add_argument("--dry-run", action="store_true", help="validate and report without saving")
    e = sub.add_parser("export-leases", aliases=["export-rentals"], help="write the leases in the renewal tracker to a CSV")
    e.add_argument("path", nargs="?", default="data/leases.csv")
    a = p.parse_args(argv)

    if a.cmd == "fetch":
        feed.run_daily()
    elif a.cmd == "serve":
        web.serve(a.port, not a.no_browser)
    elif a.cmd == "morning":
        lease_import.import_inbox()
        feed.run_daily()
        web.serve(a.port)
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
