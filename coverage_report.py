"""
Per-journal citation-coverage accounting for the methodology chapter and the
/coverage page. Writes to data_exports/coverage/.

Produces, for every journal in articles.db:
- article count, pub-date range, source-field breakdown
- DOI / abstract / OpenAlex enrichment rates
- outbound citation coverage: refs-attempted, refs-returned, raw edges,
  edges resolved to internal article IDs
- inbound citation coverage: articles cited from inside the corpus, total
  internal inbound edges, plus external CrossRef and OpenAlex cited-by sums
- self-citation rate (within-journal edges / resolved outbound edges)
- per-article network topology within Pinakes: isolates, pure sources,
  pure sinks, hubs (both in and out)
- era breakdown (pre-2000, 2000s, 2010s, 2020+) of article counts and
  outbound-coverage rate

Outputs:
- data_exports/coverage/per_journal.csv         wide table, one row/journal
- data_exports/coverage/per_journal_era.csv     long table, journal x era
- data_exports/coverage/corpus_totals.json      overall totals
- data_exports/coverage/coverage_snapshot.json  everything the Flask route
                                                needs, in one file
- data_exports/coverage/methodology_summary.md  human-readable table
"""

import csv
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).with_name("articles.db")
OUT_DIR = Path(__file__).parent / "data_exports" / "coverage"
OUT_DIR.mkdir(parents=True, exist_ok=True)

ERA_BUCKETS = [
    ("pre-2000", 0, 1999),
    ("2000s", 2000, 2009),
    ("2010s", 2010, 2019),
    ("2020+", 2020, 9999),
]


def connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA cache_size=-40000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _year_clause(year_min, alias=""):
    """Return (sql_fragment, params) for a `pub_date >= year_min` filter on
    the given table alias (e.g. 'a' or 's' or 't'). Empty fragment if no
    filter is set."""
    if not year_min:
        return "", []
    prefix = f"{alias}." if alias else ""
    return f"AND CAST(substr({prefix}pub_date,1,4) AS INTEGER) >= ?", [year_min]


def fetch_per_journal_base(conn, year_min=None):
    """One row per journal: counts, date range, source/doi/abstract rates,
    ref-fetch attempts, openalex enrichment."""
    where, params = _year_clause(year_min)
    sql = f"""
        SELECT
            journal,
            COUNT(*)                                                AS article_count,
            MIN(substr(pub_date,1,4))                               AS year_min,
            MAX(substr(pub_date,1,4))                               AS year_max,
            SUM(CASE WHEN source='crossref' THEN 1 ELSE 0 END)      AS src_crossref,
            SUM(CASE WHEN source='scrape'   THEN 1 ELSE 0 END)      AS src_scrape,
            SUM(CASE WHEN source='rss'      THEN 1 ELSE 0 END)      AS src_rss,
            SUM(CASE WHEN source='manual'   THEN 1 ELSE 0 END)      AS src_manual,
            SUM(CASE WHEN doi      IS NOT NULL AND doi      != '' THEN 1 ELSE 0 END) AS with_doi,
            SUM(CASE WHEN abstract IS NOT NULL AND abstract != '' THEN 1 ELSE 0 END) AS with_abstract,
            SUM(CASE WHEN references_fetched_at   IS NOT NULL THEN 1 ELSE 0 END) AS refs_attempted,
            SUM(CASE WHEN openalex_enriched_at    IS NOT NULL THEN 1 ELSE 0 END) AS openalex_enriched,
            COALESCE(SUM(crossref_cited_by_count),  0)              AS ext_crossref_citedby,
            COALESCE(SUM(openalex_cited_by_count),  0)              AS ext_openalex_citedby
        FROM articles
        WHERE 1=1 {where}
        GROUP BY journal
        ORDER BY journal
    """
    rows = conn.execute(sql, params).fetchall()
    return {r["journal"]: dict(r) for r in rows}


def fetch_outbound_by_journal(conn, year_min=None):
    """For each journal (as source), count articles that actually returned
    any citation data and the raw/resolved edge totals. Filtered by source
    article pub year if year_min is set."""
    where, params = _year_clause(year_min, "s")
    sql = f"""
        SELECT
            s.journal                                             AS journal,
            COUNT(DISTINCT c.source_article_id)                   AS articles_with_outbound,
            COUNT(*)                                              AS raw_outbound_edges,
            SUM(CASE WHEN c.target_article_id IS NOT NULL THEN 1 ELSE 0 END)
                                                                  AS resolved_outbound_edges
        FROM citations c
        JOIN articles s ON c.source_article_id = s.id
        WHERE 1=1 {where}
        GROUP BY s.journal
    """
    rows = conn.execute(sql, params).fetchall()
    return {r["journal"]: dict(r) for r in rows}


def fetch_inbound_by_journal(conn, year_min=None):
    """For each journal (as target), count articles that are cited from
    inside the corpus and total internal inbound edges. Filtered by target
    article pub year if year_min is set — so each row describes the
    inbound footprint of articles in that journal published in [range]."""
    where, params = _year_clause(year_min, "t")
    sql = f"""
        SELECT
            t.journal                                             AS journal,
            COUNT(DISTINCT c.target_article_id)                   AS articles_with_inbound,
            COUNT(*)                                              AS internal_inbound_edges
        FROM citations c
        JOIN articles t ON c.target_article_id = t.id
        WHERE 1=1 {where}
        GROUP BY t.journal
    """
    rows = conn.execute(sql, params).fetchall()
    return {r["journal"]: dict(r) for r in rows}


def fetch_self_citation_by_journal(conn, year_min=None):
    """Intra-journal edges where the source is in the year range — matches
    the outbound denominator so intra_journal_pct stays interpretable."""
    where, params = _year_clause(year_min, "s")
    sql = f"""
        SELECT
            s.journal                                             AS journal,
            COUNT(*)                                              AS intra_journal_edges
        FROM citations c
        JOIN articles s ON c.source_article_id = s.id
        JOIN articles t ON c.target_article_id = t.id
        WHERE s.journal = t.journal {where}
        GROUP BY s.journal
    """
    rows = conn.execute(sql, params).fetchall()
    return {r["journal"]: r["intra_journal_edges"] for r in rows}


def fetch_topology_by_journal(conn, year_min=None):
    """Per-article network topology counted by journal. Uses the full
    citations table (not just intra-journal). When year_min is set, only
    articles in that range are classified — but their edges can connect
    anywhere in the corpus."""
    where, params = _year_clause(year_min, "a")
    sql = f"""
        WITH out_counts AS (
            SELECT source_article_id AS aid, COUNT(*) AS n
            FROM citations
            GROUP BY source_article_id
        ),
        in_counts AS (
            SELECT target_article_id AS aid, COUNT(*) AS n
            FROM citations
            WHERE target_article_id IS NOT NULL
            GROUP BY target_article_id
        )
        SELECT
            a.journal,
            SUM(CASE WHEN o.n IS NULL     AND i.n IS NULL     THEN 1 ELSE 0 END) AS isolates,
            SUM(CASE WHEN o.n IS NOT NULL AND i.n IS NULL     THEN 1 ELSE 0 END) AS pure_sources,
            SUM(CASE WHEN o.n IS NULL     AND i.n IS NOT NULL THEN 1 ELSE 0 END) AS pure_sinks,
            SUM(CASE WHEN o.n IS NOT NULL AND i.n IS NOT NULL THEN 1 ELSE 0 END) AS hubs
        FROM articles a
        LEFT JOIN out_counts o ON o.aid = a.id
        LEFT JOIN in_counts  i ON i.aid = a.id
        WHERE 1=1 {where}
        GROUP BY a.journal
    """
    rows = conn.execute(sql, params).fetchall()
    return {r["journal"]: dict(r) for r in rows}


def fetch_era_breakdown(conn):
    """Journal x era: article count, outbound-fetch-attempted count,
    articles-with-returned-cites count."""
    rows = conn.execute("""
        WITH out_counts AS (
            SELECT source_article_id AS aid, COUNT(*) AS n
            FROM citations
            GROUP BY source_article_id
        )
        SELECT
            a.journal                                             AS journal,
            CAST(substr(a.pub_date,1,4) AS INTEGER)               AS year,
            COUNT(*)                                              AS articles,
            SUM(CASE WHEN a.references_fetched_at IS NOT NULL THEN 1 ELSE 0 END)
                                                                  AS refs_attempted,
            SUM(CASE WHEN o.n IS NOT NULL THEN 1 ELSE 0 END)      AS with_outbound
        FROM articles a
        LEFT JOIN out_counts o ON o.aid = a.id
        WHERE a.pub_date IS NOT NULL AND a.pub_date != ''
        GROUP BY a.journal, year
    """).fetchall()

    by_je = {}
    for r in rows:
        y = r["year"]
        if y is None:
            continue
        era = next((name for name, lo, hi in ERA_BUCKETS if lo <= y <= hi), None)
        if era is None:
            continue
        key = (r["journal"], era)
        d = by_je.setdefault(key, {"articles": 0, "refs_attempted": 0, "with_outbound": 0})
        d["articles"]       += r["articles"]
        d["refs_attempted"] += r["refs_attempted"]
        d["with_outbound"]  += r["with_outbound"]
    return by_je


def pct(numer, denom):
    if not denom:
        return 0.0
    return round(100.0 * numer / denom, 1)


def build_per_journal(base, outbound, inbound, self_cit, topo):
    """Merge all the per-journal aggregates into a single list of dicts."""
    out = []
    for journal, b in base.items():
        o  = outbound.get(journal, {})
        i  = inbound.get(journal,  {})
        t  = topo.get(journal,     {})
        sc = self_cit.get(journal, 0)

        n         = b["article_count"]
        resolved  = o.get("resolved_outbound_edges", 0)
        raw       = o.get("raw_outbound_edges", 0)
        with_out  = o.get("articles_with_outbound", 0)

        row = {
            "journal":                  journal,
            "article_count":            n,
            "year_min":                 b["year_min"],
            "year_max":                 b["year_max"],

            "src_crossref":             b["src_crossref"],
            "src_scrape":               b["src_scrape"],
            "src_rss":                  b["src_rss"],
            "src_manual":               b["src_manual"],

            "with_doi":                 b["with_doi"],
            "doi_pct":                  pct(b["with_doi"], n),
            "with_abstract":            b["with_abstract"],
            "abstract_pct":             pct(b["with_abstract"], n),

            "refs_attempted":           b["refs_attempted"],
            "refs_attempted_pct":       pct(b["refs_attempted"], n),

            "articles_with_outbound":   with_out,
            "outbound_yield_pct":       pct(with_out, b["refs_attempted"]),
            "raw_outbound_edges":       raw,
            "resolved_outbound_edges":  resolved,
            "outbound_resolution_pct":  pct(resolved, raw),

            "articles_with_inbound":    i.get("articles_with_inbound", 0),
            "internal_inbound_edges":   i.get("internal_inbound_edges", 0),

            "intra_journal_edges":      sc,
            "intra_journal_pct":        pct(sc, resolved),

            "ext_crossref_citedby":     b["ext_crossref_citedby"],
            "ext_openalex_citedby":     b["ext_openalex_citedby"],
            "openalex_enriched":        b["openalex_enriched"],
            "openalex_enriched_pct":    pct(b["openalex_enriched"], n),

            "isolates":                 t.get("isolates",     0),
            "pure_sources":             t.get("pure_sources", 0),
            "pure_sinks":               t.get("pure_sinks",   0),
            "hubs":                     t.get("hubs",         0),
        }
        out.append(row)

    return sorted(out, key=lambda r: (-r["article_count"], r["journal"]))


def build_era_rows(era_map):
    out = []
    for (journal, era), d in era_map.items():
        out.append({
            "journal":             journal,
            "era":                 era,
            "articles":            d["articles"],
            "refs_attempted":      d["refs_attempted"],
            "with_outbound":       d["with_outbound"],
            "refs_attempted_pct":  pct(d["refs_attempted"], d["articles"]),
            "outbound_yield_pct":  pct(d["with_outbound"],  d["articles"]),
        })
    era_order = {name: i for i, (name, _, _) in enumerate(ERA_BUCKETS)}
    return sorted(out, key=lambda r: (r["journal"], era_order.get(r["era"], 99)))


def corpus_totals(conn, per_journal):
    c = conn.cursor()
    articles_total       = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    citations_total      = c.execute("SELECT COUNT(*) FROM citations").fetchone()[0]
    resolved_total       = c.execute(
        "SELECT COUNT(*) FROM citations WHERE target_article_id IS NOT NULL"
    ).fetchone()[0]
    books_total          = c.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    journals_total       = c.execute("SELECT COUNT(DISTINCT journal) FROM articles").fetchone()[0]
    oa_enriched          = c.execute(
        "SELECT COUNT(*) FROM articles WHERE openalex_enriched_at IS NOT NULL"
    ).fetchone()[0]

    return {
        "articles_total":           articles_total,
        "journals_total":           journals_total,
        "books_total":              books_total,
        "citations_total":          citations_total,
        "citations_resolved_total": resolved_total,
        "citations_resolved_pct":   pct(resolved_total, citations_total),
        "articles_openalex_enriched":     oa_enriched,
        "articles_openalex_enriched_pct": pct(oa_enriched, articles_total),
        "articles_refs_attempted":        sum(r["refs_attempted"]         for r in per_journal),
        "articles_with_outbound":         sum(r["articles_with_outbound"] for r in per_journal),
        "articles_with_inbound":          sum(r["articles_with_inbound"]  for r in per_journal),
    }


def write_csv(rows, path):
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def write_markdown_summary(per_journal, totals, path):
    lines = []
    lines.append("# Pinakes Citation Coverage — Per-Journal Accounting")
    lines.append("")
    lines.append(f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")
    lines.append("")
    lines.append("## Corpus totals")
    lines.append("")
    lines.append(f"- **{totals['articles_total']:,}** articles across **{totals['journals_total']}** journals")
    lines.append(f"- **{totals['books_total']:,}** books")
    lines.append(f"- **{totals['citations_total']:,}** citation edges total; "
                 f"**{totals['citations_resolved_total']:,}** "
                 f"({totals['citations_resolved_pct']}%) resolved to internal article IDs")
    lines.append(f"- **{totals['articles_refs_attempted']:,}** articles had a reference-fetch attempted; "
                 f"**{totals['articles_with_outbound']:,}** returned any reference data")
    lines.append(f"- **{totals['articles_with_inbound']:,}** articles are cited from at least one other article in the corpus")
    lines.append(f"- OpenAlex enrichment: **{totals['articles_openalex_enriched']:,}** articles "
                 f"({totals['articles_openalex_enriched_pct']}%)")
    lines.append("")
    lines.append("## Per-journal summary")
    lines.append("")
    lines.append("| Journal | Articles | Years | Refs attempted | With outbound | Raw edges | Resolved edges | With inbound | Intra-journal% | OpenAlex% |")
    lines.append("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|")
    for r in per_journal:
        yr = f"{r['year_min']}–{r['year_max']}" if r['year_min'] else "—"
        lines.append(
            f"| {r['journal']} | {r['article_count']:,} | {yr} | "
            f"{r['refs_attempted']:,} ({r['refs_attempted_pct']}%) | "
            f"{r['articles_with_outbound']:,} | {r['raw_outbound_edges']:,} | "
            f"{r['resolved_outbound_edges']:,} ({r['outbound_resolution_pct']}%) | "
            f"{r['articles_with_inbound']:,} | "
            f"{r['intra_journal_pct']}% | "
            f"{r['openalex_enriched_pct']}% |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_snapshot(conn, year_min=None):
    """Compute the full coverage snapshot against an open sqlite connection.
    Returns a dict with keys: generated_at, totals, per_journal, per_era,
    year_min. When year_min is set, the per-journal table is filtered so
    each row describes only articles published in [year_min, ∞). Era
    breakdown and totals remain unfiltered — they're the global baseline.
    Used by both the standalone script and db.get_detailed_coverage()."""
    base     = fetch_per_journal_base(conn, year_min=year_min)
    outbound = fetch_outbound_by_journal(conn, year_min=year_min)
    inbound  = fetch_inbound_by_journal(conn, year_min=year_min)
    self_cit = fetch_self_citation_by_journal(conn, year_min=year_min)
    topo     = fetch_topology_by_journal(conn, year_min=year_min)
    era_map  = fetch_era_breakdown(conn)

    per_journal = build_per_journal(base, outbound, inbound, self_cit, topo)
    era_rows    = build_era_rows(era_map)
    totals      = corpus_totals(conn, per_journal)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "year_min":     year_min,
        "totals":       totals,
        "per_journal":  per_journal,
        "per_era":      era_rows,
    }


def main():
    print(f"Reading from {DB_PATH}")
    with connect() as conn:
        snapshot = build_snapshot(conn)

    per_journal = snapshot["per_journal"]
    era_rows    = snapshot["per_era"]
    totals      = snapshot["totals"]

    write_csv(per_journal, OUT_DIR / "per_journal.csv")
    write_csv(era_rows,    OUT_DIR / "per_journal_era.csv")

    (OUT_DIR / "corpus_totals.json").write_text(
        json.dumps(totals, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "coverage_snapshot.json").write_text(
        json.dumps(snapshot, indent=2), encoding="utf-8"
    )

    write_markdown_summary(per_journal, totals, OUT_DIR / "methodology_summary.md")

    print(f"Wrote {len(per_journal)} journals to {OUT_DIR}")
    print(f"Corpus: {totals['articles_total']:,} articles, "
          f"{totals['citations_total']:,} citations "
          f"({totals['citations_resolved_pct']}% resolved)")


if __name__ == "__main__":
    main()
