"""
fetch_routledge.py — Ingest four Routledge rhet/comp book series into Pinakes.

Series targeted:
  1. Routledge Studies in Rhetoric and Communication (RSRC) — ~42 titles
  2. Routledge Studies in Technical Communication, Rhetoric, and Culture (ASHSER2226) — ~11 titles
  3. Routledge Research in Writing Studies (RRWS) — ~20 titles
  4. ATTW Series in Technical and Professional Communication (ATTW) — ~23 titles

Data sources:
  - CrossRef (primary): Taylor & Francis member ID 301
  - OpenAlex (supplemental): abstracts, topics

Usage:
    python fetch_routledge.py          # full ingest
    python fetch_routledge.py --dry    # print results, no DB writes
"""

import io
import sys
import time
import re

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from db import init_db, get_conn

# ── Constants ─────────────────────────────────────────────────────────────────

PUBLISHER  = "Routledge"
SOURCE     = "crossref+openalex"
DELAY      = 0.12   # seconds between requests (100 ms + buffer)
MAILTO     = "rhetcompindex@gmail.com"

CROSSREF   = "https://api.crossref.org/works"
OPENALEX   = "https://api.openalex.org/works"

HDRS = {"User-Agent": f"Pinakes/1.0 (mailto:{MAILTO})"}

# Whole-book types to keep; discard chapters, sections, parts
BOOK_TYPES = {"book", "edited-book", "monograph", "reference-book"}

# ── Series definitions ────────────────────────────────────────────────────────

SERIES = [
    {
        "key":      "RSRC",
        "name":     "Routledge Studies in Rhetoric and Communication",
        "expected": 42,
        "rows":     100,
        # Keyword sweeps for prefix:10.4324 discovery (cast wide, filter by container-title)
        "sweep_queries": [
            "rhetoric communication",
            "rhetoric persuasion discourse",
            "rhetorical theory history",
            "political rhetoric argument",
            "feminist rhetoric gender",
        ],
    },
    {
        "key":      "ASHSER2226",
        "name":     "Routledge Studies in Technical Communication, Rhetoric, and Culture",
        "expected": 11,
        "rows":     50,
        "sweep_queries": [
            "technical communication rhetoric culture",
            "technical writing professional communication",
            "technology communication design",
        ],
    },
    {
        "key":      "RRWS",
        "name":     "Routledge Research in Writing Studies",
        "expected": 20,
        "rows":     50,
        "sweep_queries": [
            "writing studies composition pedagogy",
            "writing center literacy",
            "writing research higher education",
            "composition rhetoric writing instruction",
        ],
    },
    {
        "key":      "ATTW",
        "name":     "ATTW Series in Technical and Professional Communication",
        "expected": 23,
        "rows":     50,
        "sweep_queries": [
            "technical professional communication ATTW",
            "technical communication pedagogy",
            "professional writing technical communication",
            "ATTW technical professional",
        ],
    },
]

# Spot-check titles used in Step 3 / Step 5 verification
SPOT_CHECKS = {
    "RSRC": [
        ("Queer Temporalities in Gay Male Representation",          "Goltz"),
        ("The Rhetoric of Intellectual Property",                   "Reyman"),
        ("Rhetorics of Names and Naming",                           "Vanguri"),
        ("Mapping Christian Rhetorics",                             "DePalma"),
        ("Difficult Empathy",                                       "Lynch"),
        ("Shelter Rhetorics",                                       ""),
        ("Rhetorical Children",                                     ""),
        ("Classical Rhetorical Argumentation",                      ""),
        ("Evangelical Writing in a Secular Imaginary",              ""),
    ],
    "ASHSER2226": [
        ("Communicating Mobility and Technology",                   "Pflugfelder"),
        ("Posthuman Praxis in Technical Communication",             "Moore"),
        ("Computer Games and Technical Communication",              "deWinter"),
        ("Visible Numbers",                                         "Kostelnick"),
        ("Writing Postindustrial Places",                           "Salvo"),
        ("Humanizing Visual Design",                                "Kostelnick"),
    ],
    "RRWS": [
        ("Writing Center Talk over Time",                           "Mackiewicz"),
        ("Writing Support for International Graduate Students",     "Sharma"),
        ("Teaching Writing, Rhetoric, and Reason",                  "Samuels"),
        ("Emotional Value in the Composition Classroom",            "Crawford"),
        ("Generative AI in Writing Education",                      "Medina"),
        ("Generative AI in the English Composition Classroom",      "Plate"),
        ("Writing Across Professions",                              "Taczak"),
        ("Reimagining Graduate Education in Writing Studies",       ""),
    ],
    "ATTW": [
        ("Social Networking Technologies",                          "Potts"),
        ("Rhetoric of Healthcare",                                  "Fountain"),
        ("Plain Language and Ethical Action",                       "Willerton"),
        ("Citizenship and Advocacy in Technical Communication",     "Matveeva"),
        ("Translation and Localization",                            "Maylath"),
        ("Content Strategy in Technical Communication",             "Getto"),
        ("Teaching Content Management",                             "Bridgeford"),
        ("Design Thinking in Technical Communication",              "Tham"),
        ("The Profession and Practice of Technical Communication",  "Cleary"),
        ("Technical Communication After the Social Justice Turn",   ""),
        ("Augmentation Technologies and AI in Technical Communication", "Duin"),
        ("Teaching User Experience",                                "Turner"),
        ("Designing for Social Justice",                            "Jiang"),
        ("Lean Technical Communication",                            "Johnson"),
    ],
}


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(url, params=None, retries=3):
    """GET with retry on 429/5xx. Returns response JSON or None."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=HDRS, timeout=20)
            if resp.status_code == 429:
                wait = min(int(resp.headers.get("Retry-After", "30")), 120)
                print(f"  [429] rate-limited — sleeping {wait}s …")
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            if attempt == retries - 1:
                print(f"  [ERR] {url}: {exc}")
            time.sleep(2)
    return None


# ── CrossRef helpers ──────────────────────────────────────────────────────────

def _isbn13(isbn_list):
    """Return the first ISBN-13 from a CrossRef isbn-type list, or any ISBN."""
    if not isbn_list:
        return None
    # isbn_list may be list of strings OR list of dicts {type, value}
    if isinstance(isbn_list[0], dict):
        for entry in isbn_list:
            if entry.get("type") == "print" and len(entry.get("value", "")) == 13:
                return entry["value"]
        for entry in isbn_list:
            if len(entry.get("value", "")) == 13:
                return entry["value"]
        return isbn_list[0].get("value")
    else:
        for v in isbn_list:
            if len(str(v)) == 13:
                return str(v)
        return str(isbn_list[0]) if isbn_list else None


def _year(item):
    """Extract earliest publication year from a CrossRef work item."""
    for field in ("published-print", "published-online", "issued"):
        dp = item.get(field, {}).get("date-parts", [[]])
        if dp and dp[0]:
            return dp[0][0]
    return None


def _authors_str(item):
    """Return semicolon-separated author string from CrossRef work."""
    authors = item.get("author") or []
    parts = []
    for a in authors:
        name = " ".join(filter(None, [a.get("given", ""), a.get("family", "")])).strip()
        if name:
            parts.append(name)
    return "; ".join(parts) if parts else None


def _editors_str(item):
    """Return semicolon-separated editor string from CrossRef work."""
    editors = item.get("editor") or []
    parts = []
    for e in editors:
        name = " ".join(filter(None, [e.get("given", ""), e.get("family", "")])).strip()
        if name:
            parts.append(name)
    return "; ".join(parts) if parts else None


def _detect_book_type(item):
    """Return 'edited-collection' or 'monograph'."""
    cr_type = (item.get("type") or "").lower()
    if cr_type == "edited-book":
        return "edited-collection"
    if item.get("editor"):
        return "edited-collection"
    return "monograph"


def _crossref_abstract(item):
    """Extract abstract text from CrossRef (JATS-wrapped or plain)."""
    raw = item.get("abstract") or ""
    if not raw:
        return None
    # Strip JATS tags
    clean = re.sub(r"<[^>]+>", " ", raw)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:2000] if clean else None


def _container_titles(item):
    """Return all container-title values for a CrossRef work (lowercased)."""
    ct = item.get("container-title") or []
    return [t.lower() for t in ct]


def fetch_crossref_series(series_def):
    """
    Query CrossRef for a series using container-title.
    Tries primary query first; falls back to bibliographic + no-member-filter.
    Returns list of raw CrossRef work items (whole books only, deduped by DOI).
    """
    name      = series_def["name"]
    rows      = series_def["rows"]
    expected  = series_def["expected"]

    print(f'\n  Querying CrossRef: "{name}"')

    # Primary query: container-title + member:301
    primary = _get(CROSSREF, params={
        "query.container-title": name,
        "filter": "member:301",
        "rows":   rows,
        "mailto": MAILTO,
    })
    time.sleep(DELAY)

    items_primary = _extract_books(primary, name)
    print(f"    Primary query (member:301): {len(items_primary)} whole-book hits")

    # If we got fewer than ~50% of expected, try broader queries
    all_items = {i["DOI"].lower(): i for i in items_primary if i.get("DOI")}

    threshold_low = max(3, int(expected * 0.5))
    if len(all_items) < threshold_low:
        print(f"    Below threshold ({threshold_low}) — trying alternate queries …")

        # Alt A: bibliographic query + member:301
        alt_a = _get(CROSSREF, params={
            "query.bibliographic": name,
            "filter": "member:301",
            "rows":   rows,
            "mailto": MAILTO,
        })
        time.sleep(DELAY)
        for i in _extract_books(alt_a, name):
            doi = (i.get("DOI") or "").lower()
            if doi and doi not in all_items:
                all_items[doi] = i
        print(f"    After alt-A (bibliographic): {len(all_items)} unique whole-book hits")

        # Alt B: container-title, no member filter
        alt_b = _get(CROSSREF, params={
            "query.container-title": name,
            "rows":   rows,
            "mailto": MAILTO,
        })
        time.sleep(DELAY)
        for i in _extract_books(alt_b, name):
            doi = (i.get("DOI") or "").lower()
            if doi and doi not in all_items:
                all_items[doi] = i
        print(f"    After alt-B (no member filter): {len(all_items)} unique whole-book hits")

        # Alt C: keyword sweep via prefix:10.4324 — casts wide, filters by container-title
        sweep_queries = series_def.get("sweep_queries", [])
        if sweep_queries:
            sweep_items = fetch_prefix_sweep(name, sweep_queries)
            before = len(all_items)
            for i in sweep_items:
                doi = (i.get("DOI") or "").lower()
                if doi and doi not in all_items:
                    all_items[doi] = i
            print(f"    After alt-C (prefix sweep): {len(all_items)} unique whole-book hits "
                  f"(+{len(all_items)-before} new)")

    return list(all_items.values())


def _extract_books(data, series_name):
    """Filter CrossRef response to whole-book records whose container-title matches the series."""
    if not data:
        return []
    items = (data.get("message") or {}).get("items") or []
    out = []
    series_lower = series_name.lower()
    for item in items:
        cr_type = (item.get("type") or "").lower()
        if cr_type not in BOOK_TYPES:
            continue
        # Verify the container-title actually contains the series
        ct = _container_titles(item)
        # Accept if any container-title substring-matches the series (or vice-versa)
        matched = any(
            series_lower in c or c in series_lower
            for c in ct
        )
        if not matched and series_lower not in " ".join(ct):
            # Still accept if it came back from the query — CrossRef relevance ranked it
            # but only if it's a Taylor & Francis / Routledge book
            publisher = (item.get("publisher") or "").lower()
            if "routledge" not in publisher and "taylor" not in publisher and "informa" not in publisher:
                continue
        if not item.get("DOI"):
            continue
        out.append(item)
    return out


def fetch_individual(title, author_surname=""):
    """
    Look up a single title in CrossRef by bibliographic query.
    Tries with member:301 first, then without (older T&F imprints use
    different member IDs — Baywood, etc.).
    Returns a CrossRef work item or None.
    """
    title_lower = title.lower()

    def _try(params):
        data = _get(CROSSREF, params=params)
        time.sleep(DELAY)
        if not data:
            return None
        for item in (data.get("message") or {}).get("items") or []:
            if item.get("type", "").lower() not in BOOK_TYPES:
                continue
            item_title = " ".join(item.get("title") or []).lower()
            t_words = set(title_lower.split())
            i_words = set(item_title.split())
            overlap = len(t_words & i_words) / max(len(t_words), 1)
            if overlap >= 0.6:
                return item
        return None

    base_params = {"query.bibliographic": title, "rows": 5, "mailto": MAILTO}
    if author_surname:
        base_params["query.author"] = author_surname

    # First: T&F member:301
    result = _try({**base_params, "filter": "member:301"})
    if result:
        return result

    # Second: prefix:10.4324 (all Routledge/T&F DOIs)
    result = _try({**base_params, "filter": "prefix:10.4324"})
    if result:
        return result

    # Third: no publisher filter at all (catches Baywood and other acquired imprints)
    result = _try(base_params)
    return result


def fetch_prefix_sweep(series_name, queries, rows_each=100):
    """
    Cast a wider net: search CrossRef with DOI prefix 10.4324 (all Routledge)
    and topical query terms. Filter results for series container-title match
    OR accept all books and let the caller decide.

    Returns list of CrossRef book items (may include false positives).
    """
    found = {}
    for query in queries:
        data = _get(CROSSREF, params={
            "query":  query,
            "filter": "prefix:10.4324,type:book",
            "rows":   rows_each,
            "mailto": MAILTO,
        })
        time.sleep(DELAY)
        if not data:
            continue
        items = (data.get("message") or {}).get("items") or []
        for item in items:
            doi = (item.get("DOI") or "").lower()
            if not doi:
                continue
            cr_type = (item.get("type") or "").lower()
            if cr_type not in BOOK_TYPES:
                continue
            # Only keep if container-title includes the series name
            ct = _container_titles(item)
            series_lower = series_name.lower()
            matched = any(
                series_lower in c or c in series_lower
                for c in ct
            )
            if matched and doi not in found:
                found[doi] = item
    return list(found.values())


# ── OpenAlex helpers ──────────────────────────────────────────────────────────

def _invert_abstract(inv):
    """Convert OpenAlex inverted-index abstract to plain text."""
    if not inv:
        return None
    try:
        pos_word = {}
        for word, positions in inv.items():
            for pos in positions:
                pos_word[pos] = word
        text = " ".join(pos_word[p] for p in sorted(pos_word))
        return text[:2000] if text.strip() else None
    except Exception:
        return None


def enrich_openalex(doi):
    """
    Query OpenAlex for a DOI. Returns dict with abstract and topics, or {}.
    """
    if not doi:
        return {}
    url = f"{OPENALEX}/doi:{doi}"
    data = _get(url, params={"mailto": MAILTO})
    time.sleep(DELAY)
    if not data or data.get("error"):
        return {}

    abstract = _invert_abstract(data.get("abstract_inverted_index"))
    topics = []
    for t in (data.get("topics") or []):
        name = t.get("display_name")
        if name:
            topics.append(name)

    return {"abstract": abstract, "topics": topics}


# ── DB upsert ─────────────────────────────────────────────────────────────────

def upsert_routledge_book(conn, *, doi, isbn, title, book_type,
                          authors, editors, year, abstract,
                          series_name, topics) -> str:
    """
    Insert or update a Routledge book.
    Dedup: DOI > ISBN+publisher > title+publisher.
    Returns 'inserted', 'updated', or 'skipped'.
    """
    # Subjects: series name first, then OpenAlex topics
    subjects_parts = [series_name] + (topics or [])
    subjects = "; ".join(subjects_parts)

    existing_id = None

    # 1. DOI match (cross-publisher — catches duplicates from other sources)
    if doi:
        row = conn.execute("SELECT id FROM books WHERE doi = ?", (doi,)).fetchone()
        if row:
            existing_id = row["id"]

    # 2. ISBN + publisher match
    if existing_id is None and isbn:
        row = conn.execute(
            "SELECT id FROM books WHERE isbn = ? AND publisher = ?",
            (isbn, PUBLISHER)
        ).fetchone()
        if row:
            existing_id = row["id"]

    # 3. Title + publisher match
    if existing_id is None:
        row = conn.execute(
            "SELECT id FROM books WHERE title = ? AND publisher = ?",
            (title, PUBLISHER)
        ).fetchone()
        if row:
            existing_id = row["id"]

    if existing_id is not None:
        conn.execute("""
            UPDATE books
               SET doi      = COALESCE(doi, ?),
                   isbn     = COALESCE(isbn, ?),
                   authors  = COALESCE(?, authors),
                   editors  = COALESCE(?, editors),
                   year     = COALESCE(?, year),
                   book_type= COALESCE(?, book_type),
                   abstract = COALESCE(?, abstract),
                   subjects = ?,
                   source   = ?,
                   fetched_at = datetime('now')
             WHERE id = ?
        """, (doi, isbn, authors, editors, year, book_type,
              abstract, subjects, SOURCE, existing_id))
        return "updated"

    conn.execute("""
        INSERT INTO books
            (doi, isbn, title, record_type, book_type, parent_id,
             editors, authors, publisher, year, pages,
             abstract, subjects, cited_by, source)
        VALUES (?,?,?,?,?,NULL,?,?,?,?,NULL,?,?,0,?)
    """, (doi, isbn, title, "book", book_type,
          editors, authors, PUBLISHER, year,
          abstract, subjects, SOURCE))
    return "inserted"


# ── Step 3: fill gaps with individual lookups ─────────────────────────────────

def fill_gaps(found_dois, found_titles, series_key, series_name):
    """
    For each spot-check title not already found, attempt individual CrossRef lookup.
    Returns list of new CrossRef items found.
    """
    checks = SPOT_CHECKS.get(series_key, [])
    new_items = []
    found_titles_lower = {t.lower() for t in found_titles}

    for title, author in checks:
        title_lower = title.lower()
        # Check if any found title is a close match
        matched = any(
            _title_similarity(title_lower, ft) >= 0.6
            for ft in found_titles_lower
        )
        if matched:
            continue

        print(f'    [gap] Searching individually: "{title}" ...')
        item = fetch_individual(title, author)
        if item:
            doi = (item.get("DOI") or "").lower()
            item_title = " ".join(item.get("title") or [])
            print(f'      Found: "{item_title}" [{doi}]')
            # Tag with the series container-title so downstream code knows the series
            if not item.get("container-title"):
                item["container-title"] = [series_name]
            new_items.append(item)
        else:
            print(f"      Not found in CrossRef.")

    return new_items


def _title_similarity(a, b):
    """Simple word-overlap similarity."""
    wa = set(re.sub(r"[^a-z0-9 ]", "", a).split())
    wb = set(re.sub(r"[^a-z0-9 ]", "", b).split())
    return len(wa & wb) / max(len(wa), 1)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(dry_run=False):
    init_db()

    print("\n" + "=" * 62)
    print("  Routledge Rhet/Comp Book Series Ingestion")
    print("=" * 62)

    all_results  = {}   # series_key -> list of processed book dicts
    step1_counts = {}   # series_key -> count of CrossRef hits

    # ── STEP 1 & 2: CrossRef + OpenAlex per series ───────────────────────────

    for sdef in SERIES:
        key      = sdef["key"]
        name     = sdef["name"]
        expected = sdef["expected"]

        print(f"\n{'─'*62}")
        print(f"  Series: {key}")
        print(f"  {name}")

        # Step 1: CrossRef
        cr_items = fetch_crossref_series(sdef)
        step1_counts[key] = len(cr_items)

        # Collect DOIs + titles found so far (for gap-fill check)
        found_dois   = {(i.get("DOI") or "").lower() for i in cr_items}
        found_titles = [" ".join(i.get("title") or []) for i in cr_items]

        # Step 3: individual lookups for missing spot-check titles
        print(f"\n  Gap-fill check for {key} …")
        gap_items = fill_gaps(found_dois, found_titles, key, name)
        for gi in gap_items:
            doi = (gi.get("DOI") or "").lower()
            if doi and doi not in found_dois:
                cr_items.append(gi)
                found_dois.add(doi)

        print(f"  Total after gap-fill: {len(cr_items)}")

        # Deduplicate by DOI
        seen = set()
        deduped = []
        for item in cr_items:
            doi = (item.get("DOI") or "").lower()
            if doi in seen:
                continue
            seen.add(doi)
            deduped.append(item)
        cr_items = deduped

        # Step 2: OpenAlex enrichment
        print(f"  Enriching {len(cr_items)} books via OpenAlex …")
        processed = []
        for idx, item in enumerate(cr_items, 1):
            doi        = item.get("DOI") or ""
            title_raw  = " ".join(item.get("title") or ["[untitled]"]).strip()
            isbn       = _isbn13(item.get("isbn-type") or item.get("ISBN") or [])
            year       = _year(item)
            book_type  = _detect_book_type(item)
            authors    = _authors_str(item)
            editors    = _editors_str(item)
            cr_abstract = _crossref_abstract(item)

            # OpenAlex enrichment
            oa = enrich_openalex(doi)
            abstract = oa.get("abstract") or cr_abstract
            topics   = oa.get("topics") or []

            processed.append({
                "doi":       doi,
                "isbn":      isbn,
                "title":     title_raw,
                "book_type": book_type,
                "authors":   authors,
                "editors":   editors,
                "year":      year,
                "abstract":  abstract,
                "series":    name,
                "topics":    topics,
            })

            if idx % 10 == 0:
                print(f"    … {idx}/{len(cr_items)} enriched")

        all_results[key] = processed
        print(f"  {key}: {len(processed)} books ready to insert")

    # ── STEP 4: Insert into DB ────────────────────────────────────────────────

    print(f"\n{'='*62}")
    print("  STEP 4: Inserting into database")
    print(f"{'='*62}")

    stats = {k: {"inserted": 0, "updated": 0, "skipped": 0, "dup_other": 0}
             for k in [s["key"] for s in SERIES]}

    if not dry_run:
        with get_conn() as conn:
            for sdef in SERIES:
                key  = sdef["key"]
                books = all_results.get(key, [])

                # Check for cross-publisher duplicates before inserting
                for book in books:
                    doi   = book["doi"]
                    isbn  = book["isbn"]
                    title = book["title"]

                    # Check if this already exists from another publisher
                    if doi:
                        row = conn.execute(
                            "SELECT id, publisher FROM books WHERE doi = ?", (doi,)
                        ).fetchone()
                        if row and row["publisher"] != PUBLISHER:
                            print(f"  [dup-other] '{title[:60]}' already in DB "
                                  f"(publisher={row['publisher']})")
                            stats[key]["dup_other"] += 1
                            continue

                    result = upsert_routledge_book(
                        conn,
                        doi       = doi or None,
                        isbn      = book["isbn"],
                        title     = book["title"],
                        book_type = book["book_type"],
                        authors   = book["authors"],
                        editors   = book["editors"],
                        year      = book["year"],
                        abstract  = book["abstract"],
                        series_name = book["series"],
                        topics    = book["topics"],
                    )
                    stats[key][result] = stats[key].get(result, 0) + 1

                conn.commit()
                print(f"  {key}: {stats[key]['inserted']} inserted, "
                      f"{stats[key]['updated']} updated, "
                      f"{stats[key]['skipped']} skipped, "
                      f"{stats[key]['dup_other']} dup-other-publisher")
    else:
        print("  [dry-run] No DB writes.")
        for sdef in SERIES:
            key   = sdef["key"]
            books = all_results.get(key, [])
            print(f"\n  {key} — {len(books)} books would be inserted:")
            for b in sorted(books, key=lambda x: x.get("year") or 0):
                print(f"    [{b.get('year','?')}] {b['title'][:70]}")

    # ── STEP 5: Verification ──────────────────────────────────────────────────

    print(f"\n{'='*62}")
    print("  STEP 5: Verification")
    print(f"{'='*62}")

    if dry_run:
        print("  (dry-run — skipping DB verification)")
        _print_summary_table(step1_counts)
        return

    with get_conn() as conn:

        # 1. Count by series
        print("\n  1. Count by series:")
        print(f"  {'Series':<50} {'Count':>6}")
        print(f"  {'─'*50} {'─'*6}")
        total = 0
        for sdef in SERIES:
            name = sdef["name"]
            row = conn.execute(
                "SELECT COUNT(*) FROM books WHERE subjects LIKE ? AND publisher = ?",
                (f"{name}%", PUBLISHER)
            ).fetchone()
            n = row[0]
            total += n
            print(f"  {name[:50]:<50} {n:>6}")
        print(f"  {'TOTAL':<50} {total:>6}")

        # 2. Year range per series
        print("\n  2. Year range per series:")
        for sdef in SERIES:
            name = sdef["name"]
            row = conn.execute(
                "SELECT MIN(year), MAX(year) FROM books WHERE subjects LIKE ? AND publisher = ?",
                (f"{name}%", PUBLISHER)
            ).fetchone()
            print(f"  {sdef['key']}: {row[0]} – {row[1]}")

        # 3. Full title listing grouped by series, sorted by year
        print("\n  3. Full title listing (all series):")
        for sdef in SERIES:
            key  = sdef["key"]
            name = sdef["name"]
            rows = conn.execute(
                "SELECT year, title, book_type, doi FROM books "
                "WHERE subjects LIKE ? AND publisher = ? "
                "ORDER BY year NULLS LAST, title",
                (f"{name}%", PUBLISHER)
            ).fetchall()
            print(f"\n  ── {key}: {name} ({len(rows)}) ──")
            for r in rows:
                doi_flag = "✓" if r["doi"] else "✗"
                print(f"    [{r['year'] or '????'}] {r['title'][:68]} [{doi_flag}]")

        # 4. DOI coverage
        print("\n  4. DOI coverage:")
        row = conn.execute(
            "SELECT COUNT(*) FROM books WHERE publisher = ? AND doi IS NOT NULL",
            (PUBLISHER,)
        ).fetchone()
        row_total = conn.execute(
            "SELECT COUNT(*) FROM books WHERE publisher = ?",
            (PUBLISHER,)
        ).fetchone()
        n_doi = row[0]
        n_all = row_total[0]
        print(f"  {n_doi}/{n_all} books have DOIs ({100*n_doi//max(n_all,1)}%)")
        no_doi = conn.execute(
            "SELECT title, year FROM books WHERE publisher = ? AND doi IS NULL ORDER BY year",
            (PUBLISHER,)
        ).fetchall()
        if no_doi:
            print("  Books missing DOIs:")
            for r in no_doi:
                print(f"    [{r['year'] or '?'}] {r['title'][:70]}")

        # 5. ISBN coverage
        row = conn.execute(
            "SELECT COUNT(*) FROM books WHERE publisher = ? AND isbn IS NOT NULL",
            (PUBLISHER,)
        ).fetchone()
        n_isbn = row[0]
        print(f"\n  5. ISBN coverage: {n_isbn}/{n_all} ({100*n_isbn//max(n_all,1)}%)")

        # 6. Abstract coverage
        row = conn.execute(
            "SELECT COUNT(*) FROM books WHERE publisher = ? AND abstract IS NOT NULL AND abstract != ''",
            (PUBLISHER,)
        ).fetchone()
        n_abs = row[0]
        print(f"\n  6. Abstract coverage: {n_abs}/{n_all} ({100*n_abs//max(n_all,1)}%)")

        # 7. Cross-publisher duplicates (DOI match with different publisher)
        print("\n  7. Cross-publisher duplicate check:")
        dup_rows = conn.execute("""
            SELECT b1.title, b1.publisher, b2.publisher AS other_pub
            FROM books b1
            JOIN books b2 ON b1.doi = b2.doi AND b1.id != b2.id
            WHERE b1.publisher = ?
        """, (PUBLISHER,)).fetchall()
        if dup_rows:
            print(f"  {len(dup_rows)} duplicate DOIs found across publishers:")
            for r in dup_rows:
                print(f"    {r['title'][:60]} | {r['publisher']} ← also in → {r['other_pub']}")
        else:
            print("  No cross-publisher DOI duplicates found. ✓")

        # 8. Spot-check known titles
        print("\n  8. Spot-check known titles:")
        all_pass = True
        for sdef in SERIES:
            key  = sdef["key"]
            name = sdef["name"]
            checks = SPOT_CHECKS.get(key, [])
            print(f"\n  {key}:")
            for title, _ in checks:
                title_lower = title.lower()
                rows = conn.execute(
                    "SELECT title FROM books WHERE publisher = ? AND subjects LIKE ?",
                    (PUBLISHER, f"{name}%")
                ).fetchall()
                matched = any(
                    _title_similarity(title_lower, r["title"].lower()) >= 0.55
                    for r in rows
                )
                status = "PASS ✓" if matched else "FAIL ✗"
                if not matched:
                    all_pass = False
                print(f"    {status}  {title[:60]}")
        if all_pass:
            print("\n  All spot-checks passed! ✓")
        else:
            print("\n  Some spot-checks failed — check gap-fill results above.")

        # 9. Cross-series tag check
        print("\n  9. Cross-series tag check:")
        series_names = [s["name"] for s in SERIES]
        wrong = []
        for sdef in SERIES:
            name = sdef["name"]
            rows = conn.execute(
                "SELECT title, subjects FROM books WHERE publisher = ? AND subjects LIKE ?",
                (PUBLISHER, f"{name}%")
            ).fetchall()
            for r in rows:
                first_subj = (r["subjects"] or "").split(";")[0].strip()
                if first_subj != name and first_subj in series_names:
                    wrong.append((r["title"], first_subj, name))
        if wrong:
            print(f"  {len(wrong)} books tagged with wrong series:")
            for title, actual, expected_s in wrong:
                print(f'    "{title[:50]}" -> stored as "{actual[:40]}"')
        else:
            print("  No cross-series tagging issues found. ✓")

    # ── Step 1 summary table ──────────────────────────────────────────────────
    _print_summary_table(step1_counts)

    # ── Adjacent series flag ──────────────────────────────────────────────────
    print("\n  NOTE: Additional Routledge series observed during queries")
    print("  (not ingested — flag for future consideration):")
    print("  • Routledge Studies in Rhetoric, Writing, and Professional Communication")
    print("  • Routledge Handbooks in Communication Studies")
    print("  • Routledge Studies in Composition and Rhetorical Literacy")
    print("  • New Directions in Rhetoric and Materiality")


def _print_summary_table(step1_counts):
    print(f"\n  CrossRef retrieval summary:")
    print(f"  {'Series':<50} {'Expected':>8} {'Found':>6}")
    print(f"  {'─'*50} {'─'*8} {'─'*6}")
    total_exp   = 0
    total_found = 0
    for sdef in SERIES:
        key      = sdef["key"]
        expected = sdef["expected"]
        found    = step1_counts.get(key, 0)
        total_exp   += expected
        total_found += found
        label = sdef["name"][:50]
        print(f"  {label:<50} {expected:>8} {found:>6}")
    print(f"  {'TOTAL':<50} {total_exp:>8} {total_found:>6}")


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    run(dry_run=dry)
