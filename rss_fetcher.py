"""
rss_fetcher.py — RSS/Atom feed fetcher for web-native journals.

Uses feedparser to handle RSS 2.0, Atom, and Drupal node feeds uniformly.
Stores results via db.upsert_article, deduplicating by article URL.

Usage:
    python rss_fetcher.py
"""

import re
import logging
import requests
import xml.etree.ElementTree as ET
from time import mktime, struct_time
from datetime import datetime

import feedparser

from db import init_db, upsert_article, update_fetch_log, get_last_fetch
from journals import RSS_JOURNALS
from tagger import auto_tag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

USER_AGENT = "RhetCompIndex/1.0 (mailto:your-email@example.com)"

ABSTRACT_MAX = 2000   # truncate feed summaries that are actually full article HTML


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_date(entry):
    """Return ISO date string from a feedparser entry, or None."""
    for field in ("published_parsed", "updated_parsed"):
        t = getattr(entry, field, None)
        if isinstance(t, struct_time):
            try:
                return datetime.fromtimestamp(mktime(t)).strftime("%Y-%m-%d")
            except (ValueError, OverflowError, OSError):
                pass
    # Fall back to raw string
    for field in ("published", "updated"):
        raw = getattr(entry, field, "")
        if raw:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
            if m:
                return m.group(1)
            m = re.search(r"(\d{4})", raw)
            if m:
                return m.group(1)
    return None


def _parse_authors(entry):
    """Return semicolon-separated author string, or None."""
    names = []
    if hasattr(entry, "authors"):
        for a in entry.authors:
            n = a.get("name", "").strip()
            if n:
                names.append(n)
    elif getattr(entry, "author", "").strip():
        names.append(entry.author.strip())
    return "; ".join(names) if names else None


def _strip_html(text):
    if not text:
        return None
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean or None


def _parse_abstract(entry):
    """Extract and clean abstract/summary from entry. Returns None if too short."""
    # Prefer full content over summary
    if hasattr(entry, "content") and entry.content:
        raw = entry.content[0].get("value", "")
    else:
        raw = getattr(entry, "summary", "") or ""

    text = _strip_html(raw)
    if not text:
        return None
    if len(text) > ABSTRACT_MAX:
        text = text[:ABSTRACT_MAX].rsplit(" ", 1)[0] + "…"
    # If it's very short it's probably just a byline, not an abstract
    return text if len(text) > 80 else None


# ── OAI-PMH harvester ─────────────────────────────────────────────────────────
# Used for OJS journals where the RSS feed is capped (typically 10 items).
# OAI-PMH returns all records and supports incremental harvesting via `from`.
# No extra dependencies — uses stdlib xml.etree.ElementTree + requests.

OAI_NS = {
    "oai":    "http://www.openarchives.org/OAI/2.0/",
    "dc":     "http://purl.org/dc/elements/1.1/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
}


def _harvest_oai(oai_url, journal_name, since_date=None):
    """
    Harvest all article records from an OAI-PMH endpoint (Dublin Core format).

    Handles resumption tokens so the full archive is fetched regardless of
    how many records exist. If since_date (YYYY-MM-DD) is given, only records
    modified on or after that date are returned — useful for incremental runs.

    Returns count of new articles inserted.
    """
    log.info("Fetching OAI-PMH: %s%s", journal_name,
             f"  (since {since_date})" if since_date else "  [full]")

    params = {"verb": "ListRecords", "metadataPrefix": "oai_dc"}
    if since_date:
        params["from"] = since_date

    total_new = 0
    page = 0

    while True:
        try:
            resp = requests.get(
                oai_url, params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error("  OAI request failed for %s: %s", journal_name, e)
            break

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            log.error("  OAI XML parse error for %s: %s", journal_name, e)
            break

        # Check for OAI error response (e.g. noRecordsMatch)
        error_el = root.find(".//oai:error", OAI_NS)
        if error_el is not None:
            code = error_el.get("code", "")
            if code == "noRecordsMatch":
                log.info("  %s — no new records since %s", journal_name, since_date)
            else:
                log.warning("  OAI error for %s: %s — %s",
                            journal_name, code, error_el.text)
            break

        records = root.findall(".//oai:record", OAI_NS)
        page += 1
        log.info("  OAI page %d — %d records", page, len(records))

        for record in records:
            # Skip deleted records
            header = record.find("oai:header", OAI_NS)
            if header is not None and header.get("status") == "deleted":
                continue

            metadata = record.find(".//oai_dc:dc", OAI_NS)
            if metadata is None:
                continue

            # Title
            title_el = metadata.find("dc:title", OAI_NS)
            title = (title_el.text or "").strip() if title_el is not None else ""
            if not title:
                continue

            # URL — prefer an http dc:identifier over the OAI handle
            url = None
            for id_el in metadata.findall("dc:identifier", OAI_NS):
                text = (id_el.text or "").strip()
                if text.startswith("http"):
                    url = text
                    break
            if not url:
                # Fall back to OAI record identifier
                oai_id = record.find(".//oai:identifier", OAI_NS)
                url = (oai_id.text or "").strip() if oai_id is not None else None
            if not url:
                continue

            # Publication date — dc:date is typically YYYY or YYYY-MM-DD
            date_el = metadata.find("dc:date", OAI_NS)
            pub_date = None
            if date_el is not None and date_el.text:
                raw = date_el.text.strip()
                # Take only the first date if multiple are listed
                pub_date = raw.split("\n")[0].strip()[:10]

            # Authors (dc:creator)
            creators = [
                (el.text or "").strip()
                for el in metadata.findall("dc:creator", OAI_NS)
                if el.text and el.text.strip()
            ]
            authors = "; ".join(creators) if creators else None

            # Abstract (dc:description — first one that looks like an abstract)
            abstract = None
            for desc_el in metadata.findall("dc:description", OAI_NS):
                text = _strip_html((desc_el.text or "").strip())
                if text and len(text) > 80:
                    abstract = text[:ABSTRACT_MAX]
                    break

            tags = auto_tag(title, abstract)
            new = upsert_article(
                url, None, title, authors, abstract, pub_date,
                journal_name, "rss", tags=tags,
            )
            total_new += new

        # Follow resumption token if present
        resumption = root.find(".//oai:resumptionToken", OAI_NS)
        if resumption is not None and (resumption.text or "").strip():
            params = {"verb": "ListRecords", "resumptionToken": resumption.text.strip()}
        else:
            break  # no more pages

    log.info("  %s — %d new articles (OAI)", journal_name, total_new)
    return total_new


# ── WordPress REST API harvester ──────────────────────────────────────────────
# Used for WordPress journals where:
#   (a) the RSS feed is capped at 10 items, AND
#   (b) the WP REST API is accessible (requires browser-like User-Agent on some hosts)
#
# Metadata notes: most academic WP themes do not expose author names or abstracts
# through the standard REST API fields — those are typically in custom fields or
# page HTML. We harvest title + date + URL, which is consistent with what scraping
# would give us and is already a major improvement over the RSS cap.

# Browser UA required by some WordPress hosts that block automation user agents.
WP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def _harvest_wp_api(api_url, journal_name, since_date=None):
    """
    Harvest all published posts from a WordPress REST API endpoint.

    Paginates automatically using the X-WP-TotalPages response header.
    If since_date (YYYY-MM-DD) is given, only posts published on or after
    that date are returned — used for incremental runs.

    Returns count of new articles inserted.
    """
    log.info("Fetching WP API: %s%s", journal_name,
             f"  (since {since_date})" if since_date else "  [full]")

    params = {"per_page": 100, "status": "publish", "orderby": "date", "order": "desc"}
    if since_date:
        params["after"] = f"{since_date}T00:00:00"

    total_new = 0
    page = 1

    while True:
        params["page"] = page
        try:
            resp = requests.get(
                api_url, params=params,
                headers={"User-Agent": WP_UA},
                timeout=30,
            )
            # WordPress returns 400 when page number exceeds total pages
            if resp.status_code == 400:
                break
            resp.raise_for_status()
        except requests.RequestException as e:
            log.error("  WP API request failed for %s: %s", journal_name, e)
            break

        posts = resp.json()
        if not posts:
            break

        total_pages = int(resp.headers.get("X-WP-TotalPages", 1))
        log.info("  WP API page %d/%d — %d posts", page, total_pages, len(posts))

        for post in posts:
            # Strip any HTML from the title (some themes add markup)
            title = _strip_html(post.get("title", {}).get("rendered", "") or "")
            if not title:
                continue

            url = (post.get("link") or "").strip()
            if not url:
                continue

            # Use GMT date for consistency
            date_str = post.get("date_gmt") or post.get("date") or ""
            pub_date = date_str[:10] if date_str else None  # YYYY-MM-DD

            # Authors: try _embedded first, fall back to empty
            # (many academic WP themes store author in custom fields, not the
            # standard WordPress user system — so this is often empty)
            authors = None
            embedded_authors = post.get("_embedded", {}).get("author", [])
            if embedded_authors:
                names = [a.get("name", "").strip() for a in embedded_authors if a.get("name")]
                if names:
                    authors = "; ".join(names)

            # Excerpt / abstract (also often empty on academic themes)
            abstract = _strip_html(post.get("excerpt", {}).get("rendered", "") or "")
            if abstract and len(abstract) < 80:
                abstract = None

            tags = auto_tag(title, abstract)
            new = upsert_article(
                url, None, title, authors, abstract, pub_date,
                journal_name, "rss", tags=tags,
            )
            total_new += new

        if page >= total_pages:
            break
        page += 1

    log.info("  %s — %d new articles (WP API)", journal_name, total_new)
    return total_new


# ── RSS fetch ──────────────────────────────────────────────────────────────────

def fetch_rss_journal(journal):
    """
    Fetch one RSS journal.

    If the journal config includes an `oai_url`, OAI-PMH is used instead of
    RSS — this gives full archive coverage rather than just the most recent
    items. Incremental runs pass the last-fetch date to the OAI `from` param.

    Returns count of new articles inserted.
    """
    name = journal["name"]
    last = get_last_fetch(name)
    since = last[:10] if last else None   # YYYY-MM-DD

    # ── OAI-PMH path (OJS journals — full archive, incremental support) ───────
    if journal.get("oai_url"):
        total_new = _harvest_oai(journal["oai_url"], name, since_date=since)
        update_fetch_log(name)
        return total_new

    # ── WordPress REST API path (WP journals — full archive over RSS cap) ─────
    if journal.get("wp_api_url"):
        total_new = _harvest_wp_api(journal["wp_api_url"], name, since_date=since)
        update_fetch_log(name)
        return total_new

    # ── Standard RSS/Atom path ────────────────────────────────────────────────
    feed_url = journal["feed_url"]
    log.info("Fetching RSS: %s", name)

    feed = feedparser.parse(
        feed_url,
        agent=USER_AGENT,
        request_headers={"User-Agent": USER_AGENT},
    )

    if feed.bozo and not feed.entries:
        log.warning("  Bad feed for %s: %s", name, feed.bozo_exception)
        return 0

    total_new = 0
    for entry in feed.entries:
        url = getattr(entry, "link", "").strip()
        if not url:
            continue

        title = _strip_html(getattr(entry, "title", ""))
        if not title:
            continue

        authors   = _parse_authors(entry)
        abstract  = _parse_abstract(entry)
        pub_date  = _parse_date(entry)
        tags      = auto_tag(title, abstract)

        new = upsert_article(
            url, None, title, authors, abstract, pub_date, name, "rss",
            tags=tags,
        )
        total_new += new

    update_fetch_log(name)
    log.info("  %s — %d new articles", name, total_new)
    return total_new


def fetch_all():
    """Fetch all RSS journals. Returns total new article count."""
    init_db()
    total = 0
    for journal in RSS_JOURNALS:
        try:
            total += fetch_rss_journal(journal)
        except Exception as e:
            log.error("RSS fetch error for %s: %s", journal["name"], e)
    log.info("RSS fetch complete. Total new: %d", total)
    return total


if __name__ == "__main__":
    fetch_all()
