"""python3 -m desk [fetch|serve|morning|recheck|export]"""
import argparse
import csv
import sys

from . import db, feed, rentals, web


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
    e = sub.add_parser("export-rentals", help="write rentals entered so far to a CSV")
    e.add_argument("path", nargs="?", default="data/rentals.csv")
    a = p.parse_args(argv)

    if a.cmd == "fetch":
        feed.run_daily()
    elif a.cmd == "serve":
        web.serve(a.port, not a.no_browser)
    elif a.cmd == "morning":
        feed.run_daily()
        web.serve(a.port)
    elif a.cmd == "recheck":
        print(dict(feed.recheck_all(db.connect())))
    elif a.cmd == "export-rentals":
        rows = rentals.export_rows(db.connect())
        with open(a.path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["id", "created_at", "followup_date"] + rentals.FIELD_KEYS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"{len(rows)} rentals written to {a.path}")
    else:
        p.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
