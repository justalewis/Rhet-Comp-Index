"""
backfill_abstracts.py — Re-query OpenAlex for articles missing abstracts.

OpenAlex has expanded significantly since the initial enrichment pass.
This script:
  1. Clears stub "Preview this article..." abstracts to NULL
  2. Resets openalex_enriched_at for articles that were previously 404
     or that have stub abstracts (so they get re-queried)
  3. Runs the existing enrich_openalex() machinery
  4. Re-tags articles that gained abstracts
  5. Rebuilds the FTS index

Usage:
    python backfill_abstracts.py              # full run
    python backfill_abstracts.py --prep-only  # just reset flags, don't fetch
    python backfill_abstracts.py --limit 500  # process at most N articles
"""

import argparse
import logging
import time

from db import get_conn, init_db
from enrich_openalex import (
    decode_abstract, _fetch_with_retry, _name_matches,
    _strip_orcid_prefix, OPENALEX_BASE, CONTACT_EMAIL, REQUEST_DELAY,
)
from tagger import auto_tag
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def prep_for_backfill():
    """
    Reset enrichment flags so the enrichment pass will re-query OpenAlex
    for articles that previously had no data.
    """
    init_db()

    with get_conn() as conn:
        conn.execute("PRAGMA busy_timeout = 60000")

        # 1. Clear stub abstracts — these are JSTOR preview placeholders
        stub_count = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE abstract LIKE 'Preview this article%'"
        ).fetchone()[0]
        if stub_count:
            conn.execute(
                "UPDATE articles SET abstract = NULL WHERE abstract LIKE 'Preview this article%'"
            )
            log.info("Cleared %d stub 'Preview...' abstracts to NULL.", stub_count)

        # 2. Reset enrichment flag for articles that were previously 404
        #    (have openalex_enriched_at but no openalex_id)
        prev_404 = conn.execute("""
            SELECT COUNT(*) FROM articles
            WHERE openalex_enriched_at IS NOT NULL
              AND (openalex_id IS NULL OR openalex_id = '')
              AND doi IS NOT NULL AND doi != ''
        """).fetchone()[0]
        if prev_404:
            conn.execute("""
                UPDATE articles SET openalex_enriched_at = NULL
                WHERE openalex_enriched_at IS NOT NULL
                  AND (openalex_id IS NULL OR openalex_id = '')
                  AND doi IS NOT NULL AND doi != ''
            """)
            log.info("Reset enrichment flag for %d previously-404 DOIs.", prev_404)

        # 3. Reset enrichment flag for stub-abstract articles that were
        #    "enriched" but OpenAlex couldn't overwrite the non-empty stub
        stub_enriched = conn.execute("""
            SELECT COUNT(*) FROM articles
            WHERE abstract IS NULL
              AND openalex_enriched_at IS NOT NULL
              AND openalex_id IS NOT NULL AND openalex_id != ''
              AND doi IS NOT NULL AND doi != ''
        """).fetchone()[0]
        # These are articles where we just cleared the stub, but they
        # already have an openalex_id. We need to re-query to get the abstract.
        if stub_enriched:
            conn.execute("""
                UPDATE articles SET openalex_enriched_at = NULL
                WHERE abstract IS NULL
                  AND openalex_enriched_at IS NOT NULL
                  AND openalex_id IS NOT NULL AND openalex_id != ''
                  AND doi IS NOT NULL AND doi != ''
            """)
            log.info("Reset enrichment flag for %d ex-stub articles with OpenAlex IDs.", stub_enriched)

        conn.commit()

    # Count how many are now ready for enrichment
    with get_conn() as conn:
        ready = conn.execute("""
            SELECT COUNT(*) FROM articles
            WHERE doi IS NOT NULL AND doi != ''
              AND openalex_enriched_at IS NULL
        """).fetchone()[0]
        log.info("Total articles ready for OpenAlex re-enrichment: %d", ready)

    return ready


def enrich_batch(limit=None):
    """
    Fetch abstracts (and other metadata) from OpenAlex for all articles
    where openalex_enriched_at IS NULL and a DOI exists.

    This is a focused version of enrich_openalex() that also re-tags
    articles gaining abstracts.
    """
    with get_conn() as conn:
        conn.execute("PRAGMA busy_timeout = 60000")
        query = """
            SELECT id, doi, authors, abstract, oa_url
            FROM articles
            WHERE doi IS NOT NULL AND doi != ''
              AND openalex_enriched_at IS NULL
            ORDER BY pub_date DESC
        """
        if limit:
            query += f" LIMIT {int(limit)}"
        rows = conn.execute(query).fetchall()

    total = len(rows)
    log.info("OpenAlex backfill: %d articles to process.", total)

    stats = {
        "processed": 0,
        "abstracts_filled": 0,
        "oa_status_set": 0,
        "affiliations_written": 0,
        "tags_updated": 0,
        "skipped_429": 0,
    }
    consecutive_429s = 0

    for row in rows:
        article_id  = row["id"]
        doi         = row["doi"].strip()
        current_abs = row["abstract"]
        current_oa  = row["oa_url"]
        authors_str = row["authors"] or ""

        url = f"{OPENALEX_BASE}/https://doi.org/{doi}?mailto={CONTACT_EMAIL}"

        try:
            data, status = _fetch_with_retry(url)
        except Exception as exc:
            log.error("Error fetching article %d (doi=%s): %s", article_id, doi, exc)
            _mark_done(article_id)
            stats["processed"] += 1
            time.sleep(REQUEST_DELAY)
            continue

        if data is None:
            if status == 429:
                # Don't mark as done — leave in queue for next run
                consecutive_429s += 1
                stats["skipped_429"] += 1
                if consecutive_429s >= 10:
                    wait = min(300, 30 * (consecutive_429s // 10))
                    log.warning("Hit %d consecutive 429s — pausing %ds…",
                                consecutive_429s, wait)
                    time.sleep(wait)
                    if consecutive_429s >= 50:
                        log.error("50+ consecutive 429s — aborting to avoid burning queue.")
                        break
                stats["processed"] += 1
                time.sleep(REQUEST_DELAY)
                continue
            # 404 or other error — mark as done so we don't re-query
            _mark_done(article_id)
            consecutive_429s = 0
            stats["processed"] += 1
            time.sleep(REQUEST_DELAY)
            continue

        consecutive_429s = 0

        # ── Extract fields ──────────────────────────────────────────
        openalex_id = data.get("id") or None

        new_abstract = None
        if not current_abs or current_abs.strip() == "":
            inv_idx = data.get("abstract_inverted_index")
            new_abstract = decode_abstract(inv_idx)

        oa_block = data.get("open_access") or {}
        oa_status = oa_block.get("oa_status") or None

        new_oa_url = None
        if not current_oa:
            new_oa_url = oa_block.get("oa_url") or None
            if not new_oa_url:
                best = data.get("best_oa_location") or {}
                new_oa_url = best.get("pdf_url") or best.get("landing_page_url") or None

        # ── Write article fields ────────────────────────────────────
        with get_conn() as conn:
            conn.execute("PRAGMA busy_timeout = 60000")
            updates = []
            params  = []

            if openalex_id:
                updates.append("openalex_id = ?")
                params.append(openalex_id)

            if new_abstract:
                updates.append("abstract = ?")
                params.append(new_abstract)
                stats["abstracts_filled"] += 1

            if oa_status:
                updates.append("oa_status = ?")
                params.append(oa_status)
                stats["oa_status_set"] += 1

            if new_oa_url:
                updates.append("oa_url = ?")
                params.append(new_oa_url)

            updates.append("openalex_enriched_at = ?")
            params.append(datetime.utcnow().isoformat())

            params.append(article_id)
            conn.execute(
                f"UPDATE articles SET {', '.join(updates)} WHERE id = ?",
                params,
            )

            # Re-tag if we got a new abstract
            if new_abstract:
                title_row = conn.execute(
                    "SELECT title FROM articles WHERE id = ?", (article_id,)
                ).fetchone()
                if title_row:
                    new_tags = auto_tag(title_row["title"], new_abstract)
                    conn.execute(
                        "UPDATE articles SET tags = ? WHERE id = ?",
                        (new_tags, article_id),
                    )
                    stats["tags_updated"] += 1

            conn.commit()

        # ── Author affiliations ─────────────────────────────────────
        authorships = data.get("authorships") or []
        for authorship in authorships:
            author_block = authorship.get("author") or {}
            oa_author_id = author_block.get("id") or None
            display_name = author_block.get("display_name") or ""
            orcid = _strip_orcid_prefix(author_block.get("orcid"))

            institutions = authorship.get("institutions") or []
            inst = institutions[0] if institutions else {}
            inst_name = inst.get("display_name") or None
            inst_ror  = inst.get("ror") or None
            raw_aff   = "; ".join(authorship.get("raw_affiliation_strings") or []) or None

            if not _name_matches(display_name, authors_str):
                continue

            try:
                with get_conn() as conn:
                    conn.execute("PRAGMA busy_timeout = 60000")
                    conn.execute("""
                        INSERT INTO authors (name, openalex_id, orcid, institution_name, institution_ror)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(name) DO UPDATE SET
                            openalex_id      = COALESCE(openalex_id,      excluded.openalex_id),
                            orcid            = COALESCE(orcid,            excluded.orcid),
                            institution_name = excluded.institution_name,
                            institution_ror  = excluded.institution_ror
                    """, (display_name, oa_author_id, orcid, inst_name, inst_ror))
                    conn.execute("""
                        INSERT INTO author_article_affiliations
                            (article_id, author_name, openalex_author_id,
                             institution_name, institution_ror, raw_affiliation_string)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(article_id, author_name) DO UPDATE SET
                            openalex_author_id     = excluded.openalex_author_id,
                            institution_name       = excluded.institution_name,
                            institution_ror        = excluded.institution_ror,
                            raw_affiliation_string = excluded.raw_affiliation_string
                    """, (article_id, display_name, oa_author_id,
                          inst_name, inst_ror, raw_aff))
                    conn.commit()
                    stats["affiliations_written"] += 1
            except Exception as exc:
                log.warning("Affiliation UPSERT failed for '%s' article %d: %s",
                            display_name, article_id, exc)

        stats["processed"] += 1
        if stats["processed"] % 100 == 0:
            log.info("  %d/%d processed | %d abstracts | %d tags | %d affiliations",
                     stats["processed"], total,
                     stats["abstracts_filled"], stats["tags_updated"],
                     stats["affiliations_written"])

        time.sleep(REQUEST_DELAY)

    # ── Final FTS rebuild ───────────────────────────────────────────
    if stats["abstracts_filled"] > 0:
        log.info("Rebuilding FTS index...")
        with get_conn() as conn:
            conn.execute("PRAGMA busy_timeout = 60000")
            conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
            conn.commit()
        log.info("FTS rebuild complete.")

    log.info("")
    log.info("=== Abstract Backfill Complete ===")
    log.info("Articles processed:    %d", stats["processed"])
    log.info("Abstracts filled:      %d", stats["abstracts_filled"])
    log.info("Tags updated:          %d", stats["tags_updated"])
    log.info("OA status set:         %d", stats["oa_status_set"])
    log.info("Affiliations written:  %d", stats["affiliations_written"])

    return stats


def _mark_done(article_id):
    """Stamp openalex_enriched_at without writing other fields."""
    try:
        with get_conn() as conn:
            conn.execute("PRAGMA busy_timeout = 60000")
            conn.execute(
                "UPDATE articles SET openalex_enriched_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), article_id),
            )
            conn.commit()
    except Exception as exc:
        log.warning("Could not mark article %d as done: %s", article_id, exc)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill abstracts from OpenAlex for articles that were previously missed."
    )
    parser.add_argument("--prep-only", action="store_true",
                        help="Just reset flags, don't fetch from OpenAlex.")
    parser.add_argument("--limit", type=int, default=None,
                        help="Process at most N articles.")
    args = parser.parse_args()

    ready = prep_for_backfill()

    if args.prep_only:
        log.info("Prep complete. Run without --prep-only to fetch from OpenAlex.")
    else:
        enrich_batch(limit=args.limit)
