"""
cite_fetcher.py — Harvest CrossRef reference lists and build the internal citations table.

For each article in the database that has a DOI and has not yet been processed
(references_fetched_at IS NULL), this script:

  1. Calls CrossRef: GET https://api.crossref.org/works/{doi}?mailto=...
  2. Extracts the `reference` array and `is-referenced-by-count`
  3. For each reference that carries a DOI, checks whether that DOI belongs to
     another article already in our index and stores the relationship in `citations`
  4. Stamps references_fetched_at on the source article so it is not re-processed
  5. After all articles are processed, recomputes the internal_cited_by_count and
     internal_cites_count denormalised counters

Usage:
    python cite_fetcher.py                   # process all un-fetched articles
    python cite_fetcher.py --limit 500       # process at most N articles
    python cite_fetcher.py --rebuild         # clear stamps and re-fetch everything
    python cite_fetcher.py --counts-only     # skip API calls; only recompute counts
"""

import argparse
import logging
import time

import requests

from db import (
    get_articles_needing_citation_fetch,
    get_doi_to_article_id_map,
    get_conn,
    init_db,
    mark_references_fetched,
    update_citation_counts,
    upsert_citation,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

CONTACT_EMAIL = "rhetcompindex@gmail.com"
CROSSREF_BASE = "https://api.crossref.org/works"
HEADERS = {
    "User-Agent": f"RhetCompIndex/1.0 (mailto:{CONTACT_EMAIL})",
}
# 50 ms between requests — comfortably within CrossRef's polite-pool rate limit
REQUEST_DELAY = 0.05


# ── CrossRef API ───────────────────────────────────────────────────────────────

def _fetch_crossref_work(doi: str) -> dict | None:
    """
    Retrieve a single CrossRef work by DOI.
    Returns the 'message' dict on success, None on 404 or any other error.
    """
    url = f"{CROSSREF_BASE}/{doi}?mailto={CONTACT_EMAIL}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 404:
            log.debug("  DOI not in CrossRef: %s", doi)
            return None
        resp.raise_for_status()
        return resp.json().get("message")
    except Exception as exc:
        log.warning("  CrossRef error [%s]: %s", doi, exc)
        return None


# ── Per-article processing ─────────────────────────────────────────────────────

def _process_article(article: dict, doi_map: dict) -> tuple[int, int]:
    """
    Fetch CrossRef data for one article, store its citations.
    Returns (citations_inserted, total_references_with_doi).
    """
    article_id = article["id"]
    doi        = article["doi"].strip()

    message = _fetch_crossref_work(doi)
    if message is None:
        # Mark as fetched so we skip it on future runs (no useful data available)
        mark_references_fetched(article_id, crossref_cited_by_count=None)
        return 0, 0

    cited_by_count = message.get("is-referenced-by-count")
    references     = message.get("reference", [])

    citations_inserted = 0
    dois_in_refs       = 0

    for ref in references:
        ref_doi = ref.get("DOI")
        if not ref_doi:
            continue

        dois_in_refs       += 1
        ref_doi_normalised  = ref_doi.strip().lower()
        target_article_id   = doi_map.get(ref_doi_normalised)

        citations_inserted += upsert_citation(
            source_article_id = article_id,
            target_doi        = ref_doi_normalised,
            target_article_id = target_article_id,
            raw_reference     = ref,
        )

    mark_references_fetched(article_id, crossref_cited_by_count=cited_by_count)
    return citations_inserted, dois_in_refs


# ── Main entry points ──────────────────────────────────────────────────────────

def run_fetch(limit: int | None = None, rebuild: bool = False) -> None:
    """Fetch CrossRef references for all un-processed articles."""
    init_db()

    if rebuild:
        log.info("Rebuild mode: clearing references_fetched_at …")
        with get_conn() as conn:
            conn.execute(
                "UPDATE articles SET references_fetched_at = NULL WHERE doi IS NOT NULL"
            )
            conn.commit()

    articles = get_articles_needing_citation_fetch(limit=limit)
    total    = len(articles)
    log.info("Articles to process: %d", total)

    if total == 0:
        log.info("Nothing to do. Run with --rebuild to re-process all articles.")
        _recompute_counts()
        return

    # Build the DOI lookup once — far cheaper than hitting the DB per reference
    doi_map = get_doi_to_article_id_map()
    log.info("DOI lookup map loaded: %d entries", len(doi_map))

    processed        = 0
    total_citations  = 0
    total_doi_refs   = 0
    errors           = 0

    for article in articles:
        try:
            ins, refs = _process_article(article, doi_map)
            total_citations += ins
            total_doi_refs  += refs
        except Exception as exc:
            log.error(
                "  Unexpected error — article %d (%s): %s",
                article["id"], article.get("doi", ""), exc,
            )
            errors += 1
            # Still mark fetched so a transient error doesn't block the whole queue
            try:
                mark_references_fetched(article["id"])
            except Exception:
                pass

        processed += 1
        if processed % 100 == 0:
            log.info(
                "  %d / %d  |  citations inserted: %d  |  DOI refs seen: %d",
                processed, total, total_citations, total_doi_refs,
            )

        time.sleep(REQUEST_DELAY)

    log.info(
        "Fetch complete — processed: %d  |  citations inserted: %d  |  "
        "DOI refs: %d  |  errors: %d",
        processed, total_citations, total_doi_refs, errors,
    )
    _recompute_counts()


def _recompute_counts() -> None:
    log.info("Recomputing internal citation counts …")
    update_citation_counts()
    log.info("Done.")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Harvest CrossRef reference lists and build the citation network."
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process at most this many articles (useful for testing)",
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Clear all references_fetched_at stamps and re-fetch from scratch",
    )
    parser.add_argument(
        "--counts-only", action="store_true",
        help="Skip API calls; only recompute internal_cited_by_count / internal_cites_count",
    )
    args = parser.parse_args()

    if args.counts_only:
        init_db()
        _recompute_counts()
    else:
        run_fetch(limit=args.limit, rebuild=args.rebuild)
