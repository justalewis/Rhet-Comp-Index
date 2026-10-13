"""
ingest_rpc_4_1.py

One-time script: imports Rhetoric, Politics & Culture 4.1 (Summer 2024) and its
reference lists from data/seeds/rpc_4_1.json into articles.db.

Vols 1–3 come from CrossRef (online ISSN 2693-7549) through the normal fetcher.
Issue 4.1 was never deposited — no DOIs, nothing in CrossRef or OpenAlex as of
2026-10-12 — so its metadata was harvested by hand from the MUSE article pages'
citation_* meta tags. Rows are source='manual' and keyed on the MUSE URL.

Idempotent: articles are INSERT-OR-IGNORE on url, and each article's citation
rows are wiped and re-inserted, so re-running re-syncs to the seed file.

If MSU Press later deposits 4.1 in CrossRef, the fetcher will add doi.org rows
for the same articles. This script skips any article whose title and year are
already in the journal from another source, but it can't undo rows it inserted before
the deposit; delete the manual rows (source='manual' AND journal=...) then.

Usage:
    python ingest_rpc_4_1.py            # uses DB_PATH or ./articles.db
"""

import json
import logging
import os
import re

from db import (
    delete_citations_for_article,
    get_conn,
    init_db,
    update_citation_counts,
    upsert_article,
    upsert_citation,
)
from tagger import auto_tag

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

SEED_PATH = os.path.join(os.path.dirname(__file__), "data", "seeds", "rpc_4_1.json")


def _norm(s):
    """Lowercased alphanumerics only — for exact-title matching across sources
    that disagree about apostrophes, dashes and subtitle punctuation."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _raw_reference(ref):
    """MUSE reference → the CrossRef-shaped dict get_article_all_references()
    renders: article-title for journal pieces, volume-title for books."""
    authors = "; ".join(ref["authors"])
    raw = {"author": authors, "year": ref["year"]}
    if ref["journal_title"]:
        raw["article-title"] = ref["title"]
        raw["journal-title"] = ref["journal_title"]
    else:
        raw["volume-title"] = ref["title"]
    for key, src in (("volume", "volume"), ("issue", "issue"), ("first-page", "first_page")):
        if ref[src]:
            raw[key] = ref[src]

    tail = ref["journal_title"] or ref["publisher"]
    if ref["journal_title"] and ref["volume"]:
        tail += f" {ref['volume']}" + (f".{ref['issue']}" if ref["issue"] else "")
    parts = [authors, f"({ref['year']})" if ref["year"] else None, ref["title"], tail]
    raw["unstructured"] = ". ".join(p for p in parts if p)
    return {k: v for k, v in raw.items() if v}


def _build_title_index(conn):
    """{normalised title: [(id, normalised journal, year)]} over the whole index."""
    index = {}
    for r in conn.execute("SELECT id, title, journal, pub_date FROM articles"):
        key = _norm(r["title"])
        if len(key) >= 20:  # short titles ("Introduction") match far too much
            index.setdefault(key, []).append((r["id"], _norm(r["journal"]), (r["pub_date"] or "")[:4]))
    return index


def _resolve(ref, title_index):
    """Indexed article id for a reference, or None. Conservative: the title
    must match exactly and so must the journal or the year, and only one
    candidate may survive."""
    hits = [
        aid for aid, journal, year in title_index.get(_norm(ref["title"]), ())
        if (ref["journal_title"] and journal == _norm(ref["journal_title"]))
        or (ref["year"] and year == ref["year"])
    ]
    return hits[0] if len(hits) == 1 else None


def main():
    init_db()
    with open(SEED_PATH, encoding="utf-8") as f:
        seed = json.load(f)
    journal = seed["journal"]

    with get_conn() as conn:
        # Title + year: every issue has an "Editor's Introduction".
        existing = {
            (_norm(r["title"]), (r["pub_date"] or "")[:4])
            for r in conn.execute(
                "SELECT title, pub_date FROM articles WHERE journal = ? AND source != 'manual'",
                (journal,),
            )
        }
        title_index = _build_title_index(conn)

    added = refs_total = linked = 0
    for art in seed["articles"]:
        if (_norm(art["title"]), seed["pub_date"][:4]) in existing:
            log.info("  Skipping (already in index from another source): %s", art["title"])
            continue

        added += upsert_article(
            art["url"], None, art["title"], "; ".join(art["authors"]), None,
            seed["pub_date"], journal, "manual",
            tags=auto_tag(art["title"], None), oa_status="closed",
        )
        with get_conn() as conn:
            row = conn.execute("SELECT id FROM articles WHERE url = ?", (art["url"],)).fetchone()
        if row is None:  # refused by a suppression/link guard in upsert_article
            continue
        art_id = row["id"]

        delete_citations_for_article(art_id)
        for i, ref in enumerate(art["references"]):
            target = _resolve(ref, title_index)
            upsert_citation(art_id, None, target, _raw_reference(ref), ord=i)
            linked += target is not None
        refs_total += len(art["references"])
        # The article page shows References only once this is set (cite_fetcher
        # sets it for DOI'd articles; these have no DOI, so it never visits them).
        with get_conn() as conn:
            conn.execute(
                "UPDATE articles SET references_fetched_at = datetime('now') WHERE id = ?", (art_id,)
            )
            conn.commit()

    # Anything that writes citations must finish by recounting.
    update_citation_counts()
    log.info("%s 4.1: %d new articles, %d references (%d linked to indexed articles).",
             journal, added, refs_total, linked)


if __name__ == "__main__":
    main()
