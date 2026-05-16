"""
backfill_crossref_titles.py — Re-sync existing CrossRef-source article titles
with the full canonical form from CrossRef (title + subtitle), entities
decoded.

The original fetcher persisted only title[0] and didn't decode HTML entities,
so the index accumulated entries like "Chicanx Filmmaking" (missing subtitle
"Producing the Next Generation of Resilient Cinema") and "Texas A&amp;M
University" (un-decoded ampersand). The fetcher has since been fixed; this
script repairs the rows already in the DB.

Strategy: for each CrossRef-source journal, walk every work via the same
filter the fetcher uses (issn:XXXX,type:journal-article) with subtitle in
the select. Build a DOI -> canonical title map. Then UPDATE any DB row
whose stored title differs.

Usage:
    python backfill_crossref_titles.py --dry-run
    python backfill_crossref_titles.py
    python backfill_crossref_titles.py --issn 1541-2075   # one journal
"""

import argparse
import logging
import time

import requests

from db import get_conn, init_db
from fetcher import HEADERS, CROSSREF_BASE, ROWS_PER_PAGE, _full_title
from journals import CROSSREF_JOURNALS, ISSN_TO_NAME

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REQUEST_DELAY = 0.5  # polite pool


def fetch_titles_for_issn(issn: str) -> dict[str, str]:
    """Walk every CrossRef work for `issn`, return DOI -> canonical title."""
    out: dict[str, str] = {}
    params = {
        "filter": f"issn:{issn},type:journal-article",
        "select": "DOI,title,subtitle",
        "rows":   ROWS_PER_PAGE,
        "cursor": "*",
    }
    page = 0
    while True:
        try:
            resp = requests.get(CROSSREF_BASE, params=params,
                                headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error("  request failed: %s", e)
            break

        data = resp.json().get("message", {})
        items = data.get("items", [])
        if not items:
            break

        for item in items:
            doi = (item.get("DOI") or "").strip().lower()
            if not doi:
                continue
            title = _full_title(item)
            if title:
                out[doi] = title

        page += 1
        if page % 5 == 0:
            log.info("    %s: page %d, %d titles collected", issn, page, len(out))

        next_cursor = data.get("next-cursor")
        if not next_cursor or len(items) < ROWS_PER_PAGE:
            break
        params["cursor"] = next_cursor
        time.sleep(REQUEST_DELAY)

    return out


def reconcile_journal(name: str, issn: str, dry_run: bool) -> int:
    """Compare CrossRef canonical titles to DB rows; UPDATE on mismatch."""
    log.info("Journal: %s  (ISSN %s)", name, issn)
    cr_titles = fetch_titles_for_issn(issn)
    log.info("  CrossRef titles collected: %d", len(cr_titles))
    if not cr_titles:
        return 0

    with get_conn() as conn:
        conn.execute("PRAGMA busy_timeout = 60000")
        # Pull every DB row for this journal that came from CrossRef.
        rows = conn.execute(
            "SELECT id, doi, title FROM articles "
            "WHERE journal = ? AND source = 'crossref' AND doi IS NOT NULL",
            (name,),
        ).fetchall()

        updates: list[tuple[str, int]] = []
        not_in_crossref = 0
        for r in rows:
            doi = (r["doi"] or "").strip().lower()
            new_title = cr_titles.get(doi)
            if new_title is None:
                not_in_crossref += 1
                continue
            if new_title != r["title"]:
                updates.append((new_title, r["id"]))

        log.info("  DB rows: %d  |  updates: %d  |  no CrossRef match: %d",
                 len(rows), len(updates), not_in_crossref)

        for new_title, rid in updates[:3]:
            old = conn.execute(
                "SELECT title FROM articles WHERE id = ?", (rid,)
            ).fetchone()["title"]
            log.info("    #%d  %r  ->  %r", rid, old, new_title)

        if not dry_run and updates:
            conn.executemany(
                "UPDATE articles SET title = ? WHERE id = ?", updates,
            )
            conn.commit()

    return len(updates)


def sweep_orphans(dry_run: bool) -> int:
    """Per-DOI sweep for crossref rows whose journal isn't in CROSSREF_JOURNALS.

    These rows came from older fetches against journals that have since
    been removed from the journals.py allowlist. They still need title
    cleanup, but there's no ISSN to iterate, so we fetch each work
    individually via /works/{doi}.
    """
    active_journals = {j["name"] for j in CROSSREF_JOURNALS}
    with get_conn() as conn:
        conn.execute("PRAGMA busy_timeout = 60000")
        all_journals = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT journal FROM articles WHERE source = 'crossref'"
            )
        }
        orphans = sorted(all_journals - active_journals)
        if not orphans:
            return 0
        log.info("Orphan crossref journals (not in CROSSREF_JOURNALS): %d",
                 len(orphans))
        placeholders = ",".join("?" for _ in orphans)
        rows = conn.execute(
            f"SELECT id, doi, title FROM articles "
            f"WHERE source = 'crossref' AND doi IS NOT NULL "
            f"AND journal IN ({placeholders})",
            orphans,
        ).fetchall()

        updates: list[tuple[str, int]] = []
        for r in rows:
            try:
                resp = requests.get(
                    f"{CROSSREF_BASE}/{r['doi']}",
                    headers=HEADERS, timeout=30,
                )
                resp.raise_for_status()
                item = resp.json().get("message", {})
            except requests.RequestException as e:
                log.warning("  DOI %s: %s", r["doi"], e)
                time.sleep(REQUEST_DELAY)
                continue

            new_title = _full_title(item)
            if new_title and new_title != r["title"]:
                updates.append((new_title, r["id"]))
            time.sleep(REQUEST_DELAY)

        log.info("Orphan sweep: %d rows scanned, %d need update",
                 len(rows), len(updates))
        for new_title, rid in updates[:3]:
            old = conn.execute(
                "SELECT title FROM articles WHERE id = ?", (rid,)
            ).fetchone()["title"]
            log.info("    #%d  %r  ->  %r", rid, old, new_title)

        if not dry_run and updates:
            conn.executemany(
                "UPDATE articles SET title = ? WHERE id = ?", updates,
            )
            conn.commit()
        return len(updates)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing.")
    parser.add_argument("--issn", help="Backfill a single journal by ISSN.")
    parser.add_argument("--orphans-only", action="store_true",
                        help="Skip the per-ISSN sweep; only handle journals "
                             "no longer in CROSSREF_JOURNALS.")
    args = parser.parse_args()

    init_db()

    if args.issn:
        if args.issn not in ISSN_TO_NAME:
            log.error("Unknown ISSN: %s", args.issn)
            return
        journals = [{"name": ISSN_TO_NAME[args.issn], "issn": args.issn}]
    else:
        journals = list(CROSSREF_JOURNALS)

    grand_total = 0
    if not args.orphans_only:
        for j in journals:
            grand_total += reconcile_journal(j["name"], j["issn"], args.dry_run)
            time.sleep(REQUEST_DELAY)

    # After the ISSN sweep, mop up rows in journals no longer on the list.
    if not args.issn:
        grand_total += sweep_orphans(args.dry_run)

    verb = "would update" if args.dry_run else "updated"
    log.info("Done — %s %d rows.", verb, grand_total)


if __name__ == "__main__":
    main()
