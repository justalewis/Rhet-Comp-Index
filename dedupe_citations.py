"""
dedupe_citations.py — Delete duplicate citation rows, install the citations
uniqueness guards, and recompute the internal citation counts.

Without --yes this is a dry run: it opens the database read-only, prints what
it would delete and how the counts would move, and writes nothing.

    cd C:\\Pinakes\\app
    C:\\Pinakes\\venv\\Scripts\\python.exe dedupe_citations.py --db C:\\Pinakes\\data\\articles.db
    C:\\Pinakes\\venv\\Scripts\\python.exe dedupe_citations.py --db C:\\Pinakes\\data\\articles.db `
        --yes --backup-dir C:\\Pinakes\\backups

With --yes, in order:
  1. Backs up the database with SQLite's online backup API (safe while the
     app is running) and runs PRAGMA quick_check on the copy. Stops if the
     check fails or the disk lacks room. --no-backup skips this; use it only
     if you have just taken a backup some other way.
  2. In one transaction:
     - deletes every row that repeats an earlier row for the same citing
       article and the same reference (the same indexed article, else the
       same DOI or synthetic key, else the same reference text), keeping the
       lowest id. Rows with no target, no DOI and no text are left alone;
     - builds the two partial unique indexes from db.core
       (CITATION_UNIQUE_INDEXES), so the duplicates cannot come back;
     - recomputes articles.internal_cited_by_count and internal_cites_count
       as distinct-article counts.
     Any failure rolls the whole transaction back.
  3. Clears the Datastories disk cache (datastories_cache.clear_all).

Running it again is safe: the second run finds nothing to delete, the indexes
already exist, and the recount reproduces the same numbers.

Options:
    --db PATH          database (default: DB_PATH, else ./articles.db)
    --yes              actually change the database
    --backup-dir DIR   where the pre-change backup goes (default: a "backups"
                       folder beside the database's folder if one exists,
                       e.g. C:\\Pinakes\\backups, else the database's folder)
    --no-backup        skip step 1
"""

import argparse
import datetime
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from citation_duplicates_report import KEY, default_db_path, open_read_only
from db.citations import update_citation_counts
from db.core import CITATION_UNIQUE_INDEXES, ensure_citation_unique_indexes

GUARD_NAMES = tuple(name for name, _ in CITATION_UNIQUE_INDEXES)


# ── Measurements ─────────────────────────────────────────────────────────────

def measure(conn):
    """Row and pair counts, guard status, and the top of Most Cited."""
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    present = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'citations'")}
    return {
        "rows": one("SELECT COUNT(*) FROM citations"),
        "resolved_rows": one(
            "SELECT COUNT(*) FROM citations WHERE target_article_id IS NOT NULL"),
        "distinct_pairs": one(
            "SELECT COUNT(*) FROM (SELECT DISTINCT source_article_id, target_article_id "
            "FROM citations WHERE target_article_id IS NOT NULL)"),
        "duplicate_rows": one(f"""
            SELECT COALESCE(SUM(n - 1), 0) FROM (
                SELECT COUNT(*) AS n FROM citations
                WHERE {KEY} IS NOT NULL
                GROUP BY source_article_id, {KEY})"""),
        "cited_by_total": one(
            "SELECT COALESCE(SUM(internal_cited_by_count), 0) FROM articles"),
        "guards": {g: g in present for g in GUARD_NAMES},
        "top_cited": [tuple(r) for r in conn.execute("""
            SELECT id, internal_cited_by_count, title FROM articles
            WHERE internal_cited_by_count > 0
            ORDER BY internal_cited_by_count DESC, id LIMIT 10""")],
    }


def projected_counts(conn, limit=15):
    """Articles whose internal_cited_by_count a distinct recount would change,
    biggest change first: (id, stored, distinct citers, title)."""
    return [tuple(r) for r in conn.execute("""
        SELECT a.id, a.internal_cited_by_count, d.citers, a.title
        FROM articles a
        JOIN (SELECT target_article_id AS id,
                     COUNT(DISTINCT source_article_id) AS citers
              FROM citations WHERE target_article_id IS NOT NULL
              GROUP BY target_article_id) d ON d.id = a.id
        WHERE COALESCE(a.internal_cited_by_count, 0) != d.citers
        ORDER BY ABS(COALESCE(a.internal_cited_by_count, 0) - d.citers) DESC, a.id
        LIMIT ?""", (limit,))]


def duplicates_by_journal(conn):
    return [tuple(r) for r in conn.execute(f"""
        WITH g AS (
            SELECT source_article_id, COUNT(*) AS n FROM citations
            WHERE {KEY} IS NOT NULL
            GROUP BY source_article_id, {KEY}
            HAVING COUNT(*) > 1)
        SELECT COALESCE(a.journal, '(citing article missing)'), SUM(g.n - 1)
        FROM g LEFT JOIN articles a ON a.id = g.source_article_id
        GROUP BY 1 ORDER BY 2 DESC""")]


def _short(s, n=60):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 3] + "..."


def print_state(label, m):
    print(f"{label}:")
    print(f"  citation rows                          {m['rows']:>10,}")
    print(f"  rows resolved to an indexed article    {m['resolved_rows']:>10,}")
    print(f"  distinct citing->cited pairs           {m['distinct_pairs']:>10,}")
    print(f"  duplicate rows                         {m['duplicate_rows']:>10,}")
    print(f"  sum of internal_cited_by_count         {m['cited_by_total']:>10,}")
    for g, ok in m["guards"].items():
        print(f"  {g:<38} {'present' if ok else 'missing':>10}")


def print_top(before, after):
    print("Most Cited, top 10 (id, count, title):")
    print("  before" + " " * 58 + "after")
    for i in range(max(len(before), len(after))):
        b = before[i] if i < len(before) else None
        a = after[i] if i < len(after) else None
        left = f"{b[0]:>6} {b[1]:>5}  {_short(b[2], 48)}" if b else ""
        right = f"{a[0]:>6} {a[1]:>5}  {_short(a[2], 48)}" if a else ""
        print(f"  {left:<62}  {right}")


# ── Backup ───────────────────────────────────────────────────────────────────

def default_backup_dir(db_path):
    sibling = Path(db_path).resolve().parent.parent / "backups"
    return sibling if sibling.is_dir() else Path(db_path).resolve().parent


def backup(db_path, backup_dir):
    """Online-backup copy of db_path into backup_dir, verified with
    quick_check. Returns the copy's path; raises SystemExit on any problem."""
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    size = os.path.getsize(db_path)
    free = shutil.disk_usage(backup_dir).free
    if free < size * 1.2:
        raise SystemExit(f"ERROR: {backup_dir} has {free / 2**20:.0f} MB free; "
                         f"the backup needs about {size / 2**20:.0f} MB.")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"{Path(db_path).stem}-{stamp}-pre-citation-dedupe.db"
    print(f"Backing up {db_path}\n        -> {dest}")
    t0 = time.time()
    src = open_read_only(db_path)
    dst = sqlite3.connect(str(dest))
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    check = sqlite3.connect(str(dest))
    try:
        result = check.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        raise SystemExit(f"ERROR: quick_check on the backup returned {result!r}; "
                         "nothing was changed.")
    print(f"Backup verified (quick_check ok, {os.path.getsize(dest) / 2**20:.1f} MB, "
          f"{time.time() - t0:.1f}s).")
    return dest


# ── The fix ──────────────────────────────────────────────────────────────────

def delete_duplicates(conn):
    """Delete rows repeating an earlier row's (source, reference) key; keep
    the lowest id. Returns the number deleted. Caller owns the transaction."""
    return conn.execute(f"""
        DELETE FROM citations
        WHERE {KEY} IS NOT NULL
          AND id NOT IN (
              SELECT MIN(id) FROM citations
              WHERE {KEY} IS NOT NULL
              GROUP BY source_article_id, {KEY})
    """).rowcount


def apply_fix(conn):
    """Delete duplicates, build the guards, recount — one transaction.
    Returns rows deleted. `conn` must be in autocommit mode
    (isolation_level=None) so the explicit BEGIN/COMMIT are ours."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        deleted = delete_duplicates(conn)
        missing = ensure_citation_unique_indexes(conn)
        if missing:
            raise RuntimeError(f"could not build {', '.join(missing)} after the delete")
        update_citation_counts(conn)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return deleted


def clear_datastories_cache():
    try:
        from datastories_cache import clear_all
        return clear_all()
    except Exception as exc:  # noqa: BLE001 — report, don't undo the fix
        print(f"WARNING: could not clear the Datastories cache: {exc}")
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Delete duplicate citation rows, add uniqueness guards, recount.")
    parser.add_argument("--db", default=default_db_path(),
                        help="database (default: DB_PATH, else ./articles.db)")
    parser.add_argument("--yes", action="store_true",
                        help="change the database (without it: dry run)")
    parser.add_argument("--backup-dir",
                        help="where the pre-change backup goes")
    parser.add_argument("--no-backup", action="store_true",
                        help="skip the backup (only with --yes)")
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(errors="replace")
    except AttributeError:
        pass

    db_path = os.path.abspath(args.db)
    if not os.path.isfile(db_path):
        print(f"ERROR: no database at {db_path}", file=sys.stderr)
        return 1
    print(f"Database: {db_path}")
    print()

    ro = open_read_only(db_path)
    try:
        if not ro.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                          "AND name = 'citations'").fetchone():
            print(f"ERROR: {db_path} has no citations table", file=sys.stderr)
            return 1
        before = measure(ro)
        by_journal = duplicates_by_journal(ro)
        projected = projected_counts(ro)
    finally:
        ro.close()

    print_state("Before", before)
    print()
    if by_journal:
        print("Duplicate rows to delete, by citing journal:")
        for journal, n in by_journal[:15]:
            print(f"  {n:>8,}  {_short(journal, 70)}")
        print()
    if projected:
        print("Cited-by counts a recount would change (id, now -> distinct citers, title):")
        for aid, now, citers, title in projected:
            print(f"  {aid:>6}  {now or 0:>5} -> {citers:<5} {_short(title, 60)}")
        print()

    nothing_to_do = (before["duplicate_rows"] == 0 and all(before["guards"].values())
                     and not projected)
    if not args.yes:
        if nothing_to_do:
            print("Nothing to do: no duplicates, guards present, counts current.")
        else:
            print("Dry run: nothing was changed. Re-run with --yes to apply "
                  "(a backup is taken first).")
        return 0
    if nothing_to_do:
        print("Nothing to do: no duplicates, guards present, counts current.")
        return 0

    if args.no_backup:
        print("Skipping the backup (--no-backup).")
    else:
        backup(db_path, args.backup_dir or default_backup_dir(db_path))
    print()

    conn = sqlite3.connect(db_path, timeout=60, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout = 60000")
        t0 = time.time()
        deleted = apply_fix(conn)
        print(f"Deleted {deleted:,} duplicate rows, built the guards and recounted "
              f"in {time.time() - t0:.1f}s.")
        after = measure(conn)
    finally:
        conn.close()
    print()
    print_state("After", after)
    print()
    print_top(before["top_cited"], after["top_cited"])
    print()

    n = clear_datastories_cache()
    if n is not None:
        print(f"Cleared {n} Datastories cache files.")
    print("Next: run the Prewarm admin job so the heavy analyses are rebuilt, and")
    print("clear the IIS/ARR cache (or wait an hour) so pages stop serving old figures.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
