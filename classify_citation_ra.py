"""
classify_citation_ra.py — DataCite integration, Phase 0.

Fill citations.target_ra with the DOI registration agency (crossref, datacite,
medra, ...) of every cited DOI, and report how much of what the corpus cites is
registered outside Crossref.

A DOI's registration agency is fixed by its prefix, so this resolves each
distinct *prefix* once (a few thousand lookups at most, batched) rather than
each DOI, using https://doi.org/doiRA/<prefix>,<prefix>,...

Idempotent: only rows with target_ra IS NULL are touched, so re-running after
cite_fetcher.py classifies just the new citations. Prefixes doi.org cannot
resolve are stored as "unknown" so they are not re-queried every run.

Usage:
    python classify_citation_ra.py            # classify + report
    python classify_citation_ra.py --report   # report only, no network
"""

import argparse
import logging
import time

import requests

from db import get_conn, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DOIRA_URL = "https://doi.org/doiRA/"
BATCH_SIZE = 50
REQUEST_DELAY = 0.5
HEADERS = {"User-Agent": "Pinakes/1.0 (+https://pinakes.xyz)"}


def doi_prefix(doi):
    """'10.5281/zenodo.123' -> '10.5281'; None if it doesn't look like a DOI."""
    if not doi:
        return None
    head = doi.strip().lower().split("/", 1)[0]
    return head if head.startswith("10.") and len(head) > 3 else None


def lookup_prefixes(prefixes, session=None, delay=REQUEST_DELAY):
    """Return {prefix: agency} for `prefixes`. Agency is lower-cased
    ('crossref', 'datacite', ...) or 'unknown' when doi.org has no record."""
    sess = session or requests.Session()
    prefixes = sorted(set(prefixes))
    out = {}
    for i in range(0, len(prefixes), BATCH_SIZE):
        chunk = prefixes[i:i + BATCH_SIZE]
        for attempt in range(4):
            resp = sess.get(DOIRA_URL + ",".join(chunk), headers=HEADERS, timeout=30)
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** (attempt + 1))
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError(f"doi.org/doiRA kept failing for batch starting {chunk[0]}")
        for item in resp.json():
            ra = item.get("RA")
            out[str(item.get("DOI", "")).lower()] = ra.strip().lower() if ra else "unknown"
        for p in chunk:  # anything the response omitted
            out.setdefault(p, "unknown")
        log.info("Resolved %d / %d prefixes", min(i + BATCH_SIZE, len(prefixes)), len(prefixes))
        time.sleep(delay)
    return out


def classify(conn, session=None):
    """Set target_ra on all unclassified citations. Returns rows updated."""
    rows = conn.execute(
        "SELECT id, target_doi FROM citations "
        "WHERE target_ra IS NULL AND target_doi IS NOT NULL"
    ).fetchall()
    by_prefix = {}
    for r in rows:
        p = doi_prefix(r["target_doi"])
        if p:
            by_prefix.setdefault(p, []).append(r["id"])
    if not by_prefix:
        return 0
    log.info("%d unclassified citations across %d distinct prefixes", len(rows), len(by_prefix))
    ra_map = lookup_prefixes(by_prefix, session=session)
    updates = [(ra_map.get(p, "unknown"), cid) for p, ids in by_prefix.items() for cid in ids]
    conn.executemany("UPDATE citations SET target_ra = ? WHERE id = ?", updates)
    conn.commit()
    return len(updates)


def report(conn):
    total = conn.execute(
        "SELECT COUNT(*) FROM citations WHERE target_doi IS NOT NULL"
    ).fetchone()[0]
    print(f"\nCitations with a DOI: {total:,}")
    print(f"{'agency':<14}{'citations':>10}{'distinct DOIs':>15}")
    for r in conn.execute(
        "SELECT COALESCE(target_ra,'(unclassified)') ra, COUNT(*) n, COUNT(DISTINCT target_doi) d "
        "FROM citations WHERE target_doi IS NOT NULL GROUP BY ra ORDER BY n DESC"
    ):
        print(f"{r['ra']:<14}{r['n']:>10,}{r['d']:>15,}")
    print("\nTop DataCite repositories by DOI prefix (cited):")
    prefixes = {}
    for r in conn.execute(
        "SELECT target_doi FROM citations WHERE target_ra = 'datacite'"
    ):
        p = doi_prefix(r["target_doi"])
        prefixes[p] = prefixes.get(p, 0) + 1
    for p, n in sorted(prefixes.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {p:<12}{n:>8,}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--report", action="store_true", help="report only; no network")
    args = ap.parse_args()
    init_db()
    with get_conn() as conn:
        if not args.report:
            n = classify(conn)
            log.info("Classified %d citations", n)
        report(conn)


if __name__ == "__main__":
    main()
