"""
weekly_maintenance.py — Automated weekly data pipeline for Pinakes.

Runs every Sunday at 03:00 UTC via cron (set up in start.sh).
Orchestrates all data-fetching and enrichment scripts in the correct order:

  1. CrossRef incremental fetch     (new articles from all journals)
  2. RSS feed fetch                 (web-native journals with feeds)
  3. Web scraper                    (journals requiring HTML scraping)
  4. Citation harvester             (CrossRef reference lists for new articles)
  5. OpenAlex enrichment            (abstracts, OA status, affiliations)
  6. LiCS reference scrape          (HTML galley references)
  7. Retag + FTS rebuild            (apply tagger rules, rebuild search index)
  8. OA status backfill             (tag gold-OA journal articles)

Each step is wrapped in a try/except so a failure in one step doesn't
block the rest. Logs to stdout (captured by cron to /tmp/weekly.log).

Usage:
    python weekly_maintenance.py          # run full pipeline
    python weekly_maintenance.py --step N # run only step N (1-8)
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("weekly")


def step_1_crossref():
    """Incremental CrossRef fetch for all journals."""
    from fetcher import fetch_all as crossref_fetch
    n = crossref_fetch(incremental=True)
    log.info("CrossRef: %d new articles", n)
    return n


def step_2_rss():
    """RSS/Atom feed fetch for web-native journals."""
    from rss_fetcher import fetch_all as rss_fetch
    n = rss_fetch()
    log.info("RSS: %d new articles", n)
    return n


def step_3_scraper():
    """Web scraper for journals without APIs or feeds."""
    from scraper import fetch_all as scrape_fetch
    n = scrape_fetch()
    log.info("Scraper: %d new articles", n)
    return n


def step_4_citations():
    """Harvest CrossRef reference lists for articles missing them."""
    from cite_fetcher import run_fetch
    run_fetch(limit=None, rebuild=False)
    log.info("Citation harvester complete.")


def step_5_openalex():
    """OpenAlex enrichment: abstracts, OA status, affiliations."""
    from enrich_openalex import enrich_openalex
    summary = enrich_openalex()
    log.info(
        "OpenAlex: processed=%d, abstracts=%d, OA=%d, affiliations=%d",
        summary.get("processed", 0),
        summary.get("abstracts_filled", 0),
        summary.get("oa_status_set", 0),
        summary.get("affiliations_written", 0),
    )
    return summary


def step_6_lics_refs():
    """Scrape reference lists from LiCS HTML galleys."""
    from scrape_lics_refs import run
    run(article_id=None, dry_run=False)
    log.info("LiCS reference scrape complete.")


def step_7_retag():
    """Re-tag all articles and rebuild FTS index."""
    from retag import retag_all
    retag_all()
    log.info("Retag + FTS rebuild complete.")


def step_8_oa_backfill():
    """Tag articles from known gold-OA journals."""
    from db import backfill_oa_status
    result = backfill_oa_status()
    if result["tagged"] > 0:
        log.info("OA backfill: tagged %d articles as gold OA", result["tagged"])
    else:
        log.info("OA backfill: no new articles to tag.")


def step_9_openalex_citations():
    """Fetch global citation counts from OpenAlex (incremental)."""
    from openalex_citations import run_enrichment
    stats = run_enrichment()
    log.info(
        "OpenAlex citations: success=%d, not_found=%d, errors=%d",
        stats.get("success", 0),
        stats.get("not_found", 0),
        stats.get("errors", 0),
    )
    return stats


STEPS = [
    (1, "CrossRef incremental fetch",     step_1_crossref),
    (2, "RSS feed fetch",                 step_2_rss),
    (3, "Web scraper",                    step_3_scraper),
    (4, "Citation harvester",             step_4_citations),
    (5, "OpenAlex enrichment",            step_5_openalex),
    (6, "LiCS reference scrape",          step_6_lics_refs),
    (7, "Retag + FTS rebuild",            step_7_retag),
    (8, "OA status backfill",             step_8_oa_backfill),
    (9, "OpenAlex citation counts",       step_9_openalex_citations),
]


def run_pipeline(only_step=None):
    from db import init_db
    init_db()

    started = datetime.now(timezone.utc)
    log.info("=" * 60)
    log.info("WEEKLY MAINTENANCE PIPELINE — %s UTC", started.strftime("%Y-%m-%d %H:%M"))
    log.info("=" * 60)

    results = {}
    for num, name, func in STEPS:
        if only_step is not None and num != only_step:
            continue
        log.info("")
        log.info("── Step %d: %s ──", num, name)
        t0 = time.time()
        try:
            func()
            elapsed = time.time() - t0
            results[num] = ("OK", elapsed)
            log.info("  Done in %.1f seconds.", elapsed)
        except Exception as e:
            elapsed = time.time() - t0
            results[num] = ("FAILED", elapsed)
            log.error("  FAILED after %.1f seconds: %s", elapsed, e, exc_info=True)

    log.info("")
    log.info("=" * 60)
    log.info("PIPELINE COMPLETE — %.1f minutes total", (time.time() - started.timestamp()) / 60)
    log.info("=" * 60)
    for num, name, _ in STEPS:
        if num in results:
            status, elapsed = results[num]
            log.info("  Step %d %-35s %s (%.1fs)", num, name, status, elapsed)
    log.info("")

    # Return non-zero if any step failed
    if any(s == "FAILED" for s, _ in results.values()):
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weekly data maintenance pipeline for Pinakes.")
    parser.add_argument("--step", type=int, default=None,
                        help="Run only step N (1-8). Omit to run all steps.")
    args = parser.parse_args()

    sys.exit(run_pipeline(only_step=args.step))
