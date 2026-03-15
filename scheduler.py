"""
scheduler.py — Standalone scheduler process.

Runs a full incremental fetch from all sources at startup,
then repeats every 24 hours.

Usage:
    python scheduler.py
"""

import logging
from apscheduler.schedulers.blocking import BlockingScheduler

from db import init_db

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


if __name__ == "__main__":
    init_db()

    log.info("Running initial fetch on startup…")
    job()

    scheduler = BlockingScheduler()
    scheduler.add_job(job, "interval", hours=24, id="daily_fetch")
    log.info("Scheduler running — next fetch in 24 hours. Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")
