"""datacite_fetcher.py — harvest DataCite records relevant to Pinakes.

Runs the five scoping methods from the DataCite planning document and writes
raw records plus the reason each was found to data/datacite_dump.jsonl;
ingest_datacite.py builds the tables and assigns tiers.

  1  cited_by_corpus     DataCite DOIs that articles in the index cite
                         (citations.target_ra = 'datacite'; run
                         classify_citation_ra.py first).
  2  relation_to_corpus  records whose relatedIdentifiers name an article or
                         book DOI already in the index (OR-batched exact DOIs;
                         DataCite matches case-insensitively).
  3  author_in_corpus    deposits by ORCIDs in the `authors` table. DataCite
                         stores ORCIDs both bare and as https://orcid.org/...
                         URLs, so both forms are queried.
  4  seed                data/datacite_seeds.tsv: extra ORCIDs, repositories
                         (narrowed by query), single DOIs.
  5  topical             discovery queries drawn from multi-word tagger
                         triggers; candidates are kept only above the topical
                         score threshold (datacite_scope.DISCOVERY_MIN).

Each dump line: {"record": <DataCite data object>, "evidence": [{method,
matched_on, relation?, score?}, ...]}. A record found by several methods carries
all of them.

Runs offline / weekly, never inside the web worker.

Usage:
    python datacite_fetcher.py                  # all methods
    python datacite_fetcher.py --methods 1,2    # a subset
    python datacite_fetcher.py --limit 500      # first 500 corpus/ORCID/term items (smoke test)
"""

import argparse
import json
import logging
import os
import re
import time

import requests

import datacite_scope as scope
from db import get_conn, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("datacite_fetcher")

API = "https://api.datacite.org/dois"
DUMP_DEFAULT = "data/datacite_dump.jsonl"
REL_BATCH = 75          # corpus DOIs per relatedIdentifier query
IDS_BATCH = 50          # DOIs per ?ids= lookup
ORCID_BATCH = 8         # ORCIDs per query. Each expands to 4 terms (2 forms x creators/
                        # contributors); 25 per query overflowed the URL limit (HTTP 414).
MAX_QUERY_URL = 7000    # sanity ceiling on an encoded query string
PAGE_SIZE = 1000
MAX_PER_QUERY = 500     # cap on records pulled by one discovery/seed query
REQUEST_DELAY = 0.3

_ORCID_RE = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dXx]")


def _headers():
    mailto = os.environ.get("CROSSREF_MAILTO", "")
    ua = "Pinakes/1.0 (+https://pinakes.xyz" + (f"; mailto:{mailto})" if mailto else ")")
    return {"User-Agent": ua, "Accept": "application/vnd.api+json"}


def _fetch(session, url, params, delay):
    """GET with retry on 429/5xx. Returns parsed JSON."""
    for attempt in range(5):
        resp = session.get(url, params=params, headers=_headers(), timeout=60)
        if resp.status_code in (429, 500, 502, 503, 504):
            wait = int(resp.headers.get("Retry-After", 0) or 0) or 2 ** (attempt + 1)
            log.warning("DataCite %s; retrying in %ss", resp.status_code, wait)
            time.sleep(min(wait, 60))
            continue
        resp.raise_for_status()
        time.sleep(delay)
        return resp.json()
    raise RuntimeError("DataCite kept failing: %r" % (params,))


def query_paged(session, params, delay=REQUEST_DELAY, max_records=None):
    """All records for a /dois query, following links.next (a full URL that
    carries the cursor). Stops at `max_records` if given."""
    p = {"page[size]": PAGE_SIZE, "page[cursor]": 1, **params}
    body = _fetch(session, API, p, delay)
    out = []
    while True:
        out.extend(body.get("data", []))
        if max_records and len(out) >= max_records:
            return out[:max_records]
        nxt = (body.get("links") or {}).get("next")
        if not nxt or not body.get("data"):
            return out
        body = _fetch(session, nxt, None, delay)


def query_related(session, dois, delay=REQUEST_DELAY):
    """Records whose relatedIdentifiers include any of `dois`."""
    q = " OR ".join(f'relatedIdentifiers.relatedIdentifier:"{d}"' for d in dois)
    return query_paged(session, {"query": q}, delay)


def lookup_ids(session, dois, delay=REQUEST_DELAY):
    """Specific DOIs (silently omits ones DataCite doesn't hold)."""
    return _fetch(session, API, {"ids": ",".join(dois), "page[size]": PAGE_SIZE},
                  delay).get("data", [])


def orcid_query(orcids):
    """OR-query over both stored forms of each ORCID, creators and contributors."""
    terms = []
    for o in orcids:
        for field in ("creators", "contributors"):
            terms.append(f'{field}.nameIdentifiers.nameIdentifier:"{o}"')
            terms.append(f'{field}.nameIdentifiers.nameIdentifier:"https://orcid.org/{o}"')
    return " OR ".join(terms)


def record_orcids(rec):
    """Set of bare ORCIDs on a record's creators and contributors."""
    found = set()
    a = rec["attributes"]
    for p in (a.get("creators") or []) + (a.get("contributors") or []):
        for ni in p.get("nameIdentifiers") or []:
            m = _ORCID_RE.search(ni.get("nameIdentifier") or "")
            if m:
                found.add(m.group(0).upper())
    return found


def _add(found, record, method, matched_on, relation=None, score=None):
    doi = record["id"].lower()
    entry = found.setdefault(doi, {"record": record, "evidence": []})
    ev = {"method": method, "matched_on": matched_on}
    if relation:
        ev["relation"] = relation
    if score is not None:
        ev["score"] = score
    if ev not in entry["evidence"]:
        entry["evidence"].append(ev)


# ── corpus lookups ───────────────────────────────────────────────────────────

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


def author_orcids(conn):
    out = set()
    for r in conn.execute("SELECT orcid FROM authors WHERE orcid IS NOT NULL AND orcid != ''"):
        m = _ORCID_RE.search(r["orcid"])
        if m:
            out.add(m.group(0).upper())
    return sorted(out)


# ── the five methods ─────────────────────────────────────────────────────────

def method1(conn, s, found, delay):
    cited = cited_datacite_dois(conn)
    log.info("Method 1: %d cited DataCite DOIs", len(cited))
    for i in range(0, len(cited), IDS_BATCH):
        for rec in lookup_ids(s, cited[i:i + IDS_BATCH], delay):
            _add(found, rec, "cited_by_corpus", rec["id"].lower())


def method2(conn, s, found, delay, limit=None):
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


def _by_orcid(orcids, method, s, found, delay):
    orcids = sorted(set(orcids))
    wanted = set(orcids)
    for i in range(0, len(orcids), ORCID_BATCH):
        batch = orcids[i:i + ORCID_BATCH]
        for rec in query_paged(s, {"query": orcid_query(batch)}, delay):
            for o in record_orcids(rec) & wanted:
                _add(found, rec, method, o)
        if (i // ORCID_BATCH) % 20 == 19:
            log.info("  %d / %d ORCIDs queried, %d records so far",
                     min(i + ORCID_BATCH, len(orcids)), len(orcids), len(found))


def method3(conn, s, found, delay, limit=None):
    orcids = author_orcids(conn)
    if limit:
        orcids = orcids[:limit]
    log.info("Method 3: %d author ORCIDs", len(orcids))
    _by_orcid(orcids, "author_in_corpus", s, found, delay)


def method4(seeds, s, found, delay):
    orcids = [v for v, _, _ in seeds.get("orcid", []) if _ORCID_RE.search(v)]
    if orcids:
        log.info("Method 4: %d seeded ORCIDs", len(orcids))
        _by_orcid([_ORCID_RE.search(o).group(0).upper() for o in orcids], "seed", s, found, delay)
    dois = [v.lower() for v, _, _ in seeds.get("doi", [])]
    for i in range(0, len(dois), IDS_BATCH):
        for rec in lookup_ids(s, dois[i:i + IDS_BATCH], delay):
            _add(found, rec, "seed", "doi:" + rec["id"].lower())
    for client, query, _note in seeds.get("client", []):
        log.info("Method 4: repository %s (%s)", client, query[:60] or "no narrowing query")
        params = {"client-id": client}
        if query:
            params["query"] = query
        for rec in query_paged(s, params, delay, max_records=MAX_PER_QUERY):
            _add(found, rec, "seed", "client:" + client)


def method5(seeds, s, found, delay, limit=None):
    terms = sorted(set(scope.discovery_terms()) | {v.lower() for v, _, _ in seeds.get("term", [])})
    if limit:
        terms = terms[:limit]
    log.info("Method 5: %d discovery phrases", len(terms))
    kept = 0
    for n, t in enumerate(terms, 1):
        q = (f'titles.title:"{t}" OR descriptions.description:"{t}" OR subjects.subject:"{t}"')
        for rec in query_paged(s, {"query": q}, delay, max_records=MAX_PER_QUERY):
            score, tags = scope.score_attrs(rec["attributes"])
            if score >= scope.DISCOVERY_MIN:
                _add(found, rec, "topical", t, score=score)
                kept += 1
        if n % 50 == 0:
            log.info("  %d / %d phrases, %d records so far", n, len(terms), len(found))


def harvest(conn, session=None, methods=(1, 2, 3, 4, 5), limit=None, delay=REQUEST_DELAY,
            seeds=None, errors=None):
    """Run the chosen methods; return {doi: {"record":…, "evidence":[…]}}.

    A method that raises does not discard the others' results: the failure is
    logged and appended to `errors` (a list the caller can pass in), and the
    harvest carries on. Callers must treat a non-empty `errors` as an
    INCOMPLETE harvest and not rebuild the tables from it."""
    s = session or requests.Session()
    seeds = scope.load_seeds() if seeds is None else seeds
    errors = errors if errors is not None else []
    found = {}
    plan = [
        (1, lambda: method1(conn, s, found, delay)),
        (2, lambda: method2(conn, s, found, delay, limit)),
        (3, lambda: method3(conn, s, found, delay, limit)),
        (4, lambda: method4(seeds, s, found, delay)),
        (5, lambda: method5(seeds, s, found, delay, limit)),
    ]
    for n, run in plan:
        if n not in methods:
            continue
        try:
            run()
        except Exception as exc:  # noqa: BLE001 — keep the other methods' results
            log.exception("Method %d failed", n)
            errors.append(f"method {n}: {exc.__class__.__name__}: {str(exc)[:200]}")
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
    ap.add_argument("--methods", default="1,2,3,4,5")
    ap.add_argument("--limit", type=int, help="only the first N corpus DOIs / ORCIDs / terms")
    args = ap.parse_args()
    methods = tuple(int(m) for m in args.methods.split(",") if m.strip())
    init_db()
    errors = []
    with get_conn() as conn:
        found = harvest(conn, methods=methods, limit=args.limit, errors=errors)
    write_dump(found, args.out)
    if errors:
        log.error("INCOMPLETE harvest, do not ingest: %s", "; ".join(errors))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
