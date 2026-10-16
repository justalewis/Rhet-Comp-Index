"""Command-line jobs for usage statistics.

    python -m usage resolve                 resolve addresses, rebuild visitor-days
    python -m usage refresh-data [--force]  download IP databases and institution lists
    python -m usage report --from 2026-09-01 --to 2026-12-31 [--format html|zip|json]
                           [--public] [--confirmed-only] [--out FILE]
    python -m usage status                  counts and data-file ages

The same jobs run from /admin/usage and the scheduled tasks; this is for the
server console and for building a report without the web app running.
"""

from __future__ import annotations

import argparse
import json
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m usage", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("resolve", help="resolve addresses and rebuild visitor-days")
    r = sub.add_parser("refresh-data", help="download IP databases and institution lists")
    r.add_argument("--force", action="store_true", help="re-download even if current")
    rep = sub.add_parser("report", help="write the usage report")
    rep.add_argument("--from", dest="start", required=True, help="YYYY-MM-DD")
    rep.add_argument("--to", dest="end", required=True, help="YYYY-MM-DD")
    rep.add_argument("--format", choices=("html", "zip", "json"), default="html")
    rep.add_argument("--public", action="store_true", help="hide anything under --min-count")
    rep.add_argument("--min-count", type=int, default=3)
    rep.add_argument("--confirmed-only", action="store_true",
                     help="count only browser-confirmed visitor-days as people")
    rep.add_argument("--out", help="file to write (default: stdout for json, else a named file)")
    sub.add_parser("status", help="counts and data-file ages")
    args = p.parse_args(argv)

    if args.cmd == "resolve":
        from .resolve import run
        result = run()
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.cmd == "refresh-data":
        from .refresh import refresh
        result = refresh(force=args.force)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    if args.cmd == "report":
        from .report import build_summary, render_html, to_zip
        classes = ("human",) if args.confirmed_only else ("human", "likely-human")
        s = build_summary(args.start, args.end, classes=classes, public=args.public,
                          min_count=args.min_count)
        stem = f"pinakes-usage-{s['period']['start']}-to-{s['period']['end']}{'-public' if args.public else ''}"
        if args.format == "json" and not args.out:
            print(json.dumps(s, indent=2, default=str))
            return 0
        out = args.out or f"{stem}.{args.format}"
        if args.format == "zip":
            with open(out, "wb") as fh:
                fh.write(to_zip(s))
        elif args.format == "json":
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(s, fh, indent=2, default=str)
        else:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(render_html(s))
        print(out)
        return 0

    if args.cmd == "status":
        from . import store
        from .refresh import data_status
        conn = store.connect()
        try:
            out = {
                "usage_db": store.usage_db_path(),
                "hits": conn.execute("SELECT COUNT(*) FROM hits").fetchone()[0],
                "unresolved": conn.execute("SELECT COUNT(*) FROM hits WHERE resolved = 0").fetchone()[0],
                "raw_addresses": conn.execute(
                    "SELECT COUNT(DISTINCT ip) FROM hits WHERE ip IS NOT NULL").fetchone()[0],
                "institutions": conn.execute("SELECT COUNT(*) FROM institutions").fetchone()[0],
                "data_files": data_status(),
            }
        finally:
            conn.close()
        print(json.dumps(out, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
