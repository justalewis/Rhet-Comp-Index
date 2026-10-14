"""
book_title_repair.py — fix malformed book and chapter titles in the books table.

WAC Clearinghouse's Crossref deposits carry three kinds of damage that land
verbatim on /books and /book/<id>:

  1. Double-escaped HTML entities  ("Liminality&amp;#x3a; The Work of ...")
  2. Lost non-ASCII characters     ("Am�rica Latina", "Teachers� Beliefs")
     — the deposit itself holds U+FFFD, so re-fetching Crossref can't recover them
  3. Stray whitespace              ("Weathering the  Storm")

plus one outright wrong title (10.37514/int-b.2025.2739, deposited as a URL to
an Adobe forum thread).

(1) and (3) are mechanical: clean_title() fixes them, and book_fetcher runs it at
ingest so new records arrive clean. (2) and the wrong title need ground truth,
which is the publisher's landing page: each WAC book page lists every chapter
title beside its DOI. Resolved titles are pinned in
data/book_title_overrides.json so the repair is reproducible on any deployment
without re-scraping.

Usage:
    python book_title_repair.py             # dry run: print what would change
    python book_title_repair.py --scrape    # refresh overrides from landing pages first
    python book_title_repair.py --apply     # write the fixes

Idempotent. upsert_book() never overwrites an existing title, so fixes stick
across incremental and deep refreshes.
"""

import argparse
import difflib
import html
import json
import os
import re
import sys
import time

OVERRIDES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "data", "book_title_overrides.json")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# Damage that clean_title() can't repair on its own.
_UNRECOVERABLE_RE = re.compile(r"�|https?:|www\.|\.pdf\b")


def clean_title(title):
    """Unescape (repeatedly — deposits are double-escaped), strip tags, tidy whitespace."""
    if not title:
        return title
    t = title
    for _ in range(4):
        u = html.unescape(t)
        if u == t:
            break
        t = u
    t = _TAG_RE.sub("", t)
    return _WS_RE.sub(" ", t).strip()


def needs_ground_truth(title):
    return not title or bool(_UNRECOVERABLE_RE.search(title))


def is_suspect(title):
    return not title or clean_title(title) != title or needs_ground_truth(title)


def load_overrides():
    if not os.path.exists(OVERRIDES_PATH):
        return {}
    with open(OVERRIDES_PATH, encoding="utf-8") as f:
        return {k.lower(): v for k, v in json.load(f).items()}


def save_overrides(overrides):
    os.makedirs(os.path.dirname(OVERRIDES_PATH), exist_ok=True)
    with open(OVERRIDES_PATH, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(overrides.items())), f, ensure_ascii=False, indent=1)
        f.write("\n")


# ── Landing-page scrape (WAC Clearinghouse) ──────────────────────────────────

_HEADERS = {"User-Agent": "Pinakes/1.0 (+https://pinakes.xyz)"}
# A chapter entry: <a href="...">Title</a>, Authors<br /> <span ...>DOI: 10.37514/...
# Hand-typed DOIs on the pages drift ("37514/..." without "10.", wrong year),
# so entries are keyed on series + item suffix, not the full DOI.
_ENTRY_RE = re.compile(
    r'<a\s[^>]*href="[^"]*"[^>]*>(?P<title>(?:(?!</?a[\s>]).){1,500}?)</a>(?:(?!<a\s).){0,600}?'
    r'DOI:\s*(?:<[^>]+>\s*)*(?:https?://doi\.org/)?(?P<doi>(?:10\.)?37514/[^\s"<]+)',
    re.S | re.I)
_DOI_KEY_RE = re.compile(r"37514/([a-z]+-b)\.\d{4}\.([\d.]+?)\.?$", re.I)
_PAGE_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
# Leading chapter numbering the page adds but the deposit may not ("3. ", "Chapter 3: ").
_NUM_PREFIX_RE = re.compile(r"^(?:chapter\s+)?\d+[.:]\s+", re.I)

# Landing pages that moved after deposit (the Crossref resource URL now 404s).
_MOVED_PAGES = {
    "/books/wrab2017/": "/books/international/wrab2017/",
    "/books/perspectives/navigating/": "/books/perspectives/systems/",
    "/books/practice/eportfolios/": "/books/practice/portfolios/",
    "/repository/collections/proceedings/cw2022/":
        "/repository/professional/conferences/collections/cw2022/",
    "/repository/collections/proceedings/cw2023/":
        "/repository/professional/conferences/collections/cw2023/",
    "/repository/collections/conferences/collections/cw2022/":
        "/repository/professional/conferences/collections/cw2022/",
}


def _doi_key(doi):
    m = _DOI_KEY_RE.search(doi or "")
    return (m.group(1).lower(), m.group(2)) if m else None


def _plausible(new, old):
    """Guard against a mis-parse: the scraped title must closely match the damaged one."""
    if not old or old.startswith("http"):  # deposit is junk, nothing to compare
        return True
    strip = lambda t: re.sub(r"\W", "", _NUM_PREFIX_RE.sub("", t.lower()))
    return difflib.SequenceMatcher(None, strip(new), strip(old)).ratio() >= 0.85


def _match_numbering(new, old):
    """Drop page-added chapter numbering when the deposited title didn't have it."""
    if old and not _NUM_PREFIX_RE.match(old):
        return _NUM_PREFIX_RE.sub("", new)
    return new


def _crossref_resource(doi, session):
    r = session.get(f"https://api.crossref.org/works/{doi}", timeout=30)
    r.raise_for_status()
    return r.json()["message"].get("resource", {}).get("primary", {}).get("URL")


def _scrape_landing(url, session):
    """Return ({doi: title}, page_title) from a WAC book landing page."""
    url = url.replace("wac.colostate.edu", "wacclearinghouse.org")
    for old, new in _MOVED_PAGES.items():
        if url.rstrip("/").endswith(old.rstrip("/")):
            url = url[:url.index(old.rstrip("/"))] + new
    r = session.get(url, timeout=60)
    r.raise_for_status()
    page = r.text
    found = {}
    for m in _ENTRY_RE.finditer(page):
        t = clean_title(m.group("title"))
        key = _doi_key(m.group("doi"))
        if t and key and "�" not in t:
            found.setdefault(key, t)
    pt = _PAGE_TITLE_RE.search(page)
    page_title = clean_title(pt.group(1)) if pt else None
    if page_title:
        page_title = re.sub(r"\s+-\s+WAC( Clearinghouse)?$", "", page_title)
    return found, page_title


def scrape_overrides(rows, overrides):
    """Fill overrides for every row clean_title() can't fix, one fetch per landing page."""
    import requests
    session = requests.Session()
    session.headers.update(_HEADERS)

    pending = [r for r in rows
               if r["doi"] and needs_ground_truth(clean_title(r["title"]))
               and r["doi"].lower() not in overrides]
    by_page = {}
    for r in pending:
        try:
            url = _crossref_resource(r["doi"], session)
        except Exception as e:
            print(f"  ! crossref {r['doi']}: {e}", file=sys.stderr)
            continue
        if url:
            by_page.setdefault(url, []).append(r)
        time.sleep(0.2)

    for url, members in by_page.items():
        try:
            found, page_title = _scrape_landing(url, session)
        except Exception as e:
            print(f"  ! landing {url}: {e}", file=sys.stderr)
            continue
        for r in members:
            doi = r["doi"].lower()
            key = _doi_key(doi)
            old = clean_title(r["title"])
            if key in found and _plausible(found[key], old):
                overrides[doi] = _match_numbering(found[key], old)
            elif r["record_type"] == "book" and page_title:
                overrides[doi] = page_title
            else:
                print(f"  ? no title on {url} for {doi}", file=sys.stderr)
        time.sleep(1)
    return overrides


# ── Repair ───────────────────────────────────────────────────────────────────

def plan_fixes(rows, overrides):
    fixes = []
    for r in rows:
        doi = (r["doi"] or "").lower()
        new = overrides.get(doi) or clean_title(r["title"])
        if new and new != r["title"] and not needs_ground_truth(new):
            fixes.append((r["id"], r["doi"], r["title"], new))
    return fixes


def _load_rows():
    from db.core import get_conn
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, doi, title, record_type FROM books")]


def _apply(fixes):
    from db.core import get_conn
    with get_conn() as conn:
        conn.executemany("UPDATE books SET title = ? WHERE id = ?",
                         [(new, _id) for _id, _, _, new in fixes])
        # scrape_ccdp.py used to write 'edited collection', splitting the
        # /books type filter in two.
        n = conn.execute("UPDATE books SET book_type = 'edited-collection' "
                         "WHERE book_type = 'edited collection'").rowcount
        conn.commit()
    return n


def norm_full_title(t):
    """Whole-title match key: 'Literate Action, Volume 2' == 'Literate Action Volume 2'."""
    return re.sub(r"\W", "", clean_title(t or "").lower())


def merge_copublished():
    """Fold Parlor Press scrape rows into their WAC Clearinghouse twins.

    WAC and Parlor co-publish (Perspectives on Writing, International
    Exchanges …), so fetch_parlor.py and the WAC Crossref harvest each
    created a record for the same book. The WAC row keeps the DOI and
    chapters; the Parlor row contributes its abstract/subjects/ISBN where
    the WAC row lacks them, then is removed."""
    from db.core import get_conn
    merged = 0
    with get_conn() as conn:
        books = conn.execute(
            "SELECT id, title, publisher, source, abstract, subjects, isbn "
            "FROM books WHERE record_type = 'book'").fetchall()
        wac = {}
        for b in books:
            if b["publisher"] == "WAC Clearinghouse":
                wac.setdefault(norm_full_title(b["title"]), b)
        for b in books:
            if b["source"] != "parlor":
                continue
            twin = wac.get(norm_full_title(b["title"]))
            if twin is None:
                continue
            conn.execute(
                "UPDATE books SET abstract = COALESCE(abstract, ?), "
                "subjects = COALESCE(subjects, ?), isbn = COALESCE(isbn, ?) "
                "WHERE id = ?",
                (b["abstract"], b["subjects"], b["isbn"], twin["id"]))
            conn.execute("UPDATE books SET parent_id = ? WHERE parent_id = ?",
                         (twin["id"], b["id"]))
            conn.execute("DELETE FROM books WHERE id = ?", (b["id"],))
            merged += 1
        conn.commit()
    return merged


def repair_all():
    """Apply every resolvable fix (no scraping). Called by weekly_maintenance
    step 12 so each deployment self-heals; a no-op once clean."""
    fixes = plan_fixes(_load_rows(), load_overrides())
    n = _apply(fixes)
    return {"titles_fixed": len(fixes), "book_types_normalised": n,
            "copublished_merged": merge_copublished()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write fixes (default: dry run)")
    ap.add_argument("--scrape", action="store_true",
                    help="resolve unrecoverable titles from publisher landing pages")
    args = ap.parse_args()

    rows = _load_rows()
    suspects = [r for r in rows if is_suspect(r["title"])]
    overrides = load_overrides()

    if args.scrape:
        before = len(overrides)
        scrape_overrides(suspects, overrides)
        save_overrides(overrides)
        print(f"overrides: {before} -> {len(overrides)} ({OVERRIDES_PATH})")

    fixes = plan_fixes(rows, overrides)
    for _id, doi, old, new in fixes:
        print(f"[{_id}] {doi}\n    - {old}\n    + {new}")
    unresolved = [r for r in suspects
                  if needs_ground_truth(clean_title(r["title"]))
                  and (r["doi"] or "").lower() not in overrides]
    for r in unresolved:
        print(f"UNRESOLVED [{r['id']}] {r['doi']}: {r['title']}")
    print(f"\n{len(suspects)} suspect titles, {len(fixes)} fixable, "
          f"{len(unresolved)} unresolved")

    if args.apply:
        n = _apply(fixes)
        m = merge_copublished()
        print(f"applied {len(fixes)} title fixes, normalised {n} book_type values, "
              f"merged {m} WAC/Parlor co-published duplicates")


if __name__ == "__main__":
    main()
