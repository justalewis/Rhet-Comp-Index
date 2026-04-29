"""
scheduler.py — Standalone scheduler process.

Runs a full incremental fetch from all sources at startup,
then repeats every 24 hours.

Also runs the OpenAlex enrichment job weekly (every 7 days).

This process invokes fetch_all() on each ingester directly (in-process),
not through the /fetch HTTP endpoint, so it does not pass through the
admin-token authentication added in auth.py. If the scheduler is ever
moved to HTTP-triggered fetches, the PINAKES_ADMIN_TOKEN env var must
be read here and sent as `Authorization: Bearer <token>`.

Usage:
    python scheduler.py
"""

import logging
from apscheduler.schedulers.blocking import BlockingScheduler

from db import init_db
from health import write_heartbeat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def job():
    log.info("=== Scheduled fetch starting ===")
    total = 0

    try:
        from fetcher import fetch_all as crossref_fetch
        n = crossref_fetch(incremental=True)
        log.info("CrossRef: %d new articles", n)
        total += n
    except Exception as e:
        log.error("CrossRef fetch failed: %s", e)

    try:
        from rss_fetcher import fetch_all as rss_fetch
        n = rss_fetch()
        log.info("RSS: %d new articles", n)
        total += n
    except Exception as e:
        log.error("RSS fetch failed: %s", e)

    try:
        from scraper import fetch_all as scrape_fetch
        n = scrape_fetch()
        log.info("Scrape: %d new articles", n)
        total += n
    except Exception as e:
        log.error("Scrape fetch failed: %s", e)

    log.info("=== Fetch complete — %d total new articles ===", total)
    # Heartbeat — let /health/deep confirm the scheduler is actually running.
    # Wrapped inside health.write_heartbeat itself; no further try/except.
    write_heartbeat()


def openalex_job():
    """Weekly OpenAlex enrichment: abstracts, OA status, author affiliations."""
    log.info("=== OpenAlex enrichment starting ===")
    try:
        from enrich_openalex import enrich_openalex
        summary = enrich_openalex()
        log.info(
            "OpenAlex complete — processed: %d, abstracts: %d, OA: %d, affiliations: %d",
            summary.get("processed", 0),
            summary.get("abstracts_filled", 0),
            summary.get("oa_status_set", 0),
            summary.get("affiliations_written", 0),
        )
    except Exception as e:
        log.error("OpenAlex enrichment failed: %s", e)
    write_heartbeat()


if __name__ == "__main__":
    init_db()

    # Initial heartbeat — a freshly-deployed scheduler is observable to
    # /health/deep before the first job() call completes.
    write_heartbeat()

    # Tag articles from known gold-OA journals (fast, no API calls)
    from db import backfill_oa_status
    oa_result = backfill_oa_status()
    if oa_result["tagged"] > 0:
        log.info("OA backfill: tagged %d articles as gold OA", oa_result["tagged"])

    log.info("Running initial fetch on startup…")
    job()

    scheduler = BlockingScheduler()
    scheduler.add_job(job, "interval", hours=24, id="daily_fetch")
    scheduler.add_job(openalex_job, "interval", weeks=1, id="weekly_openalex")
    # Frequent heartbeat so /health/deep stays accurate between job runs.
    # Heartbeat staleness threshold in health.py is 25h, but pinging every
    # 5 minutes keeps the file's mtime fresh and surfaces a wedged scheduler
    # within minutes rather than hours.
    scheduler.add_job(write_heartbeat, "interval", minutes=5, id="heartbeat")
    log.info("Scheduler running — daily fetch every 24 h, OpenAlex enrichment every 7 days. Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")
