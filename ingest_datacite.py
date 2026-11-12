"""ingest_datacite.py — rebuild the DataCite tables from the fetcher's dump.

Reads data/datacite_dump.jsonl (written by datacite_fetcher.py) and rebuilds
datacite_works / datacite_creators / datacite_relations /
datacite_scope_evidence. Idempotent full rebuild: the four tables are wiped and
repopulated, and are read-only to the web app (same pattern as ingest_wac.py).

Tiering: see datacite_scope.tier_for. Method 1/2 records are tier A; Method
3/4 records are B (topical support) or C; Method 5 alone is C. Curator
decisions in `datacite_curation` (which survives rebuilds) override: approved ->
"curated", rejected -> "rejected". Records that earn no tier are dropped.

Usage:
    python ingest_datacite.py             # data/datacite_dump.jsonl
    python ingest_datacite.py <path>
"""

import json
import logging
import re
import sys

from db import get_conn
from db.core import init_db
from redaction import apply_suppression, is_redaction_token
import datacite_scope as scope
from tagger import auto_tag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ingest_datacite")

DUMP_DEFAULT = "data/datacite_dump.jsonl"

_ORCID_RE = re.compile(r"\d{4}-\d{4}-\d{4}-\d{3}[\dXx]")


def _orcid(name_identifiers):
    for ni in name_identifiers or []:
        scheme = (ni.get("nameIdentifierScheme") or "").lower()
        val = ni.get("nameIdentifier") or ""
        if scheme == "orcid" or "orcid.org" in val.lower():
            m = _ORCID_RE.search(val)
            if m:
                return m.group(0).upper()
    return None


def _affiliation(person):
    """(name, ror) of the first affiliation. DataCite returns either plain
    strings or objects with affiliationIdentifier."""
    for aff in person.get("affiliation") or []:
        if isinstance(aff, str):
            return aff, None
        ror = None
        ident = aff.get("affiliationIdentifier") or ""
        if "ror.org" in ident.lower() or (aff.get("affiliationIdentifierScheme") or "").upper() == "ROR":
            ror = ident
        return aff.get("name"), ror
    return None, None


def _title(attrs):
    for t in attrs.get("titles") or []:
        if t.get("title"):
            return t["title"].strip()
    return None


def _description(attrs):
    descs = attrs.get("descriptions") or []
    for d in descs:
        if d.get("descriptionType") == "Abstract" and d.get("description"):
            return d["description"].strip()
    for d in descs:
        if d.get("description"):
            return d["description"].strip()
    return None


def _norm_doi(value):
    v = (value or "").strip().lower()
    for pre in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if v.startswith(pre):
            v = v[len(pre):]
    return v


def _redact(person, orcid, author_id, author_names, smap):
    """Redaction choke-point for a DataCite creator/contributor. Returns the
    token to show instead of the name, or None if the person is not redacted.
    Matches the ledger on both 'Family, Given' and 'Given Family', and also
    catches an ORCID that resolves to an already-redacted `authors` row."""
    if author_id and is_redaction_token(author_names.get(author_id)):
        return author_names[author_id]
    name = person.get("name") or ""
    forms = [name]
    given, family = person.get("givenName"), person.get("familyName")
    if given and family:
        forms += [f"{given} {family}", f"{family}, {given}"]
    for f in forms:
        out = apply_suppression(f, smap=smap)
        if out != f and is_redaction_token(out):
            return out
    return None


def build_rows(entry, doi_to_article, doi_to_book, orcid_to_author,
               author_names=None, smap=None, decisions=None):
    """Turn one dump entry into (work, creators, relations, evidence) rows."""
    author_names = author_names or {}
    smap = smap or {}
    decisions = decisions or {}
    rec = entry["record"]
    a = rec["attributes"]
    doi = rec["id"].lower()
    types = a.get("types") or {}
    title = scope.clean_text(_title(a))
    desc = scope.clean_text(_description(a))
    subjects = [
        {"subject": s.get("subject"), "scheme": s.get("subjectScheme")}
        for s in a.get("subjects") or [] if s.get("subject")
    ]
    subject_text = " ; ".join(s["subject"] for s in subjects)
    methods = scope.effective_methods(entry["evidence"])
    score, _tags = scope.score_attrs(a)
    tier = scope.tier_for(methods, score)
    decision = decisions.get(doi)
    if decision == "approved":
        tier = "curated"
    elif decision == "rejected":
        tier = "rejected"
    if tier is None:
        return None
    funders = sorted({f.get("funderName") for f in a.get("fundingReferences") or []
                      if f.get("funderName")})
    pub = a.get("publisher")
    if isinstance(pub, dict):
        pub = pub.get("name")
    work = (
        doi, types.get("resourceTypeGeneral"), types.get("resourceType"), title, desc,
        pub, (rec.get("relationships", {}).get("client", {}).get("data") or {}).get("id"),
        a.get("publicationYear"), a.get("version"),
        "; ".join(r.get("rightsUri") or r.get("rights") or "" for r in a.get("rightsList") or []).strip("; ") or None,
        json.dumps(subjects, ensure_ascii=False) if subjects else None,
        auto_tag(title, (desc or "") + " " + subject_text),
        tier, a.get("viewCount"), a.get("downloadCount"), a.get("citationCount"), a.get("url"),
        score,
        json.dumps(funders, ensure_ascii=False) if funders else None,
    )

    creators = []
    people = [("creator", p) for p in a.get("creators") or []] + [
        ("contributor:" + (p.get("contributorType") or "Other"), p)
        for p in a.get("contributors") or []
    ]
    for seq, (role, p) in enumerate(people):
        orcid = _orcid(p.get("nameIdentifiers"))
        aff, ror = _affiliation(p)
        author_id = orcid_to_author.get(orcid) if orcid else None
        token = _redact(p, orcid, author_id, author_names, smap)
        if token:
            creators.append((doi, seq, token, None, role, None, None, None))
        elif p.get("name"):
            creators.append((doi, seq, p["name"], orcid, role, aff, ror, author_id))

    relations = []
    for rel in a.get("relatedIdentifiers") or []:
        rid = rel.get("relatedIdentifier")
        if not rid or not rel.get("relationType"):
            continue
        norm = _norm_doi(rid) if (rel.get("relatedIdentifierType") or "").upper() == "DOI" else None
        relations.append((doi, rel["relationType"], rid, rel.get("relatedIdentifierType"),
                          doi_to_article.get(norm) if norm else None,
                          doi_to_book.get(norm) if norm else None))

    evidence = [(doi, e["method"],
                 e["matched_on"] + (f" ({e['relation']})" if e.get("relation") else ""),
                 e.get("score"))
                for e in entry["evidence"]]
    return work, creators, relations, evidence


def ingest(path=DUMP_DEFAULT, conn=None):
    own = conn is None
    if own:
        init_db()
        conn = get_conn()
    try:
        doi_to_article = {r["d"]: r["id"] for r in conn.execute(
            "SELECT LOWER(doi) d, id FROM articles WHERE doi IS NOT NULL AND doi != ''")}
        doi_to_book = {r["d"]: r["id"] for r in conn.execute(
            "SELECT LOWER(doi) d, id FROM books WHERE doi IS NOT NULL AND doi != ''")}
        from redaction import _suppression_map
        smap = _suppression_map(conn)
        author_names = {r["id"]: r["name"] for r in conn.execute(
            "SELECT id, name FROM authors WHERE orcid IS NOT NULL AND orcid != ''")}
        orcid_to_author = {}
        for r in conn.execute("SELECT id, orcid FROM authors WHERE orcid IS NOT NULL AND orcid != ''"):
            m = _ORCID_RE.search(r["orcid"])
            if m:
                orcid_to_author.setdefault(m.group(0).upper(), r["id"])

        decisions = {r["doi"]: r["decision"] for r in conn.execute(
            "SELECT doi, decision FROM datacite_curation")}
        works, creators, relations, evidence = [], [], [], []
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                built = build_rows(json.loads(line), doi_to_article, doi_to_book, orcid_to_author,
                                   author_names, smap, decisions)
                if built is None:
                    continue
                w, c, r, e = built
                works.append(w); creators += c; relations += r; evidence += e

        for t in ("datacite_works", "datacite_creators", "datacite_relations", "datacite_scope_evidence"):
            conn.execute(f"DELETE FROM {t}")
        conn.executemany(
            "INSERT INTO datacite_works (doi, resource_type_gen, resource_type, title, description, "
            "publisher, client_id, year, version, rights, subjects, tags, tier, view_count, "
            "download_count, citation_count, url, topic_score, funders) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", works)
        conn.executemany(
            "INSERT INTO datacite_creators (work_doi, seq, name, orcid, role, affiliation, ror, author_id) "
            "VALUES (?,?,?,?,?,?,?,?)", creators)
        conn.executemany(
            "INSERT INTO datacite_relations (work_doi, relation_type, related_id, related_id_type, "
            "article_id, book_id) VALUES (?,?,?,?,?,?)", relations)
        conn.executemany(
            "INSERT INTO datacite_scope_evidence (work_doi, method, matched_on, score) VALUES (?,?,?,?)",
            evidence)
        conn.commit()
        log.info("Ingested %d works, %d creators, %d relations (%d resolved in-corpus), %d evidence rows",
                 len(works), len(creators), len(relations),
                 sum(1 for r in relations if r[4] or r[5]), len(evidence))
        return len(works)
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    ingest(sys.argv[1] if len(sys.argv) > 1 else DUMP_DEFAULT)
