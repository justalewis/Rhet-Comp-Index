"""datacite_fetcher.py — harvest DataCite records relevant to Pinakes.

Phase 1 of the DataCite integration (see the planning doc). Runs the two
highest-precision scoping methods and writes raw records plus the reason each
was found to data/datacite_dump.jsonl; ingest_datacite.py builds the tables.

  Method 1  cited_by_corpus     DataCite DOIs that articles in the index cite
                                (citations.target_ra = 'datacite'; run
                                classify_citation_ra.py first).
  Method 2  relation_to_corpus  DataCite records whose relatedIdentifiers name
                                an article or book DOI already in the index.
                                DOIs are queried in OR-batches; DataCite matches
                                them case-insensitively.

Each dump line: {"record": <DataCite data object>, "evidence": [{method,
matched_on, relation}, ...]}. A record found by both methods carries both.

This runs offline / weekly, never inside the web worker.

Usage:
    python datacite_fetcher.py                 # methods 1 + 2
    python datacite_fetcher.py --limit 500     # only first 500 corpus DOIs (smoke test)
    python datacite_fetcher.py --out path.jsonl
"""

import argparse
import json
import logging
import os
import time

import requests

from db import get_conn, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("datacite_fetcher")

API = "https://api.datacite.org/dois"
DUMP_DEFAULT = "data/datacite_dump.jsonl"
REL_BATCH = 75        # corpus DOIs per relatedIdentifier query
IDS_BATCH = 50        # DOIs per ?ids= lookup
PAGE_SIZE = 1000
REQUEST_DELAY = 0.3


def _headers():
    mailto = os.environ.get("CROSSREF_MAILTO", "")
    ua = "Pinakes/1.0 (+https://pinakes.xyz" + (f"; mailto:{mailto})" if mailto else ")")
    return {"User-Agent": ua, "Accept": "application/vnd.api+json"}


def _get(session, params, delay=REQUEST_DELAY):
    """GET /dois with retry on 429/5xx. Returns parsed JSON."""
    for attempt in range(5):
        resp = session.get(API, params=params, headers=_headers(), timeout=60)
        if resp.status_code in (429, 500, 502, 503, 504):
            wait = int(resp.headers.get("Retry-After", 0) or 0) or 2 ** (attempt + 1)
            log.warning("DataCite %s; retrying in %ss", resp.status_code, wait)
            time.sleep(min(wait, 60))
            continue
        resp.raise_for_status()
        time.sleep(delay)
        return resp.json()
    raise RuntimeError("DataCite kept failing: %r" % (params,))


def query_related(session, dois, delay=REQUEST_DELAY):
    """All DataCite records whose relatedIdentifiers include any of `dois`.
    Follows cursor pagination."""
    q = " OR ".join(f'relatedIdentifiers.relatedIdentifier:"{d}"' for d in dois)
    params = {"query": q, "page[size]": PAGE_SIZE, "page[cursor]": 1}
    out = []
    body = _get(session, params, delay)
    while True:
        out.extend(body.get("data", []))
        nxt = (body.get("links") or {}).get("next")
        if not nxt or not body.get("data"):
            return out
        # `next` is a full URL carrying the cursor; follow it verbatim.
        resp = session.get(nxt, headers=_headers(), timeout=60)
        resp.raise_for_status()
        time.sleep(delay)
        body = resp.json()


def lookup_ids(session, dois, delay=REQUEST_DELAY):
    """Fetch specific DOIs from DataCite (silently omits ones it doesn't hold)."""
    body = _get(session, {"ids": ",".join(dois), "page[size]": PAGE_SIZE}, delay)
    return body.get("data", [])


def _add(found, record, method, matched_on, relation=None):
    doi = record["id"].lower()
    entry = found.setdefault(doi, {"record": record, "evidence": []})
    ev = {"method": method, "matched_on": matched_on}
    if relation:
        ev["relation"] = relation
    if ev not in entry["evidence"]:
        entry["evidence"].append(ev)


def corpus_dois(conn):
    rows = conn.execute(
        "SELECT LOWER(doi) d FROM articles WHERE doi IS NOT NULL AND doi != '' "
        "UNION SELECT LOWER(doi) FROM books WHERE doi IS NOT NULL AND doi != ''"
    ).fetchall()
    return sorted({r["d"] for r in rows})


def cited_datacite_dois(conn):
    rows = conn.execute(
        "SELECT DISTINCT LOWER(target_doi) d FROM citations "
        "WHERE target_ra = 'datacite' AND target_doi IS NOT NULL"
    ).fetchall()
    return sorted(r["d"] for r in rows)


def harvest(conn, session=None, limit=None, delay=REQUEST_DELAY):
    """Run methods 1 and 2; return {doi: {"record":…, "evidence":[…]}}."""
    s = session or requests.Session()
    found = {}

    # Method 1 — what the corpus cites
    cited = cited_datacite_dois(conn)
    log.info("Method 1: %d cited DataCite DOIs", len(cited))
    for i in range(0, len(cited), IDS_BATCH):
        for rec in lookup_ids(s, cited[i:i + IDS_BATCH], delay):
            _add(found, rec, "cited_by_corpus", rec["id"].lower())

    # Method 2 — records that point at the corpus
    dois = corpus_dois(conn)
    if limit:
        dois = dois[:limit]
    log.info("Method 2: querying %d corpus DOIs in batches of %d", len(dois), REL_BATCH)
    for n, i in enumerate(range(0, len(dois), REL_BATCH), 1):
        batch = dois[i:i + REL_BATCH]
        batch_set = set(batch)
        for rec in query_related(s, batch, delay):
            for rel in rec["attributes"].get("relatedIdentifiers") or []:
                rid = (rel.get("relatedIdentifier") or "").lower()
                if rid in batch_set:
                    _add(found, rec, "relation_to_corpus", rid, rel.get("relationType"))
        if n % 20 == 0:
            log.info("  %d / %d DOIs queried, %d records so far",
                     min(i + REL_BATCH, len(dois)), len(dois), len(found))
    return found


def write_dump(found, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for doi in sorted(found):
            f.write(json.dumps(found[doi], ensure_ascii=False) + "\n")
    log.info("Wrote %d records to %s", len(found), path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=DUMP_DEFAULT)
    ap.add_argument("--limit", type=int, help="only query the first N corpus DOIs")
    args = ap.parse_args()
    init_db()
    with get_conn() as conn:
        found = harvest(conn, limit=args.limit)
    write_dump(found, args.out)


if __name__ == "__main__":
    main()
