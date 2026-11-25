"""ingest_writing_spaces.py — load Writing Spaces (vols 1-6 + the Assignment and Activity Archive) into the index.

Writing Spaces: Readings on Writing is a book series, not a journal, but it is indexed as one so its chapters sit in the
journal list beside the composition journals. Its DOIs (10.37514/WSP-B.*) were registered 2026-11-25 and the Crossref
REST API had not indexed them yet, so this loads them from the Crossref deposit itself. Once the API has them, the
"no-issn:wsp-b" selector in journals.py lets the daily fetch pick up anything new (volume 7, new archive items); the
rows loaded here are keyed on the same https://doi.org/<lower-case DOI> URL the fetcher builds, so it skips them.

Two steps, both idempotent:

  python ingest_writing_spaces.py --build-from "<Master DOI Deposit>.xml"
      Deposit XML -> data/writing_spaces.json (committed). Keeps only what the index uses: no depositor details.

  python ingest_writing_spaces.py [--backup]
      data/writing_spaces.json -> articles + citations. Inserts items whose DOI isn't indexed yet, fills their
      volume/pages, replaces each item's reference list with the deposited one, links any existing reference that
      cites a Writing Spaces DOI, recomputes the citation counts. Touches no other journal's rows, so it is safe on
      the live database. Server: deploy/windows/Load-WritingSpaces.ps1 (backup, load, service restart).

Volume-level DOIs are not loaded as articles: the six volumes are edited books, which the weekly book harvest picks
up from Crossref like the rest of WAC's books.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(ROOT, "data", "writing_spaces.json")

JOURNAL = "Writing Spaces: Readings on Writing"
ARCHIVE_LABEL = "Assignment and Activity Archive"

_CR = "{http://www.crossref.org/schema/5.4.0}"
_AI = "{http://www.crossref.org/AccessIndicators.xsd}"
_JATS = "{http://www.ncbi.nlm.nih.gov/JATS1}"


# ── deposit XML -> data/writing_spaces.json ──────────────────────────────────

def _text(el, path):
    found = el.find(path)
    if found is None or found.text is None:
        return None
    return " ".join(found.text.split()) or None


def _date(el):
    """Crossref <publication_date> -> YYYY-MM-DD, padding a missing month/day with 01 as fetcher._parse_date does."""
    pd = el.find(f"{_CR}publication_date")
    if pd is None:
        return None
    y, m, d = (_text(pd, f"{_CR}{k}") for k in ("year", "month", "day"))
    if not y:
        return None
    return f"{int(y):04d}-{int(m or 1):02d}-{int(d or 1):02d}"


def _people(el, role):
    out = []
    for p in el.findall(f"{_CR}contributors/{_CR}person_name"):
        if p.get("contributor_role") != role:
            continue
        given, family = _text(p, f"{_CR}given_name"), _text(p, f"{_CR}surname")
        name = f"{given} {family}".strip() if given else (family or "")
        if name:
            out.append(name)
    return out


def build_from_deposit(xml_path):
    root = ET.parse(xml_path).getroot()
    batch_id = _text(root, f"{_CR}head/{_CR}doi_batch_id")
    volumes, items = [], []
    for book in root.iter(f"{_CR}book"):
        bm = book.find(f"{_CR}book_metadata")
        book_title = _text(bm, f"{_CR}titles/{_CR}title")
        m = re.search(r"Volume\s+(\d+)\s*$", book_title or "")
        if not m:
            raise SystemExit(f"Can't read a volume number from book title {book_title!r}")
        vol = int(m.group(1))
        volumes.append({
            "volume": vol,
            "doi": _text(bm, f"{_CR}doi_data/{_CR}doi").lower(),
            "title": book_title,
            "editors": _people(bm, "editor"),
            "pub_date": _date(bm),
            "isbn": {i.get("media_type"): " ".join(i.text.split()) for i in bm.findall(f"{_CR}isbn")},
            "url": _text(bm, f"{_CR}doi_data/{_CR}resource"),
            "abstract": _text(bm, f"{_JATS}abstract/{_JATS}p"),
        })
        for ci in book.findall(f"{_CR}content_item"):
            doi = _text(ci, f"{_CR}doi_data/{_CR}doi").lower()
            first, last = _text(ci, f"{_CR}pages/{_CR}first_page"), _text(ci, f"{_CR}pages/{_CR}last_page")
            lic = ci.find(f"{_AI}program/{_AI}license_ref[@applies_to='vor']")
            refs = []
            for c in ci.findall(f"{_CR}citation_list/{_CR}citation"):
                ref = {"key": c.get("key")}
                un = _text(c, f"{_CR}unstructured_citation")
                rdoi = _text(c, f"{_CR}doi")
                if un:
                    ref["unstructured"] = un
                if rdoi:
                    ref["DOI"] = rdoi.lower()
                refs.append(ref)
            items.append({
                "doi": doi,
                # component_type "chapter" = a chapter or the vol 1 introduction; "other" = an archive item
                "kind": "chapter" if ci.get("component_type") == "chapter" else "archive",
                "volume": vol,
                "title": _text(ci, f"{_CR}titles/{_CR}title"),
                "authors": _people(ci, "author"),
                "pub_date": _date(ci),
                "first_page": first,
                "last_page": last,
                "landing_url": _text(ci, f"{_CR}doi_data/{_CR}resource"),
                "license": " ".join(lic.text.split()) if lic is not None and lic.text else None,
                "references": refs,
            })
    if not items:
        raise SystemExit(f"No content items found in {xml_path}")
    return {
        "journal": JOURNAL,
        "source": f"Crossref deposit {batch_id} ({os.path.basename(xml_path)})",
        "volumes": sorted(volumes, key=lambda v: v["volume"]),
        "items": items,
    }


# ── data/writing_spaces.json -> database ────────────────────────────────────

def _backup(db_path):
    """SQLite online backup next to the database (a plain file copy of a live database can catch a torn page)."""
    bak = f"{db_path}.bak-writing-spaces-{time.strftime('%Y%m%d-%H%M%S')}"
    src, dst = sqlite3.connect(db_path), sqlite3.connect(bak)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return bak


def _int_or_none(s):
    return int(s) if s and s.isdigit() else None


def load(data, log=print):
    from db import (delete_citations_for_article, get_conn, get_doi_to_article_id_map, init_db,
                    mark_references_fetched, update_citation_counts, update_fetch_log, upsert_article,
                    upsert_citation)
    from journals import GOLD_OA_JOURNALS
    from tagger import auto_tag

    if data.get("journal") != JOURNAL:
        raise SystemExit(f"{DATA_PATH} is for {data.get('journal')!r}, not {JOURNAL!r}")
    init_db()
    items = data["items"]
    gold = JOURNAL in GOLD_OA_JOURNALS

    # 1. articles. upsert_article keys on url and skips one that's already there, so a re-run inserts nothing.
    added = 0
    for it in items:
        url = f"https://doi.org/{it['doi']}"
        added += upsert_article(
            url, it["doi"], it["title"], "; ".join(it["authors"]) or None, None, it["pub_date"], JOURNAL, "crossref",
            keywords=ARCHIVE_LABEL if it["kind"] == "archive" else None,
            tags=auto_tag(it["title"], None),
            oa_status="gold" if gold else None, oa_url=url if gold else None,
        )
    log(f"articles: {added} inserted, {len(items) - added} already indexed")

    # Fill volume/pages, and the archive label on a row the daily fetch inserted first (once Crossref's API has these
    # DOIs, the fetcher may get to an item before this script does; it knows nothing about the archive).
    with get_conn() as conn:
        for it in items:
            first, last = it.get("first_page"), it.get("last_page")
            conn.execute(
                "UPDATE articles SET volume = ?, pages = ?, first_page = ?, last_page = ?, "
                "keywords = COALESCE(keywords, ?) WHERE doi = ?",
                (str(it["volume"]), f"{first}-{last}" if first and last else first,
                 _int_or_none(first), _int_or_none(last),
                 ARCHIVE_LABEL if it["kind"] == "archive" else None, it["doi"]))
        conn.commit()
        ids = dict(conn.execute("SELECT LOWER(doi), id FROM articles WHERE journal = ? AND doi IS NOT NULL",
                                (JOURNAL,)).fetchall())
    missing = [it["doi"] for it in items if it["doi"] not in ids]
    if missing:
        # Suppressed DOIs are refused by upsert_article on purpose; anything else is a real failure.
        log(f"WARNING: {len(missing)} item(s) not in the articles table (suppressed?): {missing[:5]}")

    # 2. reference lists, replaced wholesale (the deposit is the source of truth for these items).
    #    The DOI map is built after step 1 so references to other Writing Spaces chapters resolve.
    doi_map = get_doi_to_article_id_map()
    refs = resolved = 0
    for it in items:
        aid = ids.get(it["doi"])
        if aid is None:
            continue
        delete_citations_for_article(aid)
        for ord_idx, ref in enumerate(it["references"]):
            tdoi = ref.get("DOI")
            tid = doi_map.get(tdoi) if tdoi else None
            raw = dict(ref)
            if tdoi:
                raw["doi-asserted-by"] = "publisher"
            # 0 when the citations uniqueness guard drops a repeat (the same DOI twice in one list)
            written = upsert_citation(source_article_id=aid, target_doi=tdoi, target_article_id=tid,
                                      raw_reference=raw, ord=ord_idx)
            refs += written
            resolved += bool(written and tid is not None)
        mark_references_fetched(aid, crossref_cited_by_count=None)
    log(f"references: {refs} stored, {resolved} resolve to an indexed article")

    # 3. references elsewhere in the index that already cite a Writing Spaces DOI (by DOI, before it was indexed).
    #    OR IGNORE: if the citing article somehow already has a row for that chapter, keep the DOI-only row as it is
    #    rather than trip idx_citations_unique_target.
    with get_conn() as conn:
        relinked = conn.execute("""
            UPDATE OR IGNORE citations
               SET target_article_id = (SELECT a.id FROM articles a
                                         WHERE LOWER(a.doi) = LOWER(citations.target_doi) AND a.journal = ?)
             WHERE target_article_id IS NULL
               AND LOWER(target_doi) LIKE '10.37514/wsp-b.%'
               AND EXISTS (SELECT 1 FROM articles a
                            WHERE LOWER(a.doi) = LOWER(citations.target_doi) AND a.journal = ?)
        """, (JOURNAL, JOURNAL)).rowcount
        conn.commit()
    log(f"existing references newly linked to Writing Spaces: {relinked}")

    update_citation_counts()
    # A fetch-log row means the daily fetch runs incrementally for this title from today instead of walking it whole.
    update_fetch_log(JOURNAL)

    with get_conn() as conn:
        q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
        n = q("SELECT COUNT(*) FROM articles WHERE journal = ?", JOURNAL)
        n_arch = q("SELECT COUNT(*) FROM articles WHERE journal = ? AND keywords = ?", JOURNAL, ARCHIVE_LABEL)
        out_c = q("SELECT COALESCE(SUM(internal_cites_count), 0) FROM articles WHERE journal = ?", JOURNAL)
        in_c = q("SELECT COALESCE(SUM(internal_cited_by_count), 0) FROM articles WHERE journal = ?", JOURNAL)
    log(f"{JOURNAL}: {n} articles ({n - n_arch} chapters, {n_arch} archive items); "
        f"in-index citations out={out_c} in={in_c}")
    return {"inserted": added, "articles": n, "references": refs, "resolved": resolved, "relinked": relinked}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--build-from", metavar="DEPOSIT_XML",
                    help="rebuild data/writing_spaces.json from a Crossref deposit; touches no database")
    ap.add_argument("--data", default=DATA_PATH, help="JSON to load (default: %(default)s)")
    ap.add_argument("--backup", action="store_true", help="take an online backup of the database first")
    args = ap.parse_args(argv)

    if args.build_from:
        data = build_from_deposit(args.build_from)
        with open(args.data, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
        kinds = {k: sum(1 for i in data["items"] if i["kind"] == k) for k in ("chapter", "archive")}
        n_refs = sum(len(i["references"]) for i in data["items"])
        print(f"wrote {args.data}: {len(data['volumes'])} volumes, {kinds['chapter']} chapters, "
              f"{kinds['archive']} archive items, {n_refs} references")
        return 0

    with open(args.data, encoding="utf-8") as f:
        data = json.load(f)
    if args.backup:
        from db import DB_PATH
        print("backup -> " + _backup(DB_PATH), flush=True)
    load(data, log=lambda s: print(s, flush=True))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
