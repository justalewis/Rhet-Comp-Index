"""
enrich.py — Post-fetch enrichment for Rhet-Comp Index.

Two enrichers:
  enrich_unpaywall()  — adds open-access PDF/landing URLs via Unpaywall API
  enrich_semantic()   — adds citation counts and paper IDs via Semantic Scholar API

Usage:
  python enrich.py                   # run both
  python enrich.py --unpaywall       # Unpaywall only
  python enrich.py --semantic        # Semantic Scholar only
  python enrich.py --rebuild         # re-check all articles, not just unchecked ones
"""

import argparse
import logging
import time
import sys

import urllib.request
import urllib.error
import json

from db import get_conn, update_oa_url, update_semantic_data

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

# Replace with your email for polite API usage (CrossRef / Unpaywall etiquette)
CONTACT_EMAIL = "your-email@example.com"

UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
SEMANTIC_BASE  = "https://api.semanticscholar.org/graph/v1/paper"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_json(url, timeout=10):
    """Fetch JSON from a URL. Returns parsed dict or None on error."""
    req = urllib.request.Request(url, headers={"User-Agent": f"RhetCompIndex/1.0 ({CONTACT_EMAIL})"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        log.warning("HTTP %d for %s", e.code, url)
        return None
    except Exception as e:
        log.warning("Request failed for %s: %s", url, e)
        return None


# ── Unpaywall ──────────────────────────────────────────────────────────────────

def enrich_unpaywall(rebuild=False):
    """
    For each article with a DOI, fetch the open-access URL from Unpaywall.
    Stores the PDF URL if available, the landing URL as fallback,
    or empty string if no OA copy found.

    If rebuild=False (default), skips articles where oa_url IS NOT NULL.
    """
    with get_conn() as conn:
        if rebuild:
            rows = conn.execute(
                "SELECT id, doi FROM articles WHERE doi IS NOT NULL AND doi != ''"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, doi FROM articles "
                "WHERE doi IS NOT NULL AND doi != '' AND oa_url IS NULL"
            ).fetchall()

    total = len(rows)
    log.info("Unpaywall: checking %d articles…", total)

    checked = 0
    found = 0
    for row in rows:
        article_id = row["id"]
        doi = row["doi"].strip()

        url = f"{UNPAYWALL_BASE}/{doi}?email={CONTACT_EMAIL}"
        data = _get_json(url)

        oa_url = ""
        if data:
            best = data.get("best_oa_location")
            if best:
                oa_url = best.get("url_for_pdf") or best.get("url") or ""

        update_oa_url(article_id, oa_url)
        if oa_url:
            found += 1

        checked += 1
        if checked % 50 == 0:
            log.info("  Unpaywall: %d/%d checked, %d OA found", checked, total, found)

        time.sleep(0.1)

    log.info("Unpaywall complete: %d/%d articles have OA links.", found, total)


# ── Semantic Scholar ───────────────────────────────────────────────────────────

def enrich_semantic(rebuild=False):
    """
    For each article with a DOI, fetch citation count and paper ID
    from Semantic Scholar.

    If rebuild=False (default), skips articles where citation_count IS NOT NULL.
    """
    with get_conn() as conn:
        if rebuild:
            rows = conn.execute(
                "SELECT id, doi FROM articles WHERE doi IS NOT NULL AND doi != ''"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, doi FROM articles "
                "WHERE doi IS NOT NULL AND doi != '' AND citation_count IS NULL"
            ).fetchall()

    total = len(rows)
    log.info("Semantic Scholar: checking %d articles…", total)

    checked = 0
    enriched = 0
    for row in rows:
        article_id = row["id"]
        doi = row["doi"].strip()

        url = f"{SEMANTIC_BASE}/DOI:{doi}?fields=citationCount,paperId"
        data = _get_json(url)

        if data and "paperId" in data:
            ss_id = data.get("paperId") or ""
            citation_count = data.get("citationCount") or 0
            enriched += 1
        else:
            # 404 or no data — mark as checked with zeros so we skip next time
            ss_id = ""
            citation_count = 0

        update_semantic_data(article_id, ss_id, citation_count)

        checked += 1
        if checked % 50 == 0:
            log.info("  Semantic: %d/%d checked, %d enriched", checked, total, enriched)

        time.sleep(0.1)

    log.info("Semantic Scholar complete: %d/%d articles enriched.", enriched, total)


# ── CLI entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich articles with OA and citation data.")
    parser.add_argument("--unpaywall", action="store_true", help="Run Unpaywall enrichment only")
    parser.add_argument("--semantic",  action="store_true", help="Run Semantic Scholar enrichment only")
    parser.add_argument("--rebuild",   action="store_true", help="Re-check all articles (not just unchecked)")
    args = parser.parse_args()

    from db import init_db
    init_db()

    run_all = not args.unpaywall and not args.semantic

    if run_all or args.unpaywall:
        enrich_unpaywall(rebuild=args.rebuild)

    if run_all or args.semantic:
        enrich_semantic(rebuild=args.rebuild)
