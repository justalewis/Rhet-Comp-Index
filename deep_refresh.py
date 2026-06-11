"""
deep_refresh.py — Full corpus revalidation against CrossRef, RSS/OAI, and
journal websites.

The daily incremental fetch (fetcher.fetch_all(incremental=True)) only asks
CrossRef for articles published after each journal's last-fetch date. That
misses two things this module exists to catch:

  1. Back-deposits. Publishers register DOIs for OLD content all the time —
     Reflections' 2000-2026 archive landed in one batch in May 2026, and
     JSTOR-era NCTE volumes may follow. An incremental fetch never sees
     these because their pub dates predate the cutoff.
  2. Metadata upgrades. A row inserted years ago without an abstract stays
     abstract-less forever: upsert_article is INSERT OR IGNORE. CrossRef
     deposits get enriched over time (abstracts, ORCIDs, subjects).

The deep refresh walks every CrossRef journal's FULL catalog, inserts
whatever is missing, and fills missing fields (abstract, authors, keywords,
pub_date, tags) on existing rows. It then re-runs the RSS/OAI harvesters
and the web scrapers, which are full-archive by design, and finishes with
the gold-OA backfill. An audit step before the walk reports each journal's
CrossRef count against the index so coverage drift is visible.

Usage:
    python deep_refresh.py                 # audit + full deep refresh
    python deep_refresh.py --audit-only    # report coverage drift, change nothing
    python deep_refresh.py --gaps-only     # only deep-fetch journals where CrossRef has more than the index
    python deep_refresh.py --issn 0010-096X  # one journal

Also reachable as POST /fetch with body {"deep": true} (admin token
required) — the "Deep refresh" button in the sidebar calls this.
"""

import argparse
import logging
import sys
import time

import requests

from db import get_conn, init_db, upsert_article, update_fetch_log
from fetcher import (
    CROSSREF_BASE, HEADERS, ROWS_PER_PAGE,
    _full_title, _parse_abstract, _parse_authors, _parse_date,
)
from journals import CROSSREF_JOURNALS, GOLD_OA_JOURNALS
from monitoring import capture_fetcher_error
from tagger import auto_tag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SOURCE_NAME = "deep_refresh"


# ── Audit ─────────────────────────────────────────────────────────────────────

def _crossref_count(issn):
    """Total journal-article works CrossRef holds for an ISSN (rows=0 query)."""
    params = {"filter": f"issn:{issn},type:journal-article", "rows": 0}
    resp = requests.get(CROSSREF_BASE, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()["message"]["total-results"]


def audit_crossref():
    """
    Compare CrossRef's per-ISSN work count against the index, journal by
    journal. Returns a list of dicts sorted by gap (largest shortfall first).

    gap > 0  → CrossRef has works the index lacks (back-deposit or fetch miss)
    gap < 0  → the index holds more rows than CrossRef returns (multi-source
               journals, ISSN variants, or items CrossRef has since retyped)
    """
    init_db()
    report = []
    with get_conn() as conn:
        for j in CROSSREF_JOURNALS:
            issn, name = j["issn"], j["name"]
            indexed = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE journal = ?", (name,)
            ).fetchone()[0]
            try:
                remote = _crossref_count(issn)
            except requests.RequestException as e:
                log.warning("audit: count query failed for %s (%s): %s", name, issn, e)
                report.append({"journal": name, "issn": issn, "crossref": None,
                               "indexed": indexed, "gap": None})
                continue
            report.append({"journal": name, "issn": issn, "crossref": remote,
                           "indexed": indexed, "gap": remote - indexed})
            time.sleep(0.3)
    report.sort(key=lambda r: (r["gap"] is None, -(r["gap"] or 0)))
    return report


def print_audit(report):
    log.info("%-58s %9s %9s %6s", "journal", "crossref", "indexed", "gap")
    for r in report:
        log.info("%-58s %9s %9s %6s",
                 r["journal"][:58],
                 r["crossref"] if r["crossref"] is not None else "?",
                 r["indexed"],
                 r["gap"] if r["gap"] is not None else "?")


# ── Deep fetch (insert + fill) ────────────────────────────────────────────────

# Fills only fields that are currently empty — the deep refresh never
# overwrites data already in the index. Tags piggyback on the same rule so
# articles that gain an abstract also gain topic tags.
_FILL_SQL = """
    UPDATE articles SET
        abstract = COALESCE(abstract, :abstract),
        authors  = CASE WHEN authors IS NULL OR authors = ''
                        THEN :authors ELSE authors END,
        keywords = COALESCE(keywords, :keywords),
        pub_date = COALESCE(pub_date, :pub_date),
        tags     = CASE WHEN (tags IS NULL OR tags = '') AND :tags IS NOT NULL
                        THEN :tags ELSE tags END
    WHERE url = :url AND (
        (abstract IS NULL AND :abstract IS NOT NULL) OR
        ((authors IS NULL OR authors = '') AND :authors IS NOT NULL) OR
        (keywords IS NULL AND :keywords IS NOT NULL) OR
        (pub_date IS NULL AND :pub_date IS NOT NULL) OR
        ((tags IS NULL OR tags = '') AND :tags IS NOT NULL)
    )
"""


def deep_fetch_journal(issn, name):
    """
    Walk the journal's complete CrossRef catalog. Insert articles the index
    lacks; fill missing metadata on rows it already has. Returns
    (inserted, updated).
    """
    log.info("Deep fetch: %s (%s)", name, issn)
    params = {
        "filter": f"issn:{issn},type:journal-article",
        "select": "DOI,title,subtitle,author,abstract,published-print,"
                  "published-online,issued,container-title,subject",
        "rows": ROWS_PER_PAGE,
        "sort": "published",
        "order": "desc",
        "cursor": "*",
    }
    inserted = updated = 0
    page = 0

    while True:
        try:
            resp = requests.get(CROSSREF_BASE, params=params,
                                headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error("deep fetch failed for %s: %s", name, e)
            capture_fetcher_error(SOURCE_NAME, name, e)
            break

        data = resp.json().get("message", {})
        items = data.get("items", [])
        if not items:
            break

        fills = []
        for item in items:
            doi = item.get("DOI", "").strip()
            if not doi:
                continue
            url = f"https://doi.org/{doi}"
            title = _full_title(item) or "(no title)"
            pub_date = _parse_date(item)
            authors = _parse_authors(item)
            abstract = _parse_abstract(item)
            subjects = item.get("subject", [])
            keywords = "; ".join(subjects) if subjects else None
            tags = auto_tag(title, abstract)
            oa_status = "gold" if name in GOLD_OA_JOURNALS else None

            added = upsert_article(
                url, doi, title, authors, abstract, pub_date, name, "crossref",
                keywords=keywords, tags=tags,
                oa_status=oa_status, oa_url=url if oa_status == "gold" else None,
            )
            if added:
                inserted += added
            else:
                fills.append({"url": url, "abstract": abstract,
                              "authors": authors, "keywords": keywords,
                              "pub_date": pub_date, "tags": tags})

        if fills:
            with get_conn() as conn:
                cur = conn.executemany(_FILL_SQL, fills)
                updated += cur.rowcount
                conn.commit()

        page += 1
        if page % 10 == 0:
            log.info("  %s — page %d: %d new, %d filled", name, page, inserted, updated)

        next_cursor = data.get("next-cursor")
        if not next_cursor or len(items) < ROWS_PER_PAGE:
            break
        params["cursor"] = next_cursor
        time.sleep(0.5)

    update_fetch_log(name)
    log.info("  %s — %d new, %d rows filled", name, inserted, updated)
    return inserted, updated


# ── Orchestrator ──────────────────────────────────────────────────────────────

def deep_refresh(gaps_only=False):
    """
    Full corpus revalidation. Returns a summary dict:
        {audit, crossref_new, crossref_filled, rss_new, scrape_new, oa_tagged}
    """
    init_db()
    t0 = time.time()

    log.info("=== Deep refresh: auditing CrossRef coverage ===")
    audit = audit_crossref()
    print_audit(audit)

    targets = [r for r in audit
               if not gaps_only or (r["gap"] is not None and r["gap"] > 0)]
    log.info("=== Deep-fetching %d journal catalog(s)%s ===",
             len(targets), " (gaps only)" if gaps_only else "")

    crossref_new = crossref_filled = 0
    for r in targets:
        n, u = deep_fetch_journal(r["issn"], r["journal"])
        crossref_new += n
        crossref_filled += u
        time.sleep(1)

    log.info("=== Re-harvesting RSS / OAI journals ===")
    from rss_fetcher import fetch_all as rss_fetch
    rss_new = rss_fetch()

    log.info("=== Re-running web scrapers (journal sites) ===")
    from scraper import fetch_all as scrape_fetch
    scrape_new = scrape_fetch()

    from db import backfill_oa_status
    oa = backfill_oa_status()

    summary = {
        "audit": audit,
        "crossref_new": crossref_new,
        "crossref_filled": crossref_filled,
        "rss_new": rss_new,
        "scrape_new": scrape_new,
        "oa_tagged": oa.get("tagged", 0),
        "minutes": round((time.time() - t0) / 60, 1),
    }
    log.info("Deep refresh complete in %.1f min — %d new via CrossRef, "
             "%d rows filled, %d via RSS/OAI, %d via scrapers",
             summary["minutes"], crossref_new, crossref_filled,
             rss_new, scrape_new)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--audit-only", action="store_true",
                        help="report CrossRef-vs-index coverage and exit")
    parser.add_argument("--gaps-only", action="store_true",
                        help="deep-fetch only journals where CrossRef holds more than the index")
    parser.add_argument("--issn", help="deep-fetch a single journal by ISSN")
    args = parser.parse_args()

    if args.audit_only:
        print_audit(audit_crossref())
        sys.exit(0)
    if args.issn:
        match = [j for j in CROSSREF_JOURNALS if j["issn"] == args.issn]
        if not match:
            print(f"Unknown ISSN: {args.issn}")
            sys.exit(1)
        init_db()
        deep_fetch_journal(args.issn, match[0]["name"])
        sys.exit(0)
    deep_refresh(gaps_only=args.gaps_only)
