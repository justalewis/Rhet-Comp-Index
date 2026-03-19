"""
fetch_institutions.py — OpenAlex institution data pipeline for Pinakes.

Queries OpenAlex for every article in the database that has not yet been
processed (no row in openalex_fetch_log). Extracts institutional affiliations
from the authorships array and stores them in the normalized institutions
and article_author_institutions tables.

Usage:
    python fetch_institutions.py                    # process all unprocessed articles
    python fetch_institutions.py --limit 500        # process at most N articles
    python fetch_institutions.py --rebuild          # clear fetch log and re-process all
"""

import sys
import time
import logging
import argparse
import requests
from urllib.parse import quote

from db import (
    init_db,
    get_articles_needing_institution_fetch,
    upsert_institution,
    insert_article_author_institution,
    log_openalex_fetch,
    get_conn,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

MAILTO   = "justalewis1@gmail.com"
BASE_URL = "https://api.openalex.org"
DELAY    = 0.30   # seconds between requests (polite pool — ~3 req/s)


class RateLimitError(Exception):
    """Raised when OpenAlex 429s persist after backoff. Article will be retried next run."""
    pass


def _get(url, params=None, _retry=0):
    """HTTP GET with polite delay, 429 backoff, and error handling. Returns JSON or None."""
    time.sleep(DELAY)
    try:
        resp = requests.get(
            url, params=params, timeout=20,
            headers={"User-Agent": f"pinakes/1.0 (mailto:{MAILTO})"},
        )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            return None
        if resp.status_code == 429:
            if _retry >= 3:
                raise RateLimitError(f"Still rate-limited after 3 retries: {url}")
            wait = int(resp.headers.get("Retry-After", 60))
            log.warning("Rate limited (429). Waiting %ds before retry %d/3…", wait, _retry + 1)
            time.sleep(wait)
            return _get(url, params=params, _retry=_retry + 1)
        log.warning("OpenAlex returned %d for %s", resp.status_code, url)
        return None
    except RateLimitError:
        raise
    except requests.RequestException as e:
        log.error("Request failed for %s: %s", url, e)
        return None


def fetch_by_doi(doi):
    """Look up an OpenAlex work by DOI. Returns work dict or None."""
    encoded = quote(f"https://doi.org/{doi}", safe="")
    return _get(f"{BASE_URL}/works/{encoded}", params={"mailto": MAILTO})


def fetch_by_title(title, journal, year):
    """Fall back to title search when no DOI. Returns first matching work or None."""
    safe_title = title[:100].replace('"', "'")
    params = {
        "mailto":   MAILTO,
        "filter":   f'title.search:"{safe_title}"',
        "per_page": 5,
    }
    if year and str(year)[:4].isdigit():
        params["filter"] += f",publication_year:{str(year)[:4]}"
    data = _get(f"{BASE_URL}/works", params=params)
    if not data or not data.get("results"):
        return None
    return data["results"][0]


def process_work(article_id, work):
    """
    Extract institution data from an OpenAlex work dict and write to DB.
    Returns the OpenAlex work ID string.
    """
    openalex_work_id = work.get("id") or ""
    authorships = work.get("authorships") or []

    for authorship in authorships:
        author         = authorship.get("author") or {}
        author_id      = author.get("id") or None
        author_name    = author.get("display_name") or ""
        author_position = authorship.get("author_position") or None
        institutions   = authorship.get("institutions") or []

        if not institutions:
            # Author with no institution — record authorship without institution
            insert_article_author_institution(
                article_id, author_name, author_id, None, author_position
            )
            continue

        for inst in institutions:
            inst_name    = inst.get("display_name") or ""
            if not inst_name:
                continue
            inst_openalex = inst.get("id") or None
            inst_ror      = inst.get("ror") or None
            inst_country  = inst.get("country_code") or None
            inst_type     = inst.get("type") or None

            institution_id = upsert_institution(
                openalex_id  = inst_openalex,
                ror_id       = inst_ror,
                display_name = inst_name,
                country_code = inst_country,
                inst_type    = inst_type,
            )

            insert_article_author_institution(
                article_id         = article_id,
                author_name        = author_name,
                openalex_author_id = author_id,
                institution_id     = institution_id,
                author_position    = author_position,
            )

    return openalex_work_id


def main():
    parser = argparse.ArgumentParser(
        description="Fetch institutional affiliation data from OpenAlex"
    )
    parser.add_argument("--limit",   type=int, default=None,
                        help="Max articles to process in this run")
    parser.add_argument("--rebuild", action="store_true",
                        help="Clear openalex_fetch_log and re-process all articles")
    args = parser.parse_args()

    init_db()

    if args.rebuild:
        with get_conn() as conn:
            conn.execute("DELETE FROM openalex_fetch_log")
            conn.commit()
        log.info("Cleared openalex_fetch_log — will re-process all articles.")

    articles = get_articles_needing_institution_fetch(batch_size=args.limit)
    total = len(articles)

    if total == 0:
        log.info("No articles to process — all caught up.")
        return

    log.info("Processing %d articles…", total)

    done = found = not_found = errors = 0

    for article in articles:
        article_id = article["id"]
        doi        = article.get("doi")
        title      = article.get("title") or ""
        year       = (article.get("pub_date") or "")[:4]
        journal    = article.get("journal") or ""

        work             = None
        status           = "not_found"
        openalex_work_id = None

        try:
            if doi:
                work = fetch_by_doi(doi)
            if not work:
                work = fetch_by_title(title, journal, year)

            if work:
                openalex_work_id = process_work(article_id, work)
                status = "found"
                found += 1
            else:
                not_found += 1

        except RateLimitError as e:
            # Don't log — leave this article out of openalex_fetch_log so the
            # next run will retry it. Sleep a bit before continuing.
            log.warning("Rate limit exhausted for article %d — will retry next run.", article_id)
            errors += 1
            time.sleep(120)  # 2-minute cool-down before resuming
            done += 1
            continue

        except Exception as e:
            log.error("Error on article %d (%s): %s",
                      article_id, doi or title[:50], e)
            status = "error"
            errors += 1

        log_openalex_fetch(article_id, openalex_work_id, status)
        done += 1

        if done % 100 == 0:
            log.info("Processed %d / %d  (found %d  not_found %d  errors %d)",
                     done, total, found, not_found, errors)

    log.info("Complete — %d processed, %d found, %d not found, %d errors",
             done, found, not_found, errors)


if __name__ == "__main__":
    main()
