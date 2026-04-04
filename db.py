"""
db.py — SQLite layer for Rhet-Comp Index.

Schema versions
───────────────
  v1 — doi as PRIMARY KEY, no url/source columns  (legacy, auto-migrated)
  v2 — url as UNIQUE key, source column added      (auto-migrated)
  v3 — keywords + tags columns, FTS5 virtual table (auto-migrated)
  v4 — oa_url, citation_count, ss_id columns       (auto-migrated)
  v5 — citation network tables                     (auto-migrated)
  v6 — openalex_id, openalex_enriched_at, oa_status on articles;
        authors table; author_article_affiliations table
  v7 — books table (monographs + edited collections + chapters)
  v8 — institutions, article_author_institutions, openalex_fetch_log tables (current)

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


def _migrate_v5_to_v6(conn):
    """Add OpenAlex columns to articles; create authors and author_article_affiliations tables (v5 → v6).
    Safe to call on an already-migrated database — all operations are idempotent."""
    existing = [r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()]
    did_work = False
    for col, typedef in [
        ("openalex_id",         "TEXT"),
        ("openalex_enriched_at","TEXT"),
        ("oa_status",           "TEXT"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {col} {typedef}")
            did_work = True

    conn.execute("""
        CREATE TABLE IF NOT EXISTS authors (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             TEXT    NOT NULL,
            openalex_id      TEXT,
            orcid            TEXT,
            institution_name TEXT,
            institution_ror  TEXT,
            UNIQUE(name)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_authors_name ON authors(name)"
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS author_article_affiliations (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id           INTEGER NOT NULL REFERENCES articles(id),
            author_name          TEXT    NOT NULL,
            openalex_author_id   TEXT,
            institution_name     TEXT,
            institution_ror      TEXT,
            raw_affiliation_string TEXT,
            UNIQUE(article_id, author_name)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_aff_article_id ON author_article_affiliations(article_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_aff_author_name ON author_article_affiliations(author_name)"
    )
    if did_work:
        log.info("v5→v6 migration complete.")


def _migrate_v6_to_v7(conn):
    """Create books table for monographs, edited collections, and their chapters (v6 → v7).
    Safe to call on an already-migrated database — uses IF NOT EXISTS throughout."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS books (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            doi         TEXT    UNIQUE,
            isbn        TEXT,
            title       TEXT    NOT NULL,
            record_type TEXT    NOT NULL DEFAULT 'book',
            book_type   TEXT,
            parent_id   INTEGER REFERENCES books(id),
            editors     TEXT,
            authors     TEXT,
            publisher   TEXT,
            year        INTEGER,
            pages       TEXT,
            abstract    TEXT,
            subjects    TEXT,
            cited_by    INTEGER DEFAULT 0,
            source      TEXT    NOT NULL DEFAULT 'crossref',
            fetched_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_books_doi
            ON books(doi);
        CREATE INDEX IF NOT EXISTS idx_books_publisher
            ON books(publisher);
        CREATE INDEX IF NOT EXISTS idx_books_year
            ON books(year DESC);
        CREATE INDEX IF NOT EXISTS idx_books_record_type
            ON books(record_type);
        CREATE INDEX IF NOT EXISTS idx_books_parent_id
            ON books(parent_id);
        CREATE INDEX IF NOT EXISTS idx_books_book_type
            ON books(book_type);
    """)
    log.info("v6→v7 migration complete (books table ready).")


def _migrate_v7_to_v8(conn):
    """Add institutions, article_author_institutions, openalex_fetch_log tables (v7 → v8).
    Safe to call on already-migrated database — uses IF NOT EXISTS throughout."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS institutions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            openalex_id  TEXT UNIQUE,
            ror_id       TEXT,
            display_name TEXT NOT NULL,
            country_code TEXT,
            type         TEXT
        );

        CREATE TABLE IF NOT EXISTS article_author_institutions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id          INTEGER NOT NULL REFERENCES articles(id),
            author_name         TEXT NOT NULL,
            openalex_author_id  TEXT,
            institution_id      INTEGER REFERENCES institutions(id),
            author_position     TEXT,
            UNIQUE(article_id, author_name, institution_id)
        );

        CREATE INDEX IF NOT EXISTS idx_aai_article_id
            ON article_author_institutions(article_id);
        CREATE INDEX IF NOT EXISTS idx_aai_institution_id
            ON article_author_institutions(institution_id);
        CREATE INDEX IF NOT EXISTS idx_aai_openalex_author_id
            ON article_author_institutions(openalex_author_id);

        CREATE TABLE IF NOT EXISTS openalex_fetch_log (
            article_id       INTEGER UNIQUE REFERENCES articles(id),
            fetched_at       TEXT,
            openalex_work_id TEXT,
            status           TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_oafl_status ON openalex_fetch_log(status);
    """)
    log.info("v7→v8 migration complete (institutions + fetch_log tables ready).")


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

        # Always run v6 migration — it uses IF NOT EXISTS / column-existence checks
        # so it is safe to call on an already-migrated database.
        _migrate_v5_to_v6(conn)

        # Always run v7 migration — idempotent via IF NOT EXISTS.
        _migrate_v6_to_v7(conn)

        # Always run v8 migration — idempotent via IF NOT EXISTS.
        _migrate_v7_to_v8(conn)

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


# ── OpenAlex / author affiliation reads ────────────────────────────────────────

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


# ── Books ───────────────────────────────────────────────────────────────────────

def upsert_book(doi, isbn, title, record_type, book_type,
                editors, authors, publisher, year,
                pages=None, abstract=None, subjects=None,
                cited_by=0, parent_id=None, source="crossref"):
    """
    Insert or update a books-table record (book, chapter, or front-matter).

    Returns (book_id, is_new).

    For books (record_type='book'):  doi is the primary dedup key.
    For chapters (record_type='chapter'/'front-matter'):  doi is also used when
    available; otherwise uniqueness falls through to the UNIQUE(doi) constraint
    with None, which SQLite allows for multiple NULL values.
    """
    with get_conn() as conn:
        # Update-if-exists by DOI
        if doi:
            row = conn.execute(
                "SELECT id FROM books WHERE doi = ?", (doi,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE books SET cited_by=?, fetched_at=datetime('now') WHERE id=?",
                    (cited_by or 0, row["id"])
                )
                conn.commit()
                return row["id"], False

        conn.execute("""
            INSERT OR IGNORE INTO books
                (doi, isbn, title, record_type, book_type, parent_id,
                 editors, authors, publisher, year, pages,
                 abstract, subjects, cited_by, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (doi, isbn, title, record_type, book_type, parent_id,
              editors, authors, publisher, year, pages,
              abstract, subjects, cited_by or 0, source))
        conn.commit()
        changes = conn.execute("SELECT changes()").fetchone()[0]
        if changes:
            book_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return book_id, True

        # No DOI and INSERT was ignored — try title+year dedup fallback
        if title and year:
            row = conn.execute(
                "SELECT id FROM books WHERE title = ? AND year = ? AND record_type = ?",
                (title, year, record_type)
            ).fetchone()
            if row:
                return row["id"], False

        return None, False


def get_books(publisher=None, book_type=None, year_from=None, year_to=None,
              q=None, limit=50, offset=0):
    """Return book-level records (record_type='book') with optional filters."""
    where = ["record_type = 'book'"]
    params = []

    if publisher:
        where.append("publisher = ?")
        params.append(publisher)
    if book_type:
        where.append("book_type = ?")
        params.append(book_type)
    if year_from:
        try:
            where.append("year >= ?")
            params.append(int(year_from))
        except (ValueError, TypeError):
            pass
    if year_to:
        try:
            where.append("year <= ?")
            params.append(int(year_to))
        except (ValueError, TypeError):
            pass
    if q:
        like = f"%{q}%"
        where.append("(title LIKE ? OR editors LIKE ? OR authors LIKE ?)")
        params.extend([like, like, like])

    clause = "WHERE " + " AND ".join(where)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM books {clause} "
            f"ORDER BY year DESC, title ASC "
            f"LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
        return [dict(r) for r in rows]


def get_book_count(publisher=None, book_type=None, year_from=None, year_to=None, q=None):
    """Return total count of book-level records matching the given filters."""
    where = ["record_type = 'book'"]
    params = []

    if publisher:
        where.append("publisher = ?")
        params.append(publisher)
    if book_type:
        where.append("book_type = ?")
        params.append(book_type)
    if year_from:
        try:
            where.append("year >= ?")
            params.append(int(year_from))
        except (ValueError, TypeError):
            pass
    if year_to:
        try:
            where.append("year <= ?")
            params.append(int(year_to))
        except (ValueError, TypeError):
            pass
    if q:
        like = f"%{q}%"
        where.append("(title LIKE ? OR editors LIKE ? OR authors LIKE ?)")
        params.extend([like, like, like])

    clause = "WHERE " + " AND ".join(where)
    with get_conn() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM books {clause}", params
        ).fetchone()[0]


def get_book_by_id(book_id):
    """Return a single books row by primary key, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM books WHERE id = ?", (book_id,)
        ).fetchone()
        return dict(row) if row else None


def get_book_by_doi(doi):
    """Return a single books row by DOI, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM books WHERE doi = ?", (doi,)
        ).fetchone()
        return dict(row) if row else None


def get_book_chapters(book_id):
    """Return all chapter/front-matter rows for a given parent book id,
    ordered by DOI suffix (which preserves chapter number order for WAC)
    then by pages."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM books
               WHERE parent_id = ?
               ORDER BY
                 CASE record_type WHEN 'front-matter' THEN 0 ELSE 1 END,
                 doi ASC,
                 pages ASC""",
            (book_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_book_publishers():
    """Return list of (publisher, book_count, chapter_count) for the sidebar/filter."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT publisher,
                   SUM(CASE WHEN record_type='book' THEN 1 ELSE 0 END) AS book_count,
                   SUM(CASE WHEN record_type IN ('chapter','front-matter') THEN 1 ELSE 0 END) AS chapter_count
            FROM books
            WHERE publisher IS NOT NULL
            GROUP BY publisher
            ORDER BY book_count DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_books_fetch_log(publisher):
    """Return last-fetched datetime for a publisher's book harvest, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_fetched FROM fetch_log WHERE journal = ?",
            (f"books:{publisher}",)
        ).fetchone()
        return row["last_fetched"] if row else None


def update_books_fetch_log(publisher):
    """Record that we just completed a book harvest for this publisher."""
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO fetch_log (journal, last_fetched)
            VALUES (?, datetime('now'))
        """, (f"books:{publisher}",))
        conn.commit()


# ── Institutions — writes ───────────────────────────────────────────────────────

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


# ── Institutions — reads ────────────────────────────────────────────────────────

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


def get_top_institutions_v2(limit=25):
    """
    Return list of dicts: {id, display_name, article_count, country_code, type}
    from the normalized institutions table.
    Falls back to the flat author_article_affiliations table if new tables are empty.
    """
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
    Timeline for top institutions. Uses normalized tables when populated;
    falls back to get_institution_timeline() if new tables are empty.
    """
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]
    if count == 0:
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


# ── Co-citation network ───────────────────────────────────────────────────────

def get_cocitation_network(min_cocitations=3, journals=None,
                           year_from=None, year_to=None, max_nodes=400):
    """
    Build an undirected co-citation network.

    Two articles are co-cited when a third article cites both of them.
    Edge weight = number of articles that co-cite the pair.

    Nodes  = articles that participate in at least one co-citation pair
             meeting the threshold (capped at max_nodes, ranked by total
             co-citation strength).
    Links  = co-citation pairs where both endpoints are in the node set.

    Returns {nodes, links, node_count, link_count}.
    """
    # ── Optional filters on the *cited* articles ──────────────────────────
    art_where = []
    art_params = []
    if journals:
        if isinstance(journals, list) and journals:
            placeholders = ",".join("?" * len(journals))
            art_where.append(f"journal IN ({placeholders})")
            art_params.extend(journals)
        elif isinstance(journals, str):
            art_where.append("journal = ?")
            art_params.append(journals)
    if year_from:
        art_where.append("pub_date >= ?")
        art_params.append(f"{year_from}-01-01")
    if year_to:
        art_where.append("pub_date <= ?")
        art_params.append(f"{year_to}-12-31")

    art_clause = ""
    if art_where:
        art_clause = "WHERE " + " AND ".join(art_where)

    with get_conn() as conn:
        # If we have article filters, first get the qualifying article IDs
        if art_clause:
            filtered_ids = conn.execute(f"""
                SELECT id FROM articles {art_clause}
            """, art_params).fetchall()
            id_set = {r["id"] for r in filtered_ids}
            if not id_set:
                return {"nodes": [], "links": [], "node_count": 0, "link_count": 0}

            # Co-citation pairs among filtered articles
            pair_rows = conn.execute(f"""
                WITH eligible AS (
                    SELECT id FROM articles {art_clause}
                )
                SELECT c1.target_article_id AS article_a,
                       c2.target_article_id AS article_b,
                       COUNT(*)             AS weight
                FROM citations c1
                JOIN citations c2
                  ON c1.source_article_id = c2.source_article_id
                 AND c1.target_article_id < c2.target_article_id
                WHERE c1.target_article_id IN (SELECT id FROM eligible)
                  AND c2.target_article_id IN (SELECT id FROM eligible)
                GROUP BY c1.target_article_id, c2.target_article_id
                HAVING COUNT(*) >= ?
                ORDER BY weight DESC
            """, art_params + [min_cocitations]).fetchall()
        else:
            pair_rows = conn.execute("""
                SELECT c1.target_article_id AS article_a,
                       c2.target_article_id AS article_b,
                       COUNT(*)             AS weight
                FROM citations c1
                JOIN citations c2
                  ON c1.source_article_id = c2.source_article_id
                 AND c1.target_article_id < c2.target_article_id
                WHERE c1.target_article_id IS NOT NULL
                  AND c2.target_article_id IS NOT NULL
                GROUP BY c1.target_article_id, c2.target_article_id
                HAVING COUNT(*) >= ?
                ORDER BY weight DESC
            """, (min_cocitations,)).fetchall()

        if not pair_rows:
            return {"nodes": [], "links": [], "node_count": 0, "link_count": 0}

        # Collect all node IDs and their total co-citation strength
        strength = {}
        for r in pair_rows:
            strength[r["article_a"]] = strength.get(r["article_a"], 0) + r["weight"]
            strength[r["article_b"]] = strength.get(r["article_b"], 0) + r["weight"]

        # Keep top max_nodes by strength
        top_ids = sorted(strength.keys(), key=lambda k: strength[k], reverse=True)[:max_nodes]
        top_set = set(top_ids)

        # Filter links to only those where both ends are in top_set
        links = []
        for r in pair_rows:
            if r["article_a"] in top_set and r["article_b"] in top_set:
                links.append({
                    "source": r["article_a"],
                    "target": r["article_b"],
                    "weight": r["weight"],
                })

        # Fetch article metadata for nodes
        if not top_ids:
            return {"nodes": [], "links": [], "node_count": 0, "link_count": 0}

        placeholders = ",".join("?" * len(top_ids))
        node_rows = conn.execute(f"""
            SELECT id, title, authors, pub_date, journal,
                   internal_cited_by_count
            FROM articles
            WHERE id IN ({placeholders})
        """, top_ids).fetchall()

    nodes = []
    for r in node_rows:
        node = dict(r)
        node["cocitation_strength"] = strength.get(r["id"], 0)
        nodes.append(node)

    # Sort by strength descending for consistent ordering
    nodes.sort(key=lambda n: n["cocitation_strength"], reverse=True)

    return {
        "nodes":      nodes,
        "links":      links,
        "node_count": len(nodes),
        "link_count": len(links),
    }


# ── Citation centrality (eigenvector + betweenness) ────────────────────────────

def _pagerank_python(G, alpha=0.85, max_iter=500, tol=1e-06):
    """
    Pure-Python iterative PageRank (no numpy/scipy required).

    Equivalent to nx.pagerank() but avoids the numpy import that fails
    on the slim production Docker image.
    """
    N = len(G)
    if N == 0:
        return {}

    nodes = list(G)
    # Start with uniform distribution
    pr = {n: 1.0 / N for n in nodes}
    # Pre-compute in-edges for each node
    in_edges = {n: list(G.predecessors(n)) for n in nodes}
    out_degree = {n: G.out_degree(n) for n in nodes}

    dangling = [n for n in nodes if out_degree[n] == 0]

    for _ in range(max_iter):
        prev = pr.copy()
        # Sum of PageRank from dangling nodes (no outgoing edges)
        dangling_sum = sum(prev[n] for n in dangling)

        for n in nodes:
            # Incoming contribution
            incoming = sum(prev[src] / out_degree[src]
                          for src in in_edges[n]
                          if out_degree[src] > 0)
            pr[n] = (1.0 - alpha) / N + alpha * (incoming + dangling_sum / N)

        # Check convergence
        err = sum(abs(pr[n] - prev[n]) for n in nodes)
        if err < N * tol:
            break

    return pr


def get_citation_centrality(min_citations=1, journals=None,
                            year_from=None, year_to=None, max_nodes=600):
    """
    Build a directed citation graph and compute eigenvector centrality and
    betweenness centrality for each node.

    Nodes  = articles with internal_cited_by_count >= min_citations (capped at
             max_nodes, ranked by citation count so the most-cited are kept).
    Links  = citations where *both* source and target are in the node set.

    Returns {
        nodes: [..., eigenvector_centrality, betweenness_centrality],
        links: [...],
        top_eigenvector: [top 25 by eigenvector],
        top_betweenness: [top 25 by betweenness],
        node_count, link_count
    }
    """
    import networkx as nx

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
            return {"nodes": [], "links": [], "top_eigenvector": [],
                    "top_betweenness": [], "node_count": 0, "link_count": 0}

        node_ids = {r["id"] for r in node_rows}

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

    # ── Build NetworkX directed graph ──────────────────────────────────────
    G = nx.DiGraph()
    G.add_nodes_from(node_ids)
    for r in link_rows:
        G.add_edge(r["source"], r["target"])

    # ── Compute centrality metrics ─────────────────────────────────────────
    # PageRank on the citation graph: incoming citations raise your score,
    # and being cited by highly-cited articles raises it further.  PageRank
    # is the damped variant of eigenvector centrality — conceptually
    # equivalent but handles disconnected components (common in real
    # citation networks) via its teleportation/damping factor.
    #
    # Pure-Python iterative implementation to avoid numpy/scipy dependency
    # in production (the Docker image doesn't ship numpy).
    eigen = _pagerank_python(G, alpha=0.85, max_iter=500, tol=1e-06)

    between = nx.betweenness_centrality(G)

    # Normalise scores to [0, 1] for consistent front-end display
    max_eigen   = max(eigen.values())   if eigen   else 1
    max_between = max(between.values()) if between else 1
    if max_eigen == 0:
        max_eigen = 1
    if max_between == 0:
        max_between = 1

    # ── Build response ─────────────────────────────────────────────────────
    nodes = []
    for r in node_rows:
        nid = r["id"]
        node = dict(r)
        node["eigenvector_centrality"] = round(eigen.get(nid, 0) / max_eigen, 6)
        node["betweenness_centrality"] = round(between.get(nid, 0) / max_between, 6)
        nodes.append(node)

    links = [{"source": r["source"], "target": r["target"]} for r in link_rows]

    # Top-25 tables sorted by each metric
    by_eigen = sorted(nodes, key=lambda n: n["eigenvector_centrality"], reverse=True)[:25]
    by_between = sorted(nodes, key=lambda n: n["betweenness_centrality"], reverse=True)[:25]

    return {
        "nodes":            nodes,
        "links":            links,
        "top_eigenvector":  by_eigen,
        "top_betweenness":  by_between,
        "node_count":       len(nodes),
        "link_count":       len(links),
    }
