"""
db.py — SQLite layer for Rhet-Comp Index.

Schema versions
───────────────
  v1 — doi as PRIMARY KEY, no url/source columns  (legacy, auto-migrated)
  v2 — url as UNIQUE key, source column added      (auto-migrated)
  v3 — keywords + tags columns, FTS5 virtual table (auto-migrated)
  v4 — oa_url, citation_count, ss_id columns       (current)

All migrations run automatically inside init_db(); no manual steps needed.
"""

import json
import sqlite3
import os
import logging
from itertools import combinations

log = logging.getLogger(__name__)

# Respect DB_PATH env var so Fly.io can point to the persistent volume (/data/articles.db)
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "articles.db"))


# ── Connection ─────────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # WAL mode lets readers and writers run concurrently — essential when
    # cite_fetcher.py is running alongside the live web server.
    conn.execute("PRAGMA journal_mode=WAL")
    # Wait up to 10 s if another connection holds a write lock rather than
    # immediately raising "database is locked".
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


# ── Schema creation ────────────────────────────────────────────────────────────

def _create_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            url            TEXT    NOT NULL UNIQUE,
            doi            TEXT,
            title          TEXT    NOT NULL,
            authors        TEXT,
            abstract       TEXT,
            pub_date       TEXT,
            journal        TEXT    NOT NULL,
            source         TEXT    NOT NULL DEFAULT 'crossref',
            keywords       TEXT,
            tags           TEXT,
            fetched_at     TEXT    DEFAULT (datetime('now')),
            oa_url         TEXT,
            citation_count INTEGER,
            ss_id          TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_articles_pub_date
            ON articles(pub_date DESC);
        CREATE INDEX IF NOT EXISTS idx_articles_journal
            ON articles(journal);
        CREATE INDEX IF NOT EXISTS idx_articles_source
            ON articles(source);
        CREATE INDEX IF NOT EXISTS idx_articles_tags
            ON articles(tags);
        CREATE INDEX IF NOT EXISTS idx_articles_fetched_at
            ON articles(fetched_at DESC);

        CREATE TABLE IF NOT EXISTS fetch_log (
            journal         TEXT PRIMARY KEY,
            last_fetched    TEXT,
            last_pub_date   TEXT
        );
    """)
    _create_fts(conn)


def _create_fts(conn):
    """
    Create the FTS5 virtual table (content table pointing at articles)
    and the three sync triggers (insert / delete / update).

    Idempotent — IF NOT EXISTS guards mean safe to call on an existing DB.
    """
    conn.executescript("""
        -- External-content FTS5 table: index only, content read from articles
        CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
            title,
            authors,
            abstract,
            content='articles',
            content_rowid='id'
        );

        -- Keep FTS in sync with articles table
        CREATE TRIGGER IF NOT EXISTS articles_fts_ai
        AFTER INSERT ON articles BEGIN
            INSERT INTO articles_fts(rowid, title, authors, abstract)
            VALUES (new.id, new.title, new.authors, new.abstract);
        END;

        CREATE TRIGGER IF NOT EXISTS articles_fts_ad
        AFTER DELETE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, title, authors, abstract)
            VALUES ('delete', old.id, old.title, old.authors, old.abstract);
        END;

        CREATE TRIGGER IF NOT EXISTS articles_fts_au
        AFTER UPDATE OF title, authors, abstract ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, title, authors, abstract)
            VALUES ('delete', old.id, old.title, old.authors, old.abstract);
            INSERT INTO articles_fts(rowid, title, authors, abstract)
            VALUES (new.id, new.title, new.authors, new.abstract);
        END;
    """)


# ── Migrations ─────────────────────────────────────────────────────────────────

def _migrate_v1_to_v3(conn):
    """Migrate v1 schema (doi PRIMARY KEY, no url/source) to v3."""
    log.info("v1 schema detected — migrating to v3…")
    conn.execute("ALTER TABLE articles RENAME TO articles_v1")

    old_fl_cols = [r[1] for r in conn.execute("PRAGMA table_info(fetch_log)").fetchall()]
    if "issn" in old_fl_cols:
        conn.execute("ALTER TABLE fetch_log RENAME TO fetch_log_v1")

    _create_tables(conn)

    conn.execute("""
        INSERT OR IGNORE INTO articles
            (url, doi, title, authors, abstract, pub_date, journal, source, fetched_at)
        SELECT
            'https://doi.org/' || doi,
            doi, title, authors, abstract, pub_date,
            COALESCE(journal, ''),
            'crossref',
            fetched_at
        FROM articles_v1
        WHERE doi IS NOT NULL AND doi != ''
    """)

    if "issn" in old_fl_cols:
        from journals import ISSN_TO_NAME
        rows = conn.execute("SELECT issn, fetched_at FROM fetch_log_v1").fetchall()
        for row in rows:
            name = ISSN_TO_NAME.get(row["issn"], row["issn"])
            conn.execute(
                "INSERT OR IGNORE INTO fetch_log (journal, last_fetched) VALUES (?, ?)",
                (name, row["fetched_at"])
            )

    # Rebuild FTS index from migrated rows
    conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
    n = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    log.info("v1→v3 migration complete — %d articles carried forward.", n)


def _migrate_v2_to_v3(conn):
    """Add keywords, tags columns and FTS5 to existing v2 schema."""
    log.info("v2 schema detected — migrating to v3…")

    existing = [r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()]
    if "keywords" not in existing:
        conn.execute("ALTER TABLE articles ADD COLUMN keywords TEXT")
    if "tags" not in existing:
        conn.execute("ALTER TABLE articles ADD COLUMN tags TEXT")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_tags ON articles(tags)"
    )

    _create_fts(conn)

    # Populate FTS index from existing rows
    conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
    log.info("v2→v3 migration complete.")


def _migrate_v3_to_v4(conn):
    """Add oa_url, citation_count, ss_id columns to existing v3 schema."""
    log.info("v3 schema detected — migrating to v4…")
    existing = [r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()]
    for col, typedef in [("oa_url", "TEXT"), ("citation_count", "INTEGER"), ("ss_id", "TEXT")]:
        if col not in existing:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {col} {typedef}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_articles_fetched_at ON articles(fetched_at DESC)"
    )
    log.info("v3→v4 migration complete.")


def _migrate_v4_to_v5(conn):
    """Add citation columns to articles + citations table (v4 → v5)."""
    existing = [r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()]
    for col, typedef in [
        ("crossref_cited_by_count", "INTEGER"),
        ("internal_cited_by_count", "INTEGER DEFAULT 0"),
        ("internal_cites_count",    "INTEGER DEFAULT 0"),
        ("references_fetched_at",   "TEXT"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {col} {typedef}")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS citations (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            source_article_id INTEGER NOT NULL REFERENCES articles(id),
            target_doi        TEXT    NOT NULL,
            target_article_id INTEGER REFERENCES articles(id),
            raw_reference     TEXT,
            created_at        TEXT    DEFAULT (datetime('now')),
            UNIQUE(source_article_id, target_doi)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_citations_source ON citations(source_article_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_citations_target_doi ON citations(target_doi)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_citations_target_id ON citations(target_article_id)"
    )


def init_db():
    with get_conn() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()]

        if not cols:
            # Fresh database
            _create_tables(conn)
        elif "url" not in cols:
            # v1 schema
            _migrate_v1_to_v3(conn)
        elif "keywords" not in cols:
            # v2 schema
            _migrate_v2_to_v3(conn)
        elif "oa_url" not in cols:
            # v3 schema — add v4 columns
            _migrate_v3_to_v4(conn)

        if "references_fetched_at" not in cols:
            _migrate_v4_to_v5(conn)
        # else: v5 already in place — nothing to do

        conn.commit()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sanitize_fts(q: str) -> str:
    """
    Prepare a user search string for FTS5 MATCH.

    If the query looks like a structured FTS expression (contains quotes,
    boolean operators, or wildcards), pass it through unchanged.
    Otherwise, split into words and wrap each as a prefix search so that
    e.g. "genre peda" matches "genre pedagogy".
    """
    q = q.strip()
    if not q:
        return '""'
    # Let structured queries through as-is
    if any(tok in q for tok in ('"', '*', ' AND ', ' OR ', ' NOT ')):
        return q
    # Prefix-search each word: "word"* (matches word and any continuation)
    words = q.split()
    return " ".join(f'"{w}"*' for w in words)


def _build_where(journal=None, source=None, q=None,
                 year_from=None, year_to=None, tag=None):
    """Build (WHERE clause string, params list) for article queries."""
    where, params = [], []

    if q:
        safe = _sanitize_fts(q)
        where.append(
            "a.id IN (SELECT rowid FROM articles_fts WHERE articles_fts MATCH ?)"
        )
        params.append(safe)

    if journal:
        if isinstance(journal, list):
            placeholders = ",".join("?" * len(journal))
            where.append(f"a.journal IN ({placeholders})")
            params.extend(journal)
        else:
            where.append("a.journal = ?")
            params.append(journal)

    if source:
        where.append("a.source = ?")
        params.append(source)

    if year_from:
        where.append("a.pub_date >= ?")
        params.append(f"{year_from}-01-01")

    if year_to:
        where.append("a.pub_date <= ?")
        params.append(f"{year_to}-12-31")

    if tag:
        # Tags stored as "|tag1|tag2|" — match a complete tag entry
        where.append("a.tags LIKE ?")
        params.append(f"%|{tag}|%")

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    return clause, params


# ── Writes ─────────────────────────────────────────────────────────────────────

def upsert_article(url, doi, title, authors, abstract, pub_date, journal, source,
                   keywords=None, tags=None):
    """
    Insert article if its URL is not already present.
    Returns 1 if a new row was inserted, 0 if it was a duplicate (ignored).

    authors  — semicolon-separated string or None
    keywords — semicolon-separated CrossRef subject terms or None
    tags     — pipe-delimited auto-tag string like "|transfer|genre theory|" or None
    """
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO articles
                (url, doi, title, authors, abstract, pub_date,
                 journal, source, keywords, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (url, doi, title, authors, abstract, pub_date,
              journal, source, keywords, tags))
        conn.commit()
        return conn.execute("SELECT changes()").fetchone()[0]


def update_fetch_log(journal, last_pub_date=None):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO fetch_log (journal, last_fetched, last_pub_date)
            VALUES (?, datetime('now'), ?)
        """, (journal, last_pub_date))
        conn.commit()


def get_last_fetch(journal):
    """Return ISO datetime string of last fetch for this journal, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_fetched FROM fetch_log WHERE journal = ?", (journal,)
        ).fetchone()
        return row["last_fetched"] if row else None


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


# ── Reads ──────────────────────────────────────────────────────────────────────

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


def get_related_articles(article_id, limit=5):
    """
    Find articles sharing the most tags with the given article.
    Returns up to `limit` articles sorted by shared-tag count desc.
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

        # Build a score expression using CASE for each tag
        cases = " + ".join(
            f"CASE WHEN tags LIKE '%|{t}|%' THEN 1 ELSE 0 END"
            for t in tags
        )
        rows = conn.execute(f"""
            SELECT *, ({cases}) AS shared_count
            FROM articles
            WHERE id != ? AND tags IS NOT NULL AND tags != ''
              AND ({cases}) > 0
            ORDER BY shared_count DESC, pub_date DESC
            LIMIT ?
        """, (article_id, limit)).fetchall()
        return [dict(r) for r in rows]


def get_timeline_data():
    """Return list of {year, journal, count} dicts for the timeline chart."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT SUBSTR(pub_date,1,4) AS year, journal, COUNT(*) AS count
            FROM articles
            WHERE pub_date IS NOT NULL AND SUBSTR(pub_date,1,4) >= '1990'
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


# ── Citation network — reads ───────────────────────────────────────────────────

def get_articles_needing_citation_fetch(limit=None):
    """Articles with DOIs where references_fetched_at IS NULL, newest-first."""
    with get_conn() as conn:
        q = (
            "SELECT id, doi, title FROM articles "
            "WHERE doi IS NOT NULL AND references_fetched_at IS NULL "
            "ORDER BY pub_date DESC"
        )
        if limit:
            q += f" LIMIT {limit}"
        return [dict(r) for r in conn.execute(q).fetchall()]


def get_doi_to_article_id_map():
    """Return {normalized_doi: article_id} for all articles that have a DOI."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, doi FROM articles WHERE doi IS NOT NULL"
        ).fetchall()
        return {r["doi"].strip().lower(): r["id"] for r in rows}


def get_article_citations(article_id):
    """Articles in our index that CITE the given article (cited-by list)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT a.* FROM articles a
            JOIN citations c ON c.source_article_id = a.id
            WHERE c.target_article_id = ?
            ORDER BY a.pub_date DESC
        """, (article_id,)).fetchall()
        return [dict(r) for r in rows]


def get_article_references(article_id):
    """Articles in our index that are CITED BY the given article (reference list)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT a.* FROM articles a
            JOIN citations c ON c.target_article_id = a.id
            WHERE c.source_article_id = ?
            ORDER BY a.pub_date DESC
        """, (article_id,)).fetchall()
        return [dict(r) for r in rows]


def get_article_all_references(article_id):
    """
    Return ALL references for an article — both in-index and out-of-index.

    Each item is a dict with an 'in_index' key:
      in_index=True  — full article fields; the DOI matched an article in our DB
      in_index=False — parsed CrossRef raw_reference metadata; DOI not in our index

    In-index refs come first (ordered by pub_date DESC), then out-of-index.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                c.target_article_id,
                c.raw_reference,
                a.id        AS a_id,
                a.title     AS a_title,
                a.authors   AS a_authors,
                a.pub_date  AS a_pub_date,
                a.journal   AS a_journal
            FROM citations c
            LEFT JOIN articles a ON a.id = c.target_article_id
            WHERE c.source_article_id = ?
            ORDER BY
                CASE WHEN c.target_article_id IS NOT NULL THEN 0 ELSE 1 END,
                a.pub_date DESC
        """, (article_id,)).fetchall()

    results = []
    for row in rows:
        if row["target_article_id"] is not None:
            results.append({
                "in_index": True,
                "id":       row["a_id"],
                "title":    row["a_title"],
                "authors":  row["a_authors"],
                "pub_date": row["a_pub_date"],
                "journal":  row["a_journal"],
            })
        else:
            raw = {}
            if row["raw_reference"]:
                try:
                    raw = json.loads(row["raw_reference"])
                except (ValueError, TypeError):
                    pass
            results.append({
                "in_index":     False,
                "title":        raw.get("article-title") or raw.get("volume-title") or "",
                "authors":      raw.get("author") or "",
                "year":         raw.get("year") or "",
                "journal":      raw.get("journal-title") or raw.get("series-title") or "",
                "doi":          raw.get("DOI") or "",
                "unstructured": raw.get("unstructured") or "",
            })

    return results


def get_ego_network(article_id):
    """
    Return the 2-degree ego network centred on article_id.

    Includes:
      - The focal article itself
      - All articles it cites that are in the index   (1st-degree outgoing)
      - All articles in the index that cite it        (1st-degree incoming)
      - All citation links between ANY two nodes in that set

    Uses CTEs so we never hit SQLite's ~999-parameter limit regardless of
    how many neighbours the focal article has.

    Returns {focal_id, nodes, links, node_count, link_count}.
    """
    with get_conn() as conn:
        node_rows = conn.execute("""
            WITH neighbors AS (
                SELECT ? AS id
                UNION
                SELECT source_article_id AS id
                FROM   citations
                WHERE  target_article_id = ?
                  AND  source_article_id IS NOT NULL
                UNION
                SELECT target_article_id AS id
                FROM   citations
                WHERE  source_article_id = ?
                  AND  target_article_id IS NOT NULL
            )
            SELECT a.id, a.title, a.authors, a.pub_date, a.journal,
                   a.doi, a.url,
                   a.internal_cited_by_count, a.internal_cites_count
            FROM   articles a
            WHERE  a.id IN (SELECT id FROM neighbors)
        """, (article_id, article_id, article_id)).fetchall()

        link_rows = conn.execute("""
            WITH neighbors AS (
                SELECT ? AS id
                UNION
                SELECT source_article_id AS id
                FROM   citations
                WHERE  target_article_id = ?
                  AND  source_article_id IS NOT NULL
                UNION
                SELECT target_article_id AS id
                FROM   citations
                WHERE  source_article_id = ?
                  AND  target_article_id IS NOT NULL
            )
            SELECT c.source_article_id AS source,
                   c.target_article_id AS target
            FROM   citations c
            WHERE  c.source_article_id IN (SELECT id FROM neighbors)
              AND  c.target_article_id  IN (SELECT id FROM neighbors)
        """, (article_id, article_id, article_id)).fetchall()

    nodes = [dict(r) for r in node_rows]
    links = [{"source": r["source"], "target": r["target"]} for r in link_rows]

    return {
        "focal_id":   article_id,
        "nodes":      nodes,
        "links":      links,
        "node_count": len(nodes),
        "link_count": len(links),
    }


def get_outside_citation_count(article_id):
    """Count of references from this article that point to DOIs outside our index."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM citations "
            "WHERE source_article_id = ? AND target_article_id IS NULL",
            (article_id,)
        ).fetchone()[0]


# ── Citation network — writes ──────────────────────────────────────────────────

def upsert_citation(source_article_id, target_doi, target_article_id, raw_reference):
    """
    Insert a citation record.  Silently ignored if the (source, target_doi)
    pair already exists (UNIQUE constraint).  Returns 1 if inserted, 0 if skipped.
    """
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO citations
                (source_article_id, target_doi, target_article_id, raw_reference)
            VALUES (?, ?, ?, ?)
        """, (
            source_article_id,
            target_doi,
            target_article_id,
            json.dumps(raw_reference) if raw_reference else None,
        ))
        conn.commit()
        return conn.execute("SELECT changes()").fetchone()[0]


def mark_references_fetched(article_id, crossref_cited_by_count=None):
    """Stamp references_fetched_at and (optionally) crossref_cited_by_count."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE articles
            SET references_fetched_at  = datetime('now'),
                crossref_cited_by_count = COALESCE(?, crossref_cited_by_count)
            WHERE id = ?
        """, (crossref_cited_by_count, article_id))
        conn.commit()


def update_citation_counts():
    """
    Recompute internal_cited_by_count and internal_cites_count for every article.
    Safe to call repeatedly — always overwrites with fresh counts.
    """
    with get_conn() as conn:
        conn.execute("""
            UPDATE articles
            SET internal_cited_by_count = (
                SELECT COUNT(*) FROM citations
                WHERE target_article_id = articles.id
            )
        """)
        conn.execute("""
            UPDATE articles
            SET internal_cites_count = (
                SELECT COUNT(*) FROM citations
                WHERE source_article_id = articles.id
                  AND target_article_id IS NOT NULL
            )
        """)
        conn.commit()


def get_most_cited(year_from=None, year_to=None, journal=None, tag=None, limit=50):
    """
    Return the top `limit` articles ranked by internal_cited_by_count.
    Optional filters: year_from, year_to, journal (exact), tag (pipe-delimited match).
    """
    where = ["internal_cited_by_count > 0", "pub_date IS NOT NULL"]
    params = []

    if year_from:
        where.append("pub_date >= ?")
        params.append(f"{year_from}-01-01")
    if year_to:
        where.append("pub_date <= ?")
        params.append(f"{year_to}-12-31")
    if journal:
        where.append("journal = ?")
        params.append(journal)
    if tag:
        where.append("tags LIKE ?")
        params.append(f"%|{tag}|%")

    clause = "WHERE " + " AND ".join(where)

    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT id, title, authors, pub_date, journal, doi, url, tags,
                   internal_cited_by_count
            FROM articles
            {clause}
            ORDER BY internal_cited_by_count DESC, pub_date DESC
            LIMIT ?
        """, params + [limit]).fetchall()
        return [dict(r) for r in rows]


def get_citation_network(min_citations=5, journals=None,
                         year_from=None, year_to=None, max_nodes=500):
    """
    Return nodes and directed links for the citation network graph.

    Nodes  = articles with internal_cited_by_count >= min_citations (capped at
             max_nodes, ranked by citation count so the most-cited are kept).
    Links  = citations where *both* source and target are in the node set.

    Uses a CTE for the edge query to avoid SQLite's ~999-parameter limit.

    Returns {nodes, links, node_count, link_count}.
    """
    where  = ["internal_cited_by_count >= ?"]
    params = [min_citations]

    if journals:
        if isinstance(journals, list) and journals:
            placeholders = ",".join("?" * len(journals))
            where.append(f"journal IN ({placeholders})")
            params.extend(journals)
        elif isinstance(journals, str):
            where.append("journal = ?")
            params.append(journals)

    if year_from:
        where.append("pub_date >= ?")
        params.append(f"{year_from}-01-01")
    if year_to:
        where.append("pub_date <= ?")
        params.append(f"{year_to}-12-31")

    clause = "WHERE " + " AND ".join(where)

    with get_conn() as conn:
        node_rows = conn.execute(f"""
            SELECT id, title, authors, pub_date, journal,
                   internal_cited_by_count, internal_cites_count
            FROM articles
            {clause}
            ORDER BY internal_cited_by_count DESC
            LIMIT ?
        """, params + [max_nodes]).fetchall()

        if not node_rows:
            return {"nodes": [], "links": [], "node_count": 0, "link_count": 0}

        # CTE avoids hitting the SQLite variable-count limit for large IN sets
        link_rows = conn.execute(f"""
            WITH filtered AS (
                SELECT id FROM articles {clause}
                ORDER BY internal_cited_by_count DESC LIMIT ?
            )
            SELECT c.source_article_id AS source,
                   c.target_article_id AS target
            FROM citations c
            WHERE c.source_article_id IN (SELECT id FROM filtered)
              AND c.target_article_id IN (SELECT id FROM filtered)
        """, params + [max_nodes]).fetchall()

        nodes = [dict(r) for r in node_rows]
        links = [{"source": r["source"], "target": r["target"]} for r in link_rows]

        return {
            "nodes":      nodes,
            "links":      links,
            "node_count": len(nodes),
            "link_count": len(links),
        }


def get_citation_trends(journal=None):
    """
    Return per-year citation behaviour for articles whose references have been fetched.

    For each year (≥ 1990, ≥ 2 articles processed) returns:
      year            — 4-digit string
      avg_cites       — average internal_cites_count (refs pointing inside the index)
      article_count   — articles with references_fetched_at IS NOT NULL
      total_cites     — raw sum of internal_cites_count for that year

    Filters to references_fetched_at IS NOT NULL so unfetched articles
    (internal_cites_count = 0) don't artificially suppress the averages.
    Optional journal filter narrows to a single journal.
    """
    where  = [
        "pub_date IS NOT NULL",
        "SUBSTR(pub_date,1,4) >= '1990'",
        "references_fetched_at IS NOT NULL",
    ]
    params = []

    if journal:
        where.append("journal = ?")
        params.append(journal)

    clause = "WHERE " + " AND ".join(where)

    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT
                SUBSTR(pub_date,1,4)                        AS year,
                ROUND(AVG(CAST(internal_cites_count AS REAL)), 2) AS avg_cites,
                COUNT(*)                                    AS article_count,
                SUM(internal_cites_count)                   AS total_cites
            FROM articles
            {clause}
            GROUP BY year
            HAVING COUNT(*) >= 2
            ORDER BY year
        """, params).fetchall()
        return [dict(r) for r in rows]
