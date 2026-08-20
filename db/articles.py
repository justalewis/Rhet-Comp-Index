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
        # Article-suppression choke-point: a blocklisted DOI/URL (test deposit,
        # spam, junk) must never be (re-)inserted, or the next CrossRef fetch
        # resurrects it. Mirrors the author-redaction guard below. Cheap: the
        # blocklist is tiny and cached by version.
        if _is_suppressed(conn, doi, url):
            return 0
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


# ── Article suppression (durable removal) ─────────────────────────────────────
#
# The article-level analog of redaction.py's author suppression spine. A row in
# `suppressed_articles` blocklists a DOI/URL so upsert_article skips it forever;
# `resweep_suppressed_articles` re-purges after each fetch as a self-healing
# backstop. See db/core.py for the table and docs/author-redaction.md for the
# design philosophy this mirrors.

# Tables carrying a per-article foreign key. A delete must clear these too, or
# they orphan (SQLite does not enforce the REFERENCES clauses without
# PRAGMA foreign_keys=ON, so we clean up explicitly rather than rely on cascade).
_DEPENDENT_TABLES = (
    ("citations", "source_article_id"),
    ("citations", "target_article_id"),
    ("author_article_affiliations", "article_id"),
    ("article_author_institutions", "article_id"),
    ("openalex_fetch_log", "article_id"),
    ("user_tags", "article_id"),
    ("tag_feedback", "article_id"),
)

# Cached blocklist, keyed by (db path, row count, max id) so it refreshes the
# instant a suppression lands and never bleeds across per-test DBs — the same
# invalidation trick redaction.py uses for its suppression map.
_SUPPRESS_CACHE: tuple | None = None
_SUPPRESS_VERSION: tuple | None = None


def _suppress_version(conn) -> tuple:
    import db as _dbpkg
    try:
        n, mx = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM suppressed_articles"
        ).fetchone()
    except sqlite3.Error:
        n, mx = 0, 0
    return (_dbpkg.DB_PATH, n, mx)


def _suppression_sets(conn) -> tuple[set, set]:
    """(suppressed DOIs, suppressed URLs), cached and version-invalidated."""
    global _SUPPRESS_CACHE, _SUPPRESS_VERSION
    ver = _suppress_version(conn)
    if _SUPPRESS_CACHE is not None and _SUPPRESS_VERSION == ver:
        return _SUPPRESS_CACHE
    dois: set[str] = set()
    urls: set[str] = set()
    try:
        for r in conn.execute("SELECT doi, url FROM suppressed_articles"):
            if r["doi"]:
                dois.add(r["doi"].strip().lower())
            if r["url"]:
                urls.add(r["url"].strip())
    except sqlite3.Error:
        pass  # table missing pre-migration: nothing suppressed
    _SUPPRESS_CACHE = (dois, urls)
    _SUPPRESS_VERSION = ver
    return _SUPPRESS_CACHE


def _is_suppressed(conn, doi, url) -> bool:
    dois, urls = _suppression_sets(conn)
    if doi and doi.strip().lower() in dois:
        return True
    if url and url.strip() in urls:
        return True
    return False


def _delete_article_rows(conn, article_id):
    """Delete one article and its dependent rows. FTS self-syncs via trigger."""
    for table, col in _DEPENDENT_TABLES:
        try:
            conn.execute(f"DELETE FROM {table} WHERE {col} = ?", (article_id,))
        except sqlite3.Error:
            pass  # table may not exist in an older/partial schema
    conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))


def delete_article(article_id) -> bool:
    """Hard-delete an article and its dependents. Returns True if a row went.

    This alone is NOT durable against re-fetch — use suppress_article for
    blocklisted junk. Exposed for callers that genuinely want a one-off delete.
    """
    with get_conn() as conn:
        existed = conn.execute(
            "SELECT 1 FROM articles WHERE id = ?", (article_id,)
        ).fetchone() is not None
        if existed:
            _delete_article_rows(conn, article_id)
            conn.commit()
        return existed


def suppress_article(article_id=None, *, doi=None, url=None,
                     reason=None, actor=None) -> dict:
    """Durably remove an article: blocklist its DOI/URL, then delete the row.

    Identify by article_id (preferred — captures its DOI+URL) or by doi/url
    directly (to pre-empt one that isn't ingested yet). Idempotent: re-suppressing
    an already-blocklisted record just ensures the row is gone.
    """
    with get_conn() as conn:
        row = None
        if article_id is not None:
            row = conn.execute(
                "SELECT id, doi, url FROM articles WHERE id = ?", (article_id,)
            ).fetchone()
        if row is not None:
            doi = doi or row["doi"]
            url = url or row["url"]
        doi = (doi or None)
        url = (url or None)
        if not doi and not url:
            return {"ok": False, "error": "need an article_id, doi, or url"}

        conn.execute(
            "INSERT OR IGNORE INTO suppressed_articles (doi, url, reason, created_by) "
            "VALUES (?, ?, ?, ?)",
            (doi, url, reason, actor),
        )
        # Delete every matching row (there is normally one, but a DOI and a URL
        # could resolve to two rows in pathological cases).
        deleted_ids = []
        clauses, params = [], []
        if doi:
            clauses.append("doi = ?")
            params.append(doi)
        if url:
            clauses.append("url = ?")
            params.append(url)
        for r in conn.execute(
            f"SELECT id FROM articles WHERE {' OR '.join(clauses)}", params
        ).fetchall():
            _delete_article_rows(conn, r["id"])
            deleted_ids.append(r["id"])
        conn.commit()
        return {"ok": True, "doi": doi, "url": url,
                "deleted_ids": deleted_ids, "reason": reason}


def unsuppress(doi=None, url=None) -> bool:
    """Remove a DOI/URL from the blocklist so it can be re-ingested. Returns
    True if a blocklist row was removed. Does not re-fetch the article."""
    with get_conn() as conn:
        clauses, params = [], []
        if doi:
            clauses.append("doi = ?")
            params.append(doi)
        if url:
            clauses.append("url = ?")
            params.append(url)
        if not clauses:
            return False
        cur = conn.execute(
            f"DELETE FROM suppressed_articles WHERE {' OR '.join(clauses)}", params
        )
        conn.commit()
        return cur.rowcount > 0


def list_suppressed() -> list[dict]:
    """All blocklist entries, newest first, for the admin surface."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM suppressed_articles ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def resweep_suppressed_articles() -> int:
    """Delete any live article whose DOI/URL is blocklisted. Idempotent
    backstop wired into the post-fetch maintenance path (mirrors
    redaction.resweep_all), catching anything an ingest path slipped past the
    upsert_article choke-point. Returns the number of rows purged."""
    with get_conn() as conn:
        ids = [r["id"] for r in conn.execute("""
            SELECT a.id FROM articles a
            WHERE (a.doi IS NOT NULL AND a.doi != '' AND a.doi IN (
                       SELECT doi FROM suppressed_articles
                       WHERE doi IS NOT NULL AND doi != ''))
               OR (a.url IN (
                       SELECT url FROM suppressed_articles
                       WHERE url IS NOT NULL AND url != ''))
        """).fetchall()]
        for aid in ids:
            _delete_article_rows(conn, aid)
        if ids:
            conn.commit()
        return len(ids)


def update_article(article_id, fields: dict) -> bool:
    """Update whitelisted columns on one article. Returns True if it existed.

    Only curation-safe columns may be set; the FTS index self-syncs for
    title/authors/abstract via the update trigger. Unknown keys are ignored.
    """
    allowed = ("title", "authors", "abstract", "pub_date", "journal",
               "doi", "oa_url", "oa_status", "keywords", "tags", "url")
    sets, params = [], []
    for k in allowed:
        if k in fields:
            sets.append(f"{k} = ?")
            v = fields[k]
            params.append(v.strip() if isinstance(v, str) else v)
    if not sets:
        return False
    with get_conn() as conn:
        existed = conn.execute(
            "SELECT 1 FROM articles WHERE id = ?", (article_id,)
        ).fetchone() is not None
        if existed:
            conn.execute(
                f"UPDATE articles SET {', '.join(sets)} WHERE id = ?",
                params + [article_id],
            )
            conn.commit()
        return existed


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
                 limit=50, offset=0, min_id=None):
    clause, params = _build_where(
        journal=journal, source=source, q=q,
        year_from=year_from, year_to=year_to, tag=tag, min_id=min_id,
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
                    year_from=None, year_to=None, tag=None, min_id=None):
    clause, params = _build_where(
        journal=journal, source=source, q=q,
        year_from=year_from, year_to=year_to, tag=tag, min_id=min_id,
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


def get_feed_articles(journal=None, limit=50):
    """Newest arrivals for an Atom feed, ordered by *arrival* (fetched_at DESC,
    id DESC), optionally narrowed to one journal.

    Arrival order, not pub_date order, is the right sort for a feed: much of
    what this index surfaces is backfilled or scraped late, so an article with
    a 2019 pub_date that lands today is genuinely new to a subscriber. Sorting
    by pub_date would bury it below items the reader saw weeks ago — or, worse,
    slot it into the middle of the feed where a reader never sees it at all.

    id DESC is the tiebreaker because fetched_at has one-second resolution and
    a single fetch inserts hundreds of rows inside the same second.
    """
    sql = "SELECT * FROM articles"
    params = []
    if journal:
        sql += " WHERE journal = ?"
        params.append(journal)
    sql += " ORDER BY fetched_at DESC, id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


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
