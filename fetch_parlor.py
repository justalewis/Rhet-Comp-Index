"""
fetch_parlor.py — Scrape Parlor Press Shopify store for rhet/comp books.

Parlor Press does not register DOIs, so this script uses their Shopify
product JSON API (no auth required) to harvest metadata from 9 curated
series collections.

Data extracted per book:
  - Title, author(s)/editor(s) [from vendor field]
  - ISBN (paperback SKU from variants)
  - Publication year (parsed from body_html)
  - Abstract/description (stripped body_html)
  - Subject tags (Shopify tags, filtered)
  - Series name (stored in subjects)

Usage:
    python fetch_parlor.py          # insert/update all 9 series
    python fetch_parlor.py --dry    # print what would be fetched, no DB writes
"""

import re
import sys
import io
import time
import requests
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    from bs4 import BeautifulSoup
    _BS4 = True
except ImportError:
    _BS4 = False

from db import init_db, get_conn

# ── Constants ───────────────────────────────────────────────────────────────

PUBLISHER = "Parlor Press"
SOURCE    = "parlor"
BASE      = "https://parlorpress.com"
HDRS      = {"User-Agent": "Pinakes/1.0 (mailto:rhetcompindex@gmail.com)"}

# Shopify series collection handles + human-readable names
COLLECTIONS = [
    ("lauer-series-in-rhetoric-and-composition",    "Lauer Series in Rhetoric and Composition"),
    ("lenses-on-composition-studies",               "Lenses on Composition Studies"),
    ("new-media-theory",                            "New Media Theory"),
    ("perspectives-on-writing",                     "Perspectives on Writing"),
    ("reference-guides-to-rhetoric-and-composition","Reference Guides to Rhetoric and Composition"),
    ("rhetoric-of-science-and-technology",          "Rhetoric of Science and Technology"),
    ("visual-rhetoric",                             "Visual Rhetoric"),
    ("working-and-writing-for-change",              "Working and Writing for Change"),
    ("writing-program-administration",              "Writing Program Administration"),
]

# Shopify operational/meta tags to strip from subjects
_SKIP_TAGS = {
    "not on sale", "sale", "new", "featured", "bestseller",
    "on sale", "gift", "bundle",
}

# Year range we'll accept as a valid publication year
_YEAR_RE = re.compile(r'\b((?:19[89]\d|20[0-3]\d))\b')

# Patterns that strongly indicate a publication year
_PUB_YEAR_PATS = [
    re.compile(r'(?:©|copyright)[^\d]{0,10}(\b(?:19[89]\d|20[0-3]\d)\b)', re.IGNORECASE),
    re.compile(r'(?:published?|publication\s+year)[^0-9]{0,15}(\b(?:19[89]\d|20[0-3]\d)\b)', re.IGNORECASE),
    re.compile(r'(?:parlor\s+press),?\s*(\b(?:19[89]\d|20[0-3]\d)\b)', re.IGNORECASE),
]

# Patterns that suggest an edited collection.
# "edited by" (applied to this specific book) is the strongest signal.
# We deliberately exclude "series editor(s)" matches by only firing on
# "edited by" constructions, not bare "editor(s)" which appear in series credits.
_EDITED_RE = re.compile(r'\bedited\s+by\b', re.IGNORECASE)

# Vendor-level edited signal: last name(s) followed by ", ed." or ", eds."
_VENDOR_EDITED_RE = re.compile(r',\s*eds?\.?\s*$', re.IGNORECASE)

# Series-name tags to strip (they're Shopify organisational, not subject tags)
_SERIES_HANDLES = {h for h, _ in COLLECTIONS}
_SERIES_NAMES   = {n.lower() for _, n in COLLECTIONS}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _strip_html(html: str) -> str:
    """Return plain text from HTML, collapsing whitespace."""
    if not html:
        return ""
    if _BS4:
        return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    # Fallback: crude tag strip
    return re.sub(r'<[^>]+>', ' ', html).strip()


def get_isbn(variants: list) -> str | None:
    """Return the paperback ISBN (SKU) from the variant list, or the first available."""
    for v in variants:
        if "paperback" in v.get("title", "").lower():
            sku = (v.get("sku") or "").strip()
            if sku:
                return sku
    for v in variants:
        sku = (v.get("sku") or "").strip()
        if sku:
            return sku
    return None


def parse_year(body_html: str) -> int | None:
    """Extract the most likely publication year from the product description."""
    if not body_html:
        return None
    text = _strip_html(body_html)

    # Priority: explicit publication-year markers
    for pat in _PUB_YEAR_PATS:
        m = pat.search(text)
        if m:
            return int(m.group(1))

    # Fallback: earliest plausible year found in text (original pub years tend
    # to appear first; Shopify store launch dates are 2020+)
    years = [int(y) for y in _YEAR_RE.findall(text)]
    if years:
        return min(years)
    return None


def parse_book_type(vendor: str, body_html: str) -> str:
    """Return 'edited-collection' if vendor or description signals edited volume.

    Parlor Press Shopify descriptions follow a consistent pattern:
      Monograph:         <h3>Author Name</h3>
      Edited collection: <h3>Edited by Name1 and Name2</h3>

    So we check the <h3> tag first — the most reliable signal — then fall back
    to vendor-level patterns.
    """
    # 1. Parse the first <h3> in body_html — most reliable signal
    if body_html:
        h3_match = re.search(r'<h3[^>]*>(.*?)</h3>', body_html, re.IGNORECASE | re.DOTALL)
        if h3_match:
            h3_text = _strip_html(h3_match.group(0)).strip()
            if _EDITED_RE.search(h3_text):   # "edited by" in the h3
                return "edited-collection"
            if _VENDOR_EDITED_RE.search(h3_text):  # "Smith, eds."
                return "edited-collection"
            # h3 is present but doesn't signal edited → monograph
            return "monograph"

    # 2. Fallback: vendor field ends with ", ed." / ", eds."
    if _VENDOR_EDITED_RE.search(vendor or ""):
        return "edited-collection"

    return "monograph"


def clean_subjects(tags: list, series_name: str) -> str:
    """Filter operational/series tags; prepend series name; return CSV string."""
    series_lower = series_name.lower()
    kept = []
    for t in tags:
        tl = t.lower()
        if tl in _SKIP_TAGS:
            continue
        if tl in _SERIES_NAMES:
            continue
        # Drop generic series shorthand tags (e.g. "Lauer Series")
        if "lauer" in tl or "lenses" in tl or "perspectives" in tl:
            continue
        kept.append(t)
    # Always include the series name first so it shows in the UI
    return "; ".join([series_name] + kept)


# ── Shopify API ───────────────────────────────────────────────────────────────

def fetch_collection_products(handle: str) -> list:
    """Fetch all products from one Shopify collection, handling pagination."""
    products = []
    page = 1
    while True:
        url = f"{BASE}/collections/{handle}/products.json?limit=250&page={page}"
        try:
            r = requests.get(url, headers=HDRS, timeout=30)
        except requests.RequestException as exc:
            print(f"    !! Network error: {exc}")
            break
        time.sleep(0.5)
        if r.status_code == 404:
            print(f"    !! Collection not found: {handle}")
            break
        if r.status_code != 200:
            print(f"    !! HTTP {r.status_code} for {url}")
            break
        batch = r.json().get("products", [])
        if not batch:
            break
        products.extend(batch)
        if len(batch) < 250:
            break
        page += 1
    return products


# ── DB upsert ────────────────────────────────────────────────────────────────

def upsert_book(conn, *, title, authors, editors, isbn, year,
                book_type, abstract, subjects) -> str:
    """
    Insert or update a Parlor Press book record.
    Dedup key: isbn+publisher (preferred) or title+publisher.
    Returns 'inserted', 'updated', or 'skipped'.
    """
    existing_id = None

    if isbn:
        row = conn.execute(
            "SELECT id FROM books WHERE isbn = ? AND publisher = ?",
            (isbn, PUBLISHER)
        ).fetchone()
        if row:
            existing_id = row["id"]

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
               SET authors   = ?,
                   editors   = ?,
                   isbn      = COALESCE(?, isbn),
                   year      = COALESCE(?, year),
                   book_type = ?,
                   abstract  = ?,
                   subjects  = ?,
                   fetched_at= datetime('now')
             WHERE id = ?
        """, (authors, editors, isbn, year, book_type, abstract, subjects, existing_id))
        return "updated"

    conn.execute("""
        INSERT INTO books
            (title, authors, editors, isbn, publisher, year,
             record_type, book_type, abstract, subjects, source)
        VALUES (?, ?, ?, ?, ?, ?, 'book', ?, ?, ?, ?)
    """, (title, authors, editors, isbn, PUBLISHER, year,
          book_type, abstract, subjects, SOURCE))
    return "inserted"


# ── Main ─────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False):
    if not _BS4:
        print("WARNING: beautifulsoup4 not installed — HTML stripping will be crude.")
        print("         pip install beautifulsoup4  to fix this.\n")

    init_db()

    total_inserted = 0
    total_updated  = 0
    total_skipped  = 0

    with get_conn() as conn:
        for handle, series_name in COLLECTIONS:
            print(f"\n── {series_name} ──")
            products = fetch_collection_products(handle)
            print(f"   {len(products)} products found")

            series_inserted = series_updated = 0

            for p in products:
                # Skip non-book products (gift cards, bundles, etc.)
                ptype = p.get("product_type", "").strip().lower()
                if ptype and ptype not in ("book", ""):
                    total_skipped += 1
                    continue

                title    = (p.get("title") or "").strip()
                if not title:
                    total_skipped += 1
                    continue

                vendor   = (p.get("vendor") or "").strip()
                body_html= p.get("body_html") or ""
                tags     = p.get("tags") or []
                variants = p.get("variants") or []

                isbn      = get_isbn(variants)
                year      = parse_year(body_html)
                book_type = parse_book_type(vendor, body_html)
                abstract  = _strip_html(body_html)[:2000].strip() or None
                subjects  = clean_subjects(tags, series_name)

                # Split vendor into authors vs editors
                if book_type == "edited-collection":
                    authors = None
                    editors = vendor or None
                else:
                    authors = vendor or None
                    editors = None

                if dry_run:
                    print(f"   [{year or '????'}] {title[:65]}")
                    print(f"          {vendor}  ISBN:{isbn}  type:{book_type}")
                    total_inserted += 1
                    continue

                action = upsert_book(
                    conn,
                    title=title, authors=authors, editors=editors,
                    isbn=isbn, year=year, book_type=book_type,
                    abstract=abstract, subjects=subjects,
                )

                if action == "inserted":
                    series_inserted += 1
                    total_inserted  += 1
                    print(f"   + [{year or '????'}] {title[:65]}")
                elif action == "updated":
                    series_updated += 1
                    total_updated  += 1
                else:
                    total_skipped  += 1

            if not dry_run:
                conn.commit()
                print(f"   → {series_inserted} inserted, {series_updated} updated")

    print(f"\n{'='*55}")
    print(f"Parlor Press fetch {'(dry run) ' if dry_run else ''}complete.")
    print(f"  Inserted : {total_inserted}")
    if not dry_run:
        print(f"  Updated  : {total_updated}")
        print(f"  Skipped  : {total_skipped}")


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    run(dry_run=dry)
