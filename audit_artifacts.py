"""audit_artifacts.py — Find (and optionally fix) markup artifacts in the index.

`backfill_html_entities.py` decodes HTML *entities* (&amp; -> &). This is the
broader audit: it scans title / authors / abstract across the whole corpus for

  * HTML entities        — "&amp;", "&#8217;", double-encoded "&amp;amp;"
  * HTML / JATS tags     — "<i>", "<sub>", "<scp>", "<italic>" etc. that
                           CrossRef and OpenAlex titles carry through

and reports what it finds with samples. With --fix it cleans each affected
value: decode entities to convergence, then strip tags while keeping their
inner text, then collapse whitespace. Only rows that actually change are
written, so it's safe to re-run.

The fetchers already html.unescape() and strip tags on the way in; this fixes
rows ingested before that and anything a new source slips through.

Usage:
    python audit_artifacts.py                     # report only (all columns)
    python audit_artifacts.py --columns title     # just titles
    python audit_artifacts.py --samples 20        # show more examples
    python audit_artifacts.py --fix               # apply the cleanup
    python audit_artifacts.py --fix --columns title abstract
    python audit_artifacts.py --json report.json  # machine-readable report
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re

from db import get_conn, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ALL_COLUMNS = ("title", "authors", "abstract")

# An HTML/JATS tag: "<i>", "</sub>", "<sc>", "<italic>", "<xref ...>". Kept
# conservative — requires a letter or slash right after "<" so we don't flag a
# stray mathematical "a < b" in a title.
_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(\s[^>]*)?>")
_ENTITY_RE = re.compile(r"&[a-zA-Z]+;|&#\d+;|&#x[0-9a-fA-F]+;")
_WS_RE = re.compile(r"\s+")


def clean_value(value: str) -> str:
    """Decode entities to convergence, strip tags (keep inner text), tidy space."""
    after = value
    for _ in range(5):  # doubly/triply-encoded -> converge, bounded guard
        decoded = html.unescape(after)
        if decoded == after:
            break
        after = decoded
    after = _TAG_RE.sub("", after)
    after = _WS_RE.sub(" ", after).strip()
    return after


def audit_column(conn, column: str, fix: bool, samples: int) -> dict:
    """Scan one column. Returns a stats dict; applies fixes when fix=True."""
    rows = conn.execute(
        f"SELECT id, {column} FROM articles "
        f"WHERE {column} IS NOT NULL AND ({column} LIKE '%&%' OR {column} LIKE '%<%')"
    ).fetchall()

    n_entity = n_tag = 0
    updates: list[tuple[str, int]] = []
    examples: list[dict] = []

    for r in rows:
        before = r[column]
        has_entity = bool(_ENTITY_RE.search(before))
        has_tag = bool(_TAG_RE.search(before))
        if not (has_entity or has_tag):
            continue
        n_entity += has_entity
        n_tag += has_tag
        after = clean_value(before)
        if after != before:
            updates.append((after, r["id"]))
            if len(examples) < samples:
                examples.append({"id": r["id"], "before": before, "after": after})

    log.info("%-8s scanned %d candidate row(s): %d with entities, %d with tags, "
             "%d would change", column, len(rows), n_entity, n_tag, len(updates))
    for ex in examples[:min(samples, 5)]:
        log.info("   #%d  %r", ex["id"], ex["before"][:110])
        log.info("        -> %r", ex["after"][:110])

    if fix and updates:
        conn.executemany(
            f"UPDATE articles SET {column} = ? WHERE id = ?", updates)
        conn.commit()
        log.info("%-8s FIXED %d row(s)", column, len(updates))

    return {
        "column": column,
        "candidates_scanned": len(rows),
        "with_entities": n_entity,
        "with_tags": n_tag,
        "would_change": len(updates),
        "changed": len(updates) if fix else 0,
        "examples": examples,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true",
                    help="apply the cleanup (default: report only)")
    ap.add_argument("--columns", nargs="+", default=list(ALL_COLUMNS),
                    choices=ALL_COLUMNS, help="columns to audit")
    ap.add_argument("--samples", type=int, default=8,
                    help="how many before/after examples to collect per column")
    ap.add_argument("--json", type=str, default=None,
                    help="write a machine-readable report to this path")
    args = ap.parse_args()

    init_db()
    report = {"mode": "fix" if args.fix else "report", "columns": []}
    with get_conn() as conn:
        conn.execute("PRAGMA busy_timeout = 60000")
        for col in args.columns:
            report["columns"].append(audit_column(conn, col, args.fix, args.samples))

    total = sum(c["would_change"] for c in report["columns"])
    verb = "fixed" if args.fix else "would fix"
    log.info("Done — %s %d row(s) across %d column(s).",
             verb, total, len(args.columns))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        log.info("Wrote report to %s", args.json)


if __name__ == "__main__":
    main()
