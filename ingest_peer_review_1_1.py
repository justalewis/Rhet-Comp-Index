"""
ingest_peer_review_1_1.py

One-time script: imports The Peer Review issue 1.1 articles and their
reference lists from peer_review_references.json into articles.db.

For references with a real DOI, the DOI is stored as target_doi and
target_article_id is resolved if the cited article is already in our index.

For references without a DOI, a synthetic key "raw:<sha256[:16]>" is stored
as target_doi so the NOT NULL / UNIQUE constraints are satisfied.  The raw
reference text is preserved in the raw_reference column for display.
"""

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "articles.db")
JSON_PATH = os.path.join(os.path.dirname(__file__), "peer_review_references.json")

# Issue 1.1 publication year — The Peer Review launched in 2017
ISSUE_PUB_DATE = "2017"
JOURNAL_NAME   = "The Peer Review"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def build_doi_map(conn):
    """Return {normalised_doi: article_id} for every indexed article with a DOI."""
    rows = conn.execute(
        "SELECT id, doi FROM articles WHERE doi IS NOT NULL"
    ).fetchall()
    return {r["doi"].strip().lower(): r["id"] for r in rows}


def synthetic_doi(raw_text: str) -> str:
    """Stable synthetic key for a reference that has no real DOI."""
    h = hashlib.sha256(raw_text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"raw:{h}"


def upsert_article(conn, article: dict) -> int:
    """Insert article if URL not already present; return its id."""
    existing = conn.execute(
        "SELECT id FROM articles WHERE url = ?", (article["url"],)
    ).fetchone()
    if existing:
        log.info("  Already in DB (id=%d): %s", existing["id"], article["title"][:60])
        return existing["id"]

    authors_str = "; ".join(article.get("article_authors") or [])
    conn.execute("""
        INSERT INTO articles
            (url, doi, title, authors, pub_date, journal, source, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, 'manual', datetime('now'))
    """, (
        article["url"],
        None,
        article["article_title"],
        authors_str,
        ISSUE_PUB_DATE,
        JOURNAL_NAME,
    ))
    art_id = conn.execute(
        "SELECT id FROM articles WHERE url = ?", (article["url"],)
    ).fetchone()["id"]

    # Keep FTS in sync (trigger handles INSERT normally, but just in case)
    log.info("  Inserted article id=%d: %s", art_id, article["article_title"][:60])
    return art_id


def ingest_references(conn, source_id: int, refs: list, doi_map: dict) -> tuple[int, int]:
    """
    Insert citation rows for each reference.
    Returns (inserted, skipped) counts.
    """
    inserted = skipped = 0
    for ref in refs:
        raw_text = ref.get("raw") or ""

        # Resolve target_doi
        real_doi = ref.get("doi")
        if real_doi:
            target_doi = real_doi.strip().lower()
        else:
            target_doi = synthetic_doi(raw_text or json.dumps(ref))

        # Try to link to an indexed article
        target_article_id = doi_map.get(target_doi) if real_doi else None

        try:
            conn.execute("""
                INSERT OR IGNORE INTO citations
                    (source_article_id, target_doi, target_article_id, raw_reference)
                VALUES (?, ?, ?, ?)
            """, (
                source_id,
                target_doi,
                target_article_id,
                json.dumps(ref),
            ))
            changes = conn.execute("SELECT changes()").fetchone()[0]
            if changes:
                inserted += 1
            else:
                skipped += 1
        except sqlite3.Error as e:
            log.warning("    Could not insert ref (%s): %s", target_doi[:30], e)
            skipped += 1

    return inserted, skipped


def update_article_references_fetched(conn, article_id: int):
    conn.execute(
        "UPDATE articles SET references_fetched_at = datetime('now') WHERE id = ?",
        (article_id,)
    )


def refresh_citation_counts(conn):
    """Recalculate internal_cited_by_count and internal_cites_count for all articles."""
    conn.execute("""
        UPDATE articles
        SET internal_cited_by_count = (
            SELECT COUNT(*) FROM citations WHERE target_article_id = articles.id
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
    log.info("Citation counts refreshed.")


def main():
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    log.info("Loaded %d articles from %s", len(data), JSON_PATH)

    conn = get_conn()
    doi_map = build_doi_map(conn)
    log.info("Indexed DOI map: %d entries", len(doi_map))

    total_ins = total_skip = 0

    for article in data:
        log.info("Processing: %s", article["article_title"][:70])
        art_id = upsert_article(conn, article)
        conn.commit()

        ins, skip = ingest_references(conn, art_id, article.get("references") or [], doi_map)
        update_article_references_fetched(conn, art_id)
        conn.commit()

        log.info("  References: %d inserted, %d skipped", ins, skip)
        total_ins += ins
        total_skip += skip

    refresh_citation_counts(conn)
    conn.commit()
    conn.close()

    log.info("")
    log.info("Done.  %d references inserted, %d skipped.", total_ins, total_skip)


if __name__ == "__main__":
    main()
