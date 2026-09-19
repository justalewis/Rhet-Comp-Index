"""
fetcher.py — CrossRef API integration.

Fetches journal-article metadata by ISSN using cursor-based pagination.
Stores results via db.upsert_article. Skips articles already in the DB.

Usage:
    python fetcher.py              # incremental fetch for all journals
    python fetcher.py --full       # full re-fetch (ignores last-fetch date)
    python fetcher.py 0010-096X    # fetch one journal by ISSN
"""

import os
import sys
import time
import re
import html
import logging
import requests
from datetime import datetime

from db import init_db, upsert_article, update_fetch_log, get_last_fetch
from journals import CROSSREF_JOURNALS, ISSN_TO_NAME, GOLD_OA_JOURNALS
from tagger import auto_tag
from monitoring import capture_fetcher_error

SOURCE_NAME = "crossref"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

CROSSREF_BASE = "https://api.crossref.org/works"
ROWS_PER_PAGE = 100

# Identify ourselves to CrossRef so requests land in the "polite pool", which
# has far more generous rate limits than the anonymous pool (the anonymous pool
# is what was returning 429s for the daily fetch). CrossRef keys the polite pool
# off a contact mailto in the User-Agent. Read it from CROSSREF_MAILTO (a Fly
# secret in prod / .env locally) so the personal email stays out of this public
# repo. https://api.crossref.org/swagger-ui/index.html#/ (Etiquette)
_MAILTO = os.environ.get("CROSSREF_MAILTO", "").strip()
# The site this crawler belongs to, for anyone reading their own access logs.
# Follows PINAKES_SITE_URL so a deployment identifies itself as the host it
# actually serves from.
_UA_URL = os.environ.get("PINAKES_SITE_URL", "https://pinakes.wacclearinghouse.org")
HEADERS = {
    "User-Agent": (
        f"Pinakes/1.0 (+{_UA_URL}; mailto:{_MAILTO})"
        if _MAILTO else f"Pinakes/1.0 (+{_UA_URL})"
    )
}

# Retry budget for transient CrossRef responses (429 + 5xx).
_MAX_RETRIES = 4
_RETRYABLE = {429, 500, 502, 503, 504}


def _crossref_get(params):
    """GET from CrossRef with polite-pool headers and backoff on 429/5xx.

    Honors the server's Retry-After header when present; otherwise backs off
    exponentially (2, 4, 8 s, capped at 60). Raises the underlying HTTPError
    only after the retry budget is exhausted, so a brief rate-limit no longer
    aborts a journal's fetch (or spams Sentry)."""
    delay = 2
    resp = None
    for attempt in range(1, _MAX_RETRIES + 1):
        resp = requests.get(CROSSREF_BASE, params=params, headers=HEADERS, timeout=30)
        if resp.status_code in _RETRYABLE and attempt < _MAX_RETRIES:
            retry_after = resp.headers.get("Retry-After")
            try:
                wait = int(retry_after) if retry_after else delay
            except ValueError:
                wait = delay
            wait = min(wait, 60)
            log.warning("CrossRef %s (attempt %d/%d) — backing off %ds",
                        resp.status_code, attempt, _MAX_RETRIES, wait)
            time.sleep(wait)
            delay = min(delay * 2, 60)
            continue
        break
    resp.raise_for_status()
    return resp


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_date(item):
    """Return best available ISO date string from a CrossRef work item."""
    for key in ("published-print", "published-online", "issued", "published"):
        dp = item.get(key, {}).get("date-parts", [[]])[0]
        if dp:
            parts = list(dp[:3])
            while len(parts) < 3:
                parts.append(1)
            try:
                return datetime(*parts).date().isoformat()
            except (ValueError, TypeError):
                continue
    return None


def _parse_authors(item):
    """Return semicolon-separated author string, or None."""
    authors = []
    for a in item.get("author", []):
        given = a.get("given", "")
        family = a.get("family", "")
        name = f"{given} {family}".strip() if given else family
        if name:
            authors.append(name)
    return "; ".join(authors) if authors else None


def _parse_abstract(item):
    """Strip JATS XML tags and decode HTML entities from abstract."""
    raw = item.get("abstract", "")
    if not raw:
        return None
    cleaned = html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
    return cleaned or None


def _clean_title_part(s):
    """Normalize one title or subtitle string from CrossRef.

    Some deposits ship doubly-encoded entities ("&amp;#x3a;") and JATS
    markup ("<i>De doctrina christiana</i>") inside the title field, so we
    loop unescape until idempotent, strip XML/HTML tags, and collapse
    whitespace.
    """
    if not s:
        return ""
    for _ in range(5):
        decoded = html.unescape(s)
        if decoded == s:
            break
        s = decoded
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _full_title(item):
    """Compose the canonical title from CrossRef's split title/subtitle arrays.

    CrossRef stores titles like "Chicanx Filmmaking" + subtitle
    "Producing the Next Generation of Resilient Cinema" as separate fields;
    we join them with ": " to match how the work is cited.
    """
    titles = item.get("title", [])
    if not titles:
        return None
    main = _clean_title_part(titles[0])
    subtitles = item.get("subtitle", [])
    if subtitles:
        sub = _clean_title_part(subtitles[0])
        if sub and sub not in main:
            sep = "" if main.endswith(":") else ": "
            main = f"{main}{sep}{sub}"
    return main or None


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_journal(issn, since_date=None):
    """
    Fetch all works for a journal ISSN from CrossRef.
    If since_date (YYYY-MM-DD) is given, only fetches items published after it.
    Returns number of new articles inserted.
    """
    journal_name = ISSN_TO_NAME.get(issn, issn)
    log.info("Fetching %s  (%s)%s", journal_name, issn,
             f"  since {since_date}" if since_date else "  [full]")

    params = {
        "filter": f"issn:{issn},type:journal-article",
        "select": "DOI,title,subtitle,author,abstract,published-print,published-online,issued,container-title",
        # NB: CrossRef rejects sort-by-date combined with cursor paging
        # ("sort-criteria-incompatible-with-cursor"), so results arrive
        # unordered; from-pub-date filtering bounds incremental fetches.
        "rows": ROWS_PER_PAGE,
        "cursor": "*",
    }
    if since_date:
        params["filter"] += f",from-pub-date:{since_date}"

    total_added = 0
    page = 0

    while True:
        try:
            resp = _crossref_get(params)
        except requests.RequestException as e:
            log.error("Request failed for %s: %s", issn, e)
            capture_fetcher_error(SOURCE_NAME, journal_name, e)
            break

        data = resp.json().get("message", {})
        items = data.get("items", [])
        if not items:
            break

        for item in items:
            doi = item.get("DOI", "").strip()
            if not doi:
                continue

            title = _full_title(item) or "(no title)"

            url = f"https://doi.org/{doi}"
            pub_date = _parse_date(item)
            authors = _parse_authors(item)
            abstract = _parse_abstract(item)

            # Always use our canonical name — CrossRef container-title can be
            # HTML-encoded ("&amp;") or spelled differently ("and" vs "&").
            jname = journal_name

            subjects = item.get("subject", [])
            keywords = "; ".join(subjects) if subjects else None
            tags = auto_tag(title, abstract)

            # OA classification: known gold-OA journals get tagged at insert
            oa_status = "gold" if jname in GOLD_OA_JOURNALS else None
            oa_url_val = url if oa_status == "gold" else None

            added = upsert_article(
                url, doi, title, authors, abstract, pub_date, jname, "crossref",
                keywords=keywords, tags=tags,
                oa_status=oa_status, oa_url=oa_url_val,
            )
            total_added += added

        page += 1
        log.info("  page %d — %d items, %d new so far", page, len(items), total_added)

        next_cursor = data.get("next-cursor")
        if not next_cursor or len(items) < ROWS_PER_PAGE:
            break

        params["cursor"] = next_cursor
        time.sleep(0.5)

    update_fetch_log(journal_name)
    log.info("Done: %s — %d new articles", journal_name, total_added)
    return total_added


def fetch_all(incremental=True):
    """Fetch all CrossRef journals. Returns total new article count."""
    init_db()
    grand_total = 0
    for journal in CROSSREF_JOURNALS:
        issn = journal["issn"]
        name = journal["name"]
        since_date = None
        if incremental:
            last = get_last_fetch(name)
            since_date = last[:10] if last else None
        grand_total += fetch_journal(issn, since_date=since_date)
        time.sleep(1)
    log.info("CrossRef fetch complete. Total new: %d", grand_total)
    return grand_total


if __name__ == "__main__":
    init_db()
    if len(sys.argv) > 1 and sys.argv[1] not in ("--full",):
        issn_arg = sys.argv[1]
        if issn_arg not in ISSN_TO_NAME:
            print(f"Unknown ISSN: {issn_arg}")
            print("Known ISSNs:", ", ".join(ISSN_TO_NAME))
            sys.exit(1)
        fetch_journal(issn_arg)
    else:
        fetch_all(incremental="--full" not in sys.argv)
