"""db.coverage — Coverage statistics and gold-OA backfill."""

import json
import os
import sqlite3
import logging

from .core import get_conn

log = logging.getLogger(__name__)

# Module-level cache used by get_detailed_coverage. Re-exported from
# db/__init__.py so existing tests can clear it via `db._DETAILED_COVERAGE_CACHE`.
_DETAILED_COVERAGE_CACHE: dict = {}
_DETAILED_COVERAGE_TTL = 3600  # 1 hour


def backfill_oa_status():
    """
    Tag articles from known gold-OA journals with oa_status='gold'.

    Uses the GOLD_OA_JOURNALS set from journals.py.  For articles with
    a DOI, also sets oa_url to the doi.org URL if not already set.
    For articles with a direct URL (RSS/scraped), sets oa_url to that URL.

    Returns dict with counts: {tagged, already_tagged, total_gold_articles}.
    """
    from journals import GOLD_OA_JOURNALS

    tagged = 0
    already = 0
    total_gold = 0

    with get_conn() as conn:
        for jname in sorted(GOLD_OA_JOURNALS):
            rows = conn.execute(
                "SELECT id, doi, url, oa_status, oa_url FROM articles WHERE journal = ?",
                (jname,),
            ).fetchall()

            for r in rows:
                total_gold += 1
                if r["oa_status"] == "gold":
                    already += 1
                    continue

                # Determine best OA URL
                oa_url = r["oa_url"]
                if not oa_url:
                    if r["doi"]:
                        oa_url = f"https://doi.org/{r['doi']}"
                    elif r["url"]:
                        oa_url = r["url"]

                conn.execute(
                    "UPDATE articles SET oa_status = 'gold', oa_url = ? WHERE id = ?",
                    (oa_url, r["id"]),
                )
                tagged += 1

        conn.commit()

    return {"tagged": tagged, "already_tagged": already,
            "total_gold_articles": total_gold}


def get_coverage_stats():
    """Return per-journal citation coverage stats (how many articles have had
    references fetched vs. total articles in the DB)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                journal,
                COUNT(*) AS article_count,
                SUM(CASE WHEN references_fetched_at IS NOT NULL THEN 1 ELSE 0 END) AS fetched_count
            FROM articles
            GROUP BY journal
            ORDER BY journal
        """).fetchall()
    result = []
    for r in rows:
        fetched = r["fetched_count"] or 0
        total   = r["article_count"] or 1
        result.append({
            "journal":      r["journal"],
            "article_count": r["article_count"],
            "fetched_count": fetched,
            "coverage_pct":  round(100.0 * fetched / total, 1),
        })
    return sorted(result, key=lambda x: (-x["coverage_pct"], x["journal"]))


def get_detailed_coverage(year_min=None):
    """Return the per-journal coverage snapshot computed against the live
    DB. When year_min is set, the per-journal table is filtered to
    articles published in [year_min, ∞). Each server reports against its
    own articles.db. Result is cached in-process per year_min for one
    hour. Falls back to the committed snapshot file (unfiltered) when the
    live query fails, so the template degrades gracefully."""
    import time
    now = time.time()
    cached = _DETAILED_COVERAGE_CACHE.get(year_min)
    if cached and now - cached["ts"] < _DETAILED_COVERAGE_TTL:
        return cached["data"]

    try:
        from coverage_report import build_snapshot
        with get_conn() as conn:
            snap = build_snapshot(conn, year_min=year_min)
        _DETAILED_COVERAGE_CACHE[year_min] = {"data": snap, "ts": now}
        return snap
    except Exception as exc:
        log.warning("Live coverage snapshot failed, falling back to file: %s", exc)

    # __file__ moved from repo root to db/coverage.py during the prompt-E1
    # split; the snapshot lives at <repo>/data_exports/..., so go up one.
    path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "data_exports", "coverage", "coverage_snapshot.json",
    )
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        log.warning("Could not read coverage snapshot at %s: %s", path, exc)
        return None
