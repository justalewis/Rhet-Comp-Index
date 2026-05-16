"""
backfill_reflections_pub_dates.py — Patch bogus Reflections pub_dates.

Penn State Libraries' May-2026 CrossRef deposit for Reflections (ISSN 1541-2075)
stamped many articles with `issued = 2025-08-XX` — the DOI registration date,
not the original publication date. Our fetcher trusted CrossRef's `issued`
field, so those articles landed in the index with pub_date = 2025-08-XX.

This script uses the journal's own /archive/ page (which lists every article
under its real Volume/Issue heading) as the authoritative source for
publication date, then updates affected rows by title match.

Usage:
    python backfill_reflections_pub_dates.py --dry-run
    python backfill_reflections_pub_dates.py
"""

import argparse
import logging
import re

from db import get_conn, init_db
from scraper import _parse_reflections_archive

JOURNAL = "Reflections: A Journal of Community-Engaged Writing and Rhetoric"
BAD_PUBDATE_PREFIX = "2025-08"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _norm_title(t: str) -> str:
    """Aggressive normalization for cross-source title matching."""
    if not t:
        return ""
    t = t.lower()
    # Smart quotes / dashes → ASCII
    for a, b in (("“", '"'), ("”", '"'),
                 ("‘", "'"), ("’", "'"),
                 ("–", "-"), ("—", "-")):
        t = t.replace(a, b)
    # Drop everything but letters, digits, and spaces
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _title_keys(raw_title: str) -> list[str]:
    """Yield normalized lookup keys for a title.

    CrossRef stores titles and subtitles in separate arrays, and our fetcher
    only persists `title[0]`. The archive page concatenates them as
    "Title: Subtitle". So we index each archive entry both under its full
    normalized form *and* under its pre-colon prefix, letting DB rows that
    lost their subtitle still match.
    """
    full = _norm_title(raw_title)
    keys = [full] if full else []
    # Split on the first colon-style separator and add the prefix as a key.
    pre = re.split(r"[:–—]| - ", raw_title, maxsplit=1)[0]
    pre_norm = _norm_title(pre)
    if pre_norm and pre_norm != full:
        keys.append(pre_norm)
    return keys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing.")
    args = parser.parse_args()

    init_db()

    log.info("Fetching Reflections archive page...")
    archive = _parse_reflections_archive()
    if not archive:
        log.error("Archive page returned no rows — aborting.")
        return

    # Normalized title -> pub_date (YYYY-MM). Each archive entry contributes
    # both its full and pre-colon-prefix keys, so DB rows that lost their
    # subtitle (CrossRef stores title/subtitle separately) still match.
    # Any key that maps to conflicting dates is dropped — better to log it as
    # unmatched than guess wrong.
    title_map: dict[str, str] = {}
    collisions: set[str] = set()
    for a in archive:
        pub = a.get("pub_date")
        if not pub:
            continue
        for key in _title_keys(a["title"]):
            if key in title_map and title_map[key] != pub:
                collisions.add(key)
            else:
                title_map[key] = pub
    for c in collisions:
        title_map.pop(c, None)
    log.info("Archive title keys indexed: %d  (collisions dropped: %d)",
             len(title_map), len(collisions))

    with get_conn() as conn:
        conn.execute("PRAGMA busy_timeout = 60000")
        rows = conn.execute(
            "SELECT id, title, pub_date FROM articles "
            "WHERE journal = ? AND pub_date LIKE ?",
            (JOURNAL, BAD_PUBDATE_PREFIX + "%"),
        ).fetchall()
        log.info("Candidate rows (pub_date starts with %s): %d",
                 BAD_PUBDATE_PREFIX, len(rows))

        updates: list[tuple[str, int]] = []
        unmatched: list[tuple[int, str]] = []
        for r in rows:
            new_date = None
            for key in _title_keys(r["title"]):
                if key in title_map:
                    new_date = title_map[key]
                    break
            if new_date and new_date != r["pub_date"]:
                updates.append((new_date, r["id"]))
            elif not new_date:
                unmatched.append((r["id"], r["title"]))

        log.info("Will update: %d   Unmatched: %d", len(updates), len(unmatched))

        if unmatched:
            log.info("First 10 unmatched titles:")
            for aid, t in unmatched[:10]:
                log.info("  #%d  %s", aid, t)

        if args.dry_run:
            log.info("Dry run — no writes performed.")
            if updates[:5]:
                log.info("Sample updates (first 5):")
                for d, i in updates[:5]:
                    log.info("  #%d -> %s", i, d)
            return

        conn.executemany(
            "UPDATE articles SET pub_date = ? WHERE id = ?",
            updates,
        )
        conn.commit()
        log.info("Done — %d rows updated.", len(updates))


if __name__ == "__main__":
    main()
