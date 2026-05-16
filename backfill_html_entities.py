"""
backfill_html_entities.py — Decode lingering HTML entities in stored fields.

Earlier versions of fetcher.py / rss_fetcher.py stripped HTML tags but didn't
decode entities, so titles and abstracts ended up with literal "&amp;",
"&lt;", "&#8217;" etc. Those get autoescaped again by Jinja on render,
displaying as "&amp;amp;" / "&amp;#8217;" in the browser.

The fetchers now call html.unescape() on the way in. This script fixes the
rows already in the DB.

Usage:
    python backfill_html_entities.py --dry-run
    python backfill_html_entities.py                  # titles only (default)
    python backfill_html_entities.py --include-abstracts
"""

import argparse
import html
import logging

from db import get_conn, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Columns to scan. Each only updates if html.unescape changes the value, so
# rows without entities are left untouched.
DEFAULT_COLUMNS = ("title",)
EXTRA_COLUMNS   = ("abstract",)


def clean_column(conn, column: str, dry_run: bool) -> int:
    """Decode HTML entities in `column` for any row whose value contains '&'.

    Returns the number of rows that would change (dry-run) or did change.
    """
    rows = conn.execute(
        f"SELECT id, {column} FROM articles "
        f"WHERE {column} IS NOT NULL AND {column} LIKE '%&%'"
    ).fetchall()

    updates: list[tuple[str, int]] = []
    for r in rows:
        before = r[column]
        after = before
        # Loop until idempotent — some rows are doubly-encoded ("&amp;amp;"
        # → "&amp;" → "&"). Bounded at 5 passes as a sanity guard.
        for _ in range(5):
            decoded = html.unescape(after)
            if decoded == after:
                break
            after = decoded
        if after != before:
            updates.append((after, r["id"]))

    log.info("%s: %d rows scanned, %d need cleaning",
             column, len(rows), len(updates))

    if updates[:3]:
        for after, rid in updates[:3]:
            row = conn.execute(
                f"SELECT {column} FROM articles WHERE id = ?", (rid,)
            ).fetchone()
            log.info("  #%d  %r  ->  %r", rid, row[column], after)

    if not dry_run and updates:
        conn.executemany(
            f"UPDATE articles SET {column} = ? WHERE id = ?",
            updates,
        )
        conn.commit()

    return len(updates)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing.")
    parser.add_argument("--include-abstracts", action="store_true",
                        help="Also clean entities in abstracts.")
    args = parser.parse_args()

    init_db()

    columns = list(DEFAULT_COLUMNS)
    if args.include_abstracts:
        columns.extend(EXTRA_COLUMNS)

    total = 0
    with get_conn() as conn:
        conn.execute("PRAGMA busy_timeout = 60000")
        for col in columns:
            total += clean_column(conn, col, args.dry_run)

    verb = "would update" if args.dry_run else "updated"
    log.info("Done — %s %d rows across %d column(s).", verb, total, len(columns))


if __name__ == "__main__":
    main()
