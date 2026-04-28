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
    # Performance PRAGMAs — safe with WAL and single-writer architecture.
    conn.execute("PRAGMA cache_size = -20000")       # 20 MB page cache (default ~2 MB)
    conn.execute("PRAGMA mmap_size = 134217728")     # 128 MB memory-mapped I/O
    conn.execute("PRAGMA synchronous = NORMAL")      # safe with WAL, avoids extra fsync
    conn.execute("PRAGMA temp_store = MEMORY")       # temp tables in memory
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
        conn.execute("""
            INSERT OR IGNORE INTO articles
                (url, doi, title, authors, abstract, pub_date,
                 journal, source, keywords, tags, oa_status, oa_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (url, doi, title, authors, abstract, pub_date,
              journal, source, keywords, tags, oa_status, oa_url))
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

        # Build a score expression using parameterized CASE for each tag
        case_parts = []
        case_params = []
        for t in tags:
            case_parts.append("CASE WHEN tags LIKE ? THEN 1 ELSE 0 END")
            case_params.append(f"%|{t}|%")
        cases = " + ".join(case_parts)
        # case_params appear twice: once in SELECT, once in WHERE
        rows = conn.execute(f"""
            SELECT *, ({cases}) AS shared_count
            FROM articles
            WHERE id != ? AND tags IS NOT NULL AND tags != ''
              AND ({cases}) > 0
            ORDER BY shared_count DESC, pub_date DESC
            LIMIT ?
        """, case_params + [article_id] + case_params + [limit]).fetchall()
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


_DETAILED_COVERAGE_CACHE: dict = {}  # keyed by year_min (None or int) → {"ts", "data"}
_DETAILED_COVERAGE_TTL = 3600  # 1 hour


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

    path = os.path.join(
        os.path.dirname(__file__), "data_exports", "coverage", "coverage_snapshot.json"
    )
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        log.warning("Could not read coverage snapshot at %s: %s", path, exc)
        return None


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
    # Swap year_from/year_to if supplied in reverse order
    if year_from and year_to and str(year_from) > str(year_to):
        year_from, year_to = year_to, year_from

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


# ── Bibliographic coupling ───────────────────────────────────────────────────

def get_bibcoupling_network(min_coupling=3, journals=None,
                            year_from=None, year_to=None, max_nodes=400):
    """
    Build an undirected bibliographic-coupling network.

    Two articles are bibliographically coupled when they both cite the same
    third article.  Edge weight = number of shared references.

    This is the inverse of co-citation: co-citation links articles that
    *are cited together*; bibliographic coupling links articles that
    *cite the same things*.

    Nodes  = articles that participate in at least one coupling pair
             meeting the threshold (capped at max_nodes, ranked by total
             coupling strength).
    Links  = coupling pairs where both endpoints are in the node set.

    Returns {nodes, links, node_count, link_count}.
    """
    # ── Optional filters on the *citing* (source) articles ───────────────
    if year_from and year_to and str(year_from) > str(year_to):
        year_from, year_to = year_to, year_from

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
        if art_clause:
            filtered_ids = conn.execute(f"""
                SELECT id FROM articles {art_clause}
            """, art_params).fetchall()
            id_set = {r["id"] for r in filtered_ids}
            if not id_set:
                return {"nodes": [], "links": [], "node_count": 0, "link_count": 0}

            # Bibliographic coupling pairs among filtered source articles
            pair_rows = conn.execute(f"""
                WITH eligible AS (
                    SELECT id FROM articles {art_clause}
                )
                SELECT c1.source_article_id AS article_a,
                       c2.source_article_id AS article_b,
                       COUNT(*)             AS weight
                FROM citations c1
                JOIN citations c2
                  ON c1.target_article_id = c2.target_article_id
                 AND c1.source_article_id < c2.source_article_id
                WHERE c1.source_article_id IN (SELECT id FROM eligible)
                  AND c2.source_article_id IN (SELECT id FROM eligible)
                  AND c1.target_article_id IS NOT NULL
                GROUP BY c1.source_article_id, c2.source_article_id
                HAVING COUNT(*) >= ?
                ORDER BY weight DESC
            """, art_params + [min_coupling]).fetchall()
        else:
            pair_rows = conn.execute("""
                SELECT c1.source_article_id AS article_a,
                       c2.source_article_id AS article_b,
                       COUNT(*)             AS weight
                FROM citations c1
                JOIN citations c2
                  ON c1.target_article_id = c2.target_article_id
                 AND c1.source_article_id < c2.source_article_id
                WHERE c1.target_article_id IS NOT NULL
                  AND c2.target_article_id IS NOT NULL
                GROUP BY c1.source_article_id, c2.source_article_id
                HAVING COUNT(*) >= ?
                ORDER BY weight DESC
            """, (min_coupling,)).fetchall()

        if not pair_rows:
            return {"nodes": [], "links": [], "node_count": 0, "link_count": 0}

        # Collect all node IDs and their total coupling strength
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
                   internal_cited_by_count, internal_cites_count
            FROM articles
            WHERE id IN ({placeholders})
        """, top_ids).fetchall()

    nodes = []
    for r in node_rows:
        node = dict(r)
        node["coupling_strength"] = strength.get(r["id"], 0)
        nodes.append(node)

    # Sort by strength descending for consistent ordering
    nodes.sort(key=lambda n: n["coupling_strength"], reverse=True)

    return {
        "nodes":      nodes,
        "links":      links,
        "node_count": len(nodes),
        "link_count": len(links),
    }


# ── Sleeping Beauties (delayed recognition) ──────────────────────────────────

def get_sleeping_beauties(min_total_citations=5, max_results=50,
                          journals=None, year_from=None, year_to=None):
    """
    Identify 'Sleeping Beauty' articles — work that went uncited for years
    before experiencing a late citation surge.

    Uses the Beauty Coefficient (B) from Ke et al. (2015):
      B = Σ_{t=t₀}^{t_m} [ c_{t_m} · (t - t₀) / (t_m - t₀) - c_t ]
    where t₀ = publication year, t_m = peak citation year,
    c_t = citations in year t, c_{t_m} = citations in peak year.

    B measures the area between the expected linear trajectory (from zero
    to peak) and the actual citation curve. Large B = long sleep + sharp
    awakening.

    To avoid false positives, articles whose global CrossRef citation count
    far exceeds their internal count are penalised: the Beauty Coefficient
    is scaled down by the ratio internal / (internal + global).  This
    prevents genuinely influential articles that are heavily cited *outside*
    this index from appearing as "sleeping."

    Returns {articles: [...], count: N}.
    Each article includes: id, title, authors, pub_date, journal,
    internal_cited_by_count, crossref_cited_by_count,
    beauty_coefficient, peak_year, peak_citations,
    sleep_years, awakening_year, citation_timeline [{year, count}, ...].
    """
    with get_conn() as conn:
        # ── Build year-by-year citation timelines ────────────────────────
        # For each cited article, count how many times it was cited per year
        # (based on the citing article's publication date)
        art_where = ["c.target_article_id IS NOT NULL",
                      "t.pub_date IS NOT NULL",
                      "s.pub_date IS NOT NULL"]
        art_params = []

        if journals:
            if isinstance(journals, list) and journals:
                ph = ",".join("?" * len(journals))
                art_where.append(f"t.journal IN ({ph})")
                art_params.extend(journals)
            elif isinstance(journals, str):
                art_where.append("t.journal = ?")
                art_params.append(journals)
        if year_from:
            art_where.append("SUBSTR(t.pub_date,1,4) >= ?")
            art_params.append(str(year_from))
        if year_to:
            art_where.append("SUBSTR(t.pub_date,1,4) <= ?")
            art_params.append(str(year_to))

        # Swap year_from/year_to if reversed
        if year_from and year_to and str(year_from) > str(year_to):
            year_from, year_to = year_to, year_from

        where_clause = " AND ".join(art_where)

        # First: get articles with enough total citations
        qualified = conn.execute(f"""
            SELECT t.id, t.title, t.authors, t.pub_date, t.journal,
                   t.internal_cited_by_count,
                   COALESCE(t.crossref_cited_by_count, 0) AS crossref_cited_by_count
            FROM articles t
            WHERE t.internal_cited_by_count >= ?
              AND t.pub_date IS NOT NULL
              {"AND t.journal IN (" + ",".join("?" * len(journals)) + ")"
               if isinstance(journals, list) and journals else
               "AND t.journal = ?" if isinstance(journals, str) else ""}
              {"AND SUBSTR(t.pub_date,1,4) >= ?" if year_from else ""}
              {"AND SUBSTR(t.pub_date,1,4) <= ?" if year_to else ""}
            ORDER BY t.internal_cited_by_count DESC
        """, ([min_total_citations] +
              (journals if isinstance(journals, list) and journals else
               [journals] if isinstance(journals, str) else []) +
              ([str(year_from)] if year_from else []) +
              ([str(year_to)] if year_to else []))).fetchall()

        if not qualified:
            return {"articles": [], "count": 0}

        qualified_ids = {r["id"]: dict(r) for r in qualified}

        # Get year-by-year citation data for all qualified articles at once
        id_list = list(qualified_ids.keys())
        ph = ",".join("?" * len(id_list))
        timeline_rows = conn.execute(f"""
            SELECT c.target_article_id AS article_id,
                   SUBSTR(s.pub_date, 1, 4) AS cite_year,
                   COUNT(*) AS cnt
            FROM citations c
            JOIN articles s ON c.source_article_id = s.id
            WHERE c.target_article_id IN ({ph})
              AND s.pub_date IS NOT NULL
            GROUP BY c.target_article_id, cite_year
            ORDER BY c.target_article_id, cite_year
        """, id_list).fetchall()

    # ── Build timelines and compute Beauty Coefficient ───────────────
    from collections import defaultdict
    timelines = defaultdict(dict)  # {article_id: {year_str: count}}
    for r in timeline_rows:
        timelines[r["article_id"]][r["cite_year"]] = r["cnt"]

    results = []
    for art_id, art in qualified_ids.items():
        tl = timelines.get(art_id, {})
        if not tl:
            continue

        pub_year_str = art["pub_date"][:4] if art["pub_date"] else None
        if not pub_year_str:
            continue
        t0 = int(pub_year_str)

        # Build a complete year-by-year series from pub year to last citing year
        years_with_cites = {int(y): c for y, c in tl.items() if y.isdigit()}
        if not years_with_cites:
            continue

        max_year = max(years_with_cites.keys())
        min_cite_year = min(years_with_cites.keys())

        # Fill in zeros for years with no citations
        full_timeline = {}
        for y in range(t0, max_year + 1):
            full_timeline[y] = years_with_cites.get(y, 0)

        # Find peak year (year with most citations)
        tm = max(full_timeline, key=lambda y: full_timeline[y])
        ctm = full_timeline[tm]

        if tm <= t0 or ctm <= 0:
            # No meaningful trajectory — peak is at publication year
            continue

        # ── Beauty Coefficient (Ke et al. 2015) ─────────────────────
        # B = Σ_{t=t0}^{tm} [ ctm * (t - t0) / (tm - t0) - c_t ]
        B_raw = 0.0
        span = tm - t0
        for t in range(t0, tm + 1):
            expected = ctm * (t - t0) / span
            actual = full_timeline.get(t, 0)
            B_raw += (expected - actual)

        # ── False-positive correction ───────────────────────────────
        # Articles heavily cited *outside* this index aren't truly
        # sleeping — they just aren't cited much internally.  Scale B
        # by the ratio of internal to total (internal + global) citations.
        # An article with 13 internal and 6,182 global citations gets
        # its B multiplied by ~0.002, effectively removing it.
        internal = art["internal_cited_by_count"] or 0
        global_ct = art.get("crossref_cited_by_count", 0) or 0
        if global_ct > internal * 3:
            # Only penalise when global count dwarfs internal count
            scaling = internal / (internal + global_ct) if (internal + global_ct) > 0 else 1
            B = B_raw * scaling
        else:
            B = B_raw

        # ── Awakening year ──────────────────────────────────────────
        # The year when citations first exceed a threshold of sustained
        # activity. We use the approach: first year where the running
        # citation count exceeds 20% of the way from sleep average to peak.
        sleep_avg = 0
        awakening_year = tm  # default to peak if we can't find one
        sleep_years_count = 0

        # Calculate average citations in the sleep period (t0 to tm-1)
        sleep_cites = [full_timeline.get(y, 0) for y in range(t0, tm)]
        if sleep_cites:
            sleep_avg = sum(sleep_cites) / len(sleep_cites)

        # Awakening = first year after t0 where citations rise above
        # max(1, sleep_avg + 0.2 * (ctm - sleep_avg)) for 2+ consecutive years
        threshold = max(1, sleep_avg + 0.2 * (ctm - sleep_avg))
        for y in range(t0, tm + 1):
            ct = full_timeline.get(y, 0)
            if ct >= threshold:
                # Check if next year also meets threshold (sustained rise)
                next_ct = full_timeline.get(y + 1, 0)
                if next_ct >= threshold or y == tm:
                    awakening_year = y
                    break

        sleep_years_count = max(0, awakening_year - t0)

        # Build the timeline list for the response
        timeline_list = [{"year": y, "count": full_timeline[y]}
                         for y in sorted(full_timeline.keys())]

        results.append({
            "id": art_id,
            "title": art["title"],
            "authors": art["authors"],
            "pub_date": art["pub_date"],
            "journal": art["journal"],
            "internal_cited_by_count": art["internal_cited_by_count"],
            "crossref_cited_by_count": art.get("crossref_cited_by_count", 0),
            "beauty_coefficient": round(B, 2),
            "peak_year": tm,
            "peak_citations": ctm,
            "sleep_years": sleep_years_count,
            "awakening_year": awakening_year,
            "citation_timeline": timeline_list,
        })

    # Sort by Beauty Coefficient descending
    results.sort(key=lambda x: x["beauty_coefficient"], reverse=True)
    results = results[:max_results]

    return {"articles": results, "count": len(results)}


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

    # Auto-swap reversed year range
    if year_from and year_to and str(year_from) > str(year_to):
        year_from, year_to = year_to, year_from

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


# ── Journal citation flow (chord diagram) ─────────────────────────────────────

def get_journal_citation_flow(min_citations=1, journals=None,
                              year_from=None, year_to=None):
    """
    Build a journal-to-journal citation flow matrix for a chord diagram.

    Each cell matrix[i][j] = number of times articles in journal i cite
    articles in journal j.  Diagonal = self-citations within a journal.

    Filters:
      journals   – restrict both source and target articles to these journals
      year_from/year_to – restrict the *citing* (source) articles by pub_date
      min_citations – exclude flows below this threshold (post-aggregation)

    Returns {
        journals:   [ordered list of journal names],
        matrix:     [[int, …], …],   # N × N
        total_citations: int,
        self_citations:  int,
    }
    """
    src_where = []
    tgt_where = []
    src_params = []
    tgt_params = []

    if journals:
        jlist = journals if isinstance(journals, list) else [journals]
        ph = ",".join("?" * len(jlist))
        src_where.append(f"src.journal IN ({ph})")
        src_params.extend(jlist)
        tgt_where.append(f"tgt.journal IN ({ph})")
        tgt_params.extend(jlist)

    # Auto-swap reversed year range
    if year_from and year_to and str(year_from) > str(year_to):
        year_from, year_to = year_to, year_from

    if year_from:
        src_where.append("src.pub_date >= ?")
        src_params.append(f"{year_from}-01-01")
    if year_to:
        src_where.append("src.pub_date <= ?")
        src_params.append(f"{year_to}-12-31")

    src_clause = (" AND " + " AND ".join(src_where)) if src_where else ""
    tgt_clause = (" AND " + " AND ".join(tgt_where)) if tgt_where else ""

    params = src_params + tgt_params

    with get_conn() as conn:
        rows = conn.execute(f"""
            SELECT src.journal  AS source_journal,
                   tgt.journal  AS target_journal,
                   COUNT(*)     AS cnt
            FROM citations c
            JOIN articles src ON c.source_article_id = src.id
            JOIN articles tgt ON c.target_article_id = tgt.id
            WHERE c.target_article_id IS NOT NULL
              {src_clause}
              {tgt_clause}
            GROUP BY src.journal, tgt.journal
            HAVING cnt >= ?
            ORDER BY cnt DESC
        """, params + [min_citations]).fetchall()

    if not rows:
        return {"journals": [], "matrix": [], "total_citations": 0,
                "self_citations": 0}

    # Collect all journal names that appear and sort alphabetically
    jset = set()
    for r in rows:
        jset.add(r["source_journal"])
        jset.add(r["target_journal"])
    journal_list = sorted(jset)
    idx = {name: i for i, name in enumerate(journal_list)}
    n = len(journal_list)

    # Build N × N matrix
    matrix = [[0] * n for _ in range(n)]
    for r in rows:
        i = idx[r["source_journal"]]
        j = idx[r["target_journal"]]
        matrix[i][j] = r["cnt"]

    total = sum(r["cnt"] for r in rows)
    self_cit = sum(matrix[i][i] for i in range(n))

    return {
        "journals":         journal_list,
        "matrix":           matrix,
        "total_citations":  total,
        "self_citations":   self_cit,
    }


# ── Citation half-life by journal ──────────────────────────────────────────────

def _median_from_freq(pairs):
    """Compute median from sorted (value, count) frequency pairs."""
    total = sum(c for _, c in pairs)
    if total == 0:
        return None
    mid = total / 2.0
    cumulative = 0
    for val, cnt in pairs:
        cumulative += cnt
        if cumulative >= mid:
            return val
    return pairs[-1][0] if pairs else None


def _percentile_from_freq(pairs, pct):
    """Compute a percentile from sorted (value, count) frequency pairs."""
    total = sum(c for _, c in pairs)
    if total == 0:
        return None
    target = total * pct / 100.0
    cumulative = 0
    for val, cnt in pairs:
        cumulative += cnt
        if cumulative >= target:
            return val
    return pairs[-1][0] if pairs else None


def get_journal_half_life(journals=None, year_from=None, year_to=None,
                          include_distribution=False, include_timeseries=False):
    """
    Compute citing and cited half-life for each journal.

    Citing half-life = median age of works a journal cites.
    Cited half-life  = median age at which a journal's articles are cited.

    Age = year_of_citing_article − year_of_cited_article.

    Filters:
        journals   – restrict both source and target to these journals
        year_from/year_to – restrict *citing* (source) articles by pub_date

    Returns {
        journals: [{name, citing_half_life, citing_q25, citing_q75, citing_count,
                     cited_half_life, cited_q25, cited_q75, cited_count}, ...],
        total_citations: int,
        distributions: {...}  (if include_distribution),
        timeseries: {...}     (if include_timeseries),
    }
    """
    src_where = []
    tgt_where = []
    src_params = []
    tgt_params = []

    if journals:
        jlist = journals if isinstance(journals, list) else [journals]
        ph = ",".join("?" * len(jlist))
        src_where.append(f"src.journal IN ({ph})")
        src_params.extend(jlist)
        tgt_where.append(f"tgt.journal IN ({ph})")
        tgt_params.extend(jlist)

    # Auto-swap reversed year range
    if year_from and year_to and str(year_from) > str(year_to):
        year_from, year_to = year_to, year_from

    if year_from:
        src_where.append("src.pub_date >= ?")
        src_params.append(f"{year_from}-01-01")
    if year_to:
        src_where.append("src.pub_date <= ?")
        src_params.append(f"{year_to}-12-31")

    src_clause = (" AND " + " AND ".join(src_where)) if src_where else ""
    tgt_clause = (" AND " + " AND ".join(tgt_where)) if tgt_where else ""

    base_where = """
        WHERE c.target_article_id IS NOT NULL
          AND src.pub_date IS NOT NULL AND LENGTH(src.pub_date) >= 4
          AND tgt.pub_date IS NOT NULL AND LENGTH(tgt.pub_date) >= 4
          AND CAST(SUBSTR(src.pub_date,1,4) AS INTEGER)
              >= CAST(SUBSTR(tgt.pub_date,1,4) AS INTEGER)
    """

    params = src_params + tgt_params

    with get_conn() as conn:
        # ── Citing half-life (outgoing refs, grouped by SOURCE journal) ──
        citing_rows = conn.execute(f"""
            SELECT src.journal AS journal,
                   CAST(SUBSTR(src.pub_date,1,4) AS INTEGER)
                     - CAST(SUBSTR(tgt.pub_date,1,4) AS INTEGER) AS age,
                   COUNT(*) AS cnt
            FROM citations c
            JOIN articles src ON c.source_article_id = src.id
            JOIN articles tgt ON c.target_article_id  = tgt.id
            {base_where}
              {src_clause}
              {tgt_clause}
            GROUP BY src.journal, age
            ORDER BY src.journal, age
        """, params).fetchall()

        # ── Cited half-life (incoming refs, grouped by TARGET journal) ──
        cited_rows = conn.execute(f"""
            SELECT tgt.journal AS journal,
                   CAST(SUBSTR(src.pub_date,1,4) AS INTEGER)
                     - CAST(SUBSTR(tgt.pub_date,1,4) AS INTEGER) AS age,
                   COUNT(*) AS cnt
            FROM citations c
            JOIN articles src ON c.source_article_id = src.id
            JOIN articles tgt ON c.target_article_id  = tgt.id
            {base_where}
              {src_clause}
              {tgt_clause}
            GROUP BY tgt.journal, age
            ORDER BY tgt.journal, age
        """, params).fetchall()

        # ── Optional: timeseries (citing half-life by source year) ──
        ts_rows = None
        if include_timeseries:
            ts_rows = conn.execute(f"""
                SELECT src.journal AS journal,
                       CAST(SUBSTR(src.pub_date,1,4) AS INTEGER) AS cite_year,
                       CAST(SUBSTR(src.pub_date,1,4) AS INTEGER)
                         - CAST(SUBSTR(tgt.pub_date,1,4) AS INTEGER) AS age,
                       COUNT(*) AS cnt
                FROM citations c
                JOIN articles src ON c.source_article_id = src.id
                JOIN articles tgt ON c.target_article_id  = tgt.id
                {base_where}
                  {src_clause}
                  {tgt_clause}
                GROUP BY src.journal, cite_year, age
                ORDER BY src.journal, cite_year, age
            """, params).fetchall()

    # ── Build citing distributions per journal ──
    citing_dist = {}          # journal -> [(age, cnt), ...]
    for r in citing_rows:
        citing_dist.setdefault(r["journal"], []).append((r["age"], r["cnt"]))

    cited_dist = {}
    for r in cited_rows:
        cited_dist.setdefault(r["journal"], []).append((r["age"], r["cnt"]))

    # ── Compute per-journal metrics ──
    all_journals = sorted(set(list(citing_dist.keys()) + list(cited_dist.keys())))
    journal_results = []
    total_cit = 0

    for jname in all_journals:
        citing_pairs = citing_dist.get(jname, [])
        cited_pairs  = cited_dist.get(jname, [])
        citing_n = sum(c for _, c in citing_pairs)
        cited_n  = sum(c for _, c in cited_pairs)
        total_cit += citing_n

        entry = {
            "name":             jname,
            "citing_half_life": _median_from_freq(citing_pairs),
            "citing_q25":       _percentile_from_freq(citing_pairs, 25),
            "citing_q75":       _percentile_from_freq(citing_pairs, 75),
            "citing_count":     citing_n,
            "cited_half_life":  _median_from_freq(cited_pairs),
            "cited_q25":        _percentile_from_freq(cited_pairs, 25),
            "cited_q75":        _percentile_from_freq(cited_pairs, 75),
            "cited_count":      cited_n,
        }
        journal_results.append(entry)

    result = {
        "journals":        journal_results,
        "total_citations": total_cit,
    }

    # ── Optional: full distribution data ──
    if include_distribution:
        dists = {}
        for jname in all_journals:
            dists[jname] = {
                "citing": [{"age": a, "count": c} for a, c in citing_dist.get(jname, [])],
                "cited":  [{"age": a, "count": c} for a, c in cited_dist.get(jname, [])],
            }
        result["distributions"] = dists

    # ── Optional: timeseries ──
    if include_timeseries and ts_rows:
        # Group by journal -> year -> [(age, cnt)]
        ts_data = {}
        for r in ts_rows:
            jname = r["journal"]
            yr    = r["cite_year"]
            ts_data.setdefault(jname, {}).setdefault(yr, []).append((r["age"], r["cnt"]))

        timeseries = {}
        for jname in sorted(ts_data.keys()):
            citing_ts = []
            for yr in sorted(ts_data[jname].keys()):
                pairs = ts_data[jname][yr]
                n = sum(c for _, c in pairs)
                if n >= 10:  # require minimum sample
                    citing_ts.append({
                        "year":      yr,
                        "half_life": _median_from_freq(pairs),
                        "count":     n,
                    })
            if citing_ts:
                timeseries[jname] = {"citing": citing_ts}
        result["timeseries"] = timeseries

    return result


# ── Community detection (Louvain) ──────────────────────────────────────────────

def get_community_detection(min_citations=2, journals=None,
                            year_from=None, year_to=None,
                            max_nodes=600, resolution=1.0):
    """
    Run Louvain community detection on the citation network.

    Builds an undirected, weighted graph from citations (weight = number of
    citation links between two articles), then partitions into communities
    that maximise Newman-Girvan modularity.

    Returns {
        nodes, links, communities (with top articles/journals/topics),
        modularity, community_count, node_count, link_count, resolution
    }
    """
    import networkx as nx
    from networkx.algorithms.community import louvain_communities, modularity
    from collections import Counter
    import re

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

    # Auto-swap reversed year range
    if year_from and year_to and str(year_from) > str(year_to):
        year_from, year_to = year_to, year_from

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
            return {"nodes": [], "links": [], "communities": [],
                    "modularity": 0, "community_count": 0,
                    "node_count": 0, "link_count": 0, "resolution": resolution}

        node_ids = {r["id"] for r in node_rows}
        node_map = {r["id"]: dict(r) for r in node_rows}

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

    # ── Build undirected weighted graph ──
    G = nx.Graph()
    G.add_nodes_from(node_ids)
    for r in link_rows:
        s, t = r["source"], r["target"]
        if G.has_edge(s, t):
            G[s][t]["weight"] += 1
        else:
            G.add_edge(s, t, weight=1)

    # Remove isolates for cleaner community detection
    isolates = list(nx.isolates(G))
    G.remove_nodes_from(isolates)

    if G.number_of_nodes() < 3:
        return {"nodes": [], "links": [], "communities": [],
                "modularity": 0, "community_count": 0,
                "node_count": 0, "link_count": 0, "resolution": resolution}

    # ── Louvain community detection ──
    communities_sets = louvain_communities(G, weight="weight",
                                          resolution=resolution, seed=42)
    mod_score = modularity(G, communities_sets, weight="weight")

    # Sort communities by size descending, assign IDs
    communities_sets = sorted(communities_sets, key=len, reverse=True)
    node_community = {}
    for cid, members in enumerate(communities_sets):
        for nid in members:
            node_community[nid] = cid

    # ── Stopwords for topic extraction ──
    STOP = {
        "the", "a", "an", "and", "or", "of", "in", "on", "to", "for", "is",
        "are", "was", "were", "be", "been", "with", "from", "by", "at", "as",
        "its", "it", "this", "that", "these", "those", "not", "but", "if",
        "so", "how", "what", "who", "which", "their", "our", "we", "they",
        "about", "into", "over", "more", "than", "between", "through",
        "toward", "towards", "beyond", "within", "among", "across",
        "new", "study", "analysis", "case", "using", "approach", "journal",
        "review", "research", "article", "introduction", "essay",
    }

    # Global word frequency (for TF-IDF-like topic extraction)
    global_words = Counter()
    comm_words = {}
    for cid, members in enumerate(communities_sets):
        wc = Counter()
        for nid in members:
            title = node_map.get(nid, {}).get("title", "") or ""
            words = re.findall(r'[a-z]{3,}', title.lower())
            words = [w for w in words if w not in STOP]
            wc.update(words)
            global_words.update(words)
        comm_words[cid] = wc

    total_docs = G.number_of_nodes()

    # ── Build community summaries ──
    community_list = []
    for cid, members in enumerate(communities_sets):
        if len(members) < 2:
            continue
        # Top journals
        journal_counter = Counter()
        for nid in members:
            j = node_map.get(nid, {}).get("journal", "")
            if j:
                journal_counter[j] += 1
        top_journals = [{"name": n, "count": c}
                        for n, c in journal_counter.most_common(3)]

        # Top articles by citation count
        member_articles = [node_map[nid] for nid in members if nid in node_map]
        member_articles.sort(key=lambda a: a.get("internal_cited_by_count", 0),
                             reverse=True)
        top_articles = []
        for a in member_articles[:5]:
            top_articles.append({
                "id": a["id"], "title": a["title"], "authors": a["authors"],
                "pub_date": a["pub_date"], "journal": a["journal"],
                "internal_cited_by_count": a["internal_cited_by_count"],
            })

        # Topics via TF-IDF-like scoring
        wc = comm_words.get(cid, Counter())
        topics = []
        for word, count in wc.items():
            if count < 2:
                continue
            tf = count / max(len(members), 1)
            idf = total_docs / max(global_words.get(word, 1), 1)
            score = tf * (idf ** 0.5)
            topics.append((word, score))
        topics.sort(key=lambda x: -x[1])
        topic_words = [w for w, _ in topics[:5]]

        community_list.append({
            "id":            cid,
            "size":          len(members),
            "top_journals":  top_journals,
            "top_articles":  top_articles,
            "topics":        topic_words,
        })

    # ── Build node/link lists ──
    nodes = []
    for nid in G.nodes():
        info = node_map.get(nid, {})
        nodes.append({
            "id":    nid,
            "title": info.get("title", ""),
            "authors": info.get("authors", ""),
            "pub_date": info.get("pub_date", ""),
            "journal": info.get("journal", ""),
            "internal_cited_by_count": info.get("internal_cited_by_count", 0),
            "community": node_community.get(nid, 0),
        })

    links = [{"source": u, "target": v, "weight": d.get("weight", 1)}
             for u, v, d in G.edges(data=True)]

    return {
        "nodes":           nodes,
        "links":           links,
        "communities":     community_list,
        "modularity":      round(mod_score, 4),
        "community_count": len(community_list),
        "node_count":      len(nodes),
        "link_count":      len(links),
        "resolution":      resolution,
    }


# ── Main path analysis (SPC) ──────────────────────────────────────────────────

def get_main_path(min_citations=1, journals=None,
                  year_from=None, year_to=None, max_nodes=800):
    """
    Main path analysis via Search Path Count (SPC).

    Builds a citation DAG, computes SPC for each edge, then traces the
    global main path by greedily following highest-SPC edges from the
    most-connected source to a sink.

    Returns { path, edges, stats }
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

    # Auto-swap reversed year range
    if year_from and year_to and str(year_from) > str(year_to):
        year_from, year_to = year_to, year_from

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
            return {"path": [], "edges": [], "stats": {
                "dag_nodes": 0, "dag_edges": 0, "source_count": 0,
                "sink_count": 0, "path_length": 0, "max_spc": 0,
                "cycles_removed": 0}}

        node_map = {r["id"]: dict(r) for r in node_rows}

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

    # ── Build directed graph (source→target = citer→cited) ──
    G = nx.DiGraph()
    for nid in node_map:
        G.add_node(nid)
    for r in link_rows:
        G.add_edge(r["source"], r["target"])

    # ── Remove cycles by dropping edges from older to newer ──
    cycles_removed = 0
    while not nx.is_directed_acyclic_graph(G):
        try:
            cycle = nx.find_cycle(G)
        except nx.NetworkXNoCycle:
            break
        # Find the edge with the "wrong" direction (older→newer)
        worst_edge = None
        worst_score = -1
        for u, v in cycle:
            u_year = node_map.get(u, {}).get("pub_date", "9999")[:4]
            v_year = node_map.get(v, {}).get("pub_date", "9999")[:4]
            # Remove edges where source is older than target
            if u_year <= v_year:
                score = int(v_year) - int(u_year)
                if score > worst_score:
                    worst_score = score
                    worst_edge = (u, v)
        if worst_edge is None:
            # All same year — just remove first edge
            worst_edge = cycle[0]
        G.remove_edge(*worst_edge)
        cycles_removed += 1

    # Remove isolates
    G.remove_nodes_from(list(nx.isolates(G)))

    if G.number_of_nodes() < 2:
        return {"path": [], "edges": [], "stats": {
            "dag_nodes": 0, "dag_edges": 0, "source_count": 0,
            "sink_count": 0, "path_length": 0, "max_spc": 0,
            "cycles_removed": cycles_removed}}

    # ── Identify sources (in_degree=0) and sinks (out_degree=0) ──
    sources = [n for n in G.nodes() if G.in_degree(n) == 0]
    sinks   = [n for n in G.nodes() if G.out_degree(n) == 0]

    if not sources or not sinks:
        return {"path": [], "edges": [], "stats": {
            "dag_nodes": G.number_of_nodes(), "dag_edges": G.number_of_edges(),
            "source_count": len(sources), "sink_count": len(sinks),
            "path_length": 0, "max_spc": 0,
            "cycles_removed": cycles_removed}}

    # ── Compute SPC weights ──
    topo_order = list(nx.topological_sort(G))
    # Edges go source→target (citer→cited), so topological order goes
    # from sources (recent, citing, in_degree=0) toward sinks (old, cited,
    # out_degree=0).

    # paths_from_source[n] = number of paths from any source to n
    paths_from_source = {}
    for n in topo_order:
        preds = list(G.predecessors(n))
        if not preds:
            paths_from_source[n] = 1  # source node
        else:
            paths_from_source[n] = sum(paths_from_source.get(p, 0) for p in preds)

    # paths_to_sink[n] = number of paths from n to any sink
    paths_to_sink = {}
    for n in reversed(topo_order):
        succs = list(G.successors(n))
        if not succs:
            paths_to_sink[n] = 1  # sink node
        else:
            paths_to_sink[n] = sum(paths_to_sink.get(s, 0) for s in succs)

    # SPC for each edge
    edge_spc = {}
    max_spc = 0
    for u, v in G.edges():
        spc = paths_from_source.get(u, 0) * paths_to_sink.get(v, 0)
        edge_spc[(u, v)] = spc
        if spc > max_spc:
            max_spc = spc

    # ── Trace main path (greedy from best source) ──
    # Start from the source whose outgoing edges have highest total SPC
    best_source = max(sources,
                      key=lambda s: sum(edge_spc.get((s, t), 0)
                                        for t in G.successors(s)))

    path = [best_source]
    visited = {best_source}
    current = best_source
    while G.out_degree(current) > 0:
        succs = list(G.successors(current))
        # Pick the successor with highest SPC on the connecting edge
        best_next = max(succs, key=lambda t: edge_spc.get((current, t), 0))
        if best_next in visited:
            break
        path.append(best_next)
        visited.add(best_next)
        current = best_next

    # ── Build response ──
    path_nodes = []
    for i, nid in enumerate(path):
        info = node_map.get(nid, {})
        path_nodes.append({
            "id":       nid,
            "title":    info.get("title", ""),
            "authors":  info.get("authors", ""),
            "pub_date": info.get("pub_date", ""),
            "journal":  info.get("journal", ""),
            "internal_cited_by_count": info.get("internal_cited_by_count", 0),
            "internal_cites_count":    info.get("internal_cites_count", 0),
            "position": i,
        })

    path_edges = []
    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        spc = edge_spc.get((u, v), 0)
        path_edges.append({
            "source":         u,
            "target":         v,
            "spc_weight":     spc,
            "spc_normalized": round(spc / max_spc, 4) if max_spc > 0 else 0,
        })

    return {
        "path":  path_nodes,
        "edges": path_edges,
        "stats": {
            "dag_nodes":      G.number_of_nodes(),
            "dag_edges":      G.number_of_edges(),
            "source_count":   len(sources),
            "sink_count":     len(sinks),
            "path_length":    len(path),
            "max_spc":        max_spc,
            "cycles_removed": cycles_removed,
        },
    }


# ── Temporal network evolution ─────────────────────────────────────────────────

def get_temporal_network_evolution(min_citations=1, journals=None,
                                   year_from=None, year_to=None,
                                   window_size=1, max_nodes_per_window=500,
                                   snapshot_year=None):
    """
    Slice the citation network into time windows and compute structural
    metrics for each window, revealing how the network evolves over time.

    Returns { windows, snapshot (if requested), stats }
    """
    import networkx as nx
    from networkx.algorithms.community import louvain_communities
    from networkx.algorithms.community.quality import modularity as nx_modularity

    # ── Build WHERE clause ──
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

    # Auto-swap reversed year range
    if year_from and year_to:
        yf, yt = int(year_from), int(year_to)
        if yf > yt:
            year_from, year_to = str(yt), str(yf)

    if year_from:
        where.append("CAST(SUBSTR(pub_date,1,4) AS INTEGER) >= ?")
        params.append(int(year_from))
    if year_to:
        where.append("CAST(SUBSTR(pub_date,1,4) AS INTEGER) <= ?")
        params.append(int(year_to))

    clause = "WHERE " + " AND ".join(where) + " AND pub_date IS NOT NULL"

    with get_conn() as conn:
        # Fetch qualifying articles
        node_rows = conn.execute(f"""
            SELECT id, title, authors, pub_date, journal,
                   internal_cited_by_count, internal_cites_count
            FROM articles
            {clause}
            ORDER BY pub_date ASC
        """, params).fetchall()

        if not node_rows:
            return {"windows": [], "snapshot": None,
                    "stats": {"total_windows": 0, "year_range": [],
                              "window_size": window_size,
                              "total_articles": 0, "total_citations": 0}}

        # Fetch citation links where both endpoints are in our set
        link_rows = conn.execute(f"""
            WITH filtered AS (
                SELECT id FROM articles {clause}
            )
            SELECT c.source_article_id AS source,
                   c.target_article_id AS target
            FROM citations c
            WHERE c.source_article_id IN (SELECT id FROM filtered)
              AND c.target_article_id IN (SELECT id FROM filtered)
        """, params).fetchall()

    # ── Build lookup structures ──
    node_year = {}
    node_map  = {}
    for r in node_rows:
        yr_str = (r["pub_date"] or "")[:4]
        if yr_str.isdigit():
            yr = int(yr_str)
            node_year[r["id"]] = yr
            node_map[r["id"]] = dict(r)

    # Edge list with the year the citation "appeared" (citing article's year)
    edges_with_years = []
    for r in link_rows:
        src, tgt = r["source"], r["target"]
        src_yr = node_year.get(src)
        tgt_yr = node_year.get(tgt)
        if src_yr is not None and tgt_yr is not None:
            edges_with_years.append((src, tgt, src_yr))

    all_years = sorted(set(node_year.values()))
    if not all_years:
        return {"windows": [], "snapshot": None,
                "stats": {"total_windows": 0, "year_range": [],
                          "window_size": window_size,
                          "total_articles": 0, "total_citations": 0}}

    min_yr, max_yr = all_years[0], all_years[-1]

    # ── Generate time windows ──
    window_starts = list(range(min_yr, max_yr + 1, window_size))

    prev_node_set = set()
    prev_edge_set = set()
    cum_nodes = set()
    cum_edges = set()
    windows = []

    for w_start in window_starts:
        w_end = w_start + window_size - 1
        label = str(w_start) if window_size == 1 else f"{w_start}\u2013{w_end}"

        # Nodes published in this window
        w_nodes = {nid for nid, yr in node_year.items()
                   if w_start <= yr <= w_end}

        # Edges where both endpoints published by w_end and citing article <= w_end
        # Per-window: only among nodes in this window
        pw_edges = set()
        for src, tgt, eyr in edges_with_years:
            if src in w_nodes and tgt in w_nodes:
                pw_edges.add((src, tgt))

        # Cumulative: all nodes/edges up to this window
        cum_nodes |= w_nodes
        for src, tgt, eyr in edges_with_years:
            if eyr <= w_end and src in cum_nodes and tgt in cum_nodes:
                cum_edges.add((src, tgt))

        # Build per-window graph
        G_pw = nx.Graph()
        G_pw.add_nodes_from(w_nodes)
        for s, t in pw_edges:
            G_pw.add_edge(s, t)

        n = G_pw.number_of_nodes()
        m = G_pw.number_of_edges()

        # Per-window metrics
        density = nx.density(G_pw) if n > 1 else 0.0
        avg_deg = (2.0 * m / n) if n > 0 else 0.0
        trans = nx.transitivity(G_pw) if n >= 3 else 0.0

        # Giant component
        gcc_frac = 0.0
        num_comp = 0
        gcc_size = 0
        if n > 0:
            components = list(nx.connected_components(G_pw))
            gcc = max(components, key=len)
            gcc_size = len(gcc)
            gcc_frac = gcc_size / n
            num_comp = len(components)

        # Modularity via Louvain (if enough nodes)
        mod_score = None
        if n >= 10 and m >= 5:
            try:
                comm_sets = louvain_communities(G_pw, seed=42)
                if len(comm_sets) > 1:
                    mod_score = round(nx_modularity(G_pw, comm_sets), 4)
            except Exception:
                pass

        # Average path length in GCC (gate for performance)
        avg_pl = None
        if gcc_size >= 3 and gcc_size <= 1500:
            try:
                gcc_sub = G_pw.subgraph(gcc)
                avg_pl = round(nx.average_shortest_path_length(gcc_sub), 3)
            except Exception:
                pass

        # New nodes/edges vs previous window
        new_n = len(w_nodes - prev_node_set)
        new_e = len(pw_edges - prev_edge_set)

        # Cumulative metrics
        cn = len(cum_nodes)
        cm = len(cum_edges)
        cum_dens = (2.0 * cm / (cn * (cn - 1))) if cn > 1 else 0.0

        # Cumulative giant component (build graph for it)
        cum_gcc_frac = 0.0
        if cn > 0 and cn <= 8000:
            G_cum = nx.Graph()
            G_cum.add_nodes_from(cum_nodes)
            G_cum.add_edges_from(cum_edges)
            cum_comps = list(nx.connected_components(G_cum))
            cum_gcc = max(cum_comps, key=len)
            cum_gcc_frac = len(cum_gcc) / cn

        windows.append({
            "year":                 w_start,
            "window_label":         label,
            "node_count":           n,
            "edge_count":           m,
            "density":              round(density, 6),
            "avg_degree":           round(avg_deg, 3),
            "transitivity":         round(trans, 4),
            "giant_component_frac": round(gcc_frac, 4),
            "num_components":       num_comp,
            "modularity":           mod_score,
            "avg_path_length":      avg_pl,
            "new_nodes":            new_n,
            "new_edges":            new_e,
            "cum_node_count":       cn,
            "cum_edge_count":       cm,
            "cum_density":          round(cum_dens, 6),
            "cum_giant_frac":       round(cum_gcc_frac, 4),
        })

        prev_node_set = w_nodes
        prev_edge_set = pw_edges

    # ── Optional snapshot for force-directed graph ──
    snapshot = None
    if snapshot_year is not None:
        sy = int(snapshot_year)
        snap_nodes = {nid for nid, yr in node_year.items() if yr <= sy}
        snap_edges = [(s, t) for s, t, eyr in edges_with_years
                      if eyr <= sy and s in snap_nodes and t in snap_nodes]

        # Cap to max_nodes_per_window by highest-cited
        if len(snap_nodes) > max_nodes_per_window:
            ranked = sorted(snap_nodes,
                            key=lambda nid: node_map.get(nid, {}).get(
                                "internal_cited_by_count", 0),
                            reverse=True)
            snap_nodes = set(ranked[:max_nodes_per_window])
            snap_edges = [(s, t) for s, t in snap_edges
                          if s in snap_nodes and t in snap_nodes]

        G_snap = nx.Graph()
        G_snap.add_nodes_from(snap_nodes)
        for s, t in snap_edges:
            G_snap.add_edge(s, t)
        # Remove isolates for cleaner display
        G_snap.remove_nodes_from(list(nx.isolates(G_snap)))

        snap_node_list = []
        for nid in G_snap.nodes():
            info = node_map.get(nid, {})
            snap_node_list.append({
                "id":     nid,
                "title":  info.get("title", ""),
                "authors": info.get("authors", ""),
                "pub_date": info.get("pub_date", ""),
                "journal": info.get("journal", ""),
                "internal_cited_by_count": info.get("internal_cited_by_count", 0),
                "degree":  G_snap.degree(nid),
            })
        snap_link_list = [{"source": u, "target": v}
                          for u, v in G_snap.edges()]

        snapshot = {
            "year":  sy,
            "nodes": snap_node_list,
            "links": snap_link_list,
        }

    return {
        "windows":  windows,
        "snapshot": snapshot,
        "stats": {
            "total_windows":  len(windows),
            "year_range":     [min_yr, max_yr],
            "window_size":    window_size,
            "total_articles": len(node_year),
            "total_citations": len(edges_with_years),
        },
    }


# ── Reading path ───────────────────────────────────────────────────────────────

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


def get_reading_path(article_id):
    """
    Build a reading path around a seed article.

    Assembles four relationship sets (backward citations, forward
    citations, co-citation neighbours, bibliographic coupling neighbours),
    computes a relevance score for each unique article, and returns
    a ranked reading list plus a graph structure for D3 rendering.

    Returns { seed, cites, cited_by, cocited, coupled,
              reading_list, graph, stats }
    """
    with get_conn() as conn:
        # ── Seed article ──
        seed_row = conn.execute(
            "SELECT * FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        if not seed_row:
            return {"error": "Article not found"}
        seed = dict(seed_row)
        seed_tags = set((seed.get("tags") or "").strip("|").split("|"))
        seed_tags.discard("")

        # ── 3a. Backward citations (what the seed cites) ──
        cites_rows = conn.execute("""
            SELECT a.id, a.title, a.authors, a.journal, a.pub_date,
                   a.doi, a.url, a.internal_cited_by_count, a.tags
            FROM articles a
            JOIN citations c ON c.target_article_id = a.id
            WHERE c.source_article_id = ?
            ORDER BY a.pub_date DESC
        """, (article_id,)).fetchall()
        cites = [dict(r) for r in cites_rows]

        # ── 3b. Forward citations (what cites the seed) ──
        cited_by_rows = conn.execute("""
            SELECT a.id, a.title, a.authors, a.journal, a.pub_date,
                   a.doi, a.url, a.internal_cited_by_count, a.tags
            FROM articles a
            JOIN citations c ON c.source_article_id = a.id
            WHERE c.target_article_id = ?
            ORDER BY a.internal_cited_by_count DESC
        """, (article_id,)).fetchall()
        cited_by = [dict(r) for r in cited_by_rows]

        # ── 3c. Co-citation neighbours ──
        # Articles X where some article C cites both seed and X
        cocited_rows = conn.execute("""
            SELECT a.id, a.title, a.authors, a.journal, a.pub_date,
                   a.doi, a.url, a.internal_cited_by_count, a.tags,
                   COUNT(DISTINCT c1.source_article_id) AS cocitation_count
            FROM citations c1
            JOIN citations c2
              ON c1.source_article_id = c2.source_article_id
             AND c2.target_article_id = ?
            JOIN articles a ON a.id = c1.target_article_id
            WHERE c1.target_article_id != ?
              AND c1.target_article_id IS NOT NULL
            GROUP BY c1.target_article_id
            ORDER BY cocitation_count DESC
            LIMIT 20
        """, (article_id, article_id)).fetchall()
        cocited = [dict(r) for r in cocited_rows]

        # ── 3d. Bibliographic coupling neighbours ──
        # Articles X where both seed and X cite the same article R
        coupled_rows = conn.execute("""
            SELECT a.id, a.title, a.authors, a.journal, a.pub_date,
                   a.doi, a.url, a.internal_cited_by_count, a.tags,
                   COUNT(DISTINCT c1.target_article_id) AS coupling_count
            FROM citations c1
            JOIN citations c2
              ON c1.target_article_id = c2.target_article_id
             AND c2.source_article_id = ?
            JOIN articles a ON a.id = c1.source_article_id
            WHERE c1.source_article_id != ?
              AND c1.target_article_id IS NOT NULL
            GROUP BY c1.source_article_id
            ORDER BY coupling_count DESC
            LIMIT 20
        """, (article_id, article_id)).fetchall()
        coupled = [dict(r) for r in coupled_rows]

    # ── 3e. Deduplication and scoring ──
    articles_map = {}  # id → {article data + score info}

    def _add(art, rel_type, weight=1):
        aid = art["id"]
        if aid == article_id:
            return
        if aid not in articles_map:
            art_tags = set((art.get("tags") or "").strip("|").split("|"))
            art_tags.discard("")
            articles_map[aid] = {
                "id":       aid,
                "title":    art.get("title", ""),
                "authors":  art.get("authors", ""),
                "journal":  art.get("journal", ""),
                "pub_date": art.get("pub_date", ""),
                "doi":      art.get("doi", ""),
                "url":      art.get("url", ""),
                "internal_cited_by_count": art.get("internal_cited_by_count", 0),
                "relationships": [],
                "score":    0,
                "shared_tags": bool(seed_tags & art_tags),
                "same_journal": (art.get("journal") or "") == (seed.get("journal") or ""),
            }
        entry = articles_map[aid]
        entry["relationships"].append({"type": rel_type, "weight": weight})

    # Add each set
    for art in cites:
        _add(art, "cites")
    for art in cited_by:
        _add(art, "cited_by")
    for art in cocited:
        _add(art, "cocited", art.get("cocitation_count", 1))
    for art in coupled:
        _add(art, "coupled", art.get("coupling_count", 1))

    # Compute scores
    for entry in articles_map.values():
        score = 0
        for rel in entry["relationships"]:
            if rel["type"] == "cites":
                score += 2
            elif rel["type"] == "cited_by":
                score += 2
            elif rel["type"] == "cocited":
                score += min(rel["weight"], 5)
            elif rel["type"] == "coupled":
                score += min(rel["weight"], 5)
        if entry["shared_tags"]:
            score += 1
        if entry["same_journal"]:
            score += 1
        entry["score"] = score

    # Build reading list sorted by score
    reading_list = sorted(articles_map.values(), key=lambda x: -x["score"])

    # Build reason strings
    for entry in reading_list:
        parts = []
        rel_types = {r["type"] for r in entry["relationships"]}
        for rel in entry["relationships"]:
            if rel["type"] == "cites":
                parts.append("Cited by this article")
            elif rel["type"] == "cited_by":
                parts.append("Cites this article")
            elif rel["type"] == "cocited":
                parts.append(f"Co-cited {rel['weight']}×")
            elif rel["type"] == "coupled":
                parts.append(f"Shares {rel['weight']} reference{'s' if rel['weight'] != 1 else ''}")
        if entry["shared_tags"]:
            parts.append("shared topic")
        if entry["same_journal"]:
            parts.append("same journal")
        entry["reason"] = "; ".join(parts)
        entry["rel_types"] = sorted(rel_types)

    # ── Build graph ──
    nodes = [{
        "id":    seed["id"],
        "title": seed.get("title", ""),
        "authors": seed.get("authors", ""),
        "journal": seed.get("journal", ""),
        "pub_date": seed.get("pub_date", ""),
        "internal_cited_by_count": seed.get("internal_cited_by_count", 0),
        "is_seed": True,
        "rel_types": ["seed"],
    }]
    node_ids = {seed["id"]}

    for entry in reading_list:
        if entry["id"] not in node_ids:
            nodes.append({
                "id":    entry["id"],
                "title": entry["title"],
                "authors": entry["authors"],
                "journal": entry["journal"],
                "pub_date": entry["pub_date"],
                "internal_cited_by_count": entry["internal_cited_by_count"],
                "is_seed": False,
                "rel_types": entry["rel_types"],
                "score": entry["score"],
            })
            node_ids.add(entry["id"])

    links = []
    for art in cites:
        if art["id"] in node_ids:
            links.append({
                "source": article_id, "target": art["id"],
                "type": "cites", "weight": 1,
            })
    for art in cited_by:
        if art["id"] in node_ids:
            links.append({
                "source": art["id"], "target": article_id,
                "type": "cited_by", "weight": 1,
            })
    for art in cocited:
        if art["id"] in node_ids:
            links.append({
                "source": article_id, "target": art["id"],
                "type": "cocited", "weight": art.get("cocitation_count", 1),
            })
    for art in coupled:
        if art["id"] in node_ids:
            links.append({
                "source": article_id, "target": art["id"],
                "type": "coupled", "weight": art.get("coupling_count", 1),
            })

    return {
        "seed": {
            "id":       seed["id"],
            "title":    seed.get("title", ""),
            "authors":  seed.get("authors", ""),
            "journal":  seed.get("journal", ""),
            "pub_date": seed.get("pub_date", ""),
            "doi":      seed.get("doi", ""),
            "url":      seed.get("url", ""),
            "abstract": seed.get("abstract", ""),
            "tags":     seed.get("tags", ""),
            "internal_cited_by_count": seed.get("internal_cited_by_count", 0),
        },
        "cites":        [{"id": a["id"], "title": a["title"], "authors": a["authors"],
                          "journal": a["journal"], "pub_date": a["pub_date"],
                          "doi": a["doi"]} for a in cites],
        "cited_by":     [{"id": a["id"], "title": a["title"], "authors": a["authors"],
                          "journal": a["journal"], "pub_date": a["pub_date"],
                          "doi": a["doi"],
                          "internal_cited_by_count": a["internal_cited_by_count"]}
                         for a in cited_by],
        "cocited":      [{"id": a["id"], "title": a["title"], "authors": a["authors"],
                          "journal": a["journal"], "pub_date": a["pub_date"],
                          "doi": a["doi"],
                          "cocitation_count": a["cocitation_count"]} for a in cocited],
        "coupled":      [{"id": a["id"], "title": a["title"], "authors": a["authors"],
                          "journal": a["journal"], "pub_date": a["pub_date"],
                          "doi": a["doi"],
                          "coupling_count": a["coupling_count"]} for a in coupled],
        "reading_list": reading_list,
        "graph":        {"nodes": nodes, "links": links},
        "stats": {
            "cites_count":     len(cites),
            "cited_by_count":  len(cited_by),
            "cocited_count":   len(cocited),
            "coupled_count":   len(coupled),
            "unique_articles": len(articles_map),
        },
    }


# ── Author co-citation ──────────────────────────────────────────────────────

def get_author_cocitation_network(min_cocitations=3, max_authors=200,
                                  year_from=None, year_to=None,
                                  journals=None):
    """
    Build an author-level co-citation network.

    Two authors are co-cited when a third article cites at least one work
    by each of them. The co-citation count for a pair (A, B) is the number
    of distinct citing articles that reference at least one work by A and
    at least one by B.

    Author pairs who appear on the SAME cited article are excluded to
    avoid inflating co-author co-citation counts.

    Returns {nodes, edges, pairs, stats}.
    """
    with get_conn() as conn:
        # ── Build optional filter on the CITING article ──
        cite_where = []
        cite_params = []
        if year_from:
            cite_where.append("citing.pub_date >= ?")
            cite_params.append(f"{year_from}-01-01")
        if year_to:
            cite_where.append("citing.pub_date <= ?")
            cite_params.append(f"{year_to}-12-31")
        if journals:
            if isinstance(journals, list) and journals:
                placeholders = ",".join("?" * len(journals))
                cite_where.append(f"citing.journal IN ({placeholders})")
                cite_params.extend(journals)
            elif isinstance(journals, str):
                cite_where.append("citing.journal = ?")
                cite_params.append(journals)

        cite_clause = ""
        if cite_where:
            cite_clause = "AND " + " AND ".join(cite_where)

        # ── Step 1: Get all (citing_article, cited_article) pairs ──
        # Only include cited articles that have been cited at least 2 times
        # internally (pre-filter for performance) and have known authors.
        rows = conn.execute(f"""
            SELECT c.source_article_id AS citing_id,
                   c.target_article_id AS cited_id,
                   cited.authors       AS cited_authors
            FROM citations c
            JOIN articles citing ON citing.id = c.source_article_id
            JOIN articles cited  ON cited.id  = c.target_article_id
            WHERE c.target_article_id IS NOT NULL
              AND cited.authors IS NOT NULL
              AND cited.authors != ''
              AND cited.internal_cited_by_count >= 2
              {cite_clause}
        """, cite_params).fetchall()

    if not rows:
        return {"nodes": [], "edges": [], "pairs": [],
                "stats": {"total_authors": 0, "total_edges": 0,
                           "max_cocitation": 0}}

    # ── Step 2: Build citing_article → set of (cited_article, author) ──
    # For each citing article, collect which authors it cites (via which articles)
    from collections import defaultdict

    citing_to_author_articles = defaultdict(list)
    # Map: article_id → set of author names (for same-article exclusion)
    article_authors_map = {}

    for r in rows:
        citing_id = r["citing_id"]
        cited_id = r["cited_id"]
        authors_str = r["cited_authors"]
        authors = [a.strip() for a in authors_str.split(";") if a.strip()]
        if not authors:
            continue

        if cited_id not in article_authors_map:
            article_authors_map[cited_id] = set(authors)

        for author in authors:
            citing_to_author_articles[citing_id].append((author, cited_id))

    # ── Step 3: For each citing article, generate author co-citation pairs ──
    # Only pair authors across DIFFERENT cited articles (same-article exclusion)
    pair_counts = defaultdict(int)

    for citing_id, author_article_list in citing_to_author_articles.items():
        # Group by author → set of article IDs they were cited through
        author_articles = defaultdict(set)
        for author, article_id in author_article_list:
            author_articles[author].add(article_id)

        # Get unique authors cited by this citing article
        authors_list = sorted(author_articles.keys())

        for i in range(len(authors_list)):
            for j in range(i + 1, len(authors_list)):
                a1, a2 = authors_list[i], authors_list[j]

                # Same-article exclusion: check if a1 and a2 ONLY co-occur
                # on the same cited articles. If all their cited articles overlap,
                # they're co-authors being inflated — skip.
                a1_articles = author_articles[a1]
                a2_articles = author_articles[a2]

                # Check: is there at least one cited article for a1 that is
                # different from all cited articles for a2?
                # i.e., are they cited through at least one distinct article each?
                shared_articles = a1_articles & a2_articles
                a1_unique = a1_articles - shared_articles
                a2_unique = a2_articles - shared_articles

                # If one author only appears on shared articles, the co-citation
                # is entirely from same-article co-authorship — skip
                if not a1_unique and not a2_unique:
                    continue

                pair_counts[(a1, a2)] += 1

    if not pair_counts:
        return {"nodes": [], "edges": [], "pairs": [],
                "stats": {"total_authors": 0, "total_edges": 0,
                           "max_cocitation": 0}}

    # ── Step 4: Filter by minimum co-citation threshold ──
    filtered_pairs = {k: v for k, v in pair_counts.items() if v >= min_cocitations}

    if not filtered_pairs:
        return {"nodes": [], "edges": [], "pairs": [],
                "stats": {"total_authors": 0, "total_edges": 0,
                           "max_cocitation": 0}}

    # ── Step 5: Compute author strength and select top authors ──
    author_strength = defaultdict(int)
    for (a1, a2), count in filtered_pairs.items():
        author_strength[a1] += count
        author_strength[a2] += count

    # Count articles per author (from the data we already have)
    author_article_count = defaultdict(set)
    for article_id, author_set in article_authors_map.items():
        for author in author_set:
            author_article_count[author].add(article_id)
    author_article_count = {a: len(ids) for a, ids in author_article_count.items()}

    # Top authors by total strength
    top_authors = sorted(author_strength.keys(),
                         key=lambda a: author_strength[a], reverse=True)[:max_authors]
    top_set = set(top_authors)

    # ── Step 6: Build edges (only between top authors) ──
    edges = []
    for (a1, a2), count in filtered_pairs.items():
        if a1 in top_set and a2 in top_set:
            edges.append({"source": a1, "target": a2, "weight": count})

    # Sort edges by weight descending
    edges.sort(key=lambda e: e["weight"], reverse=True)

    # ── Step 7: Determine each author's top journal ──
    # Quick lookup from articles we already loaded
    author_journal_counts = defaultdict(lambda: defaultdict(int))
    with get_conn() as conn:
        for author in top_set:
            journal_rows = conn.execute("""
                SELECT journal, COUNT(*) as cnt
                FROM articles
                WHERE authors LIKE ?
                  AND journal IS NOT NULL
                GROUP BY journal
                ORDER BY cnt DESC
                LIMIT 1
            """, (f"%{author}%",)).fetchall()
            if journal_rows:
                author_journal_counts[author] = journal_rows[0]["journal"]
            else:
                author_journal_counts[author] = ""

    # ── Step 8: Build nodes ──
    nodes = []
    for author in top_authors:
        if author_strength[author] > 0:
            nodes.append({
                "id":   author,
                "name": author,
                "article_count": author_article_count.get(author, 0),
                "total_cocitation_strength": author_strength[author],
                "top_journal": author_journal_counts.get(author, ""),
            })

    # ── Step 9: Build ranked pairs list for the table ──
    pairs_list = []
    for (a1, a2), count in sorted(filtered_pairs.items(),
                                    key=lambda x: -x[1])[:100]:
        if a1 in top_set and a2 in top_set:
            pairs_list.append({
                "author1": a1,
                "author2": a2,
                "cocitation_count": count,
            })

    max_cc = max(filtered_pairs.values()) if filtered_pairs else 0

    return {
        "nodes": nodes,
        "edges": edges,
        "pairs": pairs_list,
        "stats": {
            "total_authors": len(nodes),
            "total_edges":   len(edges),
            "max_cocitation": max_cc,
        },
    }


def get_author_cocitation_partners(author_name, limit=10):
    """
    Get top co-citation partners for a specific author.
    Returns list of {partner, cocitation_count}.
    Used on author profile pages.
    """
    # Compute a lightweight version — only pairs involving this author
    with get_conn() as conn:
        # Get articles by this author that have internal citations
        author_articles = conn.execute("""
            SELECT id FROM articles
            WHERE authors LIKE ? AND internal_cited_by_count >= 2
        """, (f"%{author_name}%",)).fetchall()

        if not author_articles:
            return []

        author_article_ids = {r["id"] for r in author_articles}

        # Find citing articles that cite at least one of this author's works
        placeholders = ",".join("?" * len(author_article_ids))
        citing_rows = conn.execute(f"""
            SELECT DISTINCT c.source_article_id
            FROM citations c
            WHERE c.target_article_id IN ({placeholders})
        """, list(author_article_ids)).fetchall()

        citing_ids = [r["source_article_id"] for r in citing_rows]
        if not citing_ids:
            return []

        # For each citing article, get all OTHER cited articles and their authors
        cite_ph = ",".join("?" * len(citing_ids))
        all_cited = conn.execute(f"""
            SELECT c.source_article_id AS citing_id,
                   a.id AS cited_id,
                   a.authors
            FROM citations c
            JOIN articles a ON a.id = c.target_article_id
            WHERE c.source_article_id IN ({cite_ph})
              AND c.target_article_id IS NOT NULL
              AND a.authors IS NOT NULL AND a.authors != ''
        """, citing_ids).fetchall()

    # Build partner counts
    from collections import defaultdict
    partner_counts = defaultdict(int)

    # Group by citing article
    citing_groups = defaultdict(list)
    for r in all_cited:
        citing_groups[r["citing_id"]].append(r)

    for citing_id, cited_list in citing_groups.items():
        # Check if this citing article cites the target author
        cites_target = False
        for r in cited_list:
            if r["cited_id"] in author_article_ids:
                cites_target = True
                break

        if not cites_target:
            continue

        # Collect other authors cited by this same citing article
        other_authors = set()
        for r in cited_list:
            if r["cited_id"] not in author_article_ids:
                authors = [a.strip() for a in r["authors"].split(";") if a.strip()]
                other_authors.update(authors)

        # Remove the target author themselves
        other_authors.discard(author_name)

        for partner in other_authors:
            partner_counts[partner] += 1

    # Sort by count and return top N
    ranked = sorted(partner_counts.items(), key=lambda x: -x[1])[:limit]
    return [{"partner": name, "cocitation_count": count}
            for name, count in ranked if count >= 2]
