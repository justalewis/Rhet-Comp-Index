"""db.core — Connection, schema, migrations, and the two SQL-building helpers
shared across submodules (_build_where, _sanitize_fts).

DB_PATH lives in the package's __init__.py; get_conn() reads it on each call
so per-test monkeypatch of `db.DB_PATH` continues to work after the split.
"""

import json
import os
import sqlite3
import logging

log = logging.getLogger(__name__)


def get_conn():
    # Imported on each call so per-test `monkeypatch.setattr(db, "DB_PATH", ...)`
    # is picked up immediately, matching the pre-refactor behavior.
    from . import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # WAL mode lets readers and writers run concurrently — essential when
    # cite_fetcher.py is running alongside the live web server.
    conn.execute("PRAGMA journal_mode=WAL")
    # Wait up to 10 s if another connection holds a write lock rather than
    # immediately raising "database is locked".
    conn.execute("PRAGMA busy_timeout=10000")
    # Performance PRAGMAs — safe with WAL and single-writer architecture.
    conn.execute("PRAGMA cache_size = -20000")       # 20 MB page cache (default ~2 MB)
    conn.execute("PRAGMA mmap_size = 134217728")     # 128 MB memory-mapped I/O
    conn.execute("PRAGMA synchronous = NORMAL")      # safe with WAL, avoids extra fsync
    conn.execute("PRAGMA temp_store = MEMORY")       # temp tables in memory
    return conn


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
