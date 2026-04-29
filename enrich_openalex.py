"""
enrich_openalex.py — OpenAlex enrichment for Rhet-Comp Index.

For each article with a DOI that has not yet been enriched via OpenAlex, fetches:
  - Abstract (decoded from inverted index)
  - Open-access status and URL
  - OpenAlex work ID
  - Author affiliations (institution name, ROR, ORCID)

Writes results to:
  - articles.abstract              (if currently NULL/empty)
  - articles.oa_status             (gold/green/hybrid/bronze/closed)
  - articles.oa_url                (if currently NULL)
  - articles.openalex_id
  - articles.openalex_enriched_at  (ISO timestamp, marks the article as done)
  - authors                        (UPSERT: openalex_id, orcid, institution)
  - author_article_affiliations    (UPSERT: per-article affiliation record)

Usage:
  python enrich_openalex.py          # enrich all unenriched articles
  python enrich_openalex.py --help   # show options

Also importable as enrich_openalex() for use by scheduler.py.
"""

import argparse
import logging
import time
import urllib.request
import urllib.error
import json
from datetime import datetime

from db import get_conn, init_db
from monitoring import capture_fetcher_error

SOURCE_NAME = "openalex"

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

CONTACT_EMAIL  = "rhetcompindex@gmail.com"
OPENALEX_BASE  = "https://api.openalex.org/works"
REQUEST_DELAY  = 0.1   # seconds between requests (polite pool)
RETRY_DELAY    = 5     # seconds before retry on 429/5xx
BATCH_SIZE     = 50


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_json(url, timeout=15):
    """
    Fetch JSON from a URL with polite headers.
    Returns (data_dict, http_status) or (None, status) on error.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": f"RhetCompIndex/1.0 (mailto:{CONTACT_EMAIL})",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), resp.status
    except urllib.error.HTTPError as e:
        return None, e.code
    except Exception as e:
        log.warning("Request failed for %s: %s", url, e)
        return None, None


def _fetch_with_retry(url):
    """
    Fetch a URL with one retry on 429 or 5xx.
    Returns (data_dict_or_None, final_status).
    """
    data, status = _get_json(url)
    if status in (429,) or (status is not None and status >= 500):
        log.warning("HTTP %s for %s — waiting %ds then retrying…", status, url, RETRY_DELAY)
        time.sleep(RETRY_DELAY)
        data, status = _get_json(url)
        if status in (429,) or (status is not None and status >= 500):
            log.warning("Retry also failed (HTTP %s) for %s — skipping.", status, url)
            return None, status
    return data, status


def decode_abstract(inverted_index):
    """
    Reconstruct plain-text abstract from OpenAlex inverted index format.
    The inverted index is {word: [position, ...], ...}.
    """
    if not inverted_index:
        return None
    positions = []
    for word, pos_list in inverted_index.items():
        for pos in pos_list:
            positions.append((pos, word))
    positions.sort(key=lambda x: x[0])
    return " ".join(word for _, word in positions) or None


def _name_matches(openalex_name, article_authors_str):
    """
    Case-insensitive check: does openalex_name appear as a semicolon-separated
    token in article_authors_str?  Uses exact match after lowercasing.
    """
    if not openalex_name or not article_authors_str:
        return False
    needle = openalex_name.strip().lower()
    for token in article_authors_str.split(";"):
        if token.strip().lower() == needle:
            return True
    return False


def _strip_orcid_prefix(orcid_url):
    """Convert 'https://orcid.org/0000-0001-2345-6789' → '0000-0001-2345-6789'."""
    if not orcid_url:
        return None
    prefix = "https://orcid.org/"
    if orcid_url.startswith(prefix):
        return orcid_url[len(prefix):]
    return orcid_url


# ── Core enrichment ────────────────────────────────────────────────────────────

def enrich_openalex():
    """
    Main enrichment function. Processes all articles with a DOI where
    openalex_enriched_at IS NULL, in batches of BATCH_SIZE.

    Returns a summary dict with counts.
    """
    init_db()

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, doi, authors, abstract, oa_url
            FROM articles
            WHERE doi IS NOT NULL AND doi != ''
              AND openalex_enriched_at IS NULL
            ORDER BY pub_date DESC
        """).fetchall()

    total = len(rows)
    log.info("OpenAlex: %d articles to process…", total)

    stats = {
        "processed":           0,
        "abstracts_filled":    0,
        "oa_status_set":       0,
        "affiliations_written":0,
        "institutions_seen":   set(),
    }

    for row in rows:
        article_id    = row["id"]
        doi           = row["doi"].strip()
        current_abs   = row["abstract"]
        current_oa    = row["oa_url"]
        authors_str   = row["authors"] or ""

        url = f"{OPENALEX_BASE}/https://doi.org/{doi}?mailto={CONTACT_EMAIL}"

        try:
            data, status = _fetch_with_retry(url)
        except Exception as exc:
            log.error("Unexpected error fetching article %d (doi=%s): %s", article_id, doi, exc)
            capture_fetcher_error(SOURCE_NAME, None, exc)
            _mark_enriched(article_id)
            stats["processed"] += 1
            time.sleep(REQUEST_DELAY)
            continue

        if data is None:
            if status == 404:
                log.debug("404 for doi=%s — marking enriched.", doi)
            else:
                log.warning("No data returned for doi=%s (status=%s) — skipping.", doi, status)
            _mark_enriched(article_id)
            stats["processed"] += 1
            time.sleep(REQUEST_DELAY)
            continue

        # ── Extract fields ──────────────────────────────────────────────────

        openalex_id = data.get("id") or None

        # Abstract
        new_abstract = None
        if not current_abs or current_abs.strip() == "":
            inv_idx = data.get("abstract_inverted_index")
            new_abstract = decode_abstract(inv_idx)

        # Open access
        oa_block = data.get("open_access") or {}
        oa_status = oa_block.get("oa_status") or None

        new_oa_url = None
        if not current_oa:
            new_oa_url = oa_block.get("oa_url") or None
            if not new_oa_url:
                best = data.get("best_oa_location") or {}
                new_oa_url = best.get("pdf_url") or best.get("landing_page_url") or None

        # ── Write article fields ────────────────────────────────────────────

        with get_conn() as conn:
            # Build SET clause dynamically so we only touch what's needed
            updates = []
            params  = []

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
            conn.commit()

        # Rebuild FTS for this row if abstract changed
        if new_abstract:
            try:
                with get_conn() as conn:
                    conn.execute(
                        "INSERT INTO articles_fts(articles_fts, rowid, title, authors, abstract) "
                        "VALUES('delete', ?, (SELECT title FROM articles WHERE id=?), "
                        "(SELECT authors FROM articles WHERE id=?), ?)",
                        (article_id, article_id, article_id, current_abs or ""),
                    )
                    row2 = conn.execute(
                        "SELECT title, authors, abstract FROM articles WHERE id = ?",
                        (article_id,),
                    ).fetchone()
                    if row2:
                        conn.execute(
                            "INSERT INTO articles_fts(rowid, title, authors, abstract) "
                            "VALUES(?, ?, ?, ?)",
                            (article_id, row2["title"], row2["authors"], row2["abstract"]),
                        )
                    conn.commit()
            except Exception as exc:
                log.warning("FTS update failed for article %d: %s", article_id, exc)

        # ── Author affiliations ─────────────────────────────────────────────

        authorships = data.get("authorships") or []
        for authorship in authorships:
            author_block = authorship.get("author") or {}
            oa_author_id = author_block.get("id") or None
            display_name = author_block.get("display_name") or ""
            orcid_raw    = author_block.get("orcid") or None
            orcid        = _strip_orcid_prefix(orcid_raw)

            institutions = authorship.get("institutions") or []
            inst = institutions[0] if institutions else {}
            inst_name = inst.get("display_name") or None
            inst_ror  = inst.get("ror") or None
            raw_aff   = "; ".join(authorship.get("raw_affiliation_strings") or []) or None

            # Only write affiliation if this author name is in the article's authors field
            if not _name_matches(display_name, authors_str):
                continue

            # UPSERT into authors table
            try:
                with get_conn() as conn:
                    conn.execute("""
                        INSERT INTO authors (name, openalex_id, orcid, institution_name, institution_ror)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(name) DO UPDATE SET
                            openalex_id      = COALESCE(openalex_id,      excluded.openalex_id),
                            orcid            = COALESCE(orcid,            excluded.orcid),
                            institution_name = excluded.institution_name,
                            institution_ror  = excluded.institution_ror
                    """, (display_name, oa_author_id, orcid, inst_name, inst_ror))
                    conn.commit()
            except Exception as exc:
                log.warning("authors UPSERT failed for '%s': %s", display_name, exc)

            # UPSERT into author_article_affiliations table
            try:
                with get_conn() as conn:
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
                    if inst_name:
                        stats["institutions_seen"].add(inst_name)
            except Exception as exc:
                log.warning("author_article_affiliations UPSERT failed for '%s' article %d: %s",
                            display_name, article_id, exc)

        stats["processed"] += 1
        if stats["processed"] % 50 == 0:
            log.info("  OpenAlex: %d/%d processed", stats["processed"], total)

        time.sleep(REQUEST_DELAY)

    unique_institutions = len(stats["institutions_seen"])

    log.info("")
    log.info("=== OpenAlex Enrichment Complete ===")
    log.info("Articles processed:    %d", stats["processed"])
    log.info("Abstracts filled:      %d", stats["abstracts_filled"])
    log.info("OA status set:         %d", stats["oa_status_set"])
    log.info("Affiliations written:  %d", stats["affiliations_written"])
    log.info("Unique institutions:   %d", unique_institutions)

    return {
        "processed":            stats["processed"],
        "abstracts_filled":     stats["abstracts_filled"],
        "oa_status_set":        stats["oa_status_set"],
        "affiliations_written": stats["affiliations_written"],
        "unique_institutions":  unique_institutions,
    }


def _mark_enriched(article_id):
    """Stamp openalex_enriched_at without writing any other fields."""
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE articles SET openalex_enriched_at = ? WHERE id = ?",
                (datetime.utcnow().isoformat(), article_id),
            )
            conn.commit()
    except Exception as exc:
        log.warning("Could not mark article %d as enriched: %s", article_id, exc)


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Enrich articles with OpenAlex data (abstracts, OA status, affiliations)."
    )
    parser.parse_args()   # no extra flags yet — reserved for future use
    enrich_openalex()
