"""
book_fetcher.py — Harvests monographs and edited collections from CrossRef.

Publishers covered:
  WAC Clearinghouse        member 23835  prefix 10.37514
  University Press of Colorado / Utah State UP
                           member 3910   prefixes 10.7330, 10.5876

WAC chapter strategy:
  Each WAC edited-book DOI follows the pattern:
    book  →  10.37514/PER-B.2024.2180
    ch 1  →  10.37514/PER-B.2024.2180.2.01
    ch 2  →  10.37514/PER-B.2024.2180.2.02
    ...
  Chapters are enumerated by incrementing the suffix until 3 consecutive
  404s are returned.  Front-matter lives at .1.01, .1.02, etc.

  Note: CrossRef does not deposit reference lists for book chapters at
  either press — only structural metadata (title, authors, DOI, ISBN,
  container) is available.  The books section therefore functions as a
  bibliographic index rather than a citation network.

Usage:
    python book_fetcher.py              # fetch all publishers (incremental)
    python book_fetcher.py wac          # WAC Clearinghouse only
    python book_fetcher.py upc          # UP Colorado / Utah State only
    python book_fetcher.py --full       # ignore last-fetch dates (full re-fetch)
    python book_fetcher.py wac --full   # WAC full re-fetch
"""

import sys
import time
import logging
import requests
from urllib.parse import quote_plus

from db import (
    init_db,
    upsert_book,
    get_books_fetch_log,
    update_books_fetch_log,
    get_book_by_doi,
)
from tagger import auto_tag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

CROSSREF_BASE = "https://api.crossref.org"
MAILTO        = "rhetcompindex@gmail.com"
HEADERS       = {"User-Agent": f"Pinakes/1.0 (mailto:{MAILTO})"}
DELAY         = 0.6   # seconds between API requests
DELAY_ENUM    = 0.4   # seconds between chapter-DOI enumeration hits


# ── Publisher configs ───────────────────────────────────────────────────────────

PUBLISHERS = {
    "wac": {
        "label":     "WAC Clearinghouse",
        "member_id": 23835,
        "prefixes":  ["10.37514"],
        "strategy":  "wac",
    },
    "upc": {
        "label":     "Utah State University Press",
        "member_id": 3910,
        "prefixes":  ["10.7330", "10.5876"],
        "strategy":  "upc",
    },
}

BOOK_TYPES_TO_FETCH = ["monograph", "edited-book", "book"]


# ── CrossRef helpers ────────────────────────────────────────────────────────────

def _get(url, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                log.warning("Rate-limited — sleeping 15 s …")
                time.sleep(15)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries - 1:
                log.error("Request failed %s: %s", url, e)
                return None
            time.sleep(2)
    return None


def _fetch_doi(doi):
    data = _get(f"{CROSSREF_BASE}/works/{doi}", params={"mailto": MAILTO})
    time.sleep(DELAY_ENUM)
    if data:
        return data.get("message")
    return None


def _fetch_member_works(member_id, work_type, rows=100, cursor="*", since_date=None):
    """Fetch one page of works for a member + type. Returns (items, next_cursor)."""
    params = {
        "filter": f"type:{work_type}",
        "rows":   rows,
        "cursor": cursor,
        "mailto": MAILTO,
        "sort":   "published",
        "order":  "desc",
    }
    if since_date:
        params["filter"] += f",from-pub-date:{since_date}"

    url = f"{CROSSREF_BASE}/members/{member_id}/works"
    data = _get(url, params=params)
    time.sleep(DELAY)
    if not data:
        return [], None
    msg   = data.get("message", {})
    items = msg.get("items", [])
    nc    = msg.get("next-cursor")
    return items, nc


# ── Metadata extraction helpers ─────────────────────────────────────────────────

def _title(work):
    t = work.get("title") or []
    return t[0].strip() if t else (work.get("display_name") or "").strip()


def _people(work, field):
    people = work.get(field) or []
    names  = []
    for p in people:
        given  = (p.get("given") or "").strip()
        family = (p.get("family") or "").strip()
        if family:
            names.append(f"{given} {family}".strip())
        elif given:
            names.append(given)
    return "; ".join(names) if names else None


def _year(work):
    for key in ("published", "published-print", "published-online"):
        pd = work.get(key) or {}
        parts = pd.get("date-parts", [[]])
        if parts and parts[0]:
            return parts[0][0]
    return None


def _isbn(work):
    """Return the first ISBN as a plain string, or None."""
    isbns = work.get("ISBN") or []
    return isbns[0] if isbns else None


def _subjects(work):
    subj = work.get("subject") or []
    return "; ".join(subj[:8]) if subj else None


def _crossref_type_to_book_type(cr_type, work):
    """Map CrossRef type + editor presence to our book_type enum."""
    if cr_type == "monograph":
        return "monograph"
    if cr_type == "edited-book":
        return "edited-collection"
    if cr_type == "book":
        # Check whether the record has editors (not just authors)
        if work.get("editor"):
            return "edited-collection"
        return "monograph"
    return "monograph"


# ── WAC Clearinghouse strategy ──────────────────────────────────────────────────

def _enumerate_wac_chapters(book_doi, book_id):
    """
    Enumerate all chapters for a WAC book by probing the .2.XX DOI suffix.
    Also probes .1.XX for front-matter (introductions, forewords).

    Returns (chapter_count, front_matter_count).
    """
    ch_new = 0
    fm_new = 0

    # Front-matter: .1.01 through .1.09 (stop at 3 consecutive 404s)
    fm_num = 1
    fm_404s = 0
    while fm_404s < 3 and fm_num <= 12:
        doi = f"{book_doi}.1.{fm_num:02d}"
        work = _fetch_doi(doi)
        if work:
            fm_404s = 0
            t       = _title(work)
            authors = _people(work, "author")
            year    = _year(work)
            pub_date = f"{year}-01-01" if year else None
            tags    = auto_tag(t, None)
            _, is_new = upsert_book(
                doi        = work.get("DOI") or doi,
                isbn       = _isbn(work),
                title      = t,
                record_type= "front-matter",
                book_type  = None,
                editors    = None,
                authors    = authors,
                publisher  = "WAC Clearinghouse",
                year       = year,
                pages      = work.get("page"),
                abstract   = None,
                subjects   = tags,
                cited_by   = work.get("is-referenced-by-count", 0),
                parent_id  = book_id,
            )
            if is_new:
                fm_new += 1
        else:
            fm_404s += 1
        fm_num += 1

    # Chapters: .2.01 through ...  stop after 3 consecutive 404s
    ch_num  = 1
    ch_404s = 0
    while ch_404s < 3:
        doi = f"{book_doi}.2.{ch_num:02d}"
        work = _fetch_doi(doi)
        if work:
            ch_404s = 0
            t       = _title(work)
            authors = _people(work, "author")
            year    = _year(work)
            tags    = auto_tag(t, None)
            _, is_new = upsert_book(
                doi        = work.get("DOI") or doi,
                isbn       = _isbn(work),
                title      = t,
                record_type= "chapter",
                book_type  = None,
                editors    = None,
                authors    = authors,
                publisher  = "WAC Clearinghouse",
                year       = year,
                pages      = work.get("page"),
                abstract   = None,
                subjects   = tags,
                cited_by   = work.get("is-referenced-by-count", 0),
                parent_id  = book_id,
            )
            if is_new:
                ch_new += 1
        else:
            ch_404s += 1
        ch_num += 1

    return ch_new, fm_new


def fetch_wac(full=False):
    """Harvest all WAC Clearinghouse books and chapters."""
    pub    = PUBLISHERS["wac"]
    label  = pub["label"]
    mid    = pub["member_id"]

    since = None if full else get_books_fetch_log(label)
    log.info("WAC Clearinghouse — %s", "full fetch" if full else f"incremental since {since}")

    total_books = 0
    total_ch    = 0
    total_fm    = 0

    for cr_type in BOOK_TYPES_TO_FETCH:
        cursor = "*"
        page   = 0
        while True:
            items, next_cursor = _fetch_member_works(
                mid, cr_type, rows=100, cursor=cursor, since_date=since
            )
            if not items:
                break
            page += 1
            log.info("  WAC %s page %d — %d items", cr_type, page, len(items))

            for work in items:
                doi      = work.get("DOI") or ""
                t        = _title(work)
                if not t or not doi:
                    continue

                book_type = _crossref_type_to_book_type(cr_type, work)
                editors   = _people(work, "editor")
                authors   = _people(work, "author")
                year      = _year(work)
                tags      = auto_tag(t, work.get("abstract", [None])[0] if work.get("abstract") else None)

                book_id, is_new = upsert_book(
                    doi        = doi,
                    isbn       = _isbn(work),
                    title      = t,
                    record_type= "book",
                    book_type  = book_type,
                    editors    = editors,
                    authors    = authors,
                    publisher  = label,
                    year       = year,
                    pages      = None,
                    abstract   = None,
                    subjects   = tags,
                    cited_by   = work.get("is-referenced-by-count", 0),
                    parent_id  = None,
                )

                if book_id is None:
                    log.warning("  Could not upsert book: %s", t[:60])
                    continue

                if is_new:
                    total_books += 1

                # For edited collections, enumerate chapters
                if book_type == "edited-collection":
                    ch_new, fm_new = _enumerate_wac_chapters(doi, book_id)
                    total_ch += ch_new
                    total_fm += fm_new
                    if ch_new or fm_new:
                        log.info(
                            "    %s — %d new chapters, %d front-matter",
                            t[:55], ch_new, fm_new
                        )

            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

    update_books_fetch_log(label)
    log.info(
        "WAC Clearinghouse complete — %d new books, %d new chapters, %d front-matter",
        total_books, total_ch, total_fm
    )
    return total_books, total_ch


# ── UP Colorado / Utah State strategy ──────────────────────────────────────────

def _fetch_upc_chapters_by_isbn(isbn, publisher):
    """
    Fetch book-chapter records that share an ISBN with the parent book.
    Returns count of new chapter records inserted.
    """
    if not isbn:
        return 0

    url = f"{CROSSREF_BASE}/works"
    params = {
        "filter": f"type:book-chapter,isbn:{isbn}",
        "rows":   100,
        "mailto": MAILTO,
    }
    data = _get(url, params=params)
    time.sleep(DELAY)
    if not data:
        return 0

    items = data.get("message", {}).get("items", [])
    new_count = 0

    # Look up parent_id by isbn
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM books WHERE isbn = ? AND record_type = 'book'",
            (isbn,)
        ).fetchone()
        parent_id = row["id"] if row else None

    if not parent_id:
        return 0

    for work in items:
        doi  = work.get("DOI") or ""
        t    = _title(work)
        if not t:
            continue
        authors = _people(work, "author")
        year    = _year(work)
        tags    = auto_tag(t, None)

        _, is_new = upsert_book(
            doi        = doi or None,
            isbn       = _isbn(work),
            title      = t,
            record_type= "chapter",
            book_type  = None,
            editors    = None,
            authors    = authors,
            publisher  = publisher,
            year       = year,
            pages      = work.get("page"),
            abstract   = None,
            subjects   = tags,
            cited_by   = work.get("is-referenced-by-count", 0),
            parent_id  = parent_id,
        )
        if is_new:
            new_count += 1

    return new_count


def fetch_upc(full=False):
    """Harvest all UP Colorado / Utah State University Press books and chapters."""
    pub   = PUBLISHERS["upc"]
    label = pub["label"]
    mid   = pub["member_id"]

    since = None if full else get_books_fetch_log(label)
    log.info("UP Colorado / Utah State — %s", "full fetch" if full else f"incremental since {since}")

    total_books = 0
    total_ch    = 0

    for cr_type in BOOK_TYPES_TO_FETCH:
        cursor = "*"
        page   = 0
        while True:
            items, next_cursor = _fetch_member_works(
                mid, cr_type, rows=100, cursor=cursor, since_date=since
            )
            if not items:
                break
            page += 1
            log.info("  UPC %s page %d — %d items", cr_type, page, len(items))

            for work in items:
                doi  = work.get("DOI") or ""
                t    = _title(work)
                if not t:
                    continue

                book_type = _crossref_type_to_book_type(cr_type, work)
                editors   = _people(work, "editor")
                authors   = _people(work, "author")
                year      = _year(work)
                isbn      = _isbn(work)
                tags      = auto_tag(t, None)

                book_id, is_new = upsert_book(
                    doi        = doi or None,
                    isbn       = isbn,
                    title      = t,
                    record_type= "book",
                    book_type  = book_type,
                    editors    = editors,
                    authors    = authors,
                    publisher  = label,
                    year       = year,
                    pages      = None,
                    abstract   = None,
                    subjects   = tags,
                    cited_by   = work.get("is-referenced-by-count", 0),
                    parent_id  = None,
                )

                if is_new:
                    total_books += 1

                # For edited collections, attempt chapter fetch via ISBN
                if book_type == "edited-collection" and isbn and book_id:
                    ch_new = _fetch_upc_chapters_by_isbn(isbn, label)
                    total_ch += ch_new
                    if ch_new:
                        log.info(
                            "    %s — %d new chapters (ISBN lookup)",
                            t[:55], ch_new
                        )

            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor

    update_books_fetch_log(label)
    log.info(
        "UP Colorado complete — %d new books, %d new chapters",
        total_books, total_ch
    )
    return total_books, total_ch


# ── Main ────────────────────────────────────────────────────────────────────────

def main():
    init_db()

    args = [a.lower() for a in sys.argv[1:]]
    full = "--full" in args
    targets = [a for a in args if not a.startswith("--")]

    # Default: fetch both publishers
    if not targets:
        targets = ["wac", "upc"]

    for target in targets:
        if target == "wac":
            fetch_wac(full=full)
        elif target in ("upc", "usc", "utah"):
            fetch_upc(full=full)
        else:
            log.error("Unknown publisher target: %s  (use 'wac' or 'upc')", target)

    # Print summary
    from db import get_book_publishers
    publishers = get_book_publishers()
    if publishers:
        print("\nBooks DB summary:")
        print(f"  {'Publisher':<35} {'Books':>6} {'Chapters':>9}")
        print("  " + "-" * 54)
        for p in publishers:
            print(f"  {p['publisher']:<35} {p['book_count']:>6} {p['chapter_count']:>9}")


if __name__ == "__main__":
    main()
