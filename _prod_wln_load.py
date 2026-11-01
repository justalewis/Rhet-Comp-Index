"""Scoped loader for "WLN: A Journal of Writing Center Scholarship" (article-level DOIs 10.37514/wln-j., no ISSN).

backup -> full CrossRef fetch for the journal -> harvest reference lists for ONLY these articles -> recompute the
denormalized citation counts -> report. It never touches another journal's rows, so it is safe to run against a database
that has newer data than your dev copy (do NOT replace that database with a copy of this one).

  dev   : python _prod_wln_load.py
  server: set DB_PATH (C:\\Pinakes\\data\\articles.db) and PYTHONPATH (C:\\Pinakes\\app), then run it with the venv python.
          deploy\\windows\\Load-WLN.ps1 does all of that.
"""
import os
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ISSN = "no-issn:wln-j"
JOURNAL = "WLN: A Journal of Writing Center Scholarship"

from db import DB_PATH, get_conn, get_doi_to_article_id_map, init_db, update_citation_counts  # noqa: E402

# 1. backup next to the live database
# (SQLite online-backup API, not a file copy: copying a live SQLite file can capture a torn page.)
bak = f"{DB_PATH}.bak-wln-load-{time.strftime('%Y%m%d-%H%M%S')}"
_src, _dst = sqlite3.connect(DB_PATH), sqlite3.connect(bak)
_src.backup(_dst)
_dst.close()
_src.close()
print("backup -> " + bak, flush=True)

# 2. article metadata
from fetcher import fetch_journal  # noqa: E402

init_db()
added = fetch_journal(ISSN)
print("fetch_journal added: " + str(added), flush=True)

# 3. reference lists, scoped to this journal
import cite_fetcher as cf  # noqa: E402

with get_conn() as conn:
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT id, doi FROM articles WHERE journal=? AND doi IS NOT NULL ORDER BY doi", (JOURNAL,)).fetchall()]
print("articles to harvest: " + str(len(rows)), flush=True)
doi_map = get_doi_to_article_id_map()
print("DOI map entries: " + str(len(doi_map)), flush=True)
ins = err = 0
for art in rows:
    try:
        a, _ = cf._process_article(art, doi_map)
        ins += a
    except cf.RateLimitError:
        print("  429 backoff 30s " + str(art["doi"]), flush=True)
        time.sleep(30)
        continue
    except Exception as e:  # noqa: BLE001
        err += 1
        print("  ERR " + str(art["doi"]) + ": " + str(e), flush=True)
    time.sleep(0.5)
print("stored " + str(ins) + " citation rows; errors " + str(err) + "; recomputing counts", flush=True)
update_citation_counts()

# 4. report
with get_conn() as conn:
    conn.row_factory = sqlite3.Row
    q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]  # noqa: E731
    arts = q("SELECT COUNT(*) FROM articles WHERE journal=?", JOURNAL)
    refs = q("SELECT COUNT(*) FROM citations c JOIN articles a ON a.id=c.source_article_id WHERE a.journal=?", JOURNAL)
    res = q("SELECT COUNT(*) FROM citations c JOIN articles a ON a.id=c.source_article_id WHERE a.journal=? AND c.target_article_id IS NOT NULL", JOURNAL)
    out_c = q("SELECT COALESCE(SUM(internal_cites_count),0) FROM articles WHERE journal=?", JOURNAL)
    in_c = q("SELECT COALESCE(SUM(internal_cited_by_count),0) FROM articles WHERE journal=?", JOURNAL)
    early = q("SELECT COUNT(*) FROM articles WHERE journal=? AND doi LIKE '10.37514/wln-j.19%'", JOURNAL)
print("WLN: articles=" + str(arts) + " (1977-1999 DOIs: " + str(early) + ") refs=" + str(refs) + " resolved_in_index=" + str(res) +
      " out_cites=" + str(out_c) + " in_cites=" + str(in_c), flush=True)
