"""
scrape_lics_refs.py — Scrape reference lists from LiCS HTML galleys
and deposit them into the citations table.

For each LiCS article that has a DOI but no references on CrossRef,
this script:
  1. Visits the OJS landing page to find the HTML galley link
  2. Fetches the HTML galley content (via iframe src)
  3. Extracts the Works Cited / References section
  4. Stores each reference as an unstructured citation record
  5. Stamps references_fetched_at so the article isn't re-processed

Usage:
    python scrape_lics_refs.py                # process all missing
    python scrape_lics_refs.py --dry-run      # preview without writing to DB
    python scrape_lics_refs.py --article 33452  # process a single article ID
"""

import argparse
import hashlib
import logging
import re
import time
import unicodedata

import requests
from bs4 import BeautifulSoup

from db import (
    get_conn,
    init_db,
    upsert_citation,
    mark_references_fetched,
    update_citation_counts,
    get_doi_to_article_id_map,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
BASE_URL = "https://licsjournal.org/index.php/LiCS"
REQUEST_DELAY = 1.0  # seconds between requests

# Volume 3 Issue 1 DOIs to skip (per user request)
SKIP_DOIS = {doi for doi in [
    "10.21623/1.3.1.1", "10.21623/1.3.1.2", "10.21623/1.3.1.3",
    "10.21623/1.3.1.4", "10.21623/1.3.1.5", "10.21623/1.3.1.6",
    "10.21623/1.3.1.7", "10.21623/1.3.1.8", "10.21623/1.3.1.9",
    "10.21623/1.3.1.10", "10.21623/1.3.1.11", "10.21623/1.3.1.12",
    "10.21623/1.3.1.13", "10.21623/1.3.1.14", "10.21623/1.3.1.15",
    "10.21623/1.3.1.16",
]}


# ── HTML fetching ────────────────────────────────────────────────────────────

def _resolve_doi_to_view_url(doi: str) -> str | None:
    """Follow a DOI to get the OJS article view URL."""
    try:
        r = requests.get(
            f"https://doi.org/{doi}",
            headers=HEADERS,
            timeout=15,
            allow_redirects=True,
        )
        if r.status_code == 200 and "licsjournal.org" in r.url:
            # Ensure we have the base /view/ URL (strip any galley suffix)
            url = r.url
            # /article/view/XXXX or /article/view/XXXX/YYYY
            m = re.search(r"(/article/view/\d+)", url)
            if m:
                return f"{BASE_URL}{m.group(1)}"
        return None
    except Exception as e:
        log.warning("  DOI resolution failed for %s: %s", doi, e)
        return None


def _get_html_galley_url(view_url: str) -> str | None:
    """From an OJS landing page, find the HTML galley iframe content URL."""
    try:
        r = requests.get(view_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        # Find HTML galley link
        html_link = None
        for a in soup.find_all("a", class_="obj_galley_link"):
            if "html" in a.get_text(strip=True).lower():
                html_link = a.get("href")
                break

        if not html_link:
            return None

        time.sleep(0.5)

        # Fetch galley page to get iframe src
        r2 = requests.get(html_link, headers=HEADERS, timeout=15)
        soup2 = BeautifulSoup(r2.text, "html.parser")
        iframe = soup2.find("iframe")
        if iframe:
            return iframe.get("src", "").strip()

        return None
    except Exception as e:
        log.warning("  Failed to find HTML galley for %s: %s", view_url, e)
        return None


def _fetch_html_content(url: str) -> str | None:
    """Fetch HTML content from the galley iframe URL."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        ct = r.headers.get("content-type", "")
        if "text/html" not in ct:
            log.debug("  Not HTML content: %s", ct)
            return None
        return r.text
    except Exception as e:
        log.warning("  Failed to fetch content: %s", e)
        return None


# ── Reference extraction ────────────────────────────────────────────────────

def _extract_references(html: str) -> list[str]:
    """
    Extract individual reference strings from an HTML article.

    Strategy:
      1. Find the last occurrence of a "Works Cited" / "References" heading
         in the HTML body (not TOC links near the top).
      2. Parse the HTML after that heading.
      3. Collect <p> tags that contain reference text.
      4. Deduplicate (nested <p> tags in Word-exported HTML cause repeats).
    """
    # Find the last (i.e. body, not TOC) references heading
    patterns = [
        r"(?i)>\s*WORKS\s+CITED\s*<",
        r"(?i)>\s*Works\s+Cited\s*<",
        r"(?i)>\s*REFERENCES\s*<",
        r"(?i)>\s*References\s*<",
        r"(?i)>\s*BIBLIOGRAPHY\s*<",
    ]

    last_pos = -1
    for pat in patterns:
        for m in re.finditer(pat, html):
            if m.start() > last_pos:
                last_pos = m.start()

    if last_pos < 0:
        return []

    # Parse everything after the heading
    refs_html = html[last_pos:]
    soup = BeautifulSoup(refs_html, "html.parser")

    # Collect text from <p> tags, but only "leaf" paragraphs
    # (paragraphs that don't contain other <p> tags)
    raw_refs = []
    for p in soup.find_all("p"):
        # Skip if this <p> contains nested <p> tags
        if p.find("p"):
            continue

        text = p.get_text(strip=True)
        if not text or len(text) < 20:
            continue

        # Stop at endnotes section
        lower = text.lower()
        if lower.startswith("works cited") or lower.startswith("references"):
            continue
        if lower.startswith("endnotes") or lower.startswith("notes"):
            break
        if re.match(r"^\[([ivx\d]+)\]", text):
            break

        raw_refs.append(text)

    # Deduplicate while preserving order
    seen = set()
    refs = []
    for r in raw_refs:
        # Normalize for dedup (strip whitespace variance)
        key = re.sub(r"\s+", " ", r)[:100]
        if key not in seen:
            seen.add(key)
            refs.append(r)

    return refs


def _extract_doi_from_ref(ref_text: str) -> str | None:
    """Try to extract a DOI from a reference string."""
    m = re.search(r"(10\.\d{4,}/[^\s,;\"'<>]+)", ref_text)
    if m:
        doi = m.group(1).rstrip(".")
        return doi.lower()
    return None


def _make_ref_key(ref_text: str) -> str:
    """
    Generate a unique key for a reference without a DOI.
    Uses a short hash of the normalized text so each reference
    gets its own row in the citations table.
    """
    return "ref:" + hashlib.md5(ref_text.encode("utf-8")).hexdigest()[:12]


def _normalize_for_match(s: str) -> str:
    """Normalize a string for fuzzy title matching."""
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[^a-z0-9 ]", "", s.lower())
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _build_title_index() -> dict[str, int]:
    """
    Build a lookup of normalized article titles -> article IDs.
    For matching scraped references against the database.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title FROM articles WHERE title IS NOT NULL"
        ).fetchall()

    index = {}
    for row in rows:
        norm = _normalize_for_match(row["title"])
        if norm and len(norm) > 15:
            index[norm] = row["id"]
    return index


def _match_ref_to_article(ref_text: str, title_index: dict) -> int | None:
    """
    Try to match a reference string to an article in the database
    by extracting the quoted title and looking it up.
    """
    # Extract title in quotes: "Title Here" or \u201cTitle Here\u201d
    m = re.search(r'[\u201c"]\s*(.+?)\s*[\u201d"]', ref_text)
    if not m:
        return None

    ref_title = _normalize_for_match(m.group(1))
    if len(ref_title) < 15:
        return None

    # Exact match
    if ref_title in title_index:
        return title_index[ref_title]

    # Substring match: check if ref title is contained in any DB title
    for db_title, article_id in title_index.items():
        if ref_title in db_title or db_title in ref_title:
            return article_id

    return None


# ── Main processing ─────────────────────────────────────────────────────────

def get_articles_to_process(article_id: int | None = None) -> list[dict]:
    """
    Get LiCS articles that have a DOI, have references_fetched_at set
    (from the prefix backfill), but have 0 citations stored — meaning
    CrossRef had no references but we can try scraping them.

    If article_id is specified, return just that one.
    """
    with get_conn() as conn:
        if article_id:
            rows = conn.execute("""
                SELECT id, title, doi FROM articles
                WHERE id = ? AND doi IS NOT NULL
            """, (article_id,)).fetchall()
        else:
            # All LiCS articles with DOIs that need scraping:
            # either no citations at all, or only scraped refs (ref:xxx keys)
            # that need re-matching with the title index
            rows = conn.execute("""
                SELECT a.id, a.title, a.doi
                FROM articles a
                WHERE a.journal LIKE '%Literacy in Composition%'
                  AND a.doi IS NOT NULL
                  AND a.doi LIKE '10.21623/%'
                ORDER BY a.doi
            """).fetchall()

    return [dict(r) for r in rows]


def process_article(article: dict, doi_map: dict, title_index: dict, dry_run: bool = False) -> int:
    """
    Scrape references for one article. Returns count of references found.
    """
    article_id = article["id"]
    doi = article["doi"]
    title = article["title"]

    if doi in SKIP_DOIS:
        log.info("  Skipping (Vol 3 Issue 1): %s", doi)
        return 0

    log.info("Processing id=%d doi=%s", article_id, doi)
    log.info("  Title: %s", title[:80])

    # Step 1: Resolve DOI to OJS view URL
    view_url = _resolve_doi_to_view_url(doi)
    if not view_url:
        log.warning("  Could not resolve DOI to OJS URL")
        return 0

    time.sleep(REQUEST_DELAY)

    # Step 2: Find HTML galley
    galley_url = _get_html_galley_url(view_url)
    if not galley_url:
        log.warning("  No HTML galley available (PDF only)")
        return 0

    time.sleep(REQUEST_DELAY)

    # Step 3: Fetch HTML content
    html = _fetch_html_content(galley_url)
    if not html:
        log.warning("  Could not fetch HTML content")
        return 0

    # Step 4: Extract references
    refs = _extract_references(html)
    log.info("  Found %d references", len(refs))

    if dry_run:
        for i, ref in enumerate(refs[:5], 1):
            log.info("    %d. %s", i, ref[:120])
        if len(refs) > 5:
            log.info("    ... and %d more", len(refs) - 5)
        return len(refs)

    # Step 5: Clear old scraped citations (ref:xxx keys) so we can re-insert with matches
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM citations WHERE source_article_id = ? AND target_doi LIKE 'ref:%'",
            (article_id,),
        )
        conn.commit()

    # Step 6: Store citations
    citations_inserted = 0
    internal_matches = 0
    for ref in refs:
        ref_doi = _extract_doi_from_ref(ref)
        target_id = None

        # Try DOI-based match first
        if ref_doi:
            target_id = doi_map.get(ref_doi)

        # Fall back to title-based match
        if target_id is None:
            target_id = _match_ref_to_article(ref, title_index)

        if target_id is not None:
            internal_matches += 1

        # Use a hash-based key for refs without DOIs so each gets its own row
        cite_doi = ref_doi or _make_ref_key(ref)

        citations_inserted += upsert_citation(
            source_article_id=article_id,
            target_doi=cite_doi,
            target_article_id=target_id,
            raw_reference={"unstructured": ref},
        )

    log.info("  Internal matches: %d", internal_matches)

    # Step 6: Update references_fetched_at
    mark_references_fetched(article_id, crossref_cited_by_count=None)

    log.info("  Stored %d citation records", citations_inserted)
    return citations_inserted


def run(article_id=None, dry_run=False):
    init_db()

    articles = get_articles_to_process(article_id=article_id)
    log.info("Articles to process: %d", len(articles))

    if not articles:
        log.info("Nothing to do.")
        return

    doi_map = get_doi_to_article_id_map()
    log.info("DOI map loaded: %d entries", len(doi_map))

    title_index = _build_title_index()
    log.info("Title index loaded: %d entries", len(title_index))

    total_refs = 0
    processed = 0
    skipped = 0

    for article in articles:
        try:
            count = process_article(article, doi_map, title_index, dry_run=dry_run)
            if count > 0:
                total_refs += count
                processed += 1
            else:
                skipped += 1
        except Exception as e:
            log.error("  Error processing %s: %s", article["doi"], e)
            skipped += 1

        time.sleep(REQUEST_DELAY)

    log.info(
        "Done. Processed: %d, Skipped: %d, Total references: %d",
        processed, skipped, total_refs,
    )

    if not dry_run:
        log.info("Recomputing citation counts...")
        update_citation_counts()
        log.info("Citation counts updated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape LiCS references from HTML galleys")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to DB")
    parser.add_argument("--article", type=int, help="Process a single article ID")
    args = parser.parse_args()

    run(article_id=args.article, dry_run=args.dry_run)
