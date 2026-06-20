"""db.articles — Article CRUD, search, tags, year range, new-article queries."""

import json
import os
import sqlite3
import logging
from collections import Counter, defaultdict
from itertools import combinations

from .core import get_conn

log = logging.getLogger(__name__)
from .core import _build_where, _sanitize_fts


def upsert_article(url, doi, title, authors, abstract, pub_date, journal, source,
                   keywords=None, tags=None, oa_status=None, oa_url=None):
    """
    Insert article if its URL is not already present.
    Returns 1 if a new row was inserted, 0 if it was a duplicate (ignored).

    authors   — semicolon-separated string or None
    keywords  — semicolon-separated CrossRef subject terms or None
    tags      — pipe-delimited auto-tag string like "|transfer|genre theory|" or None
    oa_status — 'gold', 'green', 'hybrid', 'bronze', 'closed', or None
    oa_url    — direct URL to open-access version, or None
    """
    with get_conn() as conn:
        # Author-redaction choke-point: a redacted author's newly-published
        # work must come in already suppressed, or the next fetch resurrects
        # the name. apply_suppression is exact-match and exception-safe.
        from redaction import apply_suppression
        authors = apply_suppression(authors, conn=conn)
        conn.execute("""
            INSERT OR IGNORE INTO articles
                (url, doi, title, authors, abstract, pub_date,
                 journal, source, keywords, tags, oa_status, oa_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (url, doi, title, authors, abstract, pub_date,
              journal, source, keywords, tags, oa_status, oa_url))
        conn.commit()
        return conn.execute("SELECT changes()").fetchone()[0]


def update_oa_url(article_id, oa_url):
    """Store the open-access URL (or empty string if none found) for an article."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE articles SET oa_url = ? WHERE id = ?",
            (oa_url, article_id)
        )
        conn.commit()


def update_semantic_data(article_id, ss_id, citation_count):
    """Store Semantic Scholar paper ID and citation count."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE articles SET ss_id = ?, citation_count = ? WHERE id = ?",
            (ss_id, citation_count, article_id)
        )
        conn.commit()


def get_articles(journal=None, source=None, q=None,
                 year_from=None, year_to=None, tag=None,
                 limit=50, offset=0):
    clause, params = _build_where(
        journal=journal, source=source, q=q,
        year_from=year_from, year_to=year_to, tag=tag,
    )
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT a.* FROM articles a {clause} "
            f"ORDER BY a.pub_date DESC, a.fetched_at DESC "
            f"LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
        return [dict(r) for r in rows]


def get_total_count(journal=None, source=None, q=None,
                    year_from=None, year_to=None, tag=None):
    clause, params = _build_where(
        journal=journal, source=source, q=q,
        year_from=year_from, year_to=year_to, tag=tag,
    )
    with get_conn() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM articles a {clause}", params
        ).fetchone()[0]


def get_article_counts():
    """Return list of {journal, source, count} dicts for sidebar display."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT journal, source, COUNT(*) as count
            FROM articles
            GROUP BY journal
            ORDER BY journal
        """).fetchall()
        return [dict(r) for r in rows]


def get_all_tags(journal=None, source=None):
    """
    Return list of (tag_name, count) tuples, sorted by count descending
    then alphabetically. Optionally scoped to a journal or source type.
    """
    where = ["tags IS NOT NULL", "tags != ''"]
    params = []
    if journal:
        where.append("journal = ?")
        params.append(journal)
    if source:
        where.append("source = ?")
        params.append(source)

    clause = "WHERE " + " AND ".join(where)

    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT tags FROM articles {clause}", params
        ).fetchall()

    tag_counts: dict[str, int] = {}
    for row in rows:
        for tag in row["tags"].strip("|").split("|"):
            tag = tag.strip()
            if tag:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))


def get_year_range():
    """
    Return (min_year, max_year) as integers from articles.pub_date,
    or (None, None) if the database is empty.
    """
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                MIN(CAST(SUBSTR(pub_date, 1, 4) AS INTEGER)) AS min_year,
                MAX(CAST(SUBSTR(pub_date, 1, 4) AS INTEGER)) AS max_year
            FROM articles
            WHERE pub_date IS NOT NULL AND pub_date != ''
        """).fetchone()
        if row and row["min_year"]:
            return int(row["min_year"]), int(row["max_year"])
        return None, None


def get_article_by_id(article_id):
    """Return a single article as a dict, or None if not found."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        return dict(row) if row else None


# Inverted tag index: tag -> list of article ids. The "related articles"
# query used to score every one of the ~54k rows with one `tags LIKE
# '%|tag|%'` per source tag (no index possible with a leading wildcard),
# evaluated twice per row — ~640ms locally, 2-3.5s on the prod single CPU,
# and it dominated the article-page load. Tags come from a fixed 61-term
# vocabulary, so an in-memory inverted index is tiny (61 keys) and turns the
# query into a dict lookup. Built once per process and rebuilt when the
# corpus changes (fingerprint = max id + row count, same idea as the
# datastories cache). The first article hit after a deploy/fetch pays the
# one-time scan; every hit after is sub-millisecond.
_TAG_INDEX = None          # dict[str, list[int]]
_TAG_INDEX_FP = None       # (max_id, row_count)


def _tag_index(conn):
    global _TAG_INDEX, _TAG_INDEX_FP
    fp = tuple(conn.execute(
        "SELECT COALESCE(MAX(id), 0), COUNT(*) FROM articles").fetchone())
    if _TAG_INDEX is not None and _TAG_INDEX_FP == fp:
        return _TAG_INDEX
    idx = defaultdict(list)
    for r in conn.execute(
        "SELECT id, tags FROM articles WHERE tags IS NOT NULL AND tags != ''"
    ):
        for t in r["tags"].strip("|").split("|"):
            t = t.strip()
            if t:
                idx[t].append(r["id"])
    _TAG_INDEX = idx
    _TAG_INDEX_FP = fp
    return idx


def get_related_articles(article_id, limit=5):
    """
    Find articles sharing the most tags with the given article.
    Returns up to `limit` articles sorted by shared-tag count desc, then by
    publication date desc.
    """
    with get_conn() as conn:
        src = conn.execute(
            "SELECT tags FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        if not src or not src["tags"]:
            return []
        tags = [t.strip() for t in src["tags"].strip("|").split("|") if t.strip()]
        if not tags:
            return []

        idx = _tag_index(conn)
        scores = Counter()
        for t in tags:
            for aid in idx.get(t, ()):
                if aid != article_id:
                    scores[aid] += 1
        if not scores:
            return []

        # Over-fetch the highest-scoring candidates, then break ties by
        # pub_date (fetched with the rows) — generous enough that the true
        # top `limit` by (shared_count, pub_date) is always present.
        top_ids = [aid for aid, _ in scores.most_common(max(limit * 20, 100))]
        placeholders = ",".join("?" * len(top_ids))
        rows = conn.execute(
            f"SELECT * FROM articles WHERE id IN ({placeholders})", top_ids
        ).fetchall()
        ranked = sorted(
            (dict(r) for r in rows),
            key=lambda r: (scores[r["id"]], r["pub_date"] or ""),
            reverse=True,
        )
        return ranked[:limit]


def get_timeline_data():
    """Return list of {year, journal, count} dicts for the timeline chart.

    Full history: the corpus reaches back to the 1930s and the timeline
    should show it. (A 1990 floor here used to hide ~17,000 pre-1990
    articles — 31% of the corpus — with no indication in the UI.) The
    sanity floor of 1900 only drops obviously malformed dates.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT SUBSTR(pub_date,1,4) AS year, journal, COUNT(*) AS count
            FROM articles
            WHERE pub_date IS NOT NULL AND SUBSTR(pub_date,1,4) >= '1900'
            GROUP BY year, journal
            ORDER BY year, journal
        """).fetchall()
        return [dict(r) for r in rows]


def get_tag_cooccurrence():
    """
    Compute tag co-occurrence counts across all tagged articles.
    Returns {"tags": [...], "matrix": [[...]]} where matrix[i][j] = count of
    articles having both tag[i] and tag[j].
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT tags FROM articles WHERE tags IS NOT NULL AND tags != ''"
        ).fetchall()

    # Collect all tags and co-occurrence pairs
    tag_set: dict[str, int] = {}
    pair_counts: dict[tuple, int] = {}

    for row in rows:
        article_tags = sorted(set(
            t.strip() for t in row["tags"].strip("|").split("|") if t.strip()
        ))
        for tag in article_tags:
            tag_set[tag] = tag_set.get(tag, 0) + 1
        for a, b in combinations(article_tags, 2):
            key = (a, b)
            pair_counts[key] = pair_counts.get(key, 0) + 1

    # Sort tags by frequency desc
    tags = sorted(tag_set.keys(), key=lambda t: -tag_set[t])
    tag_idx = {t: i for i, t in enumerate(tags)}
    n = len(tags)
    matrix = [[0] * n for _ in range(n)]
    for (a, b), count in pair_counts.items():
        i, j = tag_idx.get(a), tag_idx.get(b)
        if i is not None and j is not None:
            matrix[i][j] = count
            matrix[j][i] = count

    return {"tags": tags, "matrix": matrix}


def get_new_articles(days=7):
    """Return articles fetched within the last `days` days, sorted by pub_date DESC."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM articles
            WHERE fetched_at >= datetime('now', ?)
            ORDER BY pub_date DESC, fetched_at DESC
        """, (f"-{days} days",)).fetchall()
        return [dict(r) for r in rows]


def get_new_article_count(days=7):
    """Return count of articles fetched within the last `days` days."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT COUNT(*) FROM articles
            WHERE fetched_at >= datetime('now', ?)
        """, (f"-{days} days",)).fetchone()[0]


def search_articles_autocomplete(q, limit=10):
    """
    Fast article search for autocomplete.  Returns a small set of
    fields: id, title, authors, journal, pub_date, doi.
    """
    q = (q or "").strip()
    if not q:
        return []
    safe = _sanitize_fts(q)
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT a.id, a.title, a.authors, a.journal, a.pub_date, a.doi
            FROM articles a
            WHERE a.id IN (
                SELECT rowid FROM articles_fts WHERE articles_fts MATCH ?
            )
            ORDER BY a.internal_cited_by_count DESC
            LIMIT ?
        """, (safe, limit)).fetchall()
        return [dict(r) for r in rows]
