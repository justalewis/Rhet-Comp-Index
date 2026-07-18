"""
fetcher.py — CrossRef API integration.

Fetches journal-article metadata by ISSN using cursor-based pagination.
Stores results via db.upsert_article. Skips articles already in the DB.

Usage:
    python fetcher.py              # incremental fetch for all journals
    python fetcher.py --full       # full re-fetch (ignores last-fetch date)
    python fetcher.py 0010-096X    # fetch one journal by ISSN
"""

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

# Identify ourselves to CrossRef. A real mailto puts requests in the polite
# pool, which has materially better rate limits than the anonymous pool — the
# placeholder that used to sit here left us anonymous and getting 429s.
# https://github.com/CrossRef/rest-api-doc#etiquette
HEADERS = {
    "User-Agent": "RhetCompIndex/1.0 (mailto:rhetcompindex@gmail.com)"
}


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
        "rows": ROWS_PER_PAGE,
        "sort": "published",
        "order": "desc",
        "cursor": "*",
    }
    if since_date:
        params["filter"] += f",from-pub-date:{since_date}"

    total_added = 0
    page = 0
    # Only stamp the last-fetch date when pagination reached a natural end. A
    # request failure part-way through must leave it alone: update_fetch_log
    # drives the `from-pub-date` filter on the next run, so advancing it after a
    # failed page moves the window past articles we never saw and the
    # incremental path can never reach them again (incident: 429s on 2392-3113).
    completed = False

    while True:
        try:
            resp = requests.get(CROSSREF_BASE, params=params, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error("Request failed for %s: %s", issn, e)
            capture_fetcher_error(SOURCE_NAME, journal_name, e)
            break

        data = resp.json().get("message", {})
        items = data.get("items", [])
        if not items:
            completed = True
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
            completed = True
            break

        params["cursor"] = next_cursor
        time.sleep(0.5)

    if completed:
        update_fetch_log(journal_name)
        log.info("Done: %s — %d new articles", journal_name, total_added)
    else:
        # Articles inserted before the failure are kept — upsert_article is
        # insert-or-ignore, so re-querying the same window next run is cheap
        # and idempotent. The window widens until a run succeeds, which is the
        # correct trade: re-reading pages costs a little, missing them is
        # permanent.
        log.warning(
            "Incomplete fetch for %s — last-fetch date left unchanged so the "
            "next run retries this window (%d new articles kept).",
            journal_name, total_added,
        )
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
