"""
fetch_pitt.py — Scrape University of Pittsburgh Press rhetoric catalog.

Harvests all books from the Language Arts & Disciplines / Rhetoric subject
page at upittpress.org. Since UPitt books on CrossRef are registered under
the JSTOR prefix (10.2307) and bulk-querying JSTOR is impractical, we scrape
the publisher's own site directly.

Data extracted per book:
  - Title (h1 + optional h3 subtitle)
  - Author(s) or Editor(s) with role (By / Edited by)
  - ISBNs (hardcover + paperback, from page text)
  - Publication year
  - Series name (Composition, Literacy, and Culture, etc.)
  - Description / abstract
  - Subject categories

Dedup key: ISBN (preferred) or title + publisher.

Usage:
    python fetch_pitt.py          # insert/update all Rhetoric catalog books
    python fetch_pitt.py --dry    # print what would be fetched, no DB writes
"""

import re
import sys
import io
import time
import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from bs4 import BeautifulSoup

from db import init_db, get_conn

# ── Constants ────────────────────────────────────────────────────────────────

PUBLISHER   = "University of Pittsburgh Press"
SOURCE      = "pitt"
BASE        = "https://upittpress.org"
SUBJECT_URL = f"{BASE}/subject/language-arts-disciplines-rhetoric/"
HDRS        = {"User-Agent": "Pinakes/1.0 (mailto:rhetcompindex@gmail.com)"}
DELAY       = 0.9   # seconds between requests — be polite

# ── Regex patterns ────────────────────────────────────────────────────────────

_ISBN_RE   = re.compile(r'\b(97[89]\d{10})\b')
_MONTH_YEAR_RE = re.compile(
    r'(?:January|February|March|April|May|June|July|August|September|'
    r'October|November|December)[,\s]+(\d{4})',
    re.IGNORECASE
)
_YEAR_ONLY_RE = re.compile(r'\b((?:19[89]\d|20[0-3]\d))\b')

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(url: str) -> BeautifulSoup | None:
    """GET a URL with retries; return BeautifulSoup or None on failure."""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HDRS, timeout=30)
            time.sleep(DELAY)
            if r.status_code == 200:
                return BeautifulSoup(r.text, "html.parser")
            if r.status_code == 404:
                return None
            print(f"  !! HTTP {r.status_code} for {url} (attempt {attempt+1})")
        except requests.RequestException as exc:
            print(f"  !! {exc} (attempt {attempt+1})")
            time.sleep(2)
    return None


# ── Listing page scraper ──────────────────────────────────────────────────────

def collect_book_urls() -> list[str]:
    """
    Walk all pages of the Rhetoric subject listing and collect unique
    book detail page URLs (/books/{ISBN}/).
    """
    seen: set[str] = set()
    ordered: list[str] = []
    page = 1

    while True:
        url = SUBJECT_URL if page == 1 else f"{SUBJECT_URL}page/{page}/"
        print(f"  Listing page {page}: {url}")
        soup = _get(url)
        if soup is None:
            break

        found_on_page = 0
        for a in soup.find_all("a", href=True):
            href: str = a["href"]
            # Book detail URLs: /books/<13-digit-ISBN>/ (sometimes with domain)
            if "/books/" in href:
                slug = href.rstrip("/").split("/")[-1]
                if slug.isdigit() and len(slug) == 13:
                    full = href if href.startswith("http") else BASE + href
                    if full not in seen:
                        seen.add(full)
                        ordered.append(full)
                    found_on_page += 1

        if found_on_page == 0:
            break  # No more book links → end of pagination

        # Detect last page via pagination text  ("Page N of N")
        page_info = soup.find(string=re.compile(r"Page\s+\d+\s+of\s+\d+"))
        if page_info:
            m = re.search(r"Page\s+(\d+)\s+of\s+(\d+)", page_info)
            if m and int(m.group(1)) >= int(m.group(2)):
                break

        page += 1
        if page > 25:   # Safety ceiling
            break

    print(f"  Collected {len(ordered)} unique book URLs across {page} listing pages\n")
    return ordered


# ── Detail page parser ────────────────────────────────────────────────────────

def parse_book_page(url: str) -> dict | None:
    """
    Fetch a book detail page and extract metadata.
    Returns a dict or None if the page couldn't be parsed.
    """
    soup = _get(url)
    if soup is None:
        return None

    # ── Title ────────────────────────────────────────────────────────────────
    h1 = soup.find("h1")
    if not h1:
        return None
    title = h1.get_text(" ", strip=True)

    # Subtitle: look for an h3 that follows the h1 and reads like a real subtitle
    # (multiple words, not navigation/category labels like "Subjects").
    h1_found = False
    for tag in soup.find_all(["h1", "h3"]):
        if tag.name == "h1":
            h1_found = True
            continue
        if h1_found and tag.name == "h3":
            text = tag.get_text(" ", strip=True)
            words = text.split()
            # Real subtitles: ≥3 words, not a one-word nav label
            if (len(words) >= 3
                    and text.lower() not in ("subjects", "subject",
                                             "edited by", "by author")
                    and not any(kw in text.lower() for kw in
                                ("series editor", "learn more", "add to cart",
                                 "isbn", "paperback", "hardcover"))):
                title = f"{title}: {text}"
            break

    # ── Contributors ─────────────────────────────────────────────────────────
    # Look for "By <a>Name</a>" or "Edited by <a>Name</a>" patterns.
    # The page text near contributor links contains the role label.
    full_text = soup.get_text(" ", strip=True)

    # Try to extract "Edited by Name1 and Name2" or "By Name"
    edited_match = re.search(
        r'Edited\s+by\s+((?:[A-Z][^,\n]+?)(?:\s+and\s+[A-Z][^,\n]+?)*)'
        r'(?=\s+(?:Paperback|Hardcover|ISBN|Series|$|\d))',
        full_text
    )
    by_match = re.search(
        r'\bBy\s+([A-Z][A-Za-z\s\.\-\']+?)(?=\s+(?:Paperback|Hardcover|ISBN|Series|$|\d))',
        full_text
    )

    # Use contributor <a> tags for accuracy
    contributor_links = soup.find_all("a", href=re.compile(r"/authors/[^\"]+"))
    _NAV_TEXTS = {"author", "authors", "editor", "editors",
                  "learn more", "more", "all authors", "see all"}
    contributor_names = []
    for a in contributor_links:
        # Normalise whitespace (strip=True alone won't collapse internal \n)
        name = " ".join(a.get_text().split()).strip()
        if (name
                and name.lower() not in _NAV_TEXTS
                and not name.lower().startswith("learn")
                and len(name) > 3):
            contributor_names.append(name)

    # Determine role
    book_type = "monograph"
    authors_str = None
    editors_str = None

    if contributor_names:
        # Check if the role label says "Edited by"
        for a in contributor_links:
            # Walk back in the DOM to find the role text near this link
            parent_text = ""
            p = a.parent
            if p:
                parent_text = p.get_text(" ", strip=True)
            if "edited by" in parent_text.lower() or "edited by" in full_text[:2000].lower():
                book_type = "edited-collection"
                break

        joined = "; ".join(contributor_names)
        if book_type == "edited-collection":
            editors_str = joined
        else:
            authors_str = joined
    else:
        # Fallback: regex-based extraction
        if edited_match:
            book_type = "edited-collection"
            editors_str = edited_match.group(1).strip()
        elif by_match:
            authors_str = by_match.group(1).strip()

    # ── ISBNs ────────────────────────────────────────────────────────────────
    isbn_from_url = url.rstrip("/").split("/")[-1]
    all_isbns = list(dict.fromkeys(_ISBN_RE.findall(full_text)))  # unique, ordered

    # Prefer paperback (978-0-8229-6/7 vs hardcover 978-0-8229-4)
    # Pitt paperbacks start with 97808229[5-9] and hardcovers with 97808229[34]
    paperback_isbn = None
    hardcover_isbn = None
    for isbn in all_isbns:
        if isbn.startswith("978082296") or isbn.startswith("978082297"):
            paperback_isbn = isbn
        elif isbn.startswith("978082294") or isbn.startswith("978082293"):
            hardcover_isbn = isbn

    primary_isbn = paperback_isbn or hardcover_isbn or isbn_from_url or (all_isbns[0] if all_isbns else None)

    # ── Year ─────────────────────────────────────────────────────────────────
    year = None
    m = _MONTH_YEAR_RE.search(full_text)
    if m:
        year = int(m.group(1))
    else:
        years = [int(y) for y in _YEAR_ONLY_RE.findall(full_text)]
        if years:
            year = min(y for y in years if y >= 1990)

    # ── Series ───────────────────────────────────────────────────────────────
    series_tag = soup.find("a", href=re.compile(r"/series/"))
    series = series_tag.get_text(strip=True) if series_tag else None

    # ── Description ──────────────────────────────────────────────────────────
    # Main description is usually in the longest <p> block or a content div
    desc_paras = []
    for p in soup.find_all("p"):
        text = p.get_text(" ", strip=True)
        # Skip navigation/boilerplate fragments
        if len(text) > 80 and not any(kw in text.lower() for kw in
                                       ("isbn", "paperback", "hardcover", "add to cart",
                                        "©", "university of pittsburgh")):
            desc_paras.append(text)
    abstract = " ".join(desc_paras[:3])[:2000] or None

    # ── Subjects ─────────────────────────────────────────────────────────────
    subject_links = soup.find_all("a", href=re.compile(r"/subject/"))
    subjects_list = [a.get_text(strip=True) for a in subject_links if a.get_text(strip=True)]
    if series:
        subjects_list = [series] + subjects_list
    subjects = "; ".join(dict.fromkeys(subjects_list))[:500] or None

    return {
        "title":     title,
        "authors":   authors_str,
        "editors":   editors_str,
        "isbn":      primary_isbn,
        "year":      year,
        "book_type": book_type,
        "abstract":  abstract,
        "subjects":  subjects,
    }


# ── DB upsert ─────────────────────────────────────────────────────────────────

def upsert_book(conn, book: dict) -> str:
    """
    Insert or update a UPitt Press book record.
    Dedup key: isbn + publisher (preferred) or title + publisher.
    Returns 'inserted', 'updated', or 'skipped'.
    """
    existing_id = None

    if book["isbn"]:
        row = conn.execute(
            "SELECT id FROM books WHERE isbn = ? AND publisher = ?",
            (book["isbn"], PUBLISHER)
        ).fetchone()
        if row:
            existing_id = row["id"]

    if existing_id is None:
        row = conn.execute(
            "SELECT id FROM books WHERE title = ? AND publisher = ?",
            (book["title"], PUBLISHER)
        ).fetchone()
        if row:
            existing_id = row["id"]

    fields = (
        book["authors"], book["editors"], book["isbn"],
        book["year"], book["book_type"],
        book["abstract"], book["subjects"],
    )

    if existing_id is not None:
        conn.execute("""
            UPDATE books
               SET authors   = ?,
                   editors   = ?,
                   isbn      = COALESCE(?, isbn),
                   year      = COALESCE(?, year),
                   book_type = ?,
                   abstract  = ?,
                   subjects  = ?,
                   fetched_at= datetime('now')
             WHERE id = ?
        """, (*fields, existing_id))
        return "updated"

    conn.execute("""
        INSERT INTO books
            (title, authors, editors, isbn, publisher, year,
             record_type, book_type, abstract, subjects, source)
        VALUES (?, ?, ?, ?, ?, ?, 'book', ?, ?, ?, ?)
    """, (book["title"], book["authors"], book["editors"], book["isbn"],
          PUBLISHER, book["year"], book["book_type"],
          book["abstract"], book["subjects"], SOURCE))
    return "inserted"


# ── Main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False):
    init_db()

    print("Collecting book URLs from UPitt Press Rhetoric catalog…\n")
    book_urls = collect_book_urls()

    total_inserted = 0
    total_updated  = 0
    total_failed   = 0
    seen_titles: set[str] = set()   # Dedup hardcover/paperback editions

    with get_conn() as conn:
        for i, url in enumerate(book_urls, 1):
            print(f"[{i}/{len(book_urls)}] {url}")
            book = parse_book_page(url)

            if book is None:
                print("  !! Could not parse — skipping")
                total_failed += 1
                continue

            # Skip duplicate editions of the same title
            title_key = book["title"].lower().strip()
            if title_key in seen_titles:
                print(f"  = Duplicate edition, skipping: {book['title'][:60]}")
                continue
            seen_titles.add(title_key)

            year_str = str(book["year"]) if book["year"] else "????"
            contrib   = book["editors"] or book["authors"] or "Unknown"
            print(f"  [{year_str}] {book['title'][:65]}")
            print(f"          {contrib[:55]}  type:{book['book_type']}")

            if dry_run:
                total_inserted += 1
                continue

            action = upsert_book(conn, book)
            if action == "inserted":
                total_inserted += 1
            elif action == "updated":
                total_updated += 1

        if not dry_run:
            conn.commit()

    print(f"\n{'='*55}")
    print(f"UPitt Press Rhetoric fetch {'(dry run) ' if dry_run else ''}complete.")
    print(f"  Inserted : {total_inserted}")
    if not dry_run:
        print(f"  Updated  : {total_updated}")
        print(f"  Failed   : {total_failed}")


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    run(dry_run=dry)
