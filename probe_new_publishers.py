"""
probe_new_publishers.py — Find CrossRef member IDs and sample rhet/comp
book catalogs for: Routledge/T&F, SIUP, Pitt Press, OSU Press.

Usage:
    python probe_new_publishers.py
"""

import sys, io, time, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import requests

MAILTO = "rhetcompindex@gmail.com"
BASE   = "https://api.crossref.org"
HDRS   = {"User-Agent": f"Pinakes/1.0 (mailto:{MAILTO})"}
DELAY  = 0.8

def get(url, params=None):
    try:
        r = requests.get(url, params=params, headers=HDRS, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        time.sleep(DELAY)
        return r.json()
    except Exception as e:
        print(f"  ERROR {url}: {e}")
        time.sleep(DELAY)
        return None

def member_from_prefix(prefix):
    """Return member info from a known DOI prefix."""
    data = get(f"{BASE}/prefixes/{prefix}", {"mailto": MAILTO})
    if not data:
        return None
    msg = data.get("message", {})
    member_url = msg.get("member", "")
    # member_url looks like https://api.crossref.org/members/297
    mid = member_url.rstrip("/").split("/")[-1]
    if mid.isdigit():
        mdata = get(f"{BASE}/members/{mid}", {"mailto": MAILTO})
        if mdata:
            m = mdata.get("message", {})
            return {
                "id":       int(mid),
                "name":     m.get("primary-name", ""),
                "prefixes": m.get("prefixes", []),
            }
    return None

def search_member(query):
    """Search for a CrossRef member by name."""
    data = get(f"{BASE}/members", {"query": query, "rows": 5, "mailto": MAILTO})
    if not data:
        return []
    return data.get("message", {}).get("items", [])

def count_works(member_id, work_type, extra_filter=""):
    filt = f"type:{work_type}"
    if extra_filter:
        filt += f",{extra_filter}"
    data = get(f"{BASE}/members/{member_id}/works",
               {"filter": filt, "rows": 0, "mailto": MAILTO})
    if not data:
        return 0
    return data.get("message", {}).get("total-results", 0)

def sample_books(member_id, work_type, rows=5, query=None):
    params = {
        "filter":  f"type:{work_type}",
        "rows":    rows,
        "sort":    "is-referenced-by-count",
        "order":   "desc",
        "mailto":  MAILTO,
    }
    if query:
        params["query"] = query
    data = get(f"{BASE}/members/{member_id}/works", params)
    if not data:
        return []
    return data.get("message", {}).get("items", [])

def search_book_by_title(title, author=None):
    params = {
        "query.bibliographic": title,
        "filter": "type:book,type:monograph,type:edited-book",
        "rows": 3,
        "mailto": MAILTO,
    }
    if author:
        params["query.author"] = author
    data = get(f"{BASE}/works", params)
    if not data:
        return []
    return data.get("message", {}).get("items", [])

def title_str(work):
    t = work.get("title") or work.get("container-title") or []
    return t[0][:70] if t else "[no title]"

def year_str(work):
    for k in ("published", "published-print", "published-online"):
        p = work.get(k, {})
        parts = p.get("date-parts", [[]])
        if parts and parts[0]:
            return str(parts[0][0])
    return "?"

def people_str(work, field):
    ppl = work.get(field) or []
    names = []
    for p in ppl[:3]:
        g = (p.get("given") or "").strip()
        f = (p.get("family") or "").strip()
        names.append(f"{g} {f}".strip() if g else f)
    if len(work.get(field) or []) > 3:
        names.append("…")
    return "; ".join(names) if names else "—"

# ─────────────────────────────────────────────────────────────────────────────

print("=" * 72)
print("  PROBE: Routledge / SIUP / Pitt Press / OSU Press")
print("=" * 72)

results = {}

# ── 1. ROUTLEDGE / Taylor & Francis ──────────────────────────────────────────
print("\n── Routledge / Taylor & Francis (prefix 10.4324) ──")
tf_member = member_from_prefix("10.4324")
if tf_member:
    mid = tf_member["id"]
    print(f"  Member ID: {mid}  name: {tf_member['name']}")
    print(f"  Prefixes: {tf_member['prefixes'][:5]}")

    # Count rhet/comp-relevant books (use query narrowing)
    for btype in ("book", "monograph", "edited-book"):
        n_all = count_works(mid, btype)
        print(f"  {btype}: {n_all} total")

    # Sample rhet/comp books with query filter
    print("\n  Top cited rhet/comp books (query: rhetoric composition writing):")
    for btype in ("book", "edited-book"):
        items = sample_books(mid, btype, rows=8,
                             query="rhetoric composition writing technical communication")
        for w in items:
            cby = w.get("is-referenced-by-count", 0)
            pub = w.get("publisher", "")
            doi = w.get("DOI", "")
            has_ch = bool(w.get("relation", {}).get("has-part"))
            print(f"    [{year_str(w)}] {title_str(w)}")
            print(f"           type:{w.get('type','')}  cited:{cby}  doi:{doi}")
            print(f"           by/ed: {people_str(w,'editor') or people_str(w,'author')}")

    # Check chapter counts with query filter
    print("\n  Book-chapter count (ALL, no filter):")
    n_ch = count_works(mid, "book-chapter")
    print(f"  book-chapter (total): {n_ch}")
    print("  (Routledge has ~millions of chapters — must filter by series/query)")

    # Sample rhet/comp chapters
    print("\n  Sample rhet/comp chapters:")
    params = {
        "filter":  "type:book-chapter",
        "query":   "rhetoric composition writing pedagogy",
        "rows":    5,
        "sort":    "is-referenced-by-count",
        "order":   "desc",
        "mailto":  MAILTO,
    }
    d = get(f"{BASE}/members/{mid}/works", params)
    if d:
        for w in d.get("message", {}).get("items", [])[:5]:
            cby = w.get("is-referenced-by-count", 0)
            has_refs = len(w.get("reference", [])) > 0
            ct = (w.get("container-title") or ["?"])[0][:50]
            print(f"    [{year_str(w)}] {title_str(w)}")
            print(f"           in: {ct}  cited:{cby}  refs:{has_refs}")

    results["routledge"] = tf_member
else:
    print("  NOT FOUND via prefix 10.4324")
    results["routledge"] = None

# ── 2. SOUTHERN ILLINOIS UNIVERSITY PRESS ────────────────────────────────────
print("\n── Southern Illinois University Press ──")

# Try various name searches
siup_member = None
for q in ["southern illinois university press", "southern illinois university",
          "SIU Press", "Southern Illinois"]:
    items = search_member(q)
    for m in items:
        name = m.get("primary-name", "")
        if "southern illinois" in name.lower():
            siup_member = m
            break
    if siup_member:
        break

if not siup_member:
    # Try looking up a known SIUP title directly
    print("  Name search failed — trying known title lookup...")
    items = search_book_by_title("Rhetorical Listening", "Ratcliffe")
    for w in items:
        doi = w.get("DOI", "")
        pub = w.get("publisher", "")
        print(f"  Candidate: '{title_str(w)}' doi:{doi} pub:{pub}")
        if doi:
            prefix = doi.split("/")[0]
            print(f"  Trying prefix {prefix} ...")
            m = member_from_prefix(prefix)
            if m:
                print(f"  Found via prefix: ID={m['id']} name={m['name']}")
                siup_member = m
                break

    # Try Surrender by Restaino
    if not siup_member:
        items = search_book_by_title("Surrender Combating Burnout", "Restaino")
        for w in items:
            doi = w.get("DOI", "")
            pub = w.get("publisher", "")
            print(f"  Candidate: '{title_str(w)}' doi:{doi} pub:{pub}")
            if "illinois" in pub.lower() and doi:
                prefix = doi.split("/")[0]
                m = member_from_prefix(prefix)
                if m:
                    siup_member = m
                    break

    # Try Rhetoric Retold by Glenn
    if not siup_member:
        items = search_book_by_title("Rhetoric Retold Regendering", "Glenn")
        for w in items:
            doi = w.get("DOI", "")
            pub = w.get("publisher", "")
            print(f"  Candidate: '{title_str(w)}' doi:{doi} pub:{pub}")
            if doi:
                prefix = doi.split("/")[0]
                m = member_from_prefix(prefix)
                if m and "illinois" in m.get("name","").lower():
                    siup_member = m
                    break

if siup_member:
    mid = siup_member.get("id") or siup_member.get("member_id")
    if isinstance(siup_member, dict) and "message" in siup_member:
        siup_member = siup_member["message"]
        mid = siup_member.get("id")
    name = siup_member.get("primary-name", siup_member.get("name",""))
    prefixes = siup_member.get("prefixes", [])
    print(f"  Found: ID={mid}  name={name}  prefixes={prefixes}")
    for btype in ("book", "monograph", "edited-book", "book-chapter"):
        n = count_works(mid, btype)
        print(f"  {btype}: {n}")
    print("  Sample books (top cited):")
    for btype in ("book", "monograph", "edited-book"):
        items = sample_books(mid, btype, rows=5)
        for w in items:
            cby = w.get("is-referenced-by-count", 0)
            if cby > 0:
                print(f"    [{year_str(w)}] {title_str(w)}  cited:{cby}  doi:{w.get('DOI','')}")
    results["siup"] = {"id": mid, "name": name, "prefixes": prefixes}
else:
    print("  NOT FOUND — SIUP may not have a CrossRef membership")
    results["siup"] = None

# ── 3. UNIVERSITY OF PITTSBURGH PRESS ────────────────────────────────────────
print("\n── University of Pittsburgh Press ──")

pitt_member = None
for q in ["university of pittsburgh press", "pittsburgh university press",
          "University of Pittsburgh"]:
    items = search_member(q)
    for m in items:
        name = m.get("primary-name", "")
        if "pittsburgh" in name.lower():
            pitt_member = m
            break
    if pitt_member:
        break

if not pitt_member:
    print("  Name search failed — trying known title lookup...")
    for title, author in [
        ("Translingual Inheritance Language Families", "Kimball"),
        ("Shades of Sulh Peace Rhetoric", "Diab"),
        ("Reclaiming Rhetorica Women", "Lunsford"),
    ]:
        items = search_book_by_title(title, author)
        for w in items:
            doi = w.get("DOI", "")
            pub = w.get("publisher", "")
            print(f"  Candidate: '{title_str(w)}' doi:{doi} pub:{pub}")
            if "pittsburgh" in pub.lower() and doi:
                prefix = doi.split("/")[0]
                m = member_from_prefix(prefix)
                if m:
                    pitt_member = m
                    break
        if pitt_member:
            break

if pitt_member:
    mid = pitt_member.get("id") or pitt_member.get("member_id")
    name = pitt_member.get("primary-name", pitt_member.get("name",""))
    prefixes = pitt_member.get("prefixes", [])
    print(f"  Found: ID={mid}  name={name}  prefixes={prefixes}")
    for btype in ("book", "monograph", "edited-book", "book-chapter"):
        n = count_works(mid, btype)
        print(f"  {btype}: {n}")
    print("  Sample books (top cited):")
    for btype in ("book", "monograph", "edited-book"):
        items = sample_books(mid, btype, rows=5)
        for w in items:
            cby = w.get("is-referenced-by-count", 0)
            if cby > 0:
                print(f"    [{year_str(w)}] {title_str(w)}  cited:{cby}  doi:{w.get('DOI','')}")
    results["pitt"] = {"id": mid, "name": name, "prefixes": prefixes}
else:
    print("  NOT FOUND — Pitt Press may not have a CrossRef membership or uses a distributor")
    results["pitt"] = None

# ── 4. OHIO STATE UNIVERSITY PRESS ───────────────────────────────────────────
print("\n── Ohio State University Press ──")

osu_member = None
for q in ["ohio state university press", "ohio state press", "Ohio State University"]:
    items = search_member(q)
    for m in items:
        name = m.get("primary-name", "")
        if "ohio state" in name.lower():
            osu_member = m
            break
    if osu_member:
        break

if not osu_member:
    print("  Name search failed — trying known title lookup...")
    for title, author in [
        ("Vaccine Rhetorics", "Lawrence"),
        ("Inconvenient Strangers", "Yam"),
        ("Rhetoric and the Human Sciences", ""),
    ]:
        items = search_book_by_title(title, author)
        for w in items:
            doi = w.get("DOI", "")
            pub = w.get("publisher", "")
            print(f"  Candidate: '{title_str(w)}' doi:{doi} pub:{pub}")
            if "ohio" in pub.lower() and doi:
                prefix = doi.split("/")[0]
                m = member_from_prefix(prefix)
                if m:
                    osu_member = m
                    break
        if osu_member:
            break

if osu_member:
    mid = osu_member.get("id") or osu_member.get("member_id")
    name = osu_member.get("primary-name", osu_member.get("name",""))
    prefixes = osu_member.get("prefixes", [])
    print(f"  Found: ID={mid}  name={name}  prefixes={prefixes}")
    for btype in ("book", "monograph", "edited-book", "book-chapter"):
        n = count_works(mid, btype)
        print(f"  {btype}: {n}")
    print("  Sample books (top cited):")
    for btype in ("book", "monograph", "edited-book"):
        items = sample_books(mid, btype, rows=5)
        for w in items:
            cby = w.get("is-referenced-by-count", 0)
            if cby > 0:
                print(f"    [{year_str(w)}] {title_str(w)}  cited:{cby}  doi:{w.get('DOI','')}")
    results["osu"] = {"id": mid, "name": name, "prefixes": prefixes}
else:
    print("  NOT FOUND")
    results["osu"] = None

# ── Known SIUP/Pitt/OSU titles — direct DOI search ───────────────────────────
print("\n" + "=" * 72)
print("  DIRECT TITLE SEARCHES for known rhet/comp titles")
print("=" * 72)

KNOWN_TITLES = [
    # (title, author_last, expected_publisher)
    ("Rhetorical Listening Identification Contemplation",  "Ratcliffe",  "SIUP"),
    ("Surrender Combating Burnout",                        "Restaino",   "SIUP"),
    ("Situating Composition Composition Studies",          "Ede",        "SIUP"),
    ("Trust in Texts",                                     "Miller",     "SIUP"),
    ("Working in the Archives",                            "Ramsey",     "SIUP"),
    ("Translingual Inheritance",                           "Kimball",    "Pitt"),
    ("Shades of Sulh",                                     "Diab",       "Pitt"),
    ("Writing and Desire",                                 "Alexander",  "Pitt"),
    ("Resisting Brown",                                    "Epps-Robertson", "Pitt"),
    ("Vaccine Rhetorics",                                  "Lawrence",   "OSU"),
    ("Inconvenient Strangers",                             "Yam",        "OSU"),
    ("Naming What We Know",                                "Adler-Kassner", "USU"),
    ("Antiracist Writing Assessment Ecologies",            "Inoue",      "WAC"),
]

for title, author, expected in KNOWN_TITLES:
    params = {
        "query.bibliographic": title,
        "rows": 3,
        "mailto": MAILTO,
    }
    if author:
        params["query.author"] = author
    d = get(f"{BASE}/works", params)
    if not d:
        print(f"\n  [{expected}] {title[:50]} — API error")
        continue
    items = d.get("message", {}).get("items", [])
    if not items:
        print(f"\n  [{expected}] {title[:50]} — NOT FOUND in CrossRef")
        continue
    w = items[0]
    doi = w.get("DOI", "")
    pub = w.get("publisher", "")
    wtype = w.get("type", "")
    cby = w.get("is-referenced-by-count", 0)
    prefix = doi.split("/")[0] if "/" in doi else ""
    print(f"\n  [{expected}] {title[:50]}")
    print(f"    title:  {title_str(w)}")
    print(f"    pub:    {pub}")
    print(f"    type:   {wtype}  doi: {doi}  cited: {cby}")
    if prefix and prefix != "10.7330" and prefix != "10.37514":
        m = member_from_prefix(prefix)
        if m:
            print(f"    → member: ID={m['id']} name={m['name']} prefixes={m['prefixes'][:3]}")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
print("  SUMMARY")
print("=" * 72)
for pub, info in results.items():
    if info:
        mid = info.get("id") or info.get("member_id")
        name = info.get("name","")
        prefixes = info.get("prefixes", [])
        print(f"  {pub.upper():10s}: ID={mid}  name={name}  prefixes={prefixes[:3]}")
    else:
        print(f"  {pub.upper():10s}: NOT FOUND")

print("\nDone.")
