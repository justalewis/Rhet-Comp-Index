"""
fetch_siup.py — Scrape Southern Illinois University Press for rhet/comp books.

Harvests books from two BISAC subject categories on siupress.com:
  - LAN015000  Language Arts & Disciplines / Rhetoric         (~179 books)
  - LAN005000  Language Arts & Disciplines / Composition      (~112 books)

Pagination is server-rendered (no JavaScript needed). Book detail pages
expose JSON-LD structured data for reliable metadata extraction.

CrossRef DOI lookup is attempted for each book by ISBN.

Data extracted per book:
  - Title + optional subtitle
  - Author(s) or Editor(s) with role
  - ISBN (paperback preferred, from URL and/or edition divs)
  - Publication year
  - Series name (stored first in semicolon-separated subjects)
  - Description / abstract
  - Subject categories

Dedup key: ISBN + publisher (preferred) or title + publisher.

Usage:
    python fetch_siup.py          # insert/update all rhet/comp books
    python fetch_siup.py --dry    # print what would be fetched, no DB writes
    python fetch_siup.py --limit 20   # process at most N books (for testing)
"""

import json
import re
import sys
import io
import time
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from bs4 import BeautifulSoup

from db import init_db, get_conn

# ── Constants ────────────────────────────────────────────────────────────────

PUBLISHER = "Southern Illinois University Press"
SOURCE    = "siup"
BASE      = "https://www.siupress.com"
HDRS      = {"User-Agent": "Pinakes/1.0 (mailto:rhetcompindex@gmail.com)"}
DELAY     = 0.8   # seconds between requests — be polite

CROSSREF_URL = "https://api.crossref.org/works"
CROSSREF_HDRS = {
    "User-Agent": "Pinakes/1.0 (mailto:rhetcompindex@gmail.com)"
}

# BISAC categories to harvest
CATEGORIES = [
    ("LAN015000", "Language Arts & Disciplines / Rhetoric",             12),
    ("LAN005000", "Language Arts & Disciplines / Composition",           7),
]

# ── Regex ────────────────────────────────────────────────────────────────────

_ISBN13_RE   = re.compile(r'\b(97[89]\d{10})\b')
_YEAR_RE     = re.compile(r'\b((?:19[89]\d|20[0-3]\d))\b')
_PAGES_RE    = re.compile(r'(\d+)\s+Pages?', re.IGNORECASE)
_DATE_RE     = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{4})')   # MM/DD/YYYY


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(url: str, params: dict | None = None) -> BeautifulSoup | None:
    """GET a URL (with optional params); return BeautifulSoup or None."""
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=HDRS, timeout=30)
            time.sleep(DELAY)
            if r.status_code == 200:
                return BeautifulSoup(r.text, "html.parser")
            if r.status_code == 404:
                return None
            print(f"  !! HTTP {r.status_code} for {url} (attempt {attempt+1})")
        except requests.RequestException as exc:
            print(f"  !! {exc} (attempt {attempt+1})")
            time.sleep(3)
    return None


def _get_json(url: str, params: dict | None = None) -> dict | None:
    """GET JSON (CrossRef) with one retry."""
    for attempt in range(2):
        try:
            r = requests.get(url, params=params, headers=CROSSREF_HDRS, timeout=20)
            time.sleep(0.3)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except requests.RequestException as exc:
            print(f"  !! CrossRef error: {exc}")
            time.sleep(2)
    return None


# ── Listing page scraper ───────────────────────────────────────────────────────

def collect_book_urls(category_code: str, max_pages: int) -> list[str]:
    """
    Scrape all paginated search-results pages for a BISAC category and return
    a deduplicated list of absolute book detail URLs.
    """
    urls = []
    seen = set()

    for page_num in range(1, max_pages + 1):
        url = f"{BASE}/search-results/"
        params = {
            "category":    category_code,
            "page_number": page_num,
            "amount":      "16",
        }
        soup = _get(url, params=params)
        if soup is None:
            print(f"  !! Failed to fetch page {page_num}")
            break

        # Book links have hrefs matching /{13-digit-isbn}/{slug}/
        for a in soup.select("a[href]"):
            href = a["href"]
            # Normalise to absolute URL
            if href.startswith("/"):
                href = BASE + href
            # Filter: must match /{isbn13}/{slug} pattern
            if re.search(r'/97[89]\d{10}/', href):
                if href not in seen:
                    seen.add(href)
                    urls.append(href)

        # Check if we've reached the last page
        total_tag = soup.select_one("h2.supapress-results-count")
        if total_tag:
            text = total_tag.get_text()
            m = re.search(r'of\s+(\d+)', text)
            if m:
                total = int(m.group(1))
                fetched_so_far = page_num * 16
                if fetched_so_far >= total:
                    break

    return urls


# ── JSON-LD extractor ─────────────────────────────────────────────────────────

def _extract_jsonld(soup: BeautifulSoup) -> dict:
    """Extract Book JSON-LD from the page, return dict or {}."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict) and data.get("@type") == "Book":
                return data
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("@type") == "Book":
                        return item
        except (json.JSONDecodeError, AttributeError):
            pass
    return {}


# ── Book detail page scraper ───────────────────────────────────────────────────

def scrape_book(url: str) -> dict | None:
    """
    Scrape a single SIU Press book detail page.
    Returns a metadata dict or None if the page cannot be parsed.
    """
    soup = _get(url)
    if soup is None:
        return None

    result = {}

    # ── ISBN from URL ───────────────────────────────────────────────────────
    isbn_from_url = None
    m = re.search(r'/(97[89]\d{10})/', url)
    if m:
        isbn_from_url = m.group(1)

    # ── JSON-LD (most reliable source) ─────────────────────────────────────
    jld = _extract_jsonld(soup)

    isbn_jld       = jld.get("isbn") or None
    year_jld       = None
    date_published = jld.get("datePublished") or ""
    if date_published:
        ym = _YEAR_RE.search(date_published)
        if ym:
            year_jld = int(ym.group(1))

    # ── Title ───────────────────────────────────────────────────────────────
    h1 = soup.find("h1")
    title = (h1.get_text(strip=True) if h1 else "").strip()
    if not title:
        title = (jld.get("name") or "").strip()
    if not title:
        return None

    # Optional subtitle in <h2> immediately after <h1>
    h2 = h1.find_next_sibling("h2") if h1 else None
    if h2:
        subtitle = h2.get_text(strip=True)
        if subtitle:
            title = f"{title}: {subtitle}"

    # ── Contributors ────────────────────────────────────────────────────────
    # Look for the contributor paragraph: plain text "By " or "Edited by "
    # followed by <a href="/author/..."> links.
    book_type     = "monograph"
    authors_str   = None
    editors_str   = None

    # SIU Press (Supadu CMS) puts contributor info in <p class="sp__the-author">
    # e.g. "Edited by Michael-John DePalma , Paul Lynch and Jeff Ringer"
    contrib_para = soup.find("p", class_="sp__the-author")
    if not contrib_para:
        for a in soup.find_all("a", href=re.compile(r'^/author/')):
            contrib_para = a.find_parent("p")
            if contrib_para:
                break

    if contrib_para:
        para_text = contrib_para.get_text(" ", strip=True)
        names = [a.get_text(strip=True) for a in contrib_para.find_all("a", href=re.compile(r'^/author/'))]
        if not names:
            role_stripped = re.sub(r'^(?:Edited\s+by|By|Translated\s+by)\s+', '', para_text, flags=re.IGNORECASE)
            names = [n.strip() for n in re.split(r',|\band\b', role_stripped) if n.strip()]
        names_str = ", ".join(names) if names else None

        if re.search(r'\bEdited\s+by\b', para_text, re.IGNORECASE):
            book_type   = "edited-collection"
            editors_str = names_str
        elif re.search(r'\bTranslated\s+by\b', para_text, re.IGNORECASE):
            authors_str = names_str   # treat translators as authors
        else:
            authors_str = names_str

    # Fall back to JSON-LD author
    if not authors_str and not editors_str:
        jld_author = jld.get("author")
        if jld_author:
            if isinstance(jld_author, dict):
                authors_str = jld_author.get("name")
            elif isinstance(jld_author, list):
                authors_str = ", ".join(a.get("name", "") for a in jld_author if a.get("name"))

    # ── Edition divs — ISBN + Year ──────────────────────────────────────────
    # SIU Press uses Supadu CMS. Each format is in a <div class="sp__buy-format">
    # with text like "Paperback 9780809338672 Published: 11/27/2023 $40.00 BUY"
    # ISBNs are also in <li class="sp__isbn13">, dates in <li class="sp__published">.
    isbn_pb  = None
    isbn_hb  = None
    year_pb  = None
    year_hb  = None

    for fmt_div in soup.find_all("div", class_="sp__buy-format"):
        text = fmt_div.get_text(" ", strip=True)
        fmt_lower = text.lower()

        isbn_m = _ISBN13_RE.search(text)
        if not isbn_m:
            continue
        candidate_isbn = isbn_m.group(1)
        # Skip eBook ISBNs
        if "ebook" in fmt_lower or "e-book" in fmt_lower:
            continue

        yr = None
        dm = _DATE_RE.search(text)
        if dm:
            yr = int(dm.group(3))

        if "paperback" in fmt_lower or "paper" in fmt_lower:
            isbn_pb = candidate_isbn
            if yr:
                year_pb = yr
        elif "hardback" in fmt_lower or "hardcover" in fmt_lower or "cloth" in fmt_lower:
            isbn_hb = candidate_isbn
            if yr:
                year_hb = yr
        else:
            # Unknown print format — use as fallback
            if not isbn_pb and not isbn_hb:
                isbn_hb = candidate_isbn
                if yr:
                    year_hb = yr

    # Also try sp__published li tags for year if we didn't get it from format divs
    if not year_pb and not year_hb:
        for pub_li in soup.find_all("li", class_="sp__published"):
            dm = _DATE_RE.search(pub_li.get_text(strip=True))
            if dm:
                year_hb = int(dm.group(3))
                break

    # Choose best ISBN: paperback > hardback > url > jld
    isbn = isbn_pb or isbn_hb or isbn_from_url or isbn_jld

    # Choose best year: paperback > hardback > jld
    year = year_pb or year_hb or year_jld

    # ── Pages ───────────────────────────────────────────────────────────────
    pages = None
    for p in soup.find_all("p"):
        pm = _PAGES_RE.search(p.get_text(strip=True))
        if pm:
            pages = int(pm.group(1))
            break

    # ── Abstract ─────────────────────────────────────────────────────────────
    # The description lives in a tab panel. We look for the first substantial
    # block of prose that isn't metadata (author bios, etc.).
    abstract = None

    # SIU Press: description lives in <div class="tabs__panel tabs__panel--description">
    desc_div = soup.find("div", class_="tabs__panel--description")
    if not desc_div:
        # Broader fallback
        desc_div = soup.find("div", class_=re.compile(r'description'))
    if desc_div:
        paras = [p.get_text(" ", strip=True) for p in desc_div.find_all("p")
                 if len(p.get_text(strip=True)) > 60]
        if paras:
            abstract = " ".join(paras)[:3000].strip() or None

    # Fallback: JSON-LD description
    if not abstract:
        abstract = (jld.get("description") or "").strip() or None

    # ── Series ───────────────────────────────────────────────────────────────
    series = None
    series_a = soup.find("a", href=re.compile(r'/search-results/\?series='))
    if series_a:
        series = series_a.get_text(strip=True)

    # ── Subject categories ───────────────────────────────────────────────────
    category_links = soup.find_all("a", href=re.compile(r'/search-results/\?category='))
    subject_names = [a.get_text(strip=True) for a in category_links if a.get_text(strip=True)]

    # Build subjects string: series first (if any), then categories
    subjects_parts = []
    if series:
        subjects_parts.append(series)
    subjects_parts.extend(subject_names)
    # Deduplicate while preserving order
    seen_s = set()
    subjects_deduped = []
    for s in subjects_parts:
        if s.lower() not in seen_s:
            seen_s.add(s.lower())
            subjects_deduped.append(s)
    subjects = "; ".join(subjects_deduped) if subjects_deduped else None

    return {
        "title":     title,
        "authors":   authors_str,
        "editors":   editors_str,
        "isbn":      isbn,
        "year":      year,
        "pages":     pages,
        "abstract":  abstract,
        "series":    series,
        "subjects":  subjects,
        "book_type": book_type,
        "url":       url,
    }


# ── CrossRef DOI lookup ────────────────────────────────────────────────────────

def lookup_doi_by_isbn(isbn: str) -> str | None:
    """Query CrossRef for a DOI matching the given ISBN-13."""
    if not isbn:
        return None
    data = _get_json(CROSSREF_URL, params={
        "filter": f"isbn:{isbn}",
        "rows":   1,
        "select": "DOI",
        "mailto": "rhetcompindex@gmail.com",
    })
    if not data:
        return None
    items = (data.get("message") or {}).get("items") or []
    if items:
        return (items[0].get("DOI") or "").strip() or None
    return None


# ── DB upsert ────────────────────────────────────────────────────────────────

def upsert_siup_book(conn, *, doi, isbn, title, book_type,
                     authors, editors, year, pages, abstract, subjects) -> str:
    """
    Insert or update a SIU Press book record.
    Dedup key: doi (if found) > isbn + publisher > title + publisher.
    Returns 'inserted', 'updated', or 'skipped'.
    """
    existing_id = None

    # 1. DOI match
    if doi:
        row = conn.execute("SELECT id FROM books WHERE doi = ?", (doi,)).fetchone()
        if row:
            existing_id = row["id"]

    # 2. ISBN + publisher match
    if existing_id is None and isbn:
        row = conn.execute(
            "SELECT id FROM books WHERE isbn = ? AND publisher = ?",
            (isbn, PUBLISHER)
        ).fetchone()
        if row:
            existing_id = row["id"]

    # 3. Title + publisher match
    if existing_id is None:
        row = conn.execute(
            "SELECT id FROM books WHERE title = ? AND publisher = ?",
            (title, PUBLISHER)
        ).fetchone()
        if row:
            existing_id = row["id"]

    if existing_id is not None:
        conn.execute("""
            UPDATE books
               SET doi      = COALESCE(doi, ?),
                   isbn     = COALESCE(isbn, ?),
                   authors  = ?,
                   editors  = ?,
                   year     = COALESCE(?, year),
                   pages    = COALESCE(?, pages),
                   book_type= ?,
                   abstract = COALESCE(?, abstract),
                   subjects = ?,
                   fetched_at = datetime('now')
             WHERE id = ?
        """, (doi, isbn, authors, editors, year, pages,
              book_type, abstract, subjects, existing_id))
        return "updated"

    conn.execute("""
        INSERT INTO books
            (doi, isbn, title, record_type, book_type, parent_id,
             editors, authors, publisher, year, pages,
             abstract, subjects, cited_by, source)
        VALUES (?,?,?,?,?,NULL, ?,?,?,?,?,?,?,0,?)
    """, (doi, isbn, title, "book", book_type,
          editors, authors, PUBLISHER, year, pages,
          abstract, subjects, SOURCE))
    return "inserted"


# ── Main ─────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False, limit: int | None = None):
    init_db()

    # ── Phase 1: Collect all book URLs from both categories ─────────────────
    print("\n── Collecting book URLs from SIU Press ──")
    all_urls: list[str] = []
    seen_urls: set[str] = set()

    for cat_code, cat_name, max_pages in CATEGORIES:
        print(f"\n  Category: {cat_name}  ({max_pages} pages)")
        cat_urls = collect_book_urls(cat_code, max_pages)
        new = 0
        for u in cat_urls:
            if u not in seen_urls:
                seen_urls.add(u)
                all_urls.append(u)
                new += 1
        print(f"  → {len(cat_urls)} found, {new} new after dedup")

    print(f"\n  Total unique book URLs: {len(all_urls)}")

    if limit:
        all_urls = all_urls[:limit]
        print(f"  (limited to {limit} for this run)")

    if not all_urls:
        print("No book URLs found — nothing to do.")
        return

    # ── Phase 2: Scrape each book page and upsert ────────────────────────────
    print(f"\n── Scraping {len(all_urls)} book detail pages ──")

    total_inserted = 0
    total_updated  = 0
    total_skipped  = 0
    doi_found      = 0

    with get_conn() as conn:
        for i, url in enumerate(all_urls, 1):
            meta = scrape_book(url)
            if not meta:
                print(f"  [{i}/{len(all_urls)}] !! Could not parse: {url}")
                total_skipped += 1
                continue

            title     = meta["title"]
            isbn      = meta["isbn"]
            year      = meta["year"]
            book_type = meta["book_type"]

            # CrossRef DOI lookup
            doi = None
            if isbn and not dry_run:
                doi = lookup_doi_by_isbn(isbn)
                if doi:
                    doi_found += 1

            if dry_run:
                role = "edited" if book_type == "edited-collection" else "mono"
                contribs = meta["editors"] or meta["authors"] or "?"
                print(f"  [{i}] [{year or '????'}] [{role}] {title[:60]}")
                print(f"        ISBN:{isbn}  DOI:{doi or '—'}  {contribs[:50]}")
                if meta["series"]:
                    print(f"        Series: {meta['series']}")
                total_inserted += 1
                continue

            action = upsert_siup_book(
                conn,
                doi      = doi,
                isbn     = isbn,
                title    = title,
                book_type= book_type,
                authors  = meta["authors"],
                editors  = meta["editors"],
                year     = year,
                pages    = meta["pages"],
                abstract = meta["abstract"],
                subjects = meta["subjects"],
            )

            symbol = "+" if action == "inserted" else "~" if action == "updated" else "="
            if action != "skipped":
                print(f"  {symbol} [{i}/{len(all_urls)}] [{year or '????'}] {title[:65]}")

            if action == "inserted":
                total_inserted += 1
            elif action == "updated":
                total_updated += 1
            else:
                total_skipped += 1

            if i % 25 == 0:
                conn.commit()
                print(f"  … {i}/{len(all_urls)} processed")

        if not dry_run:
            conn.commit()

    print(f"\n{'='*55}")
    print(f"SIU Press fetch {'(dry run) ' if dry_run else ''}complete.")
    print(f"  Inserted : {total_inserted}")
    if not dry_run:
        print(f"  Updated  : {total_updated}")
        print(f"  Skipped  : {total_skipped}")
        print(f"  DOI hits : {doi_found} / {len(all_urls) - total_skipped}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Fetch SIU Press rhet/comp books")
    parser.add_argument("--dry",   action="store_true", help="Dry run — no DB writes")
    parser.add_argument("--limit", type=int, default=None, help="Max books to process")
    args = parser.parse_args()
    run(dry_run=args.dry, limit=args.limit)
