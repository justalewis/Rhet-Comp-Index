"""db.institutions — Institution detail, top institutions, institution timeline, OpenAlex fetch log."""

import json
import os
import sqlite3
import logging
from itertools import combinations

from .core import get_conn

log = logging.getLogger(__name__)


def get_top_institutions(limit=25):
    """
    Return [(institution_name, article_count)] for the top institutions
    by number of distinct articles they are affiliated with.
    Uses author_article_affiliations for article-level counting.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT institution_name, COUNT(DISTINCT article_id) AS article_count
            FROM author_article_affiliations
            WHERE institution_name IS NOT NULL AND institution_name != ''
            GROUP BY institution_name
            ORDER BY article_count DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [(r["institution_name"], r["article_count"]) for r in rows]


def get_institution_timeline(top_n=10):
    """
    Return data for an institutions-over-time chart.

    Result: {
        years: [year_str, ...],
        series: [{institution: name, counts: [count_per_year, ...]}, ...]
    }
    Only covers the top_n institutions by total article count.
    """
    # Get top institutions
    top = get_top_institutions(limit=top_n)
    if not top:
        return {"years": [], "series": []}

    top_names = [name for name, _ in top]

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                aaa.institution_name,
                SUBSTR(a.pub_date, 1, 4) AS year,
                COUNT(DISTINCT aaa.article_id) AS count
            FROM author_article_affiliations aaa
            JOIN articles a ON a.id = aaa.article_id
            WHERE aaa.institution_name IS NOT NULL
              AND aaa.institution_name != ''
              AND a.pub_date IS NOT NULL
              AND SUBSTR(a.pub_date, 1, 4) >= '1990'
            GROUP BY aaa.institution_name, year
            ORDER BY year
        """).fetchall()

    # Build year set and per-institution year→count maps
    year_set: set[str] = set()
    inst_year: dict[str, dict[str, int]] = {name: {} for name in top_names}

    for row in rows:
        name = row["institution_name"]
        if name not in inst_year:
            continue
        year = row["year"]
        year_set.add(year)
        inst_year[name][year] = row["count"]

    years = sorted(year_set)
    series = [
        {
            "institution": name,
            "counts": [inst_year[name].get(y, 0) for y in years],
        }
        for name in top_names
    ]

    return {"years": years, "series": series}


def upsert_institution(openalex_id, ror_id, display_name, country_code, inst_type):
    """Insert or update an institution. Returns the institution's integer id."""
    with get_conn() as conn:
        if openalex_id:
            row = conn.execute(
                "SELECT id FROM institutions WHERE openalex_id = ?", (openalex_id,)
            ).fetchone()
            if row:
                conn.execute("""
                    UPDATE institutions
                    SET ror_id=?, display_name=?, country_code=?, type=?
                    WHERE id=?
                """, (ror_id, display_name, country_code, inst_type, row["id"]))
                conn.commit()
                return row["id"]
        conn.execute("""
            INSERT OR IGNORE INTO institutions
                (openalex_id, ror_id, display_name, country_code, type)
            VALUES (?, ?, ?, ?, ?)
        """, (openalex_id, ror_id, display_name, country_code, inst_type))
        conn.commit()
        # Fetch by openalex_id if available, otherwise by display_name
        if openalex_id:
            row = conn.execute(
                "SELECT id FROM institutions WHERE openalex_id = ?", (openalex_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM institutions WHERE display_name = ? ORDER BY id LIMIT 1",
                (display_name,)
            ).fetchone()
        return row["id"] if row else None


def insert_article_author_institution(article_id, author_name, openalex_author_id,
                                      institution_id, author_position):
    """Insert one author-institution-article link. Silently ignores duplicates."""
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO article_author_institutions
                (article_id, author_name, openalex_author_id, institution_id, author_position)
            VALUES (?, ?, ?, ?, ?)
        """, (article_id, author_name, openalex_author_id, institution_id, author_position))
        conn.commit()


def log_openalex_fetch(article_id, openalex_work_id, status):
    """Record a fetch attempt in openalex_fetch_log (upsert on article_id)."""
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO openalex_fetch_log
                (article_id, fetched_at, openalex_work_id, status)
            VALUES (?, datetime('now'), ?, ?)
        """, (article_id, openalex_work_id, status))
        conn.commit()


def get_articles_needing_institution_fetch(batch_size=None):
    """Articles not yet in openalex_fetch_log, oldest pub_date first."""
    with get_conn() as conn:
        q = """
            SELECT id, doi, title, pub_date, journal
            FROM articles
            WHERE id NOT IN (SELECT article_id FROM openalex_fetch_log)
            ORDER BY pub_date ASC
        """
        if batch_size:
            q += f" LIMIT {int(batch_size)}"
        return [dict(r) for r in conn.execute(q).fetchall()]


def get_institution_by_id(institution_id):
    """Return an institution dict, or None if not found."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM institutions WHERE id = ?", (institution_id,)
        ).fetchone()
        return dict(row) if row else None


def get_institution_article_count(institution_id):
    """Count of distinct articles with at least one author from this institution."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT COUNT(DISTINCT article_id)
            FROM article_author_institutions
            WHERE institution_id = ?
        """, (institution_id,)).fetchone()[0]


def get_institution_articles(institution_id, limit=200, offset=0):
    """Articles affiliated with this institution, reverse-chronological."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT a.*
            FROM articles a
            JOIN article_author_institutions aai ON aai.article_id = a.id
            WHERE aai.institution_id = ?
            ORDER BY a.pub_date DESC, a.fetched_at DESC
            LIMIT ? OFFSET ?
        """, (institution_id, limit, offset)).fetchall()
        return [dict(r) for r in rows]


def get_institution_top_authors(institution_id, limit=10):
    """Most prolific authors affiliated with this institution."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT author_name, COUNT(DISTINCT article_id) AS count
            FROM article_author_institutions
            WHERE institution_id = ?
            GROUP BY author_name
            ORDER BY count DESC
            LIMIT ?
        """, (institution_id, limit)).fetchall()
        return [(r["author_name"], r["count"]) for r in rows]


def _normalized_institutions_fresh():
    """Check if the normalized institution tables have reasonably current data."""
    import datetime
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]
        if count == 0:
            return False
        max_year = conn.execute("""
            SELECT MAX(SUBSTR(a.pub_date, 1, 4))
            FROM article_author_institutions aai
            JOIN articles a ON a.id = aai.article_id
            WHERE a.pub_date IS NOT NULL
        """).fetchone()[0]
    if max_year is None:
        return False
    return int(max_year) >= datetime.datetime.now().year - 5


def get_top_institutions_v2(limit=25):
    """
    Return list of dicts: {id, display_name, article_count, country_code, type}
    from the normalized institutions table.
    Falls back to the flat author_article_affiliations table if new tables are
    empty or stale (data doesn't extend to recent years).
    """
    if not _normalized_institutions_fresh():
        old = get_top_institutions(limit=limit)
        return [
            {"id": None, "display_name": name, "article_count": count,
             "country_code": None, "type": None}
            for name, count in old
        ]

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT i.id, i.display_name, i.country_code, i.type,
                   COUNT(DISTINCT aai.article_id) AS article_count
            FROM institutions i
            JOIN article_author_institutions aai ON aai.institution_id = i.id
            GROUP BY i.id
            ORDER BY article_count DESC
            LIMIT ?
        """, (limit,)).fetchall()
    if rows:
        return [dict(r) for r in rows]
    # Fallback: use old flat table
    old = get_top_institutions(limit=limit)
    return [
        {"id": None, "display_name": name, "article_count": count,
         "country_code": None, "type": None}
        for name, count in old
    ]


def get_institution_timeline_v2(top_n=10):
    """
    Timeline for top institutions. Uses normalized tables when populated
    and current; falls back to get_institution_timeline() otherwise.
    """
    if not _normalized_institutions_fresh():
        return get_institution_timeline(top_n=top_n)

    top = get_top_institutions_v2(limit=top_n)
    if not top:
        return {"years": [], "series": []}

    top_ids = [r["id"] for r in top if r.get("id")]
    if not top_ids:
        return get_institution_timeline(top_n=top_n)

    top_names = {r["id"]: r["display_name"] for r in top if r.get("id")}

    placeholders = ",".join("?" * len(top_ids))
    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT
                aai.institution_id,
                SUBSTR(a.pub_date, 1, 4) AS year,
                COUNT(DISTINCT aai.article_id) AS count
            FROM article_author_institutions aai
            JOIN articles a ON a.id = aai.article_id
            WHERE aai.institution_id IN ({placeholders})
              AND a.pub_date IS NOT NULL
              AND SUBSTR(a.pub_date, 1, 4) >= '1990'
            GROUP BY aai.institution_id, year
            ORDER BY year
        """, top_ids).fetchall()

    year_set: set[str] = set()
    inst_year: dict[int, dict[str, int]] = {iid: {} for iid in top_ids}
    for row in rows:
        iid = row["institution_id"]
        year = row["year"]
        year_set.add(year)
        if iid in inst_year:
            inst_year[iid][year] = row["count"]

    years = sorted(year_set)
    series = [
        {
            "institution": top_names[iid],
            "counts": [inst_year[iid].get(y, 0) for y in years],
        }
        for iid in top_ids
    ]
    return {"years": years, "series": series}
