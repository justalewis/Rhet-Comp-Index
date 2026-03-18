"""
crossref_book_probe.py
======================
Probes the CrossRef API for book and chapter metadata across key
rhetoric & composition publishers. Generates a structured report
and saves full results to crossref_book_probe_results.json.

Usage:
    python crossref_book_probe.py
"""

import json
import time
import sys
import io
from difflib import SequenceMatcher
from urllib.parse import quote_plus
import requests

# Force UTF-8 output on Windows (avoids cp1252 encoding errors for ✓ ✗ etc.)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

MAILTO = "mailto=rhetcompindex@gmail.com"
BASE   = "https://api.crossref.org"
DELAY  = 0.6   # seconds between requests


# ── Helpers ────────────────────────────────────────────────────────────────────

def get(url, params=None, retries=3):
    """GET with retry, returns parsed JSON or None."""
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=30,
                             headers={"User-Agent": f"PinakesProbe/1.0 ({MAILTO.replace('mailto=','')})"})
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                print("  [rate-limited] sleeping 10s …")
                time.sleep(10)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == retries - 1:
                print(f"  [error] {url}: {e}")
                return None
            time.sleep(2)
    return None


def sleep():
    time.sleep(DELAY)


def title_of(work):
    t = work.get("title") or []
    return t[0] if t else work.get("display_name", "")


def authors_of(work, field="author"):
    people = work.get(field) or []
    names = []
    for p in people:
        given = p.get("given", "")
        family = p.get("family", "")
        names.append(f"{given} {family}".strip() if family else given)
    return names


def year_of(work):
    pd = work.get("published") or work.get("published-print") or work.get("published-online") or {}
    parts = pd.get("date-parts", [[]])
    return parts[0][0] if parts and parts[0] else None


def refs_count(work):
    """Return (has_references, count). Prefer 'references-count' field."""
    rc = work.get("references-count", 0) or 0
    refs = work.get("reference") or []
    if rc:
        return True, rc
    if refs:
        return True, len(refs)
    return False, 0


def similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def fmt_list(lst, limit=3):
    if not lst:
        return "—"
    short = lst[:limit]
    suffix = f" … +{len(lst)-limit}" if len(lst) > limit else ""
    return "; ".join(short) + suffix


def pct(n, total):
    if not total:
        return "—"
    return f"{100*n//total}%"


def header(text):
    w = 72
    print()
    print("=" * w)
    print(f"  {text}")
    print("=" * w)


def subheader(text):
    print(f"\n── {text} " + "─" * max(0, 68 - len(text)))


# ── Step 1: Publisher member IDs ───────────────────────────────────────────────

PUBLISHERS = [
    {"name": "University Press of Colorado",   "aliases": ["University Press of Colorado", "Utah State University Press"]},
    {"name": "WAC Clearinghouse",              "aliases": ["WAC Clearinghouse", "Colorado State University"], "prefix": "10.37514"},
    {"name": "Routledge / Taylor & Francis",   "aliases": ["Routledge", "Taylor & Francis"]},
    {"name": "Southern Illinois University Press", "aliases": ["Southern Illinois University Press"]},
    {"name": "University of Pittsburgh Press", "aliases": ["University of Pittsburgh Press", "Pittsburgh"]},
    {"name": "Ohio State University Press",    "aliases": ["Ohio State University Press"]},
    {"name": "NCTE",                           "aliases": ["National Council of Teachers of English", "NCTE"]},
    {"name": "Penn State University Press",    "aliases": ["Pennsylvania State University Press", "Penn State"]},
    {"name": "Parlor Press",                   "aliases": ["Parlor Press"]},
    {"name": "Peter Lang",                     "aliases": ["Peter Lang"]},
]


def find_member(pub):
    """Try each alias. Return best member dict or None."""
    best = None
    for alias in pub["aliases"]:
        url = f"{BASE}/members?query={quote_plus(alias)}&rows=5&{MAILTO}"
        data = get(url)
        sleep()
        if not data:
            continue
        items = data.get("message", {}).get("items", [])
        for item in items:
            primary = item.get("primary-name", "")
            prefixes = item.get("prefixes", [])
            # exact-ish match on name
            if similarity(alias, primary) > 0.6:
                best = item
                break
            # prefix match
            if pub.get("prefix") and pub["prefix"] in prefixes:
                best = item
                break
        if best:
            break
    # Fallback: prefix endpoint
    if not best and pub.get("prefix"):
        url = f"{BASE}/prefixes/{pub['prefix']}?{MAILTO}"
        data = get(url)
        sleep()
        if data:
            msg = data.get("message", {})
            best = {"id": None, "primary-name": msg.get("name", pub["name"]),
                    "prefixes": [pub["prefix"]], "_prefix_only": True}
    return best


def step1_members():
    header("STEP 1 — CrossRef Member IDs")
    results = {}
    for pub in PUBLISHERS:
        print(f"\n  Searching: {pub['name']} …")
        member = find_member(pub)
        if member:
            mid   = member.get("id", "N/A")
            mname = member.get("primary-name", "?")
            prefs = member.get("prefixes", [])
            print(f"    ✓  ID={mid}  name='{mname}'  prefixes={prefs[:4]}")
            results[pub["name"]] = {
                "member_id": mid,
                "crossref_name": mname,
                "prefixes": prefs,
                "raw": member,
            }
        else:
            print(f"    ✗  Not found")
            results[pub["name"]] = None
    return results


# ── Step 2: Book-type counts per publisher ─────────────────────────────────────

BOOK_TYPES = ["book", "monograph", "edited-book", "book-chapter",
              "book-section", "book-part"]


def get_type_count(member_id, btype, extra_filter=""):
    f = f"type:{btype}"
    if extra_filter:
        f += f",{extra_filter}"
    url = f"{BASE}/members/{member_id}/works"
    params = {"filter": f, "rows": 0, "mailto": MAILTO.replace("mailto=", "")}
    data = get(url, params=params)
    sleep()
    if not data:
        return 0
    return data.get("message", {}).get("total-results", 0)


def step2_counts(members):
    header("STEP 2 — Book-Type Work Counts per Publisher")
    counts = {}
    col_w = 14
    print("\n  {:<32} {:>{}}{:>{}}{:>{}}{:>{}}{:>{}}{:>{}}".format(
        "Publisher", "book", col_w, "mono", col_w, "edited-bk", col_w,
        "bk-ch", col_w, "bk-sec", col_w, "bk-part", col_w))
    print("  " + "-"*116)

    for pub_name, info in members.items():
        if not info or not info.get("member_id"):
            print(f"  {pub_name:<32} [no member ID — skipped]")
            counts[pub_name] = {}
            continue
        mid = info["member_id"]
        row = {}
        for bt in BOOK_TYPES:
            # Routledge is huge — narrow to relevant query
            extra = ""
            if "Routledge" in pub_name or "Taylor" in pub_name:
                extra = ""  # we'll handle filtering in step 3 sampling
            n = get_type_count(mid, bt, extra)
            row[bt] = n
        counts[pub_name] = row
        print("  {:<32} {:>{}}{:>{}}{:>{}}{:>{}}{:>{}}{:>{}}".format(
            pub_name[:32],
            row.get("book", 0), col_w,
            row.get("monograph", 0), col_w,
            row.get("edited-book", 0), col_w,
            row.get("book-chapter", 0), col_w,
            row.get("book-section", 0), col_w,
            row.get("book-part", 0), col_w,
        ))
    return counts


# ── Step 3: Sample books ───────────────────────────────────────────────────────

def sample_books(member_id, pub_name, rows=10):
    params = {
        "filter": "type:book",
        "rows": rows,
        "sort": "published",
        "order": "desc",
        "mailto": MAILTO.replace("mailto=", ""),
    }
    # Routledge: narrow to rhet/comp
    if "Routledge" in pub_name or "Taylor" in pub_name:
        params["query"] = "rhetoric composition writing"
    url = f"{BASE}/members/{member_id}/works"
    data = get(url, params=params)
    sleep()
    if not data:
        return []
    return data.get("message", {}).get("items", [])


def summarise_book(work):
    has_refs, ref_count = refs_count(work)
    return {
        "title":         title_of(work),
        "type":          work.get("type", "?"),
        "doi":           work.get("DOI", ""),
        "year":          year_of(work),
        "authors":       authors_of(work, "author"),
        "editors":       authors_of(work, "editor"),
        "isbn":          (work.get("ISBN") or [])[:4],
        "has_abstract":  bool(work.get("abstract")),
        "has_refs":      has_refs,
        "ref_count":     ref_count,
        "cited_by":      work.get("is-referenced-by-count", 0),
        "subjects":      (work.get("subject") or [])[:5],
    }


def step3_books(members):
    header("STEP 3 — Book Metadata Quality Samples")
    book_samples = {}
    for pub_name, info in members.items():
        if not info or not info.get("member_id"):
            book_samples[pub_name] = []
            continue
        mid = info["member_id"]
        subheader(pub_name)
        works = sample_books(mid, pub_name)
        summaries = []
        for w in works:
            s = summarise_book(w)
            summaries.append(s)
            auth = fmt_list(s["authors"] or s["editors"], 2)
            print(f"    [{s['year']}] {s['title'][:65]}")
            print(f"           {s['type']} | DOI:{s['doi'][:40] or '—'}")
            print(f"           by/ed: {auth}  |  refs:{s['ref_count']}  cited:{s['cited_by']}  abs:{'Y' if s['has_abstract'] else 'N'}")
        book_samples[pub_name] = summaries
        if not summaries:
            print("    (no book records found)")
    return book_samples


# ── Step 4: Sample chapters + parent-child lookup ─────────────────────────────

def sample_chapters(member_id, pub_name, rows=10):
    params = {
        "filter": "type:book-chapter",
        "rows": rows,
        "sort": "published",
        "order": "desc",
        "mailto": MAILTO.replace("mailto=", ""),
    }
    if "Routledge" in pub_name or "Taylor" in pub_name:
        params["query"] = "rhetoric composition writing"
    url = f"{BASE}/members/{member_id}/works"
    data = get(url, params=params)
    sleep()
    if not data:
        return []
    return data.get("message", {}).get("items", [])


def find_parent_book(container_title, isbn_list):
    """Try to find a parent book record by container title or ISBN."""
    if container_title:
        ct_q = quote_plus(container_title[:80])
        url = (f"{BASE}/works?query.bibliographic={ct_q}"
               f"&filter=type:book,type:edited-book&rows=3&{MAILTO}")
        data = get(url)
        sleep()
        if data:
            items = data.get("message", {}).get("items", [])
            for item in items:
                t = title_of(item)
                if similarity(t, container_title) > 0.75:
                    return item, "title-search"
    # ISBN lookup
    for isbn in isbn_list:
        url = f"{BASE}/works?filter=isbn:{isbn}&rows=3&{MAILTO}"
        data = get(url)
        sleep()
        if data:
            items = data.get("message", {}).get("items", [])
            for item in items:
                if item.get("type") in ("book", "edited-book", "monograph"):
                    return item, "isbn"
    return None, None


def step4_chapters(members):
    header("STEP 4 — Chapter Metadata & Parent-Child Relationships")
    chapter_data = {}
    for pub_name, info in members.items():
        if not info or not info.get("member_id"):
            chapter_data[pub_name] = []
            continue
        mid = info["member_id"]
        subheader(pub_name)
        works = sample_chapters(mid, pub_name)
        if not works:
            print("    (no book-chapter records found)")
            chapter_data[pub_name] = []
            continue

        summaries = []
        parent_lookups = 0
        for i, w in enumerate(works):
            has_refs, ref_count = refs_count(w)
            container = (w.get("container-title") or [""])[0]
            isbn_list = w.get("ISBN") or []
            ch = {
                "title":      title_of(w),
                "doi":        w.get("DOI", ""),
                "year":       year_of(w),
                "authors":    authors_of(w, "author"),
                "container":  container,
                "isbn":       isbn_list[:4],
                "pages":      w.get("page", ""),
                "has_refs":   has_refs,
                "ref_count":  ref_count,
                "cited_by":   w.get("is-referenced-by-count", 0),
                "relation":   w.get("relation", {}),
                "parent":     None,
                "parent_method": None,
            }
            # Look up parent for first 3 chapters
            if parent_lookups < 3 and container:
                parent, method = find_parent_book(container, isbn_list)
                if parent:
                    ch["parent"] = {
                        "title":   title_of(parent),
                        "doi":     parent.get("DOI", ""),
                        "type":    parent.get("type", ""),
                        "editors": authors_of(parent, "editor"),
                        "isbn":    (parent.get("ISBN") or [])[:4],
                        "isbn_match": bool(set(isbn_list) & set(parent.get("ISBN") or [])),
                    }
                    ch["parent_method"] = method
                parent_lookups += 1

            summaries.append(ch)
            print(f"    [{ch['year']}] {ch['title'][:60]}")
            print(f"           in: {container[:55] or '—'}")
            auth = fmt_list(ch["authors"], 2)
            print(f"           by: {auth}  |  refs:{ref_count}  cited:{ch['cited_by']}")
            if ch["parent"]:
                p = ch["parent"]
                print(f"           ↳ parent found ({ch['parent_method']}): {p['title'][:50]}")
                print(f"             parent DOI:{p['doi'][:40] or '—'}  editors:{fmt_list(p['editors'],2)}")
                print(f"             ISBN match: {ch['parent']['isbn_match']}")

        chapter_data[pub_name] = summaries
    return chapter_data


# ── Step 5: Known edited collections deep-dive ────────────────────────────────

KNOWN_BOOKS = [
    {
        "title": "Naming What We Know",
        "editor": "Adler-Kassner",
        "press": "Utah State",
        "year": 2015,
        "kind": "edited",
    },
    {
        "title": "Antiracist Writing Assessment Ecologies",
        "editor": "Inoue",
        "press": "WAC Clearinghouse",
        "year": 2015,
        "kind": "monograph",
    },
    {
        "title": "Key Theoretical Frameworks",
        "editor": "Haas",
        "press": "Utah State",
        "year": 2018,
        "kind": "edited",
    },
    {
        "title": "Performing Antiracist Pedagogy in Rhetoric Writing and Communication",
        "editor": "Condon",
        "press": "WAC Clearinghouse",
        "year": 2016,
        "kind": "edited",
        "doi": "10.37514/ATD-B.2016.0933",
    },
    {
        "title": "Labor-Based Grading Contracts",
        "editor": "Inoue",
        "press": "WAC Clearinghouse",
        "year": 2022,
        "kind": "monograph",
        "doi": "10.37514/PER-B.2022.1824",
    },
    {
        "title": "Technical Communication after the Social Justice Turn",
        "editor": "Walton",
        "press": "Routledge",
        "year": 2019,
        "kind": "edited",
    },
    {
        "title": "Writing Spaces",
        "editor": "Lowe",
        "press": "WAC Clearinghouse",
        "year": 2010,
        "kind": "edited",
    },
    {
        "title": "WAC and Second Language Writers",
        "editor": "Zawacki",
        "press": "WAC Clearinghouse",
        "year": 2014,
        "kind": "edited",
        "doi": "10.37514/PER-B.2014.0551",
    },
    {
        "title": "A Rhetoric of Motives",
        "editor": "Burke",
        "press": "U of California Press",
        "year": 1969,
        "kind": "monograph",
    },
    {
        "title": "Learning from the Mess",
        "editor": "Holmes",
        "press": "WAC Clearinghouse",
        "year": 2024,
        "kind": "edited",
        "doi": "10.37514/PER-B.2024.2180",
    },
]


def fetch_book_record(book):
    """Find a book record by DOI (preferred) or title search."""
    if book.get("doi"):
        url = f"{BASE}/works/{book['doi']}?{MAILTO}"
        data = get(url)
        sleep()
        if data:
            return data.get("message"), "doi"
    # Title + editor search
    q = quote_plus(book["title"][:80])
    aq = quote_plus(book["editor"])
    url = f"{BASE}/works?query.bibliographic={q}&query.author={aq}&rows=5&{MAILTO}"
    data = get(url)
    sleep()
    if not data:
        return None, None
    for item in data.get("message", {}).get("items", []):
        t = title_of(item)
        if similarity(t, book["title"]) > 0.65:
            return item, "title-search"
    return None, None


def fetch_chapters_for_book(book_title, rows=25):
    """Search for book-chapter records matching a book title."""
    q = quote_plus(book_title[:80])
    url = f"{BASE}/works?filter=type:book-chapter&query.bibliographic={q}&rows={rows}&{MAILTO}"
    data = get(url)
    sleep()
    if not data:
        return []
    items = data.get("message", {}).get("items", [])
    # Filter for likely matches
    matched = []
    for item in items:
        container = (item.get("container-title") or [""])[0]
        if similarity(container, book_title) > 0.55 or similarity(title_of(item), book_title) > 0.55:
            matched.append(item)
    return matched


def step5_known_books():
    header("STEP 5 — Known Edited Collections Deep Dive")
    results = {}
    for book in KNOWN_BOOKS:
        subheader(f"{book['title'][:55]} ({book['year']})")
        print(f"    kind: {book['kind']}  press: {book['press']}")

        # Find book record
        record, method = fetch_book_record(book)
        found = record is not None
        book_doi = ""
        book_type = ""
        editors = []
        cited_by = 0

        if found:
            book_doi = record.get("DOI", "")
            book_type = record.get("type", "")
            editors = authors_of(record, "editor") or authors_of(record, "author")
            cited_by = record.get("is-referenced-by-count", 0)
            has_refs, ref_count = refs_count(record)
            print(f"    ✓ Found ({method}): {title_of(record)[:60]}")
            print(f"      DOI: {book_doi}  type: {book_type}  cited: {cited_by}")
            print(f"      eds/authors: {fmt_list(editors, 3)}")
            print(f"      refs in book record: {ref_count if has_refs else 'none'}")
        else:
            print(f"    ✗ Book record NOT found in CrossRef")

        # Find chapters (only for edited collections)
        chapters = []
        if book["kind"] == "edited":
            chapters = fetch_chapters_for_book(book["title"])
            ch_with_refs = sum(1 for c in chapters if refs_count(c)[0])
            ch_authors = [authors_of(c, "author") for c in chapters]
            unique_auth = set(n for names in ch_authors for n in names)
            if chapters:
                print(f"    chapters found: {len(chapters)}")
                print(f"    chapters with refs: {ch_with_refs}/{len(chapters)}")
                print(f"    unique chapter authors: {len(unique_auth)}")
                # Print first 5 chapters
                for c in chapters[:5]:
                    has_r, rc = refs_count(c)
                    print(f"      • {title_of(c)[:58]}")
                    print(f"        by: {fmt_list(authors_of(c,'author'),2)}  refs:{rc}  DOI:{c.get('DOI','—')[:38]}")
            else:
                print(f"    (no chapter records found for this title)")

        results[book["title"]] = {
            "found": found,
            "method": method,
            "doi": book_doi,
            "crossref_type": book_type,
            "editors": editors,
            "cited_by": cited_by,
            "chapter_count": len(chapters),
            "chapters_with_refs": sum(1 for c in chapters if refs_count(c)[0]),
            "chapters": [summarise_book(c) for c in chapters[:30]],
        }
    return results


# ── Step 6: WAC Clearinghouse DOI pattern ─────────────────────────────────────

WAC_TEST_BOOKS = [
    {
        "label": "Learning from the Mess",
        "book_doi": "10.37514/PER-B.2024.2180",
        "ch_prefix": "10.37514/PER-B.2024.2180.2.",
        "ch_count_guess": 12,
    },
    {
        "label": "Performing Antiracist Pedagogy",
        "book_doi": "10.37514/ATD-B.2016.0933",
        "ch_prefix": "10.37514/ATD-B.2016.0933.2.",
        "ch_count_guess": 12,
    },
]


def fetch_doi(doi):
    url = f"{BASE}/works/{doi}?{MAILTO}"
    data = get(url)
    sleep()
    if data:
        return data.get("message")
    return None


def step6_wac_pattern():
    header("STEP 6 — WAC Clearinghouse DOI Pattern Test")
    pattern_results = {}

    for wac in WAC_TEST_BOOKS:
        subheader(wac["label"])
        label = wac["label"]
        pattern_results[label] = {
            "book_doi": wac["book_doi"],
            "book_found": False,
            "chapter_dois_found": [],
            "chapter_dois_404": [],
            "first_chapter": None,
            "parent_link_fields": [],
        }
        pr = pattern_results[label]

        # Fetch book
        print(f"  Book DOI: {wac['book_doi']}")
        book = fetch_doi(wac["book_doi"])
        if book:
            pr["book_found"] = True
            print(f"  ✓ Book found: {title_of(book)[:65]}")
            print(f"    type: {book.get('type','?')}  cited: {book.get('is-referenced-by-count',0)}")
            editors = authors_of(book, "editor")
            if editors:
                print(f"    editors: {fmt_list(editors, 3)}")
            # Check for relation field
            rel = book.get("relation", {})
            if rel:
                print(f"    relation keys: {list(rel.keys())[:5]}")
        else:
            print(f"  ✗ Book not found at {wac['book_doi']}")

        # Enumerate chapters by suffix
        print(f"\n  Enumerating chapters via suffix pattern …")
        consec_404s = 0
        ch_num = 1
        # Also try front-matter (.1.1, .1.2, .1.3)
        print(f"  Checking front-matter (.1.x) …")
        for fm in range(1, 5):
            fm_doi = f"{wac['book_doi']}.1.{fm:02d}"
            fm_work = fetch_doi(fm_doi)
            if fm_work:
                print(f"    .1.{fm:02d} → {title_of(fm_work)[:55]}  [type:{fm_work.get('type','?')}]")
            else:
                print(f"    .1.{fm:02d} → 404")

        print(f"  Checking chapters (.2.x) …")
        while consec_404s < 3 and ch_num <= wac["ch_count_guess"] + 3:
            ch_doi = f"{wac['ch_prefix']}{ch_num:02d}"
            ch_work = fetch_doi(ch_doi)
            if ch_work:
                consec_404s = 0
                pr["chapter_dois_found"].append(ch_doi)
                if not pr["first_chapter"]:
                    pr["first_chapter"] = {
                        "title":     title_of(ch_work),
                        "authors":   authors_of(ch_work, "author"),
                        "container": (ch_work.get("container-title") or [""])[0],
                        "isbn":      (ch_work.get("ISBN") or [])[:4],
                        "relation":  ch_work.get("relation", {}),
                    }
                has_refs, rc = refs_count(ch_work)
                # Check parent-link fields
                links = []
                if ch_work.get("container-title"):
                    links.append("container-title")
                if ch_work.get("ISBN"):
                    links.append("ISBN")
                if ch_work.get("relation"):
                    links.append("relation")
                if links and label not in pr["parent_link_fields"]:
                    pr["parent_link_fields"] = links
                print(f"    .2.{ch_num:02d} → {title_of(ch_work)[:55]}  refs:{rc}")
            else:
                consec_404s += 1
                pr["chapter_dois_404"].append(ch_doi)
                print(f"    .2.{ch_num:02d} → 404  ({consec_404s} consec.)")
            ch_num += 1

        found_count = len(pr["chapter_dois_found"])
        print(f"\n  Summary for {label}:")
        print(f"    chapters found via .2.XX pattern: {found_count}")
        if pr["first_chapter"]:
            fc = pr["first_chapter"]
            print(f"    first chapter: {fc['title'][:55]}")
            print(f"    by: {fmt_list(fc['authors'],2)}")
            print(f"    container-title: {fc['container'][:55]}")
            print(f"    ISBN: {fc['isbn']}")
            print(f"    parent-link fields: {pr['parent_link_fields']}")

    return pattern_results


# ── Step 7: Final report ───────────────────────────────────────────────────────

def step7_report(members, counts, book_samples, chapter_data, known_books, wac_pattern):
    header("STEP 7 — FINAL REPORT")

    # Publisher summary table
    subheader("Publisher Summary")
    print(f"\n  {'Publisher':<32} {'MemberID':>9} {'Books':>7} {'Edited':>7} "
          f"{'Chs':>8} {'Chs w/Refs':>12} {'Avg Refs':>9}")
    print("  " + "-"*88)

    for pub_name in members:
        info  = members.get(pub_name) or {}
        cnt   = counts.get(pub_name) or {}
        cdata = chapter_data.get(pub_name) or []
        mid   = info.get("member_id", "—") if info else "—"
        books = cnt.get("book", 0) + cnt.get("monograph", 0)
        edited = cnt.get("edited-book", 0)
        chs    = cnt.get("book-chapter", 0) + cnt.get("book-section", 0) + cnt.get("book-part", 0)
        ch_with_refs = sum(1 for c in cdata if c.get("has_refs"))
        ref_counts = [c["ref_count"] for c in cdata if c.get("ref_count", 0) > 0]
        avg_refs = f"{sum(ref_counts)/len(ref_counts):.1f}" if ref_counts else "—"
        ch_ref_str = f"{ch_with_refs}/{len(cdata)}" if cdata else "—"
        print(f"  {pub_name[:32]:<32} {str(mid):>9} {books:>7} {edited:>7} "
              f"{chs:>8} {ch_ref_str:>12} {avg_refs:>9}")

    # Known books table
    subheader("Known Book Deep Dive")
    print(f"\n  {'Title':<42} {'Found':>6} {'Type':<14} {'Chs':>5} {'Ch.Refs':>8} {'CitedBy':>8}")
    print("  " + "-"*85)
    for title, r in known_books.items():
        found_s  = "Yes" if r["found"] else "No"
        btype    = r.get("crossref_type", "—")[:13]
        chs      = r.get("chapter_count", 0)
        cwrefs   = r.get("chapters_with_refs", 0)
        ch_str   = f"{cwrefs}/{chs}" if chs else ("n/a" if r["found"] else "—")
        cited    = r.get("cited_by", 0) or "—"
        print(f"  {title[:42]:<42} {found_s:>6} {btype:<14} {chs:>5} {ch_str:>8} {str(cited):>8}")

    # Parent-child relationship report
    subheader("Parent-Child Relationship Analysis")
    for pub_name, cdata in chapter_data.items():
        parents_found = [c for c in cdata if c.get("parent")]
        if not parents_found:
            continue
        print(f"\n  {pub_name}:")
        for c in parents_found[:3]:
            p = c["parent"]
            print(f"    Chapter: {c['title'][:55]}")
            print(f"    Parent:  {p['title'][:55]}")
            link_fields = []
            if p.get("isbn_match"):
                link_fields.append("ISBN")
            if (c.get("container") or "").lower() in (p.get("title") or "").lower():
                link_fields.append("container-title")
            if c.get("relation"):
                link_fields.append("relation")
            print(f"    Link via: {', '.join(link_fields) or c['parent_method']}")
            print(f"    Editors distinct from ch. authors: "
                  f"{'(check manually)' if not p['editors'] else fmt_list(p['editors'],2)}")

    # WAC DOI pattern report
    subheader("WAC Clearinghouse DOI Pattern")
    for label, pr in wac_pattern.items():
        found = len(pr["chapter_dois_found"])
        print(f"\n  {label}:")
        print(f"    Book found: {pr['book_found']}")
        print(f"    Chapters via .2.XX: {found}")
        print(f"    Parent-link fields: {pr['parent_link_fields']}")
        if found > 0:
            print(f"    → Pattern CONFIRMED. Can enumerate all chapters by incrementing .2.XX suffix.")
        else:
            print(f"    → Pattern could NOT be confirmed for this title.")

    # Interpretation
    subheader("Interpretation & Recommendations")
    print("""
  PUBLISHER TIERS FOR MONOGRAPH INGESTION:

  ■ FULL DEPTH (book + chapter records with references)
    WAC Clearinghouse   — Chapter DOIs confirmed via .2.XX suffix pattern.
                          Parent-child link reconstructable via ISBN + container-title.
                          Open-access metadata, high completeness. BUILD FIRST.
    Utah State / UP CO  — Book DOIs confirmed. Check whether chapter DOIs exist;
                          if so, same depth as WAC.
    Routledge / T&F     — DOIs for books and chapters deposited. References likely
                          deposited for recent titles. Filter by rhet/comp series.
    SIU Press (SWR)     — Probe for chapter DOIs; SWR chapters are short (~80pp)
                          so may not be broken out by chapter.

  ■ BOOK ONLY (title-level records, no chapter breakdown worth pursuing)
    U Pittsburgh Press  — Book-level records, probably no chapter DOIs.
    Ohio State UP       — Same.
    Penn State UP       — Same.

  ■ PROBE FURTHER
    NCTE                — Unknown chapter DOI practice. Probe SWR print editions.
    Parlor Press (indep)— Co-pubs covered by WAC; standalone Parlor DOI status unclear.
    Peter Lang          — DOIs assigned but reference/chapter deposit uncertain.

  DATA MODEL NOTES:

  1. Chapters in edited collections function exactly like journal articles:
     distinct author(s), title, DOI, page range, reference list, citable unit.
     → RECOMMENDED: store chapters in the existing `articles` table using
       source='crossref-book-chapter' and a new `parent_book_id` FK column.

  2. Add a new `books` table for monograph and edited-collection records:
       id, doi, isbn, title, type (monograph|edited-collection),
       authors/editors, publisher, year, cited_by, abstract
     Chapters in the `articles` table reference books.id via parent_book_id.

  3. Parent-child reconstruction strategy (in order of reliability):
       a. WAC Clearinghouse: match via DOI prefix (most reliable)
       b. Any publisher: match via ISBN (chapter ISBN == parent book ISBN)
       c. Fallback: match container-title string to books.title

  4. Distinguishing monographs from edited collections in CrossRef:
       type == 'edited-book'  → edited collection
       type == 'monograph'    → single/co-author
       type == 'book'         → check editor field; if non-empty, treat as edited
     Note: CrossRef type assignment is inconsistent — always check both
     `author` and `editor` fields to determine the actual record type.
    """)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    all_results = {}

    print("\nPinakes CrossRef Book & Chapter Probe")
    print(f"Running … (this will take several minutes due to API rate limits)\n")

    members     = step1_members()
    all_results["members"] = members

    counts      = step2_counts(members)
    all_results["counts"] = counts

    book_samples = step3_books(members)
    all_results["book_samples"] = book_samples

    chapter_data = step4_chapters(members)
    all_results["chapter_data"] = chapter_data

    known_books  = step5_known_books()
    all_results["known_books"] = known_books

    wac_pattern  = step6_wac_pattern()
    all_results["wac_pattern"] = wac_pattern

    step7_report(members, counts, book_samples, chapter_data, known_books, wac_pattern)

    # Save JSON
    out_path = "crossref_book_probe_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        # Strip non-serialisable "raw" member objects
        clean = json.loads(json.dumps(all_results, default=str))
        json.dump(clean, f, indent=2, ensure_ascii=False)
    print(f"\n  Full results saved → {out_path}")


if __name__ == "__main__":
    main()
