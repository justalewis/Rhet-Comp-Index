"""ingest_wac.py — Build the WAC Clearinghouse publisher-dashboard tables.

Reads a CrossRef harvest of every work under DOI prefix 10.37514
(data/wac_crossref_dump.jsonl, produced by the offset-paginated dump) and
rebuilds the denormalized `wac_works` + `wac_authors` tables that power the
/wac dashboard.

Design notes:
  * Idempotent full rebuild: the two tables are wiped and repopulated each run.
    They are READ-ONLY to the web app — only this script writes them.
  * Institution data: CrossRef gives raw affiliation STRINGS (no ROR/country).
    For the ~1,100 WAC DOIs that also live in `articles` and have been OpenAlex-
    enriched, we prefer the normalized institution display_name + country from
    `article_author_institutions` → `institutions`. Otherwise we fall back to a
    light heuristic normalization of the raw string.
  * Chapter → parent-book linkage is derived from the chapter DOI suffix
    (…<book>.<sec>.<chap>) and verified against the set of book DOIs present.
  * Junk "authors" (e.g. "Web Conversation", "The Editors") are flagged
    is_person=0 so author-centric views can exclude them while keeping the
    catalog complete.
  * Journal-name variants are merged to a canonical label.

Usage:
    python ingest_wac.py            # rebuild from data/wac_crossref_dump.jsonl
    python ingest_wac.py <path>     # rebuild from a specific dump file
"""

from __future__ import annotations

import json
import re
import sys
import html
import logging
import collections

from db import get_conn
from db.core import init_db
from tagger import auto_tag

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("ingest_wac")

DUMP_DEFAULT = "data/wac_crossref_dump.jsonl"

# Journal-name variants → canonical label (the rest pass through unchanged).
JOURNAL_CANON = {
    "The Journal of Writing Analytics": "Journal of Writing Analytics",
}

# Non-person "authors" that appear in the WAC corpus (RhetNet dialogic pieces,
# editorial front-matter, etc.). Matched case-insensitively on the full name.
JUNK_AUTHORS = {
    "web conversation", "the editors", "editors", "editor", "the editor",
    "anonymous", "anon", "various", "various authors", "staff", "et al",
    "et al.", "n/a", "unknown", "the author", "authors", "contributors",
}

# Affiliation strings that are role words / noise, not institutions. After
# normalization, a result matching one of these is dropped to NULL.
JUNK_INSTITUTIONS = {
    "editor", "editors", "author", "authors", "co-editor", "guest editor",
    "reviewer", "contributor", "independent scholar", "independent",
    "n/a", "none", "retired", "emeritus", "phd", "professor",
}

# Keywords that mark the institution-bearing segment of a comma-split
# affiliation string ("Department of English, University of Maine").
_INST_KEYWORDS = re.compile(
    r"univers|college|institut|school|center|centre|academy|polytechn|"
    r"laborator|hospital|department|faculty|college|college",
    re.IGNORECASE,
)

_CHAPTER_PARENT_RE = re.compile(r"(10\.37514/[^.]+\.\d{4}\.\d+)\.\d+\.\d+$")


# ── helpers ──────────────────────────────────────────────────────────────────

def _clean(s):
    """Decode HTML/XML entities. CrossRef deposits XML char-refs (and some are
    double-encoded, e.g. '&amp;#x3a;' for ':'), so unescape until stable."""
    if not s:
        return s
    for _ in range(3):
        new = html.unescape(s)
        if new == s:
            break
        s = new
    return s.strip()


def _title(w):
    t = w.get("title") or []
    return _clean(t[0].strip() if t else "") or None


def _year(w):
    for k in ("published", "published-print", "published-online", "issued", "created"):
        dp = (w.get(k) or {}).get("date-parts") or [[]]
        if dp and dp[0] and dp[0][0]:
            return dp[0][0]
    return None


def _pub_date(w):
    for k in ("published", "published-print", "published-online", "issued"):
        dp = (w.get(k) or {}).get("date-parts") or [[]]
        if dp and dp[0] and dp[0][0]:
            parts = dp[0]
            y = parts[0]
            m = parts[1] if len(parts) > 1 else 1
            d = parts[2] if len(parts) > 2 else 1
            try:
                return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
            except (TypeError, ValueError):
                return f"{y}"
    return None


def _container(w):
    ct = w.get("container-title") or []
    return _clean(ct[0].strip()) if ct else None


def _isbn(w):
    isbns = w.get("ISBN") or []
    return isbns[0] if isbns else None


def _full_name(p):
    given = _clean((p.get("given") or "").strip())
    family = _clean((p.get("family") or "").strip())
    name = (f"{given} {family}").strip()
    if not name:
        name = _clean((p.get("name") or "").strip())
    return name or None, (family or None)


def _raw_affiliation(p):
    aff = p.get("affiliation") or []
    for a in aff:
        nm = (a.get("name") or "").strip()
        if nm:
            return nm
    return None


def _normalize_institution(raw):
    """Light heuristic: from a raw CrossRef affiliation string, pull out the
    institution-bearing segment and tidy it. Returns None for empty input."""
    if not raw:
        return None
    s = " ".join(raw.split())
    # Prefer the comma-segment that names an institution.
    segs = [seg.strip() for seg in s.split(",") if seg.strip()]
    chosen = None
    for seg in segs:
        if _INST_KEYWORDS.search(seg) and not re.match(
            r"(?i)^(department|dept|faculty|school|college of|centre for|center for|program)\b", seg
        ):
            chosen = seg
            break
    if chosen is None:
        # No clean institution segment — drop obvious sub-units, else take the
        # longest segment (usually the org), else the whole string.
        non_dept = [seg for seg in segs
                    if not re.match(r"(?i)^(department|dept|faculty|program|division)\b", seg)]
        pool = non_dept or segs or [s]
        chosen = max(pool, key=len)
    chosen = re.sub(r"\s+", " ", chosen).strip(" .,;")
    # Canonical-ish cleanups
    chosen = re.sub(r"(?i)^the\s+", "", chosen)
    chosen = re.sub(r"(?i)\bUniv\.?\b", "University", chosen)
    if not chosen or chosen.lower() in JUNK_INSTITUTIONS or len(chosen) < 3:
        return None
    return chosen


def _is_person(name):
    return 0 if (name or "").strip().lower() in JUNK_AUTHORS else 1


def _canon_journal(container):
    if not container:
        return None
    return JOURNAL_CANON.get(container, container)


# ── OpenAlex-institution enrichment map (from the articles side) ─────────────

def _load_openalex_institutions(conn):
    """Map (lower(doi), lower(author_name)) → (institution_display_name, country)
    for WAC DOIs already enriched on the articles side."""
    rows = conn.execute("""
        SELECT lower(a.doi)            AS doi,
               lower(aai.author_name)  AS author,
               i.display_name          AS inst,
               i.country_code          AS country
        FROM articles a
        JOIN article_author_institutions aai ON aai.article_id = a.id
        JOIN institutions i                  ON i.id = aai.institution_id
        WHERE a.doi LIKE '10.37514%'
    """).fetchall()
    m = {}
    for r in rows:
        if r["doi"] and r["author"]:
            m[(r["doi"], r["author"])] = (r["inst"], r["country"])
    return m


# ── editorial-office affiliation cleanup ─────────────────────────────────────

def _drop_editorial_stamps(conn, min_authors=25, dominance=0.35):
    """Null affiliation strings that are clearly a journal's editorial-office
    stamp rather than real authorship geography.

    Some WAC journals deposit the journal's HOME institution as the affiliation
    on every article (e.g. The WAC Journal → Plymouth State University). Two
    tells, either of which flags a (journal, institution) pair:

      A. Dominance — one institution is attached to >= `dominance` of a single
         journal's distinct authors (and the journal has >= `min_authors`).
      B. Single-venue — an institution appears in EXACTLY ONE journal and in no
         books, yet carries >= `min_authors` distinct authors. A real prolific
         institution spreads across venues; one stuck to a single journal is an
         editorial-office stamp.

    Flagged rows have their institution nulled. Authors genuinely at that
    institution still count via their work in OTHER venues.
    """
    jtot = {r["journal"]: r["n"] for r in conn.execute("""
        SELECT w.journal AS journal, COUNT(DISTINCT a.name) n
        FROM wac_authors a JOIN wac_works w ON w.doi=a.work_doi
        WHERE w.type='journal-article' AND a.institution IS NOT NULL AND a.is_person=1
        GROUP BY w.journal
    """).fetchall()}

    to_null = {}  # (journal, inst_lower) -> reason
    # Rule A: per-journal dominance
    for r in conn.execute("""
        SELECT w.journal AS journal, a.institution AS inst, COUNT(DISTINCT a.name) AS authors
        FROM wac_authors a JOIN wac_works w ON w.doi=a.work_doi
        WHERE w.type='journal-article' AND w.journal IS NOT NULL
              AND a.institution IS NOT NULL AND a.is_person=1
        GROUP BY w.journal, lower(a.institution)
    """).fetchall():
        tot = jtot.get(r["journal"], 0)
        if tot >= min_authors and r["authors"] >= dominance * tot:
            to_null[(r["journal"], r["inst"].lower())] = "dominance"

    # Rule B: single-venue institutions
    inst_journals = collections.defaultdict(set)
    inst_books = collections.Counter()
    inst_authors = collections.defaultdict(set)
    for r in conn.execute("""
        SELECT a.institution AS inst, w.type AS wtype, w.journal AS journal, a.name AS name
        FROM wac_authors a JOIN wac_works w ON w.doi=a.work_doi
        WHERE a.institution IS NOT NULL AND a.is_person=1
    """).fetchall():
        key = r["inst"].lower()
        inst_authors[key].add(r["name"])
        if r["wtype"] == "journal-article" and r["journal"]:
            inst_journals[key].add(r["journal"])
        elif r["wtype"] != "journal-article":
            inst_books[key] += 1
    for key, authors in inst_authors.items():
        if len(authors) >= min_authors and len(inst_journals[key]) == 1 and inst_books[key] == 0:
            to_null[(next(iter(inst_journals[key])), key)] = "single-venue"

    dropped = 0
    for (journal, inst_lower), reason in to_null.items():
        cur = conn.execute("""
            UPDATE wac_authors SET institution=NULL
            WHERE lower(institution)=?
              AND work_doi IN (SELECT doi FROM wac_works WHERE journal=?)
        """, (inst_lower, journal))
        dropped += cur.rowcount
        log.info("  editorial-stamp dropped (%s): %s on %s", reason, inst_lower, journal)
    return dropped


# ── main ─────────────────────────────────────────────────────────────────────

def ingest(dump_path=DUMP_DEFAULT):
    init_db()  # ensures wac_works / wac_authors exist (v13 migration)

    works = []
    with open(dump_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                works.append(json.loads(line))
    log.info("Loaded %d works from %s", len(works), dump_path)

    book_dois = {(w.get("DOI") or "").lower()
                 for w in works if w.get("type") in ("edited-book", "monograph", "book")}

    with get_conn() as conn:
        oa_map = _load_openalex_institutions(conn)
        log.info("OpenAlex institution hints: %d (author,doi) pairs", len(oa_map))

        conn.execute("DELETE FROM wac_authors")
        conn.execute("DELETE FROM wac_works")

        n_works = 0
        n_people = 0
        n_enriched = 0
        n_linked = 0

        for w in works:
            doi = w.get("DOI")
            title = _title(w)
            if not doi or not title:
                continue
            doi_l = doi.lower()
            wtype = w.get("type")
            authors = w.get("author") or []
            editors = w.get("editor") or []
            container = _container(w)

            parent_doi = None
            if wtype == "book-chapter":
                m = _CHAPTER_PARENT_RE.match(doi)
                if m and m.group(1).lower() in book_dois:
                    parent_doi = m.group(1)
                    n_linked += 1

            journal = _canon_journal(container) if wtype == "journal-article" else None

            try:
                tags = auto_tag(title, None)
            except Exception:
                tags = None

            conn.execute("""
                INSERT OR REPLACE INTO wac_works
                    (doi, type, title, year, pub_date, journal, container,
                     parent_doi, isbn, pages, cited_by, n_authors, n_editors, tags, url)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                doi, wtype, title, _year(w), _pub_date(w), journal, container,
                parent_doi, _isbn(w), w.get("page"),
                w.get("is-referenced-by-count") or 0,
                len(authors), len(editors), tags,
                w.get("URL") or f"https://doi.org/{doi}",
            ))
            n_works += 1

            def _add_person(p, seq, role):
                nonlocal n_people, n_enriched
                name, family = _full_name(p)
                if not name:
                    return
                raw = _raw_affiliation(p)
                inst, country = None, None
                hit = oa_map.get((doi_l, name.lower()))
                if hit and hit[0]:
                    inst, country = hit
                    n_enriched += 1
                else:
                    inst = _normalize_institution(raw)
                conn.execute("""
                    INSERT INTO wac_authors
                        (work_doi, seq, name, family, role, affiliation_raw,
                         institution, country, is_person)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (doi, seq, name, family, role, raw, inst, country, _is_person(name)))
                n_people += 1

            for i, p in enumerate(authors):
                _add_person(p, i, "author")
            for i, p in enumerate(editors):
                _add_person(p, i, "editor")

        n_stamped = _drop_editorial_stamps(conn)

        conn.commit()

    log.info("Done. wac_works=%d  wac_authors=%d  oa-enriched-people=%d  "
             "chapters-linked=%d  editorial-stamps-nulled=%d",
             n_works, n_people, n_enriched, n_linked, n_stamped)
    return n_works, n_people


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DUMP_DEFAULT
    ingest(path)
