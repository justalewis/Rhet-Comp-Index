"""db.authors — Author index, timeline, co-authors, topics, institutions per author."""

import json
import os
import sqlite3
import logging
from itertools import combinations

from .core import get_conn

log = logging.getLogger(__name__)


def get_author_network(min_papers=3, top_n=150):
    """
    Compute author co-authorship network.
    Returns {"nodes": [...], "links": [...]} where:
      nodes: [{"id": name, "count": paper_count}, ...]
      links: [{"source": name, "target": name, "value": coauth_count}, ...]
    Only includes authors with >= min_papers AND in top top_n by count.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, authors FROM articles WHERE authors IS NOT NULL AND authors != ''"
        ).fetchall()

    author_counts: dict[str, int] = {}
    coauth_counts: dict[tuple, int] = {}

    for row in rows:
        authors = [a.strip() for a in row["authors"].split(";") if a.strip()]
        for a in authors:
            author_counts[a] = author_counts.get(a, 0) + 1
        for a, b in combinations(sorted(authors), 2):
            key = (a, b)
            coauth_counts[key] = coauth_counts.get(key, 0) + 1

    # Filter: min_papers and top_n
    qualified = sorted(
        [(name, cnt) for name, cnt in author_counts.items() if cnt >= min_papers],
        key=lambda x: -x[1]
    )[:top_n]
    qualified_set = {name for name, _ in qualified}

    nodes = [{"id": name, "count": cnt} for name, cnt in qualified]
    links = [
        {"source": a, "target": b, "value": cnt}
        for (a, b), cnt in coauth_counts.items()
        if a in qualified_set and b in qualified_set
    ]

    return {"nodes": nodes, "links": links}


def get_all_authors(limit=500):
    """
    Return list of (author_name, count) tuples for all authors,
    sorted by count descending, limited to top `limit`.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT authors FROM articles WHERE authors IS NOT NULL AND authors != ''"
        ).fetchall()

    author_counts: dict[str, int] = {}
    for row in rows:
        for a in row["authors"].split(";"):
            a = a.strip()
            if a:
                author_counts[a] = author_counts.get(a, 0) + 1

    return sorted(author_counts.items(), key=lambda x: (-x[1], x[0]))[:limit]


def get_author_articles(author_name):
    """Return all articles by this exact author (LIKE match), sorted by pub_date DESC."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM articles
            WHERE authors LIKE ?
            ORDER BY pub_date DESC, fetched_at DESC
        """, (f"%{author_name}%",)).fetchall()
        return [dict(r) for r in rows]


def get_author_books(author_name: str) -> list:
    """Return whole-book records where this author appears as author or editor."""
    like = f"%{author_name}%"
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, title, year, publisher, record_type, doi, isbn, editors, authors, subjects
            FROM books
            WHERE (authors LIKE ? OR editors LIKE ?)
              AND record_type NOT IN ('chapter', 'front-matter')
            ORDER BY year DESC
        """, (like, like)).fetchall()
    return [dict(r) for r in rows]


def get_author_timeline(author_name: str) -> dict:
    """Return publication timeline: articles by year+journal and whole-book entries."""
    from collections import defaultdict
    like = f"%{author_name}%"
    with get_conn() as conn:
        art_rows = conn.execute("""
            SELECT SUBSTR(pub_date, 1, 4) AS year, journal, COUNT(*) AS n
            FROM articles
            WHERE authors LIKE ?
              AND pub_date IS NOT NULL AND pub_date != ''
            GROUP BY year, journal
            ORDER BY year
        """, (like,)).fetchall()
        book_rows = conn.execute("""
            SELECT year, title, record_type
            FROM books
            WHERE (authors LIKE ? OR editors LIKE ?)
              AND record_type NOT IN ('chapter', 'front-matter')
              AND year IS NOT NULL
            ORDER BY year
        """, (like, like)).fetchall()

    journal_year: dict = defaultdict(lambda: defaultdict(int))
    all_years: set = set()
    journal_order: dict = {}

    for row in art_rows:
        yr, j, n = row["year"], row["journal"], row["n"]
        if yr and yr.isdigit() and 1970 <= int(yr) <= 2030:
            all_years.add(yr)
            journal_year[j][yr] += n
            if j not in journal_order:
                journal_order[j] = len(journal_order)

    if not all_years:
        return {"years": [], "series": [], "books": []}

    years = sorted(all_years)
    series = [
        {"journal": j, "counts": [journal_year[j].get(yr, 0) for yr in years]}
        for j in sorted(journal_order, key=lambda j: journal_order[j])
    ]
    books = [
        {
            "year": str(r["year"]),
            "title": r["title"][:60] + ("…" if len(r["title"]) > 60 else ""),
            "type": r["record_type"],
        }
        for r in book_rows if r["year"]
    ]
    return {"years": years, "series": series, "books": books}


def get_author_coauthors(author_name: str) -> dict:
    """Return co-authorship mini-network data centered on this author."""
    like = f"%{author_name}%"
    with get_conn() as conn:
        art_rows = conn.execute("""
            SELECT id, title, authors FROM articles
            WHERE authors LIKE ? AND authors IS NOT NULL
        """, (like,)).fetchall()
        all_rows = conn.execute(
            "SELECT authors FROM articles WHERE authors IS NOT NULL AND authors != ''"
        ).fetchall()

    # Count total articles per author for node sizing
    author_totals: dict = {}
    for row in all_rows:
        for a in row["authors"].split(";"):
            a = a.strip()
            if a:
                author_totals[a] = author_totals.get(a, 0) + 1

    # Find co-authors; skip LIKE false positives via exact parse check
    coauthor_articles: dict = {}
    for row in art_rows:
        parsed = [a.strip() for a in row["authors"].split(";") if a.strip()]
        if author_name not in parsed:
            continue
        for a in parsed:
            if a != author_name:
                coauthor_articles.setdefault(a, []).append(row["title"])

    if not coauthor_articles:
        return {"nodes": [], "links": [], "center": author_name}

    nodes = [{"id": author_name, "count": author_totals.get(author_name, 0),
               "is_center": True, "shared": 0}]
    for co, titles in sorted(coauthor_articles.items(), key=lambda x: -len(x[1])):
        nodes.append({"id": co, "count": author_totals.get(co, 0),
                      "is_center": False, "shared": len(titles)})

    links = [
        {"source": author_name, "target": co, "value": len(titles), "titles": titles[:5]}
        for co, titles in coauthor_articles.items()
    ]
    return {"nodes": nodes, "links": links, "center": author_name}


def get_author_topics(author_name: str) -> list:
    """Return topic tag frequency distribution for this author's articles."""
    like = f"%{author_name}%"
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT tags FROM articles
            WHERE authors LIKE ? AND tags IS NOT NULL AND tags != ''
        """, (like,)).fetchall()

    tag_counts: dict = {}
    for row in rows:
        for tag in row["tags"].strip("|").split("|"):
            tag = tag.strip()
            if tag:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

    return sorted(
        [{"tag": t, "count": c} for t, c in tag_counts.items()],
        key=lambda x: -x["count"],
    )


def get_article_affiliations(article_id):
    """
    Return a dict of {author_name: {institution_name, institution_ror, openalex_author_id,
    raw_affiliation_string}} for a single article.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT author_name, openalex_author_id,
                   institution_name, institution_ror, raw_affiliation_string
            FROM author_article_affiliations
            WHERE article_id = ?
        """, (article_id,)).fetchall()
    return {
        r["author_name"]: {
            "openalex_author_id":    r["openalex_author_id"],
            "institution_name":      r["institution_name"],
            "institution_ror":       r["institution_ror"],
            "raw_affiliation_string": r["raw_affiliation_string"],
        }
        for r in rows
    }


def get_author_by_name(name):
    """Return the authors table record for this name (exact match), or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM authors WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None


def get_authors_by_letter(letter):
    """
    Return all authors whose last name starts with `letter`, sorted
    alphabetically by last name then first name.
    Keys: name, count, institution_name, orcid.
    """
    letter = letter.upper()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT authors FROM articles WHERE authors IS NOT NULL AND authors != ''"
        ).fetchall()

    author_counts: dict[str, int] = {}
    for row in rows:
        for a in row["authors"].split(";"):
            a = a.strip()
            if not a:
                continue
            parts = a.split()
            last = parts[-1] if parts else a
            if last and last[0].upper() == letter:
                author_counts[a] = author_counts.get(a, 0) + 1

    def _sort_key(name):
        parts = name.strip().split()
        last = parts[-1] if parts else name
        first = " ".join(parts[:-1]) if len(parts) > 1 else ""
        return (last.upper(), first.upper())

    sorted_authors = sorted(author_counts.items(), key=lambda x: _sort_key(x[0]))

    with get_conn() as conn:
        author_records = {
            r["name"]: dict(r)
            for r in conn.execute("SELECT name, institution_name, orcid FROM authors").fetchall()
        }

    return [
        {
            "name": name,
            "count": count,
            "institution_name": author_records.get(name, {}).get("institution_name"),
            "orcid": author_records.get(name, {}).get("orcid"),
        }
        for name, count in sorted_authors
    ]


def get_all_authors_with_institutions(limit=500):
    """
    Return list of dicts with keys: name, count, institution_name, orcid.
    Sorted by count descending. Used by the authors index page.
    """
    with get_conn() as conn:
        # Build article count from the articles.authors text field (same as get_all_authors)
        rows = conn.execute(
            "SELECT authors FROM articles WHERE authors IS NOT NULL AND authors != ''"
        ).fetchall()

    author_counts: dict[str, int] = {}
    for row in rows:
        for a in row["authors"].split(";"):
            a = a.strip()
            if a:
                author_counts[a] = author_counts.get(a, 0) + 1

    sorted_authors = sorted(author_counts.items(), key=lambda x: (-x[1], x[0]))[:limit]

    # Fetch institution data for these authors from the authors table
    with get_conn() as conn:
        # Load all authors table records in one query for efficiency
        author_records = {
            r["name"]: dict(r)
            for r in conn.execute("SELECT name, institution_name, orcid FROM authors").fetchall()
        }

    result = []
    for name, count in sorted_authors:
        rec = author_records.get(name, {})
        result.append({
            "name":             name,
            "count":            count,
            "institution_name": rec.get("institution_name"),
            "orcid":            rec.get("orcid"),
        })
    return result


def get_author_affiliations_per_article(author_name):
    """
    Return {article_id: [institution_name, ...]} for all articles by this author.
    Uses normalized tables when populated; falls back to author_article_affiliations.
    """
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]
        if count > 0:
            rows = conn.execute("""
                SELECT aai.article_id, i.display_name
                FROM article_author_institutions aai
                JOIN institutions i ON i.id = aai.institution_id
                WHERE aai.author_name = ?
            """, (author_name,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT article_id, institution_name AS display_name
                FROM author_article_affiliations
                WHERE author_name = ? AND institution_name IS NOT NULL
            """, (author_name,)).fetchall()

    result: dict[int, list[str]] = {}
    for r in rows:
        aid = r["article_id"]
        name = r["display_name"]
        if name:
            if aid not in result:
                result[aid] = []
            if name not in result[aid]:
                result[aid].append(name)
    return result


def get_author_institution_summary(author_name):
    """
    Return [(institution_name, article_count)] for this author, sorted by count desc.
    Uses normalized tables when populated; falls back to author_article_affiliations.
    """
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]
        if count > 0:
            rows = conn.execute("""
                SELECT i.display_name, COUNT(DISTINCT aai.article_id) AS cnt
                FROM article_author_institutions aai
                JOIN institutions i ON i.id = aai.institution_id
                WHERE aai.author_name = ?
                GROUP BY i.id
                ORDER BY cnt DESC
            """, (author_name,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT institution_name AS display_name,
                       COUNT(DISTINCT article_id) AS cnt
                FROM author_article_affiliations
                WHERE author_name = ? AND institution_name IS NOT NULL
                GROUP BY institution_name
                ORDER BY cnt DESC
            """, (author_name,)).fetchall()
    return [(r["display_name"], r["cnt"]) for r in rows]
