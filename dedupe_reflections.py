"""
dedupe_reflections.py — One-off merge of duplicate Reflections rows.

Reflections was harvested three ways over the project's life: WP-API/RSS
(2024-era), archive-page scraping (early 2026), and finally Penn State
Libraries' full CrossRef deposit (May 2026). The three eras used different
URLs, so upsert-by-URL never collapsed them; after the June 2026 deep
refresh the journal held ~1,624 rows for ~600 actual articles.

The merge:
  1. CrossRef rows are canonical (DOIs, reference lists).
  2. rss/scrape rows are matched to a canonical row on normalized title +
     publication year, with a title-only fallback when the title is unique
     on both sides (scrape-era dates are approximate).
  3. Matched duplicates have their dependents repointed (citations source
     and target, author_article_affiliations, article_author_institutions,
     openalex_fetch_log), any fields missing on the canonical row copied
     over (fill-only), and are then deleted. FTS stays in sync via the
     delete trigger.
  4. Leftover rss/scrape rows that duplicate EACH OTHER (no CrossRef match)
     are merged the same way, keeping the row with the most filled fields.
  5. Whatever remains unmatched is reported, not touched.

Reflections is no longer in RSS_JOURNALS and its scraper strategy is dead
code, so deleted rows will not be re-harvested.

Usage:
    python dedupe_reflections.py            # dry run — report only
    python dedupe_reflections.py --apply    # perform the merge
"""

import argparse
import re
import sys

from db import get_conn, init_db

JOURNAL = "Reflections: A Journal of Community-Engaged Writing and Rhetoric"

# Columns eligible for fill-only copy from duplicate → canonical.
# Everything except identity/provenance columns (id, url, source,
# fetched_at, journal) — those stay canonical.
_PROTECTED = {"id", "url", "source", "fetched_at", "journal"}


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def year_of(pub_date):
    return (pub_date or "")[:4] or None


def fetch_rows(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(articles)")]
    rows = conn.execute(
        "SELECT * FROM articles WHERE journal = ?", (JOURNAL,)
    ).fetchall()
    return cols, [dict(zip(cols, r)) for r in rows]


def filled_count(row):
    return sum(1 for k, v in row.items()
               if k not in _PROTECTED and v not in (None, ""))


def build_matches(rows):
    """Return (merges, leftovers): merges is a list of (canonical, dupe)."""
    canon = [r for r in rows if r["source"] == "crossref"]
    others = [r for r in rows if r["source"] != "crossref"]

    by_key = {}
    by_title = {}
    for c in canon:
        by_key.setdefault((norm_title(c["title"]), year_of(c["pub_date"])), []).append(c)
        by_title.setdefault(norm_title(c["title"]), []).append(c)

    canon_norms = list(by_title.keys())

    def match_one(o):
        """Return the canonical row for one rss/scrape row, or None."""
        year = year_of(o["pub_date"])
        # The three harvest eras disagree on review prefixes ("Book Review:
        # X" vs the deposit's bare "X"), so try both forms.
        variants = [norm_title(o["title"])]
        stripped = re.sub(r"^(book )?review( essay)?:?\s+", "",
                          o["title"] or "", flags=re.I)
        if stripped != (o["title"] or ""):
            variants.append(norm_title(stripped))

        for nt in variants:
            if not nt:
                continue
            key_hits = by_key.get((nt, year), [])
            if len(key_hits) == 1:
                return key_hits[0]
            # Title unique among canonicals: recurring titles ("From the
            # Editors") appear multiple times in canon and fail this guard.
            title_hits = by_title.get(nt, [])
            if len(title_hits) == 1:
                return title_hits[0]
            # Subtitle drift: one era stored "Title: Subtitle", the other
            # truncated. Accept a prefix relation only when exactly one
            # canonical row relates, and demand a year match when the
            # shared stem is short enough to be generic ("introduction").
            pre = [c for c in canon_norms
                   if c.startswith(nt + " ") or nt.startswith(c + " ")]
            if len(pre) == 1 and len(by_title[pre[0]]) == 1:
                cand = by_title[pre[0]][0]
                stem = min(len(nt), len(pre[0]))
                if stem >= 25 or year_of(cand["pub_date"]) == year:
                    return cand
            # Recurring stems ("Editor's Introduction" appears once per
            # issue): unambiguous when exactly one same-stem canonical row
            # shares the publication year.
            if len(pre) == 1 and year is not None:
                same_year = [c for c in by_title[pre[0]]
                             if year_of(c["pub_date"]) == year]
                if len(same_year) == 1:
                    return same_year[0]
            if len(title_hits) > 1 and year is not None:
                same_year = [c for c in title_hits
                             if year_of(c["pub_date"]) == year]
                if len(same_year) == 1:
                    return same_year[0]
        return None

    merges, unmatched = [], []
    for o in others:
        hit = match_one(o)
        if hit is not None:
            merges.append((hit, o))
        else:
            unmatched.append(o)

    # Second pass: leftovers that duplicate each other (rss vs scrape).
    # Keep the row with the most filled fields as the local canonical.
    groups = {}
    for o in unmatched:
        groups.setdefault((norm_title(o["title"]), year_of(o["pub_date"])), []).append(o)
    leftovers = []
    for group in groups.values():
        if len(group) == 1:
            leftovers.append(group[0])
            continue
        group.sort(key=lambda r: (-filled_count(r), r["id"]))
        keeper = group[0]
        for dupe in group[1:]:
            merges.append((keeper, dupe))
        leftovers.append(keeper)
    return merges, leftovers


def apply_merge(conn, canonical, dupe, cols):
    cid, did = canonical["id"], dupe["id"]

    # Repoint dependents. citations has no uniqueness constraint on pairs;
    # the UNIQUE-constrained tables get UPDATE OR IGNORE + sweep.
    conn.execute("UPDATE citations SET source_article_id=? WHERE source_article_id=?",
                 (cid, did))
    conn.execute("UPDATE citations SET target_article_id=? WHERE target_article_id=?",
                 (cid, did))
    for table in ("author_article_affiliations",
                  "article_author_institutions",
                  "openalex_fetch_log"):
        conn.execute(f"UPDATE OR IGNORE {table} SET article_id=? WHERE article_id=?",
                     (cid, did))
        conn.execute(f"DELETE FROM {table} WHERE article_id=?", (did,))

    # Fill-only copy: any value the canonical row lacks but the dupe has.
    sets, params = [], []
    for col in cols:
        if col in _PROTECTED:
            continue
        if canonical.get(col) in (None, "") and dupe.get(col) not in (None, ""):
            sets.append(f"{col} = ?")
            params.append(dupe[col])
    if sets:
        params.append(cid)
        conn.execute(f"UPDATE articles SET {', '.join(sets)} WHERE id = ?", params)

    conn.execute("DELETE FROM articles WHERE id = ?", (did,))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="perform the merge (default: dry-run report)")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    init_db()
    with get_conn() as conn:
        conn.execute("PRAGMA busy_timeout = 60000")
        cols, rows = fetch_rows(conn)
        by_source = {}
        for r in rows:
            by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        print(f"before: {len(rows)} rows {by_source}")

        # WP-API-era issue posts ("Issue 22.1: Special Issue on ...") are
        # containers, not articles; the articles themselves have their own
        # rows. Cull them outright.
        issue_posts = [r for r in rows
                       if r["source"] != "crossref"
                       and re.match(r"^issue \d+ \d+", norm_title(r["title"]))]
        print(f"issue-container posts to remove: {len(issue_posts)}")
        if args.apply:
            for r in issue_posts:
                conn.execute("DELETE FROM articles WHERE id=?", (r["id"],))
        issue_ids = {r["id"] for r in issue_posts}
        rows = [r for r in rows if r["id"] not in issue_ids]

        merges, leftovers = build_matches(rows)
        cite_hits = 0
        for _, dupe in merges:
            cite_hits += conn.execute(
                "SELECT COUNT(*) FROM citations WHERE source_article_id=? OR target_article_id=?",
                (dupe["id"], dupe["id"])).fetchone()[0]
        print(f"planned merges: {len(merges)}  (citation edges to repoint: {cite_hits})")
        print(f"non-crossref rows kept (no match): "
              f"{len([r for r in leftovers if r['source'] != 'crossref'])}")

        if not args.apply:
            print("\nDRY RUN — sample of planned merges:")
            for canonical, dupe in merges[:10]:
                print(f"  [{dupe['source']}#{dupe['id']}] {dupe['title'][:48]!r}"
                      f" -> [crossref#{canonical['id']}]"
                      if canonical["source"] == "crossref" else
                      f"  [{dupe['source']}#{dupe['id']}] {dupe['title'][:48]!r}"
                      f" -> [{canonical['source']}#{canonical['id']}]")
            print("\nRe-run with --apply to perform the merge.")
            return

        for canonical, dupe in merges:
            apply_merge(conn, canonical, dupe, cols)
        conn.commit()

        _, after_rows = fetch_rows(conn)
        after_sources = {}
        for r in after_rows:
            after_sources[r["source"]] = after_sources.get(r["source"], 0) + 1
        print(f"after: {len(after_rows)} rows {after_sources}")
        fts_ok = conn.execute(
            "SELECT COUNT(*) FROM articles_fts WHERE articles_fts MATCH 'reflections'"
        ).fetchone()[0]
        print(f"FTS responds (matches for 'reflections'): {fts_ok}")


if __name__ == "__main__":
    main()
