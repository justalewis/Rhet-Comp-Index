"""
scrape_ccdp.py — Ethical metadata scraper for Computers and Composition Digital Press.

Source:  https://ccdigitalpress.org/books
Content: 27 open-access digital books (monographs + edited collections)

What we collect (metadata only — no full text):
  - Book-level:  title, editors/authors, description, pub date, ISBN, URL
  - Chapter-level: title, authors, URL, parent book title

Ethical scraping principles (cf. ethical_scraping_principles.txt):
  1. robots.txt checked 2026-04-07: "User-agent: * / Disallow:" — fully open
  2. Rate-limited: minimum 5 seconds between requests
  3. Only publicly visible bibliographic metadata
  4. No paywalled or restricted content
  5. User-Agent identifies Pinakes and provides contact email
  6. Limitations accepted; gaps documented
  7. Behaves like a careful human visitor
  8. Data stored accurately as published
  9. Prepared to remove on request

Output: ccdp_scraped.json (intermediate file for review before ingestion)

Usage:
    python scrape_ccdp.py              # scrape to JSON
    python scrape_ccdp.py --ingest     # scrape + ingest into Pinakes DB
"""

import os
import re
import html
import json
import sys
import time
import logging
import urllib.request
import urllib.error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

BOOKS_URL = "https://ccdigitalpress.org/books"
BASE_URL = "https://ccdigitalpress.org"
JOURNAL_NAME = "Computers and Composition Digital Press"
REQUEST_DELAY = 5        # seconds between requests (principle #2)
USER_AGENT = "Pinakes/1.0 (scholarly-index; mailto:justalewis1@gmail.com)"
OUTPUT_FILE = os.path.join("data", "seeds", "ccdp_scraped.json")


# ── HTTP helper ──────────────────────────────────────────────────────────────

def fetch(url: str) -> str:
    """Fetch a URL with identified User-Agent and polite delay."""
    log.info("  GET %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        log.warning("  HTTP %d for %s", e.code, url)
        return ""
    except urllib.error.URLError as e:
        log.warning("  URL error for %s: %s", url, e.reason)
        return ""


def polite_delay():
    """Wait between requests to protect server infrastructure (principle #2)."""
    time.sleep(REQUEST_DELAY)


# ── HTML helpers ─────────────────────────────────────────────────────────────

def strip_tags(s: str) -> str:
    """Remove HTML tags, decode entities, collapse whitespace."""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def normalize_url(href: str, base: str = BASE_URL) -> str:
    """Ensure a URL is absolute and uses https."""
    href = href.strip()
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http"):
        return href.replace("http://", "https://")
    if href.startswith("/"):
        return base.rstrip("/") + href
    return base.rstrip("/") + "/" + href


# ── Author parsing ───────────────────────────────────────────────────────────

def parse_authors(raw: str) -> str | None:
    """
    Normalize author/editor strings to semicolon-separated format.
    Strips editorial role prefixes (Eds., Ed., Edited by, etc.).
    """
    if not raw or not raw.strip():
        return None
    s = raw.strip()

    # Strip editorial/role prefixes
    s = re.sub(
        r"^(?:Eds?\.?\s+|Edited\s+by\s+|Directed\s+and\s+Produced\s+by\s+|"
        r"Ed\.\s+|featuring\s+)",
        "", s, flags=re.IGNORECASE,
    ).strip()

    # Strip trailing "(eds.)", "(ed.)", "eds." etc.
    s = re.sub(r"\s*\(?eds?\.?\)?\s*$", "", s, flags=re.IGNORECASE).strip()

    # Already semicolon-separated
    if ";" in s:
        return "; ".join(n.strip() for n in s.split(";") if n.strip())

    # Normalize connectors
    s = re.sub(r"\s+&\s+", ", ", s)
    s = re.sub(r",?\s+and\s+", ", ", s, flags=re.IGNORECASE)

    parts = [p.strip().strip(",").strip() for p in s.split(",")]
    names = [p for p in parts if p and p.lower() not in ("eds.", "ed.", "jr.", "sr.")]
    return "; ".join(names) if names else raw.strip()


# ── Date parsing ─────────────────────────────────────────────────────────────

MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}


def parse_date(date_str: str) -> str | None:
    """Parse 'Mon YYYY' or 'Month YYYY' → 'YYYY-MM-01'."""
    if not date_str:
        return None
    m = re.search(r"(\w+)\s+(\d{4})", date_str.strip())
    if not m:
        return None
    month_str = m.group(1).lower()[:3]
    year = m.group(2)
    month = MONTH_MAP.get(month_str)
    if not month:
        return f"{year}-01-01"
    return f"{year}-{month}-01"


# ── Level 1: Parse the /books listing page ───────────────────────────────────
#
# HTML structure (observed April 2026):
#
#   <div class="row book-block">
#     <div class="col-sm-4 col-xs-12">
#       <figure><a href="/slug"><img .../></a></figure>
#     </div>
#     <div class="col-sm-8">
#       <h4 id="title"><a href="/slug">Title</a></h4>
#       <h5 id="author-name">Author Name</h5>
#       <p class="book-link">
#         <strong>Read the book:</strong>
#         <a href="https://...ccdigitalpress.org/book/slug">...</a>
#       </p>
#       <div><p>Description...</p></div>
#       <p class="publication-date"><strong>Publication date:</strong> Month Year</p>
#     </div>
#   </div>

def scrape_books_listing(page_html: str) -> list[dict]:
    """
    Parse the /books page using the book-block div structure.
    Returns list of dicts: title, authors, description, date, url.
    """
    books = []

    # Split page into per-book blocks
    blocks = re.split(r'<div\s+class="row\s+book-block">', page_html)
    if len(blocks) < 2:
        log.warning("No book-block divs found on listing page")
        return books

    for block in blocks[1:]:  # skip the first segment (before first book)
        book = {
            "title": None,
            "authors": None,
            "description": None,
            "date": None,
            "url": None,
            "isbn": None,
            "chapters": [],
        }

        # Title: inside <h4 ...><a href="...">Title</a></h4>
        title_match = re.search(
            r'<h4[^>]*>\s*<a\s+href="[^"]*">(.+?)</a>\s*</h4>',
            block, re.DOTALL,
        )
        if title_match:
            book["title"] = strip_tags(title_match.group(1))

        # Authors: <h5 id="author-name" ...>Author Name</h5>
        author_match = re.search(
            r'<h5[^>]*id="author-name"[^>]*>(.+?)</h5>',
            block, re.DOTALL,
        )
        if author_match:
            book["authors"] = parse_authors(strip_tags(author_match.group(1)))

        # Book URL: inside <p class="book-link"> <a href="...">
        url_match = re.search(
            r'class="book-link".*?<a\s+href="([^"]+)"',
            block, re.DOTALL,
        )
        if url_match:
            book["url"] = normalize_url(url_match.group(1))
        elif title_match:
            # Fallback: use the title link href
            href_match = re.search(r'<h4[^>]*>\s*<a\s+href="([^"]*)"', block)
            if href_match:
                book["url"] = normalize_url(href_match.group(1))

        # Description: the <div> content between book-link and publication-date
        # Grab everything between </p> after book-link and the "More info" link
        desc_match = re.search(
            r'class="book-link".*?</p>\s*(.*?)<p>\s*<a\s+href="[^"]*">\s*More\s+info',
            block, re.DOTALL | re.IGNORECASE,
        )
        if desc_match:
            desc_raw = strip_tags(desc_match.group(1))
            if len(desc_raw) > 30:
                book["description"] = desc_raw

        # Publication date: <p class="publication-date">...: Month Year</p>
        date_match = re.search(
            r'class="publication-date"[^>]*>.*?</strong>\s*(.+?)</p>',
            block, re.DOTALL,
        )
        if date_match:
            book["date"] = parse_date(strip_tags(date_match.group(1)))

        if book["title"] and book["url"]:
            books.append(book)
        else:
            log.warning("Skipping block: missing title or URL")

    log.info("Parsed %d books from listing page", len(books))
    return books


# ── Level 2: Parse individual book pages for chapters ────────────────────────
#
# CCDP books use wildly different HTML structures for their TOCs.
# Common patterns for chapter links:
#   - 01_poe.html, 02_crow.html          (numbered .html files)
#   - buckner-daley/index.html            (author-named directories)
#   - chapters/Histories-Atkins-Reilly/   (named chapter dirs)
#   - ch-introduction.html, ch-alluvial.html (ch- prefixed files)
#
# We identify chapters by:
#   1. Finding internal links that match chapter-like patterns
#   2. Filtering out known non-chapter links (CSS, images, nav, metadata)
#   3. Extracting the link text as the chapter title

# Files and paths that are never chapters
NON_CHAPTER_PATTERNS = re.compile(
    r"(?:"
    r"\.(?:css|js|jpg|jpeg|png|gif|svg|ico|mp3|mp4|wav|pdf|epub|zip)$"
    r"|^#"
    r"|assets/"
    r"|files/"
    r"|sounds/"
    r"|^https?://"     # external links
    r"|^mailto:"
    r"|index\.html$"   # book landing page itself
    r"|^/$"
    r")",
    re.IGNORECASE,
)

# Link text that indicates non-chapter navigation
NON_CHAPTER_TEXT = {
    "table of contents", "contributors", "contributor bios",
    "biographies", "abstracts", "abstract", "playlist",
    "references", "credits", "home", "next", "prev", "previous",
    "back to", "return to", "bibliography", "works cited",
    "audio-only", "audio only", "more info", "read the book",
    "read more", "acknowledgments", "permissions", "citation",
    "accessibility", "bios", "ccdp", "contact",
    "author biographies", "citation information",
    "requests for permissions", "accessibility statement",
    "indexing metadata", "about the editors and designer",
    "about the editors", "about the authors", "about the author",
    "chapters",  # navigation label, not a real chapter
    "dedication",
}


def scrape_book_page(book: dict, page_html: str) -> dict:
    """
    Enrich a book dict with ISBN and chapter data from its individual page.
    """
    text = strip_tags(page_html)

    # ISBN
    isbn_match = re.search(r"(?:ISBN[:\s]*)([\d\-]{10,17})", text)
    if isbn_match:
        book["isbn"] = isbn_match.group(1).strip()

    # Extract all internal links with their text
    all_links = re.findall(
        r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        page_html,
        re.IGNORECASE | re.DOTALL,
    )

    book_url_base = book["url"].rstrip("/")
    seen_urls = set()
    candidates = []

    for href, link_html in all_links:
        href = href.strip()

        # Skip non-chapter links
        if NON_CHAPTER_PATTERNS.search(href):
            continue
        # Skip javascript: links
        if href.startswith("javascript:"):
            continue

        link_text = strip_tags(link_html).strip()

        # Skip empty or very short link text
        if len(link_text) < 3:
            continue

        # Skip known non-chapter text
        if link_text.lower().strip("., ") in NON_CHAPTER_TEXT:
            continue
        if any(skip in link_text.lower() for skip in [
            "table of contents", "contributor", "back to",
            "return to", "more info", "read the book",
        ]):
            continue

        # Skip metadata/admin pages
        if "collection_meta" in href or "bios.html" in href:
            continue

        # Build absolute URL
        if href.startswith("http"):
            chapter_url = href
        else:
            chapter_url = book_url_base + "/" + href.lstrip("./")

        chapter_url = chapter_url.replace("http://", "https://")

        # Deduplicate: strip query params and fragments for dedup check
        dedup_key = chapter_url.split("?")[0].split("#")[0].rstrip("/")
        if dedup_key in seen_urls:
            continue
        seen_urls.add(dedup_key)

        candidates.append({
            "title": link_text,
            "url": chapter_url,
            "authors": None,
        })

    # Heuristic: if we have too many candidates (> 30), the page likely has
    # lots of non-chapter navigation links.  In that case, try to narrow down
    # by looking for numbered patterns or "chapter" in the href/text.
    if len(candidates) > 30:
        filtered = [c for c in candidates if re.search(
            r"(?:ch[-_]|chapter|\d{2}[-_]|\d{1,2}\.)", c["url"], re.IGNORECASE
        )]
        if filtered:
            candidates = filtered

    # Extract chapter authors using TOC context
    _extract_chapter_authors(book, candidates, page_html)

    book["chapters"] = candidates
    return book


def _extract_chapter_authors(book: dict, chapters: list[dict], page_html: str):
    """
    Best-effort extraction of chapter authors from page context.
    Looks for author names near each chapter's link text.
    """
    for chapter in chapters:
        title_escaped = re.escape(chapter["title"][:30])

        # Pattern 1: "Title" followed by em-dash/dash and names
        m = re.search(
            title_escaped + r'\s*</a>.*?(?:\s*[-—–]\s*|\s*<br\s*/?>\s*)([\w][\w\s.,&]+)',
            page_html, re.IGNORECASE | re.DOTALL,
        )
        if m:
            candidate = strip_tags(m.group(1)).strip()
            # Plausible author name: contains capital letter, reasonable length
            if (5 < len(candidate) < 150
                    and re.search(r"[A-Z]", candidate)
                    and not any(w in candidate.lower() for w in [
                        "chapter", "http", "read", "abstract", "section",
                        "part ", "the ", "this ", "in ", "an ", "a ",
                    ])):
                chapter["authors"] = parse_authors(candidate)


# ── Main scraping orchestration ──────────────────────────────────────────────

def scrape_all() -> list[dict]:
    """
    Scrape all CCDP books: listing page + individual book pages.
    Returns enriched book list.
    """
    log.info("=" * 60)
    log.info("CCDP Scraper — Computers and Composition Digital Press")
    log.info("Ethical scraping: %ds delay, metadata only, identified UA",
             REQUEST_DELAY)
    log.info("=" * 60)

    # Level 1: book listing
    log.info("Fetching book listing from %s", BOOKS_URL)
    listing_html = fetch(BOOKS_URL)
    if not listing_html:
        log.error("Failed to fetch books listing page")
        return []

    books = scrape_books_listing(listing_html)
    log.info("Extracted %d books from listing page", len(books))

    # Level 2: individual book pages for chapter data + ISBN
    for i, book in enumerate(books):
        polite_delay()
        log.info("[%d/%d] %s", i + 1, len(books), book["title"][:60])
        page_html = fetch(book["url"])
        if page_html:
            scrape_book_page(book, page_html)
            ch_count = len(book["chapters"])
            isbn_note = f"  ISBN: {book['isbn']}" if book.get("isbn") else ""
            log.info("  → %d chapters%s", ch_count, isbn_note)
        else:
            log.warning("  → Failed to fetch book page")

    # Summary
    total_chapters = sum(len(b["chapters"]) for b in books)
    books_with_ch = sum(1 for b in books if b["chapters"])
    log.info("=" * 60)
    log.info("Scrape complete: %d books (%d with chapters), %d total chapters",
             len(books), books_with_ch, total_chapters)
    log.info("=" * 60)

    return books


def save_json(books: list[dict], path: str = OUTPUT_FILE):
    """Save scraped data to JSON for review before ingestion."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(books, f, indent=2, ensure_ascii=False)
    log.info("Saved to %s", path)


# ── Ingestion into Pinakes DB ────────────────────────────────────────────────

def ingest(books: list[dict]):
    """
    Ingest scraped CCDP data into BOTH the articles and books tables.

    Articles table (for main search, tagging, FTS):
      - Each book → article entry (editors as authors, description as abstract)
      - Each chapter → article entry (chapter authors, no abstract)
      - journal = "Computers and Composition Digital Press"
      - source = "scrape", oa_status = "gold"

    Books table (for /books monograph UI):
      - Each book → record_type='book', book_type='edited collection' or 'monograph'
      - Each chapter → record_type='chapter' with parent_id linking to book
    """
    from db import init_db, upsert_article, upsert_book
    from tagger import auto_tag

    init_db()

    added_articles = 0
    added_book_records = 0

    for book in books:
        # ── Articles table ───────────────────────────────────────
        tags = auto_tag(book["title"], book.get("description"))
        result = upsert_article(
            url=book["url"],
            doi=None,
            title=book["title"],
            authors=book.get("authors"),
            abstract=book.get("description"),
            pub_date=book.get("date"),
            journal=JOURNAL_NAME,
            source="scrape",
            keywords=None,
            tags=tags,
            oa_status="gold",
            oa_url=book["url"],
        )
        added_articles += result

        for chapter in book.get("chapters", []):
            chapter_tags = auto_tag(chapter["title"], None)
            result = upsert_article(
                url=chapter["url"],
                doi=None,
                title=chapter["title"],
                authors=chapter.get("authors") or book.get("authors"),
                abstract=None,
                pub_date=book.get("date"),
                journal=JOURNAL_NAME,
                source="scrape",
                keywords=None,
                tags=chapter_tags,
                oa_status="gold",
                oa_url=chapter["url"],
            )
            added_articles += result

        # ── Books table ──────────────────────────────────────────
        # Determine book type: if chapters exist, it's an edited collection
        has_chapters = len(book.get("chapters", [])) > 0
        book_type = "edited collection" if has_chapters else "monograph"

        # Extract year from date string (YYYY-MM-DD → YYYY)
        year = None
        if book.get("date"):
            try:
                year = int(book["date"][:4])
            except (ValueError, TypeError):
                pass

        book_id, is_new = upsert_book(
            doi=None,
            isbn=book.get("isbn"),
            title=book["title"],
            record_type="book",
            book_type=book_type,
            editors=book.get("authors"),
            authors=None,
            publisher="Computers and Composition Digital Press / Utah State University Press",
            year=year,
            abstract=book.get("description"),
            source="scrape",
        )
        if is_new:
            added_book_records += 1

        # Insert chapters into books table with parent_id
        for chapter in book.get("chapters", []):
            _, ch_new = upsert_book(
                doi=None,
                isbn=None,
                title=chapter["title"],
                record_type="chapter",
                book_type=None,
                editors=None,
                authors=chapter.get("authors") or book.get("authors"),
                publisher="Computers and Composition Digital Press / Utah State University Press",
                year=year,
                parent_id=book_id,
                source="scrape",
            )
            if ch_new:
                added_book_records += 1

    log.info("Ingested: %d new article entries, %d new book records",
             added_articles, added_book_records)


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    books = scrape_all()
    save_json(books)

    if "--ingest" in sys.argv:
        ingest(books)
    else:
        total_chapters = sum(len(b["chapters"]) for b in books)
        log.info("Run with --ingest to load into Pinakes DB")
        log.info("Review %s first to verify data accuracy (principle #8)",
                 OUTPUT_FILE)
