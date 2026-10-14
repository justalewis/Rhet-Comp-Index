"""
citation_duplicates_report.py — Read-only report on duplicate rows in the
citations table. Opens the database read-only and writes nothing to it.

Run this before dedupe_citations.py, and again after it to confirm:

    cd C:\\Pinakes\\app
    C:\\Pinakes\\venv\\Scripts\\python.exe citation_duplicates_report.py `
        --db C:\\Pinakes\\data\\articles.db `
        --out C:\\Pinakes\\logs\\citation-duplicates-before.txt

A "duplicate" is a second row for the same citing article and the same
reference: the same indexed article, else the same DOI or synthetic key, else
the same reference text. dedupe_citations.py collapses exactly these groups,
keeping the lowest id in each. Rows with no target, no DOI and no text carry
nothing to compare and are left out of every count here.

Options:
    --db PATH    database to read (default: the DB_PATH environment variable,
                 else ./articles.db, the same file the app opens)
    --top N      rows in each ranked table (default 25)
    --out FILE   also write the report to FILE (UTF-8)

Standard library only, so the server's venv Python is enough.
"""

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# The dedupe key, shared with dedupe_citations.py.
KEY = "COALESCE(target_article_id, target_doi, raw_reference)"

# Rows written by scrape_lics_refs.py: raw_reference is {"unstructured": ...}
# and nothing else. CrossRef reference objects lead with "key".
SCRAPED = "COALESCE(raw_reference, '') LIKE '{\"unstructured\":%'"

GUARDS = ("idx_citations_unique_target", "idx_citations_unique_doi")


def default_db_path():
    return os.path.abspath(os.environ.get(
        "DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "articles.db")))


def open_read_only(path):
    """A connection that cannot write: mode=ro refuses writes at the file
    level, and query_only refuses them at the statement level."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _table(headers, rows, aligns=None):
    """Plain fixed-width table. aligns: '<' or '>' per column."""
    rows = [["" if v is None else str(v) for v in r] for r in rows]
    aligns = aligns or ["<"] * len(headers)
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:{a}{w}}}" for a, w in zip(aligns, widths))
    out = [fmt.format(*headers), fmt.format(*["-" * w for w in widths])]
    out += [fmt.format(*r) for r in rows]
    return "\n".join("  " + line for line in out)


def _short(s, n=60):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 3] + "..."


def _pct(part, whole):
    return f"{100.0 * part / whole:.1f}%" if whole else "-"


def build_report(conn, db_path, top=25):
    lines = []
    say = lines.append

    say("Pinakes citation duplicates report (read-only)")
    say(f"Database: {db_path}")
    try:
        size = os.path.getsize(db_path)
        say(f"Size:     {size / (1024 * 1024):.1f} MB")
    except OSError:
        pass
    say(f"SQLite:   {sqlite3.sqlite_version}")
    present = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'citations'")}
    for g in GUARDS:
        say(f"Guard {g}: {'present' if g in present else 'MISSING'}")
    say("")

    # ── Totals ──────────────────────────────────────────────────────────────
    total = conn.execute("SELECT COUNT(*) FROM citations").fetchone()[0]
    resolved = conn.execute(
        "SELECT COUNT(*) FROM citations WHERE target_article_id IS NOT NULL").fetchone()[0]
    pairs = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT source_article_id, target_article_id "
        "FROM citations WHERE target_article_id IS NOT NULL)").fetchone()[0]
    with_doi = conn.execute(
        "SELECT COUNT(*) FROM citations WHERE target_doi IS NOT NULL").fetchone()[0]
    doi_pairs = conn.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT source_article_id, target_doi "
        "FROM citations WHERE target_doi IS NOT NULL)").fetchone()[0]
    surplus = conn.execute(f"""
        SELECT COALESCE(SUM(n - 1), 0) FROM (
            SELECT COUNT(*) AS n FROM citations
            WHERE {KEY} IS NOT NULL
            GROUP BY source_article_id, {KEY})
    """).fetchone()[0]

    say("TOTALS")
    say(_table(
        ["measure", "count"],
        [
            ["citation rows", total],
            ["rows resolved to an indexed article", resolved],
            ["distinct (source, target_article_id) pairs", pairs],
            ["  surplus resolved rows", resolved - pairs],
            ["rows with a target_doi (real DOI or synthetic key)", with_doi],
            ["distinct (source, target_doi) pairs", doi_pairs],
            ["  surplus target_doi rows", with_doi - doi_pairs],
            ["rows dedupe_citations.py would delete", surplus],
            ["rows after dedupe", total - surplus],
        ],
        ["<", ">"],
    ))
    say("")
    if not surplus:
        say("No duplicate rows. Nothing for dedupe_citations.py to delete.")
        say("")

    # ── By source journal ───────────────────────────────────────────────────
    by_journal = conn.execute(f"""
        WITH g AS (
            SELECT source_article_id, COUNT(*) AS n
            FROM citations
            WHERE {KEY} IS NOT NULL
            GROUP BY source_article_id, {KEY}
            HAVING COUNT(*) > 1
        )
        SELECT COALESCE(a.journal, '(citing article missing)') AS journal,
               SUM(g.n - 1)                        AS surplus,
               COUNT(DISTINCT g.source_article_id) AS sources
        FROM g LEFT JOIN articles a ON a.id = g.source_article_id
        GROUP BY 1
        ORDER BY surplus DESC
    """).fetchall()
    rows_by_journal = {r["journal"]: r["n"] for r in conn.execute("""
        SELECT COALESCE(a.journal, '(citing article missing)') AS journal, COUNT(*) AS n
        FROM citations c LEFT JOIN articles a ON a.id = c.source_article_id
        GROUP BY 1
    """)}
    say("DUPLICATES BY CITING (SOURCE) JOURNAL")
    if by_journal:
        say(_table(
            ["journal", "rows", "surplus rows", "% surplus", "citing articles affected"],
            [[_short(r["journal"], 55), rows_by_journal.get(r["journal"], 0), r["surplus"],
              _pct(r["surplus"], rows_by_journal.get(r["journal"], 0)), r["sources"]]
             for r in by_journal],
            ["<", ">", ">", ">", ">"],
        ))
    else:
        say("  (none)")
    say("")

    # ── Most affected cited articles ────────────────────────────────────────
    cited = conn.execute("""
        SELECT c.target_article_id AS id,
               COUNT(*) AS n_rows,
               COUNT(DISTINCT c.source_article_id) AS citers
        FROM citations c
        WHERE c.target_article_id IS NOT NULL
        GROUP BY c.target_article_id
        HAVING COUNT(*) > COUNT(DISTINCT c.source_article_id)
        ORDER BY COUNT(*) - COUNT(DISTINCT c.source_article_id) DESC, id
        LIMIT ?
    """, (top,)).fetchall()
    say(f"TOP {top} CITED ARTICLES INFLATED BY DUPLICATES")
    say("  'shown' is internal_cited_by_count as stored now (the site's 'Cited by' figure).")
    if cited:
        meta = _articles(conn, [r["id"] for r in cited])
        say(_table(
            ["id", "journal", "shown", "rows", "distinct citers", "title"],
            [[r["id"], _short(meta.get(r["id"], {}).get("journal"), 28),
              meta.get(r["id"], {}).get("internal_cited_by_count"),
              r["n_rows"], r["citers"], _short(meta.get(r["id"], {}).get("title"), 50)]
             for r in cited],
            [">", "<", ">", ">", ">", "<"],
        ))
    else:
        say("  (none)")
    say("")

    # ── Most affected citing articles ───────────────────────────────────────
    citing = conn.execute(f"""
        WITH g AS (
            SELECT source_article_id, COUNT(*) AS n
            FROM citations
            WHERE {KEY} IS NOT NULL
            GROUP BY source_article_id, {KEY}
            HAVING COUNT(*) > 1
        )
        SELECT source_article_id AS id, SUM(n - 1) AS surplus
        FROM g GROUP BY source_article_id
        ORDER BY surplus DESC, id
        LIMIT ?
    """, (top,)).fetchall()
    say(f"TOP {top} CITING ARTICLES WITH REPEATED REFERENCES")
    say("  'rows' is what the article page lists as its references today.")
    if citing:
        ids = [r["id"] for r in citing]
        meta = _articles(conn, ids)
        n_rows = {r["sid"]: r["n"] for r in conn.execute(
            f"SELECT source_article_id AS sid, COUNT(*) AS n FROM citations "
            f"WHERE source_article_id IN ({','.join('?' * len(ids))}) GROUP BY source_article_id",
            ids)}
        say(_table(
            ["id", "journal", "rows", "distinct refs", "surplus", "title"],
            [[r["id"], _short(meta.get(r["id"], {}).get("journal"), 28),
              n_rows.get(r["id"], 0), n_rows.get(r["id"], 0) - r["surplus"], r["surplus"],
              _short(meta.get(r["id"], {}).get("title"), 50)]
             for r in citing],
            [">", "<", ">", ">", ">", "<"],
        ))
    else:
        say("  (none)")
    say("")

    # ── Most repeated pairs ─────────────────────────────────────────────────
    pairs_top = conn.execute("""
        SELECT source_article_id AS src, target_article_id AS tgt, COUNT(*) AS n
        FROM citations
        WHERE target_article_id IS NOT NULL
        GROUP BY source_article_id, target_article_id
        HAVING COUNT(*) > 1
        ORDER BY n DESC, src, tgt
        LIMIT 10
    """).fetchall()
    say("MOST REPEATED (CITING -> CITED) PAIRS")
    if pairs_top:
        say(_table(["citing id", "cited id", "copies"],
                   [[r["src"], r["tgt"], r["n"]] for r in pairs_top],
                   [">", ">", ">"]))
    else:
        say("  (none)")
    say("")

    # ── What the surplus rows are ───────────────────────────────────────────
    if surplus:
        kinds = conn.execute(f"""
            WITH keep AS (
                SELECT MIN(id) AS id FROM citations
                WHERE {KEY} IS NOT NULL
                GROUP BY source_article_id, {KEY}
            )
            SELECT CASE
                     WHEN {SCRAPED} THEN 'LiCS galley scrape'
                     WHEN ord IS NOT NULL THEN 'CrossRef / ordered ingest'
                     ELSE 'other (legacy or manual)'
                   END AS origin,
                   CASE
                     WHEN target_doi LIKE 'ref:%' THEN 'scraper ref: key'
                     WHEN target_doi LIKE 'raw:%' THEN 'synthetic raw: key'
                     WHEN target_doi IS NOT NULL THEN 'DOI'
                     ELSE 'no DOI'
                   END AS kind,
                   SUM(target_article_id IS NOT NULL) AS resolved,
                   COUNT(*) AS n
            FROM citations
            WHERE {KEY} IS NOT NULL AND id NOT IN (SELECT id FROM keep)
            GROUP BY origin, kind
            ORDER BY n DESC
        """).fetchall()
        say("SURPLUS ROWS BY ORIGIN AND KEY (rows the dedupe would delete)")
        say("  origin is inferred: scraped rows store {\"unstructured\": ...} only.")
        say(_table(["origin", "target_doi kind", "resolved to an article", "rows"],
                   [[r["origin"], r["kind"], r["resolved"], r["n"]] for r in kinds],
                   ["<", "<", ">", ">"]))
        say("")

        dates = conn.execute(f"""
            WITH keep AS (
                SELECT MIN(id) AS id FROM citations
                WHERE {KEY} IS NOT NULL
                GROUP BY source_article_id, {KEY}
            )
            SELECT DATE(created_at) AS d,
                   STRFTIME('%w', created_at) AS dow,
                   COUNT(*) AS n
            FROM citations
            WHERE {KEY} IS NOT NULL AND id NOT IN (SELECT id FROM keep)
            GROUP BY d
            ORDER BY d
        """).fetchall()
        names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        by_dow = {}
        for r in dates:
            key = names[int(r["dow"])] if r["dow"] is not None else "unknown"
            by_dow[key] = by_dow.get(key, 0) + r["n"]
        say("WHEN THE SURPLUS ROWS WERE WRITTEN (citations.created_at, UTC)")
        if dates:
            say(f"  {len(dates)} distinct dates, {dates[0]['d']} to {dates[-1]['d']}")
            say("  by weekday: " + ", ".join(f"{k} {v}" for k, v in sorted(
                by_dow.items(), key=lambda kv: -kv[1])))
            say("  weekly maintenance runs Sundays (04:45 server time), so a weekly")
            say("  pattern points at scrape_lics_refs.py; the 20 most recent dates:")
            say(_table(["date", "weekday", "surplus rows"],
                       [[r["d"], names[int(r["dow"])] if r["dow"] is not None else "?", r["n"]]
                        for r in dates[-20:]],
                       ["<", "<", ">"]))
        say("")

    # ── LiCS articles carrying both CrossRef and scraped lists ──────────────
    both = conn.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT c.source_article_id
            FROM citations c JOIN articles a ON a.id = c.source_article_id
            WHERE a.doi LIKE '10.21623/%'
            GROUP BY c.source_article_id
            HAVING SUM({SCRAPED}) > 0 AND SUM(NOT ({SCRAPED})) > 0
        )
    """).fetchone()[0]
    say("LiCS ARTICLES WITH BOTH A CROSSREF LIST AND A SCRAPED LIST")
    say(f"  {both}  (the fixed scraper removes its own rows from these on its next run)")
    say("")

    # ── Stored counts that a recount would change ───────────────────────────
    drift_in = conn.execute("""
        SELECT COUNT(*) FROM articles a
        WHERE COALESCE(a.internal_cited_by_count, 0) != (
            SELECT COUNT(DISTINCT source_article_id) FROM citations
            WHERE target_article_id = a.id)
    """).fetchone()[0]
    drift_out = conn.execute("""
        SELECT COUNT(*) FROM articles a
        WHERE COALESCE(a.internal_cites_count, 0) != (
            SELECT COUNT(DISTINCT target_article_id) FROM citations
            WHERE source_article_id = a.id AND target_article_id IS NOT NULL)
    """).fetchone()[0]
    say("STORED COUNTS THAT DIFFER FROM DISTINCT COUNTS")
    say(_table(["column", "articles affected"],
               [["internal_cited_by_count", drift_in],
                ["internal_cites_count", drift_out]],
               ["<", ">"]))
    say("")
    say("Next: python dedupe_citations.py --db <same path>   (dry run, writes nothing)")
    return "\n".join(lines) + "\n"


def _articles(conn, ids):
    if not ids:
        return {}
    rows = conn.execute(
        f"SELECT id, title, journal, internal_cited_by_count FROM articles "
        f"WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    return {r["id"]: dict(r) for r in rows}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only report on duplicate citation rows.")
    parser.add_argument("--db", default=default_db_path(),
                        help="database to read (default: DB_PATH, else ./articles.db)")
    parser.add_argument("--top", type=int, default=25,
                        help="rows in each ranked table (default 25)")
    parser.add_argument("--out", help="also write the report to this file (UTF-8)")
    args = parser.parse_args(argv)

    db_path = os.path.abspath(args.db)
    if not os.path.isfile(db_path):
        print(f"ERROR: no database at {db_path}", file=sys.stderr)
        return 1

    conn = open_read_only(db_path)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                            "AND name = 'citations'").fetchone():
            print(f"ERROR: {db_path} has no citations table", file=sys.stderr)
            return 1
        report = build_report(conn, db_path, top=args.top)
    finally:
        conn.close()

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(report, encoding="utf-8")
    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass
    sys.stdout.write(report)
    if args.out:
        print(f"(written to {os.path.abspath(args.out)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
