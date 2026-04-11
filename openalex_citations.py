"""
openalex_citations.py — Fetch global citation counts from OpenAlex
for all articles in the Pinakes database.

This is a DATA ENRICHMENT script, not a visualization tool.  It extends
the existing enrich_openalex.py pipeline (which fetches abstracts, OA
status, and affiliations) by adding global citation counts.

Strategy:
  1. For articles that already have an openalex_id: use the batch
     filter API (GET /works?filter=openalex_id:W1|W2|...&per_page=50)
     to fetch up to 50 works per request.  ~500 requests for 25K articles.
  2. For articles with a DOI but no openalex_id: individual lookups
     via GET /works/doi:10.xxxx.

Stores results in a NEW column: articles.openalex_cited_by_count
(distinct from crossref_cited_by_count, which comes from CrossRef).
Also logs each fetch in openalex_fetch_log for incrementality.

This data is a prerequisite for:
  - Tool 2.2: Internal vs. External Citation Comparison
  - Tool 2.8: Missed Classics identification

Rate limiting: uses OpenAlex polite pool (email in User-Agent header)
for 10 requests/second.  The batch approach keeps total requests low.

Usage:
  python openalex_citations.py              # full run
  python openalex_citations.py --test 20    # test with 20 articles
  python openalex_citations.py --refresh    # re-fetch all, even if done

Localhost-only.
"""

import argparse
import json
import logging
import os
import sqlite3
import time
import urllib.request
import urllib.error
from datetime import datetime

log = logging.getLogger(__name__)

DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(os.path.dirname(__file__), "articles.db")
)

CONTACT_EMAIL = "rhetcompindex@gmail.com"
OPENALEX_BASE = "https://api.openalex.org/works"
REQUEST_DELAY = 0.12     # seconds between requests (polite pool: 10/sec)
RETRY_DELAY = 5           # seconds before retry on 429/5xx
BATCH_SIZE = 50            # max IDs per batch filter request


# ── Database helpers ─────────────────────────────────────────────────────────

def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _ensure_column(conn):
    """Add openalex_cited_by_count column if it doesn't exist."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(articles)")]
    if "openalex_cited_by_count" not in cols:
        conn.execute(
            "ALTER TABLE articles ADD COLUMN openalex_cited_by_count INTEGER"
        )
        conn.commit()
        log.info("Added column: articles.openalex_cited_by_count")


def _ensure_log_table(conn):
    """Create openalex_fetch_log if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS openalex_fetch_log (
            article_id       INTEGER PRIMARY KEY,
            fetched_at       TEXT,
            openalex_work_id TEXT,
            status           TEXT
        )
    """)
    conn.commit()


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _get_json(url, timeout=20):
    """Fetch JSON with polite headers. Returns (data, status) or (None, status)."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"RhetCompIndex/1.0 (mailto:{CONTACT_EMAIL})",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except urllib.error.HTTPError as e:
        return None, e.code
    except Exception as e:
        log.warning("Request failed for %s: %s", url, e)
        return None, None


def _fetch_with_retry(url):
    """Fetch with one retry on 429 or 5xx."""
    data, status = _get_json(url)
    if status in (429,) or (status is not None and status >= 500):
        log.warning("HTTP %s — retrying in %ds…", status, RETRY_DELAY)
        time.sleep(RETRY_DELAY)
        data, status = _get_json(url)
    return data, status


# ── Batch fetch by openalex_id ───────────────────────────────────────────────

def _batch_fetch_by_openalex_id(conn, articles, stats):
    """
    Fetch cited_by_count for articles that already have an openalex_id,
    using the batch filter API.  Up to BATCH_SIZE IDs per request.
    """
    total = len(articles)
    log.info("Batch-fetching %d articles by openalex_id…", total)

    for batch_start in range(0, total, BATCH_SIZE):
        batch = articles[batch_start:batch_start + BATCH_SIZE]

        # Build filter: openalex_id:W1|W2|W3...
        oa_ids = [a["openalex_id"] for a in batch]
        # OpenAlex IDs are like "https://openalex.org/W1234567890"
        # The filter accepts the short form W1234567890
        short_ids = []
        for oid in oa_ids:
            if oid and "/" in oid:
                short_ids.append(oid.split("/")[-1])
            elif oid:
                short_ids.append(oid)

        if not short_ids:
            continue

        filter_val = "|".join(short_ids)
        url = (
            f"{OPENALEX_BASE}?"
            f"filter=openalex_id:{filter_val}"
            f"&per_page={BATCH_SIZE}"
            f"&select=id,cited_by_count"
            f"&mailto={CONTACT_EMAIL}"
        )

        data, status = _fetch_with_retry(url)

        if data is None:
            log.warning("Batch request failed (status=%s), batch %d–%d",
                        status, batch_start, batch_start + len(batch))
            # Mark all in batch as errored
            for a in batch:
                conn.execute("""
                    INSERT OR REPLACE INTO openalex_fetch_log
                    (article_id, fetched_at, openalex_work_id, status)
                    VALUES (?, ?, ?, 'error')
                """, (a["id"], datetime.now(tz=None).isoformat(), a["openalex_id"]))
            stats["errors"] += len(batch)
            time.sleep(RETRY_DELAY)
            continue

        # Build lookup from response: openalex_id -> cited_by_count
        results_map = {}
        for work in data.get("results", []):
            work_id = work.get("id", "")
            cited = work.get("cited_by_count", 0)
            results_map[work_id] = cited

        # Update articles
        for a in batch:
            oa_id = a["openalex_id"]
            cited = results_map.get(oa_id)

            if cited is not None:
                conn.execute("""
                    UPDATE articles
                    SET openalex_cited_by_count = ?
                    WHERE id = ?
                """, (cited, a["id"]))
                conn.execute("""
                    INSERT OR REPLACE INTO openalex_fetch_log
                    (article_id, fetched_at, openalex_work_id, status)
                    VALUES (?, ?, ?, 'success')
                """, (a["id"], datetime.now(tz=None).isoformat(), oa_id))
                stats["success"] += 1
            else:
                # OpenAlex ID not found in response (may have been merged)
                conn.execute("""
                    INSERT OR REPLACE INTO openalex_fetch_log
                    (article_id, fetched_at, openalex_work_id, status)
                    VALUES (?, ?, ?, 'not_found')
                """, (a["id"], datetime.now(tz=None).isoformat(), oa_id))
                stats["not_found"] += 1

        conn.commit()

        processed = min(batch_start + len(batch), total)
        if processed % 500 == 0 or processed == total:
            log.info("  Batch progress: %d/%d (success=%d, not_found=%d)",
                     processed, total, stats["success"], stats["not_found"])

        time.sleep(REQUEST_DELAY)


# ── Individual fetch by DOI ──────────────────────────────────────────────────

def _individual_fetch_by_doi(conn, articles, stats):
    """
    Fetch cited_by_count for articles without openalex_id but with a DOI,
    using individual requests.
    """
    total = len(articles)
    log.info("Individual-fetching %d articles by DOI…", total)

    for i, a in enumerate(articles):
        doi = a["doi"].strip()
        if doi.startswith("http"):
            doi = doi.split("doi.org/")[-1]

        url = (
            f"{OPENALEX_BASE}/doi:{doi}"
            f"?mailto={CONTACT_EMAIL}"
            f"&select=id,cited_by_count"
        )

        data, status = _fetch_with_retry(url)

        if data is not None:
            oa_id = data.get("id", "")
            cited = data.get("cited_by_count", 0)

            conn.execute("""
                UPDATE articles
                SET openalex_cited_by_count = ?,
                    openalex_id = COALESCE(openalex_id, ?)
                WHERE id = ?
            """, (cited, oa_id, a["id"]))
            conn.execute("""
                INSERT OR REPLACE INTO openalex_fetch_log
                (article_id, fetched_at, openalex_work_id, status)
                VALUES (?, ?, ?, 'success')
            """, (a["id"], datetime.now(tz=None).isoformat(), oa_id))
            stats["success"] += 1
        else:
            conn.execute("""
                INSERT OR REPLACE INTO openalex_fetch_log
                (article_id, fetched_at, openalex_work_id, status)
                VALUES (?, ?, NULL, ?)
            """, (a["id"], datetime.now(tz=None).isoformat(),
                  "not_found" if status == 404 else "error"))
            if status == 404:
                stats["not_found"] += 1
            else:
                stats["errors"] += 1

        if (i + 1) % BATCH_SIZE == 0:
            conn.commit()
            log.info("  DOI progress: %d/%d", i + 1, total)

        time.sleep(REQUEST_DELAY)

    conn.commit()


# ── Main pipeline ────────────────────────────────────────────────────────────

def run_enrichment(max_articles=None, refresh=False):
    """
    Enrich articles with OpenAlex global citation counts.

    Args:
        max_articles: limit to N articles (for testing)
        refresh: if True, re-fetch even articles already done

    Returns dict with success/not_found/errors counts.
    """
    conn = _get_conn()
    _ensure_column(conn)
    _ensure_log_table(conn)

    stats = {"success": 0, "not_found": 0, "errors": 0}

    # Phase 1: articles with openalex_id (batch fetch)
    if refresh:
        where_oa = """
            WHERE openalex_id IS NOT NULL AND openalex_id != ''
        """
    else:
        where_oa = """
            WHERE openalex_id IS NOT NULL AND openalex_id != ''
              AND openalex_cited_by_count IS NULL
        """

    query_oa = f"SELECT id, openalex_id FROM articles {where_oa}"
    if max_articles:
        query_oa += f" LIMIT {max_articles}"

    articles_oa = [dict(r) for r in conn.execute(query_oa).fetchall()]
    log.info("Phase 1: %d articles with openalex_id need citation counts",
             len(articles_oa))

    if articles_oa:
        _batch_fetch_by_openalex_id(conn, articles_oa, stats)

    # Phase 2: articles with DOI but no openalex_id (individual fetch)
    remaining = max_articles - len(articles_oa) if max_articles else None

    if remaining is not None and remaining <= 0:
        pass  # already hit the limit
    else:
        if refresh:
            where_doi = """
                WHERE doi IS NOT NULL AND doi != ''
                  AND (openalex_id IS NULL OR openalex_id = '')
            """
        else:
            where_doi = """
                WHERE doi IS NOT NULL AND doi != ''
                  AND (openalex_id IS NULL OR openalex_id = '')
                  AND openalex_cited_by_count IS NULL
            """

        query_doi = f"SELECT id, doi FROM articles {where_doi}"
        if remaining:
            query_doi += f" LIMIT {remaining}"

        articles_doi = [dict(r) for r in conn.execute(query_doi).fetchall()]
        log.info("Phase 2: %d articles with DOI (no openalex_id) need lookups",
                 len(articles_doi))

        if articles_doi:
            _individual_fetch_by_doi(conn, articles_doi, stats)

    conn.close()
    return stats


# ── Coverage report ──────────────────────────────────────────────────────────

def coverage_report():
    """Print a coverage report after enrichment."""
    conn = _get_conn()
    _ensure_column(conn)

    r = conn.execute("""
        SELECT
            COUNT(*) AS total_articles,
            SUM(CASE WHEN doi IS NOT NULL AND doi != '' THEN 1 ELSE 0 END) AS has_doi,
            SUM(CASE WHEN openalex_id IS NOT NULL AND openalex_id != '' THEN 1 ELSE 0 END) AS has_oa_id,
            SUM(CASE WHEN openalex_cited_by_count IS NOT NULL THEN 1 ELSE 0 END) AS has_oa_count,
            SUM(CASE WHEN openalex_cited_by_count > 0 THEN 1 ELSE 0 END) AS has_oa_count_positive,
            AVG(CASE WHEN openalex_cited_by_count > 0 THEN openalex_cited_by_count END) AS avg_oa_count,
            MAX(openalex_cited_by_count) AS max_oa_count,
            SUM(CASE WHEN crossref_cited_by_count IS NOT NULL AND crossref_cited_by_count > 0
                THEN 1 ELSE 0 END) AS has_crossref_count
        FROM articles
    """).fetchone()

    print("\n=== OpenAlex Citation Count Coverage ===")
    for k in r.keys():
        print(f"  {k}: {r[k]}")

    # Fetch log
    print("\n=== Fetch Log ===")
    for row in conn.execute(
        "SELECT status, COUNT(*) AS cnt FROM openalex_fetch_log GROUP BY status"
    ):
        print(f"  {row['status']}: {row['cnt']}")

    # Top 20
    print("\n=== Top 20 by OpenAlex Citation Count ===")
    for row in conn.execute("""
        SELECT title, journal, SUBSTR(pub_date,1,4) AS yr,
               internal_cited_by_count, openalex_cited_by_count,
               crossref_cited_by_count
        FROM articles
        WHERE openalex_cited_by_count IS NOT NULL AND openalex_cited_by_count > 0
        ORDER BY openalex_cited_by_count DESC
        LIMIT 20
    """):
        print(f"  {row['openalex_cited_by_count']:6d} OA | "
              f"{row['crossref_cited_by_count'] or 0:5d} CR | "
              f"{row['internal_cited_by_count'] or 0:3d} int | "
              f"{row['yr']} | {row['title'][:50]}")

    conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Fetch global citation counts from OpenAlex."
    )
    parser.add_argument("--test", type=int, default=None,
                        help="Limit to N articles for testing")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-fetch even if already done")
    parser.add_argument("--report", action="store_true",
                        help="Just print coverage report, don't fetch")
    args = parser.parse_args()

    if args.report:
        coverage_report()
    else:
        print("Fetching OpenAlex citation counts…")
        t0 = time.time()
        stats = run_enrichment(
            max_articles=args.test,
            refresh=args.refresh,
        )
        elapsed = time.time() - t0

        print(f"\nDone in {elapsed:.1f}s")
        print(f"  Success:   {stats['success']}")
        print(f"  Not found: {stats['not_found']}")
        print(f"  Errors:    {stats['errors']}")

        coverage_report()
