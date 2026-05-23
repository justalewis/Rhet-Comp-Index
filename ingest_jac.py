"""
ingest_jac.py — One-time import of JAC (Journal of Advanced Composition;
later JAC: A Journal of Rhetoric, Culture, and Politics) into Pinakes.

Source: data/seeds/jac_articles.csv — 1,224 rows from CompPile covering
1980–2014 across vols 1–34.

JAC has no DOIs, so this is a `source='manual'` import along the lines of
the Pre/Text precedent. Synthetic URLs follow the same shape:
    jac:v{volume}i{issue}p{first_page}
with a slug fallback for the handful of rows without first_page.

Usage:
    python ingest_jac.py --dry-run      # report what would happen; write nothing
    python ingest_jac.py                # apply

Idempotent. Existing rows (matched on URL) are skipped, not duplicated.
"""

import argparse
import csv
import logging
import os
import re
import sys
from collections import Counter

from db.core import init_db, get_conn

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

CSV_PATH = os.path.join(os.path.dirname(__file__), "data", "seeds", "jac_articles.csv")

# Canonical name — the final published title. All three CSV variants
# ("JAC", "JAC: Journal of Advanced Composition", "JAC: A Journal of...")
# resolve to this.
CANONICAL_JOURNAL = "JAC: A Journal of Rhetoric, Culture, and Politics"

# Author-field text that signals the title got pulled into the author slot
# in CompPile. The CSV's title field is already correct for this row; we
# only need to clear the author.
MISPARSED_AUTHOR_SENTINEL = "A reply to [Alex] Medlicott, Jr"


def slugify(text, max_len=40):
    """ASCII slug for URL fallback. Lowercased, hyphenated, alnum-only."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len] or "untitled"


def build_url(volume, issue, first_page, title, first_author_surname=None):
    """Synthetic URL: jac:v{vol}i{iss}p{first_page}-{surname}-{title-slug}.

    Both surname and title slug are part of the URL because:
      - Slug alone collapses multi-author memorial sections where each
        contributor's entry shares a starting page AND the same memorial
        title (vol 21 issue 4 has five "In Memory of Alan W. France"
        entries spread across pp 734 and 738).
      - Surname alone collapses authors with two pieces in the same
        issue (rare but possible — editor pieces, two book reviews, etc.).

    Genuine CompPile data-entry duplicates (identical author, title,
    pages) still collapse, because their components all match.
    """
    vol  = volume.strip() or "?"
    iss  = issue.strip()  or "?"
    fp   = first_page.strip()
    slug = slugify(title)
    surn = slugify(first_author_surname, max_len=20) if first_author_surname else ""
    parts = [f"v{vol}i{iss}"]
    if fp:
        parts.append(f"p{fp}")
    if surn:
        parts.append(surn)
    parts.append(slug)
    return "jac:" + "-".join(parts)


def first_author_surname(authors_raw):
    """Extract just the surname from a 'Last, First; Last, First' string.
    Returns None if the field is empty or doesn't look like 'Last, First'.
    """
    if not authors_raw:
        return None
    first = authors_raw.split(";", 1)[0].strip()
    if not first or "," not in first:
        return None
    return first.split(",", 1)[0].strip() or None


def parse_int(s):
    """Return int(s) or None if blank / non-integer."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def transform(row):
    """
    Convert a CSV row into an article dict ready for INSERT, or return
    one of the sentinel strings: 'SKIP_ANONYMOUS', 'SKIP_NO_TITLE'.

    Edge cases handled inline:
      - All three journal-name variants → CANONICAL_JOURNAL
      - Anonymous rows (award-winner announcements) → SKIP_ANONYMOUS
      - Medlicott mis-parsed author → cleared to None; title already correct
      - Book reviews ('[book review]' title prefix) → tags='|book-review|'
    """
    author_raw = (row["author"] or "").strip()
    title      = (row["title"]  or "").strip()
    year       = (row["year"]   or "").strip()
    volume     = (row["volume"] or "").strip()
    issue      = (row["issue"]  or "").strip()
    pages      = (row["pages"]  or "").strip()
    first_page = parse_int(row.get("first_page"))
    last_page  = parse_int(row.get("last_page"))
    annotation = (row.get("annotation") or "").strip() or None
    keywords   = (row.get("keywords")   or "").strip() or None

    if not title:
        return "SKIP_NO_TITLE"

    if author_raw.lower() == "anonymous":
        return "SKIP_ANONYMOUS"

    # Mis-parsed Medlicott row: title got pulled into the author field.
    # Title in the CSV is already correct; just clear the author.
    if author_raw == MISPARSED_AUTHOR_SENTINEL:
        authors = None
    else:
        authors = author_raw or None

    tags = "|book-review|" if title.lower().startswith("[book review]") else None

    url = build_url(
        volume, issue, row.get("first_page", ""), title,
        first_author_surname=first_author_surname(authors),
    )

    return {
        "url":        url,
        "doi":        None,
        "title":      title,
        "authors":    authors,
        "abstract":   annotation,   # CompPile annotation lands in the abstract column
        "pub_date":   year,         # store the year as "YYYY"
        "journal":    CANONICAL_JOURNAL,
        "source":     "manual",
        "keywords":   keywords,
        "tags":       tags,
        "oa_status":  None,         # JAC is subscription / OOP
        "oa_url":     None,
        "volume":     volume or None,
        "issue":      issue or None,
        "pages":      pages or None,
        "first_page": first_page,
        "last_page":  last_page,
    }


def build_url_set(conn):
    """Return {url} for every JAC row already in the DB (for idempotency check)."""
    rows = conn.execute(
        "SELECT url FROM articles WHERE journal = ? AND source = 'manual'",
        (CANONICAL_JOURNAL,),
    ).fetchall()
    return {r["url"] for r in rows}


def insert(conn, art):
    """Insert one article. Returns 1 if new, 0 if URL collision."""
    conn.execute(
        """
        INSERT OR IGNORE INTO articles
            (url, doi, title, authors, abstract, pub_date, journal, source,
             keywords, tags, oa_status, oa_url,
             volume, issue, pages, first_page, last_page)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (art["url"], art["doi"], art["title"], art["authors"], art["abstract"],
         art["pub_date"], art["journal"], art["source"], art["keywords"],
         art["tags"], art["oa_status"], art["oa_url"],
         art["volume"], art["issue"], art["pages"], art["first_page"], art["last_page"]),
    )
    return conn.execute("SELECT changes()").fetchone()[0]


def run(dry_run):
    if not os.path.exists(CSV_PATH):
        sys.exit(f"CSV not found at {CSV_PATH}")

    init_db()

    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    stats = Counter()
    stats["csv_rows"] = len(rows)

    # Sanity: track URL collisions WITHIN the CSV (two rows producing the
    # same synthetic URL — e.g. two articles starting on the same page,
    # which shouldn't happen but would silently dedupe).
    urls_seen = {}            # url -> first row index that produced it
    intra_csv_collisions = []  # list of (url, [row indices])

    # Pre-flight: collect already-indexed URLs so dry-run can show updates
    with get_conn() as conn:
        existing_urls = build_url_set(conn)
    stats["already_in_db"] = 0

    to_insert = []
    title_renamed_variants = Counter()
    for i, row in enumerate(rows, start=2):  # row 2 = first data row in the CSV
        title_renamed_variants[row["journal"]] += 1
        art = transform(row)
        if art == "SKIP_ANONYMOUS":
            stats["skipped_anonymous"] += 1
            continue
        if art == "SKIP_NO_TITLE":
            stats["skipped_no_title"] += 1
            continue

        if art["tags"] == "|book-review|":
            stats["book_reviews"] += 1
        if art["authors"] is None and row["author"].strip():
            # Track explicit author clears (just the Medlicott row)
            stats["author_cleared"] += 1

        url = art["url"]
        if url in urls_seen:
            intra_csv_collisions.append((url, [urls_seen[url], i]))
            stats["intra_csv_url_collision"] += 1
            continue  # don't queue the second one
        urls_seen[url] = i

        if url in existing_urls:
            stats["already_in_db"] += 1
            continue  # idempotent skip

        to_insert.append(art)

    stats["would_insert"] = len(to_insert)

    print()
    print("=" * 70)
    print(f"  JAC ingest — {'DRY RUN' if dry_run else 'APPLY'}")
    print("=" * 70)
    print(f"  CSV rows                       : {stats['csv_rows']}")
    print(f"  Title variants in CSV:")
    for name, n in title_renamed_variants.most_common():
        print(f"      {n:>5} | {name}")
    print(f"      (all resolve to canonical: {CANONICAL_JOURNAL!r})")
    print()
    print(f"  Skipped (Anonymous award lists): {stats['skipped_anonymous']}")
    print(f"      [Spec said 3; actually 31 — same category, all award-winner")
    print(f"      announcements. Skipping all per the spirit of the original ask.]")
    print(f"  Skipped (no title)             : {stats['skipped_no_title']}")
    print(f"  Already in DB (idempotent skip): {stats['already_in_db']}")
    print(f"  Intra-CSV URL collisions       : {stats['intra_csv_url_collision']}")
    if intra_csv_collisions:
        for url, idxs in intra_csv_collisions[:10]:
            print(f"      {url}  (CSV rows {idxs})")
    print(f"  Book reviews flagged           : {stats['book_reviews']}")
    print(f"  Medlicott row (author cleared) : {stats['author_cleared']}")
    print()
    print(f"  Would insert                   : {stats['would_insert']}")
    print(f"  Year range in CSV              : {min(r['year'] for r in rows)}–{max(r['year'] for r in rows)}")
    print()

    if dry_run:
        print("  (dry-run — no rows written)")
        # Show 3 sample inserts so the shape is reviewable
        print()
        print("  Sample transformed rows (first 3 inserts):")
        for art in to_insert[:3]:
            print(f"    url      : {art['url']}")
            print(f"    title    : {art['title'][:70]}")
            print(f"    authors  : {(art['authors'] or '')[:70]}")
            print(f"    year     : {art['pub_date']}")
            print(f"    vol/iss  : v{art['volume']} i{art['issue']}  pp {art['pages']}")
            print(f"    fp/lp    : {art['first_page']} / {art['last_page']}")
            print(f"    keywords : {(art['keywords'] or '')[:70]}")
            print(f"    abstract : {(art['abstract'] or '(none)')[:70]}")
            print(f"    tags     : {art['tags']}")
            print()
        return

    # Live write
    inserted = 0
    with get_conn() as conn:
        for art in to_insert:
            inserted += insert(conn, art)
        conn.commit()

    print(f"  Inserted : {inserted}")
    print(f"  (Existing JAC rows in DB now: {len(existing_urls) + inserted})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would happen; write nothing.")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
