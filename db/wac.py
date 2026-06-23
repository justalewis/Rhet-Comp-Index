"""db.wac — Query layer for the WAC Clearinghouse publisher dashboard (/wac).

Reads the denormalized wac_works + wac_authors tables (built by ingest_wac.py
from a CrossRef harvest of DOI prefix 10.37514). Everything here is READ-ONLY.

The whole point is to profile the WAC Clearinghouse AS A PRESS: the entire
catalog — journal articles, book chapters, edited collections, monographs —
seen together, which the article/journal-centric rest of Pinakes cannot do.

No citation-network analysis lives here: WAC deposits almost no outbound
references, so there are no who-cites-whom edges to draw. Everything below is
built from authors, editors, affiliations, dates, types, venues, parent-book
linkage, and inbound citation counts.
"""

from __future__ import annotations

import re
import collections
import statistics

from .core import get_conn

# Work types that carry authored content (exclude the 14 journal-level records).
_CONTENT_TYPES = ("journal-article", "book-chapter", "edited-book", "monograph")
_BOOK_TYPES = ("edited-book", "monograph")


# ── small display helpers ────────────────────────────────────────────────────

def _short_journal(name):
    """A compact label for a journal: text before the first colon, capped."""
    if not name:
        return name
    short = name.split(":")[0].strip()
    return short if len(short) <= 42 else short[:40] + "…"


# Title-term stopwords: function words + a few publishing-generic nouns. Kept
# deliberately small so domain words ("writing", "rhetoric", "literacy") survive
# — they ARE the signal for this press.
_STOPWORDS = set("""
a an the and or but of for to in on at by with from as into over under between
this that these those is are was were be been being it its their his her our your
we i you they them he she who whom whose which what when where why how than then so
not no nor can will would should could may might must do does did done has have had
about across after again against all also among any because before both during each
few more most other some such only own same too very s t re ve ll d m o
toward towards within without per via vs via using use used new case study studies
introduction chapter part section foreword afterword preface essay essays review
reviews response responses note notes volume special issue editor editors edited
""".split())

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]+")


# ── overview / KPIs ──────────────────────────────────────────────────────────

def wac_overview():
    """Headline counts for the dashboard hero."""
    with get_conn() as conn:
        type_counts = {r["type"]: r["n"] for r in conn.execute(
            "SELECT type, COUNT(*) n FROM wac_works GROUP BY type"
        ).fetchall()}
        n_journals = conn.execute(
            "SELECT COUNT(DISTINCT journal) FROM wac_works "
            "WHERE type='journal-article' AND journal IS NOT NULL"
        ).fetchone()[0]
        n_authors = conn.execute(
            "SELECT COUNT(DISTINCT name) FROM wac_authors WHERE is_person=1 AND role='author'"
        ).fetchone()[0]
        n_institutions = conn.execute(
            "SELECT COUNT(DISTINCT lower(institution)) FROM wac_authors "
            "WHERE institution IS NOT NULL AND is_person=1"
        ).fetchone()[0]
        yr = conn.execute(
            "SELECT MIN(year), MAX(year) FROM wac_works WHERE year IS NOT NULL"
        ).fetchone()
        total_cites = conn.execute(
            "SELECT COALESCE(SUM(cited_by),0) FROM wac_works"
        ).fetchone()[0]
        ph = ",".join("?" * len(_CONTENT_TYPES))
        total = conn.execute(
            f"SELECT COUNT(*) FROM wac_works WHERE type IN ({ph})", _CONTENT_TYPES
        ).fetchone()[0]
        cited_works = conn.execute(
            f"SELECT COUNT(*) FROM wac_works WHERE cited_by > 0 AND type IN ({ph})",
            _CONTENT_TYPES
        ).fetchone()[0]

    return {
        "total_works":   total,
        "cited_works":   cited_works,
        "journal_articles": type_counts.get("journal-article", 0),
        "chapters":      type_counts.get("book-chapter", 0),
        "edited_books":  type_counts.get("edited-book", 0),
        "monographs":    type_counts.get("monograph", 0),
        "journals":      n_journals,
        "authors":       n_authors,
        "institutions":  n_institutions,
        "total_citations": total_cites,
        "year_min":      yr[0],
        "year_max":      yr[1],
    }


# ── catalog over time ────────────────────────────────────────────────────────

_TYPE_LABEL = {
    "journal-article": "Journal articles",
    "book-chapter":    "Book chapters",
    "edited-book":     "Edited collections",
    "monograph":       "Monographs",
}
_TYPE_ORDER = ["journal-article", "book-chapter", "edited-book", "monograph"]


def wac_timeline():
    """Works per year, split by type — the press's output and format mix over
    time. Returns {years, series:[{type,label,counts}]}."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT year, type, COUNT(*) n FROM wac_works "
            "WHERE year IS NOT NULL AND type IN ('journal-article','book-chapter','edited-book','monograph') "
            "GROUP BY year, type"
        ).fetchall()
    if not rows:
        return {"years": [], "series": []}
    years = sorted({r["year"] for r in rows})
    by_type = {t: {y: 0 for y in years} for t in _TYPE_ORDER}
    for r in rows:
        by_type[r["type"]][r["year"]] = r["n"]
    series = [
        {"type": t, "label": _TYPE_LABEL[t], "counts": [by_type[t][y] for y in years]}
        for t in _TYPE_ORDER
    ]
    return {"years": years, "series": series}


def wac_format_composition():
    """Donut of the catalog by type, plus the journal-article split by venue."""
    with get_conn() as conn:
        types = [
            {"type": r["type"], "label": _TYPE_LABEL.get(r["type"], r["type"]), "count": r["n"]}
            for r in conn.execute(
                "SELECT type, COUNT(*) n FROM wac_works "
                "WHERE type IN ('journal-article','book-chapter','edited-book','monograph') "
                "GROUP BY type ORDER BY n DESC"
            ).fetchall()
        ]
        journals = [
            {"journal": _short_journal(r["journal"]), "full": r["journal"], "count": r["n"]}
            for r in conn.execute(
                "SELECT journal, COUNT(*) n FROM wac_works "
                "WHERE type='journal-article' AND journal IS NOT NULL "
                "GROUP BY journal ORDER BY n DESC"
            ).fetchall()
        ]
    return {"types": types, "journals": journals}


def wac_journals():
    """Per-journal roster: output, active span, citations — for a venue table.
    Flags journals whose latest article predates 2018 as 'historical'."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT journal, COUNT(*) n, MIN(year) y0, MAX(year) y1, "
            "       COALESCE(SUM(cited_by),0) cites "
            "FROM wac_works WHERE type='journal-article' AND journal IS NOT NULL "
            "GROUP BY journal ORDER BY n DESC"
        ).fetchall()
    out = []
    for r in rows:
        out.append({
            "journal": r["journal"],
            "short":   _short_journal(r["journal"]),
            "count":   r["n"],
            "year_min": r["y0"],
            "year_max": r["y1"],
            "citations": r["cites"],
            "historical": bool(r["y1"] and r["y1"] < 2018),
        })
    return out


# ── inbound influence (the WAC canon) ────────────────────────────────────────

def _authors_for(conn, doi, role="author", limit=6):
    rows = conn.execute(
        "SELECT name FROM wac_authors WHERE work_doi=? AND role=? ORDER BY seq LIMIT ?",
        (doi, role, limit)
    ).fetchall()
    return [r["name"] for r in rows]


def wac_most_cited(limit=40, work_type=None):
    """Top works by inbound CrossRef citations, across all formats."""
    params = []
    where = "cited_by > 0 AND type IN ('journal-article','book-chapter','edited-book','monograph')"
    if work_type in _CONTENT_TYPES:
        where += " AND type = ?"
        params.append(work_type)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT doi, title, type, journal, container, year, cited_by, url "
            f"FROM wac_works WHERE {where} ORDER BY cited_by DESC LIMIT ?",
            params + [limit]
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["label"] = _TYPE_LABEL.get(r["type"], r["type"])
            d["venue"] = _short_journal(r["journal"]) if r["journal"] else r["container"]
            d["authors"] = "; ".join(_authors_for(conn, r["doi"])) or None
            out.append(d)
    return out


# ── authors as a press-wide phenomenon ───────────────────────────────────────

def wac_house_authors(limit=30):
    """Most-published authors across ALL formats, with a per-format breakdown
    and the total inbound citations their work has drawn. Persons only."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT a.name,
                   SUM(CASE WHEN w.type='journal-article' THEN 1 ELSE 0 END) AS articles,
                   SUM(CASE WHEN w.type='book-chapter'     THEN 1 ELSE 0 END) AS chapters,
                   SUM(CASE WHEN w.type='monograph'        THEN 1 ELSE 0 END) AS monographs,
                   SUM(CASE WHEN w.type='edited-book'      THEN 1 ELSE 0 END) AS edited_as_author,
                   COUNT(DISTINCT a.work_doi)              AS total,
                   COALESCE(SUM(w.cited_by),0)             AS citations,
                   MIN(w.year) AS y0, MAX(w.year) AS y1
            FROM wac_authors a
            JOIN wac_works w ON w.doi = a.work_doi
            WHERE a.is_person=1 AND a.role='author' AND w.type IN
                  ('journal-article','book-chapter','edited-book','monograph')
            GROUP BY a.name
            ORDER BY total DESC, citations DESC
            LIMIT ?
        """, (limit,)).fetchall()
        # editor credits (separate, since editing is a distinct contribution)
        ed = {r["name"]: r["n"] for r in conn.execute("""
            SELECT name, COUNT(DISTINCT work_doi) n FROM wac_authors
            WHERE is_person=1 AND role='editor' GROUP BY name
        """).fetchall()}
    out = []
    for r in rows:
        d = dict(r)
        d["edited"] = ed.get(r["name"], 0)
        out.append(d)
    return out


def wac_cross_format_authors(limit=40, min_types=2):
    """Authors whose WAC portfolio spans multiple formats — the press-native
    view a single journal cannot show. Returns per-author format counts and the
    distinct number of formats they've used."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT a.name,
                   SUM(CASE WHEN w.type='journal-article' THEN 1 ELSE 0 END) AS articles,
                   SUM(CASE WHEN w.type='book-chapter'     THEN 1 ELSE 0 END) AS chapters,
                   SUM(CASE WHEN w.type='monograph'        THEN 1 ELSE 0 END) AS monographs,
                   SUM(CASE WHEN w.type='edited-book'      THEN 1 ELSE 0 END) AS edited,
                   COUNT(DISTINCT a.work_doi) AS total,
                   COALESCE(SUM(w.cited_by),0) AS citations
            FROM wac_authors a
            JOIN wac_works w ON w.doi = a.work_doi
            WHERE a.is_person=1 AND a.role='author' AND w.type IN
                  ('journal-article','book-chapter','edited-book','monograph')
            GROUP BY a.name
        """).fetchall()
    out = []
    for r in rows:
        formats = sum(1 for k in ("articles", "chapters", "monographs", "edited") if r[k] > 0)
        if formats >= min_types:
            d = dict(r)
            d["formats"] = formats
            out.append(d)
    out.sort(key=lambda d: (-d["formats"], -d["total"], -d["citations"]))
    return out[:limit]


def _author_works_map(conn, types=_CONTENT_TYPES, role="author", persons_only=True):
    """Return {work_doi: [author_name, ...]} restricted to the given types."""
    ph = ",".join("?" * len(types))
    sql = (
        f"SELECT a.work_doi, a.name FROM wac_authors a "
        f"JOIN wac_works w ON w.doi = a.work_doi "
        f"WHERE a.role = ? AND w.type IN ({ph})"
    )
    if persons_only:
        sql += " AND a.is_person = 1"
    rows = conn.execute(sql, [role] + list(types)).fetchall()
    m = collections.defaultdict(list)
    for r in rows:
        m[r["work_doi"]].append(r["name"])
    return m


def wac_coauthorship(min_works=2, top_n=160):
    """Co-authorship network across the whole press (articles + chapters +
    books). Nodes are authors (sized by work count); edges are shared works."""
    with get_conn() as conn:
        work_authors = _author_works_map(conn)
    work_count = collections.Counter()
    pair_count = collections.Counter()
    for doi, names in work_authors.items():
        uniq = sorted(set(names))
        for n in uniq:
            work_count[n] += 1
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                pair_count[(uniq[i], uniq[j])] += 1
    keep = {n for n, c in work_count.items() if c >= min_works}
    # rank kept authors by work count, cap to top_n
    ranked = sorted(keep, key=lambda n: -work_count[n])[:top_n]
    kept = set(ranked)
    nodes = [{"id": n, "count": work_count[n]} for n in ranked]
    links = [
        {"source": a, "target": b, "value": v}
        for (a, b), v in pair_count.items()
        if a in kept and b in kept
    ]
    return {"nodes": nodes, "links": links}


def wac_lasting_partnerships(min_joint=3, limit=40):
    """Author pairs who have co-published repeatedly across the catalog."""
    with get_conn() as conn:
        work_authors = _author_works_map(conn)
        years = {r["doi"]: r["year"] for r in conn.execute(
            "SELECT doi, year FROM wac_works").fetchall()}
    pairs = collections.defaultdict(list)   # (a,b) -> [years]
    for doi, names in work_authors.items():
        uniq = sorted(set(names))
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                pairs[(uniq[i], uniq[j])].append(years.get(doi))
    out = []
    for (a, b), ys in pairs.items():
        ys2 = [y for y in ys if y]
        if len(ys) >= min_joint:
            out.append({
                "a": a, "b": b, "joint_works": len(ys),
                "year_min": min(ys2) if ys2 else None,
                "year_max": max(ys2) if ys2 else None,
                "span": (max(ys2) - min(ys2)) if ys2 else 0,
            })
    out.sort(key=lambda d: (-d["joint_works"], -d["span"]))
    return out[:limit]


# ── editors & collections (book-native, no references needed) ────────────────

def wac_editor_network(top_n=220):
    """Bipartite editor → contributor network. An edge links an editor of an
    edited collection to each author of a chapter in that collection.
    Nodes carry role='editor'|'author' (an editor who also wrote chapters is
    tagged 'both')."""
    with get_conn() as conn:
        # editors per edited-book
        ed_rows = conn.execute("""
            SELECT a.work_doi AS book_doi, a.name
            FROM wac_authors a JOIN wac_works w ON w.doi=a.work_doi
            WHERE a.role='editor' AND a.is_person=1 AND w.type='edited-book'
        """).fetchall()
        editors_by_book = collections.defaultdict(set)
        for r in ed_rows:
            editors_by_book[r["book_doi"]].add(r["name"])
        # chapter authors grouped by parent book
        ch_rows = conn.execute("""
            SELECT w.parent_doi AS book_doi, a.name
            FROM wac_works w JOIN wac_authors a ON a.work_doi=w.doi
            WHERE w.type='book-chapter' AND w.parent_doi IS NOT NULL
                  AND a.role='author' AND a.is_person=1
        """).fetchall()
        authors_by_book = collections.defaultdict(set)
        for r in ch_rows:
            authors_by_book[r["book_doi"]].add(r["name"])

    editor_names = set()
    author_names = collections.Counter()
    edges = collections.Counter()
    for book_doi, editors in editors_by_book.items():
        chap_authors = authors_by_book.get(book_doi, [])
        for ed in editors:
            editor_names.add(ed)
            for au in chap_authors:
                if au == ed:
                    continue
                author_names[au] += 1
                edges[(ed, au)] += 1
    # rank authors by how many editor-links they have, cap
    top_authors = {a for a, _ in author_names.most_common(top_n)}
    nodes = []
    seen = set()
    for ed in editor_names:
        role = "both" if ed in author_names else "editor"
        nodes.append({"id": ed, "role": role,
                      "weight": sum(1 for (e, a) in edges if e == ed)})
        seen.add(ed)
    for au in top_authors:
        if au in seen:
            continue
        nodes.append({"id": au, "role": "author", "weight": author_names[au]})
        seen.add(au)
    links = [{"source": e, "target": a, "value": v}
             for (e, a), v in edges.items() if a in top_authors or a in editor_names]
    return {"nodes": nodes, "links": links}


def wac_copresence(min_shared=1, top_n=180):
    """Co-presence network: two authors are linked if they have chapters in the
    same edited collection. Edge weight = number of shared collections. A
    citation-free analogue of co-authorship that the edited-book corpus makes
    possible."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT w.parent_doi AS book_doi, a.name
            FROM wac_works w JOIN wac_authors a ON a.work_doi=w.doi
            WHERE w.type='book-chapter' AND w.parent_doi IS NOT NULL
                  AND a.role='author' AND a.is_person=1
        """).fetchall()
    authors_by_book = collections.defaultdict(set)
    for r in rows:
        authors_by_book[r["book_doi"]].add(r["name"])
    coll_count = collections.Counter()
    pair_shared = collections.Counter()
    for book_doi, authors in authors_by_book.items():
        al = sorted(authors)
        for n in al:
            coll_count[n] += 1
        for i in range(len(al)):
            for j in range(i + 1, len(al)):
                pair_shared[(al[i], al[j])] += 1
    # keep authors who appear in the most collections
    ranked = [n for n, _ in coll_count.most_common(top_n)]
    kept = set(ranked)
    nodes = [{"id": n, "count": coll_count[n]} for n in ranked]
    links = [{"source": a, "target": b, "value": v}
             for (a, b), v in pair_shared.items()
             if v >= min_shared and a in kept and b in kept]
    return {"nodes": nodes, "links": links}


def wac_collections(limit=120):
    """Edited collections with chapter counts, editors, and citations — the
    spine of the collection explorer."""
    with get_conn() as conn:
        books = conn.execute("""
            SELECT doi, title, year, cited_by FROM wac_works
            WHERE type='edited-book' ORDER BY year DESC, title
        """).fetchall()
        out = []
        for b in books:
            editors = _authors_for(conn, b["doi"], role="editor", limit=8)
            n_ch = conn.execute(
                "SELECT COUNT(*) FROM wac_works WHERE type='book-chapter' AND parent_doi=?",
                (b["doi"],)
            ).fetchone()[0]
            n_au = conn.execute("""
                SELECT COUNT(DISTINCT a.name) FROM wac_works c
                JOIN wac_authors a ON a.work_doi=c.doi
                WHERE c.type='book-chapter' AND c.parent_doi=? AND a.role='author' AND a.is_person=1
            """, (b["doi"],)).fetchone()[0]
            out.append({
                "doi": b["doi"], "title": b["title"], "year": b["year"],
                "cited_by": b["cited_by"], "n_chapters": n_ch,
                "distinct_authors": n_au,
                "editors": "; ".join(editors) or None,
            })
    out.sort(key=lambda d: (-(d["n_chapters"] or 0), -(d["year"] or 0)))
    return out[:limit]


def wac_collection_chapters(doi):
    """Chapters of one edited collection, with authors — for the explorer drill."""
    with get_conn() as conn:
        book = conn.execute(
            "SELECT doi, title, year, isbn FROM wac_works WHERE doi=? AND type='edited-book'",
            (doi,)
        ).fetchone()
        if not book:
            return None
        editors = _authors_for(conn, doi, role="editor", limit=12)
        chap = conn.execute("""
            SELECT doi, title, pages, cited_by FROM wac_works
            WHERE type='book-chapter' AND parent_doi=? ORDER BY doi
        """, (doi,)).fetchall()
        chapters = []
        for c in chap:
            chapters.append({
                "doi": c["doi"], "title": c["title"], "pages": c["pages"],
                "cited_by": c["cited_by"],
                "authors": "; ".join(_authors_for(conn, c["doi"])) or None,
            })
    return {
        "doi": book["doi"], "title": book["title"], "year": book["year"],
        "isbn": book["isbn"], "editors": "; ".join(editors) or None,
        "chapters": chapters,
    }


def wac_collection_anatomy():
    """How edited collections are built: chapters-per-volume histogram, the
    monograph-vs-collection split, and the median collection size."""
    with get_conn() as conn:
        sizes = [r["n"] for r in conn.execute("""
            SELECT b.doi, COUNT(c.doi) n FROM wac_works b
            LEFT JOIN wac_works c ON c.parent_doi=b.doi AND c.type='book-chapter'
            WHERE b.type='edited-book' GROUP BY b.doi
        """).fetchall()]
        n_mono = conn.execute("SELECT COUNT(*) FROM wac_works WHERE type='monograph'").fetchone()[0]
        n_edit = conn.execute("SELECT COUNT(*) FROM wac_works WHERE type='edited-book'").fetchone()[0]
    sizes_nonzero = sorted(s for s in sizes if s > 0)
    median = round(statistics.median(sizes_nonzero)) if sizes_nonzero else 0
    # histogram buckets
    hist = collections.Counter()
    for s in sizes:
        if s == 0:
            hist["0"] += 1
        elif s <= 5:
            hist["1-5"] += 1
        elif s <= 10:
            hist["6-10"] += 1
        elif s <= 15:
            hist["11-15"] += 1
        elif s <= 20:
            hist["16-20"] += 1
        else:
            hist["21+"] += 1
    order = ["0", "1-5", "6-10", "11-15", "16-20", "21+"]
    return {
        "histogram": [{"bucket": b, "count": hist.get(b, 0)} for b in order],
        "monographs": n_mono, "edited_collections": n_edit,
        "median_chapters": median,
        "max_chapters": max(sizes) if sizes else 0,
    }


# ── institutions ─────────────────────────────────────────────────────────────

def wac_institutions(limit=30):
    """Top contributing institutions (derived from raw CrossRef affiliation
    strings). Counts distinct works and distinct authors per institution."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT institution,
                   COUNT(DISTINCT work_doi) AS works,
                   COUNT(DISTINCT name)     AS authors
            FROM wac_authors
            WHERE institution IS NOT NULL AND is_person=1
            GROUP BY lower(institution)
            ORDER BY works DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def wac_institution_timeline(top_n=8):
    """Decade-by-decade output of the top institutions — institutional
    concentration vs diffusion over the press's life."""
    with get_conn() as conn:
        top = [r["institution"] for r in conn.execute("""
            SELECT institution, COUNT(DISTINCT work_doi) w FROM wac_authors
            WHERE institution IS NOT NULL AND is_person=1
            GROUP BY lower(institution) ORDER BY w DESC LIMIT ?
        """, (top_n,)).fetchall()]
        if not top:
            return {"decades": [], "series": []}
        rows = conn.execute("""
            SELECT a.institution AS inst, (w.year/10)*10 AS decade,
                   COUNT(DISTINCT a.work_doi) n
            FROM wac_authors a JOIN wac_works w ON w.doi=a.work_doi
            WHERE a.institution IS NOT NULL AND a.is_person=1 AND w.year IS NOT NULL
            GROUP BY lower(a.institution), decade
        """).fetchall()
    topset = {t.lower() for t in top}
    decades = sorted({r["decade"] for r in rows})
    agg = {t: {d: 0 for d in decades} for t in top}
    # map lower->display
    disp = {t.lower(): t for t in top}
    for r in rows:
        key = r["inst"].lower()
        if key in topset:
            agg[disp[key]][r["decade"]] += r["n"]
    series = [{"institution": t, "counts": [agg[t][d] for d in decades]} for t in top]
    return {"decades": decades, "series": series}


# ── topics & titles (text proxy: there are no abstracts) ─────────────────────

def wac_topics(limit=30):
    """Auto-tag frequency across the catalog (tags are derived from titles
    against a controlled vocabulary; coverage is partial)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT tags FROM wac_works WHERE tags IS NOT NULL AND tags != ''"
        ).fetchall()
    freq = collections.Counter()
    for r in rows:
        for t in [x for x in r["tags"].split("|") if x]:
            freq[t] += 1
    return [{"tag": t, "count": c} for t, c in freq.most_common(limit)]


def wac_topic_trends(top_n=8):
    """Share of the top tags by decade — how the press's subject focus shifts."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT tags, (year/10)*10 AS decade FROM wac_works
            WHERE tags IS NOT NULL AND tags!='' AND year IS NOT NULL
        """).fetchall()
    overall = collections.Counter()
    by_decade = collections.defaultdict(collections.Counter)
    for r in rows:
        tags = [x for x in r["tags"].split("|") if x]
        for t in tags:
            overall[t] += 1
            by_decade[r["decade"]][t] += 1
    top = [t for t, _ in overall.most_common(top_n)]
    decades = sorted(by_decade)
    series = [{"tag": t, "counts": [by_decade[d].get(t, 0) for d in decades]} for t in top]
    return {"decades": decades, "series": series}


def wac_title_terms(top_n=60, min_year=None):
    """Most frequent meaningful words in work titles (no abstracts exist, so
    titles are the text signal). Also returns a coarse early/late split so the
    UI can show which terms are rising."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT title, year FROM wac_works WHERE title IS NOT NULL "
            "AND type IN ('journal-article','book-chapter','edited-book','monograph')"
        ).fetchall()
    freq = collections.Counter()
    early = collections.Counter()
    late = collections.Counter()
    split_year = 2012
    for r in rows:
        words = {w.lower() for w in _WORD_RE.findall(r["title"])}
        for w in words:
            if w in _STOPWORDS or len(w) < 3:
                continue
            freq[w] += 1
            if r["year"]:
                (late if r["year"] >= split_year else early)[w] += 1
    out = []
    for w, c in freq.most_common(top_n):
        out.append({
            "term": w, "count": c,
            "early": early[w], "late": late[w],
        })
    return {"split_year": split_year, "terms": out}


def wac_team_size():
    """Solo vs collaborative authorship over time. For each decade, the share
    of authored works with 1, 2, 3, or 4+ authors."""
    buckets = ["1", "2", "3", "4+"]
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT (year/10)*10 AS decade, n_authors FROM wac_works
            WHERE year IS NOT NULL AND n_authors > 0
                  AND type IN ('journal-article','book-chapter','edited-book','monograph')
        """).fetchall()
    by_decade = collections.defaultdict(lambda: collections.Counter())
    for r in rows:
        n = r["n_authors"]
        key = "1" if n == 1 else "2" if n == 2 else "3" if n == 3 else "4+"
        by_decade[r["decade"]][key] += 1
    decades = sorted(by_decade)
    series = []
    for b in buckets:
        series.append({"bucket": b, "counts": [by_decade[d][b] for d in decades]})
    totals = [sum(by_decade[d].values()) for d in decades]
    return {"decades": decades, "series": series, "totals": totals}


# ── journal lifelines (masthead timeline) ────────────────────────────────────

# Editorial annotations the data can't derive (predecessor → successor lines).
_JOURNAL_SUCCESSION = {
    "Language and Learning Across the Disciplines": "Across the Disciplines",
    "Academic.Writing: Interdisciplinary Perspectives on Communication Across the Curriculum": "Across the Disciplines",
}


def wac_journal_lifelines():
    """One lane per journal: first→last article year plus a per-year volume
    ribbon. Ordered by launch year. Flags historical venues + succession."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT journal, year, COUNT(*) n FROM wac_works
            WHERE type='journal-article' AND journal IS NOT NULL AND year IS NOT NULL
            GROUP BY journal, year
        """).fetchall()
    by_journal = collections.defaultdict(dict)
    for r in rows:
        by_journal[r["journal"]][r["year"]] = r["n"]
    out = []
    for jrnl, yrmap in by_journal.items():
        yrs = sorted(yrmap)
        out.append({
            "journal": jrnl,
            "short": _short_journal(jrnl),
            "year_min": yrs[0],
            "year_max": yrs[-1],
            "total": sum(yrmap.values()),
            "counts": [{"year": y, "n": yrmap[y]} for y in yrs],
            "historical": yrs[-1] < 2018,
            "succeeded_by": _JOURNAL_SUCCESSION.get(jrnl),
        })
    out.sort(key=lambda d: (d["year_min"], -d["total"]))
    return out


# ── book ↔ journal crossover (set membership) ────────────────────────────────

def wac_book_journal_crossover():
    """For every person, does their WAC footprint sit on the journal side, the
    book side, or both? Returns the three bucket counts + the top crossover
    authors. This is name-string set overlap, not identity-verified."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT a.name, w.type, w.cited_by FROM wac_authors a
            JOIN wac_works w ON w.doi=a.work_doi
            WHERE a.is_person=1 AND a.role='author'
              AND w.type IN ('journal-article','book-chapter','edited-book','monograph')
        """).fetchall()
    side = collections.defaultdict(lambda: {"j": 0, "b": 0, "cites": 0})
    for r in rows:
        s = side[r["name"]]
        if r["type"] == "journal-article":
            s["j"] += 1
        else:
            s["b"] += 1
        s["cites"] += r["cited_by"] or 0
    journal_only = book_only = both = 0
    crossover = []
    for name, s in side.items():
        if s["j"] and s["b"]:
            both += 1
            crossover.append({"name": name, "articles": s["j"], "books": s["b"],
                              "total": s["j"] + s["b"], "citations": s["cites"]})
        elif s["j"]:
            journal_only += 1
        else:
            book_only += 1
    crossover.sort(key=lambda d: (-d["total"], -d["citations"]))
    return {
        "journal_only": journal_only, "both": both, "book_only": book_only,
        "top_crossover": crossover[:40],
    }


def wac_author_spans(min_works=5, limit=45):
    """Career-span lollipops: prolific authors with one dot per work at its year,
    colored by format. Sorted by span length."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT a.name, w.year, w.type, w.title FROM wac_authors a
            JOIN wac_works w ON w.doi=a.work_doi
            WHERE a.is_person=1 AND a.role='author' AND w.year IS NOT NULL
              AND w.type IN ('journal-article','book-chapter','edited-book','monograph')
        """).fetchall()
    by_author = collections.defaultdict(list)
    for r in rows:
        by_author[r["name"]].append({"year": r["year"], "type": r["type"]})
    out = []
    for name, works in by_author.items():
        if len(works) < min_works:
            continue
        yrs = [w["year"] for w in works]
        out.append({
            "name": name, "year_min": min(yrs), "year_max": max(yrs),
            "span": max(yrs) - min(yrs), "n": len(works),
            "works": sorted(works, key=lambda w: w["year"]),
        })
    out.sort(key=lambda d: (-d["span"], -d["n"]))
    return out[:limit]


# ── editors as brokers ───────────────────────────────────────────────────────

def wac_editor_brokers(limit=40):
    """Editors ranked by volumes edited and distinct chapter-authors convened.
    Each carries its volume list (with chapter authors) for inline expansion."""
    with get_conn() as conn:
        ed_rows = conn.execute("""
            SELECT a.name, a.work_doi AS book_doi, w.title, w.year
            FROM wac_authors a JOIN wac_works w ON w.doi=a.work_doi
            WHERE a.role='editor' AND a.is_person=1 AND w.type='edited-book'
        """).fetchall()
        # chapter authors per parent book
        ch_rows = conn.execute("""
            SELECT w.parent_doi AS book_doi, a.name
            FROM wac_works w JOIN wac_authors a ON a.work_doi=w.doi
            WHERE w.type='book-chapter' AND w.parent_doi IS NOT NULL
              AND a.role='author' AND a.is_person=1
        """).fetchall()
    authors_by_book = collections.defaultdict(set)
    for r in ch_rows:
        authors_by_book[r["book_doi"]].add(r["name"])
    by_editor = collections.defaultdict(lambda: {"volumes": [], "authors": set()})
    for r in ed_rows:
        e = by_editor[r["name"]]
        chap_authors = authors_by_book.get(r["book_doi"], set())
        e["volumes"].append({"doi": r["book_doi"], "title": r["title"],
                             "year": r["year"], "n_chapters": len(chap_authors)})
        e["authors"] |= chap_authors
    out = []
    for name, e in by_editor.items():
        out.append({
            "name": name,
            "volumes": len(e["volumes"]),
            "authors_convened": len(e["authors"]),
            "volume_list": sorted(e["volumes"], key=lambda v: -(v["year"] or 0)),
        })
    out.sort(key=lambda d: (-d["volumes"], -d["authors_convened"]))
    return out[:limit]


def wac_editor_author_overlap():
    """The broker/author duality: how many names edit, how many write, and how
    many do both — plus the people most active in both roles."""
    with get_conn() as conn:
        editors = {r["name"]: r["n"] for r in conn.execute("""
            SELECT name, COUNT(DISTINCT work_doi) n FROM wac_authors
            WHERE role='editor' AND is_person=1 GROUP BY name
        """).fetchall()}
        authors = {r["name"]: r["n"] for r in conn.execute("""
            SELECT a.name, COUNT(DISTINCT a.work_doi) n FROM wac_authors a
            JOIN wac_works w ON w.doi=a.work_doi
            WHERE a.role='author' AND a.is_person=1
              AND w.type IN ('journal-article','book-chapter','edited-book','monograph')
            GROUP BY a.name
        """).fetchall()}
    both = set(editors) & set(authors)
    members = [{"name": n, "edited": editors[n], "authored": authors[n],
                "total": editors[n] + authors[n]} for n in both]
    members.sort(key=lambda d: -d["total"])
    return {
        "editors_total": len(editors),
        "authors_total": len(authors),
        "both": len(both),
        "editor_only": len(set(editors) - set(authors)),
        "members": members[:40],
    }


# ── institutions × journals + coverage honesty ───────────────────────────────

def wac_institution_journal(top_inst=22):
    """Heatmap matrix: which institutions cluster in which journals. Cells =
    distinct journal-articles by an author at that institution."""
    with get_conn() as conn:
        top = [r["institution"] for r in conn.execute("""
            SELECT a.institution, COUNT(DISTINCT a.work_doi) w
            FROM wac_authors a JOIN wac_works k ON k.doi=a.work_doi
            WHERE a.institution IS NOT NULL AND a.is_person=1 AND k.type='journal-article'
            GROUP BY lower(a.institution) ORDER BY w DESC LIMIT ?
        """, (top_inst,)).fetchall()]
        if not top:
            return {"institutions": [], "journals": [], "cells": []}
        topset = {t.lower() for t in top}
        disp = {t.lower(): t for t in top}
        rows = conn.execute("""
            SELECT a.institution AS inst, w.journal AS journal,
                   COUNT(DISTINCT a.work_doi) n
            FROM wac_authors a JOIN wac_works w ON w.doi=a.work_doi
            WHERE a.institution IS NOT NULL AND a.is_person=1
              AND w.type='journal-article' AND w.journal IS NOT NULL
            GROUP BY lower(a.institution), w.journal
        """).fetchall()
    journ_totals = collections.Counter()
    cellmap = collections.defaultdict(int)
    for r in rows:
        key = r["inst"].lower()
        if key in topset:
            cellmap[(disp[key], r["journal"])] += r["n"]
            journ_totals[r["journal"]] += r["n"]
    journals = [j for j, _ in journ_totals.most_common()]
    cells = [{"institution": inst, "journal": _short_journal(j), "value": v}
             for (inst, j), v in cellmap.items()]
    return {
        "institutions": top,
        "journals": [_short_journal(j) for j in journals],
        "cells": cells,
    }


def wac_affiliation_coverage():
    """Honesty panel: share of author-rows carrying an institution, by decade
    and by work type — so readers can weigh the institution charts."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT w.type AS type, (w.year/10)*10 AS decade,
                   COUNT(*) total,
                   SUM(CASE WHEN a.institution IS NOT NULL THEN 1 ELSE 0 END) covered
            FROM wac_authors a JOIN wac_works w ON w.doi=a.work_doi
            WHERE a.is_person=1 AND w.year IS NOT NULL
              AND w.type IN ('journal-article','book-chapter','edited-book','monograph')
            GROUP BY w.type, decade
        """).fetchall()
        overall = conn.execute("""
            SELECT COUNT(*) total,
                   SUM(CASE WHEN a.institution IS NOT NULL THEN 1 ELSE 0 END) covered
            FROM wac_authors a JOIN wac_works w ON w.doi=a.work_doi
            WHERE a.is_person=1 AND w.year IS NOT NULL
              AND w.type IN ('journal-article','book-chapter','edited-book','monograph')
        """).fetchone()
    decades = sorted({r["decade"] for r in rows})
    by_type = collections.defaultdict(lambda: {d: [0, 0] for d in decades})
    for r in rows:
        by_type[r["type"]][r["decade"]] = [r["covered"], r["total"]]
    series = []
    for t in _TYPE_ORDER:
        if t in by_type:
            series.append({
                "type": t, "label": _TYPE_LABEL[t],
                "pct": [round(100 * by_type[t][d][0] / by_type[t][d][1]) if by_type[t][d][1] else 0
                        for d in decades],
            })
    return {
        "decades": decades, "series": series,
        "overall_pct": round(100 * overall["covered"] / overall["total"]) if overall["total"] else 0,
    }


# ── the cited canon: distribution ────────────────────────────────────────────

def _gini(values):
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    if n == 0 or sum(vals) == 0:
        return 0.0
    cum = 0
    for i, v in enumerate(vals, 1):
        cum += i * v
    return (2 * cum) / (n * sum(vals)) - (n + 1) / n


def wac_citation_lorenz():
    """Lorenz curve + Gini of inbound citations (how concentrated attention is),
    overall and per work type."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT type, COALESCE(cited_by,0) c FROM wac_works
            WHERE type IN ('journal-article','book-chapter','edited-book','monograph')
        """).fetchall()
    allc = sorted(r["c"] for r in rows)

    def lorenz(values):
        vals = sorted(values)
        n = len(vals)
        tot = sum(vals)
        pts = [{"x": 0.0, "y": 0.0}]
        if n == 0 or tot == 0:
            return pts + [{"x": 1.0, "y": 1.0}], 0.0
        cum = 0
        for i, v in enumerate(vals, 1):
            cum += v
            pts.append({"x": i / n, "y": cum / tot})
        return pts, _gini(vals)

    pts, gini = lorenz(allc)
    per_type = {}
    for t in _TYPE_ORDER:
        tv = [r["c"] for r in rows if r["type"] == t]
        per_type[t] = round(_gini(tv), 3)
    cited = sum(1 for c in allc if c > 0)
    return {"points": pts, "gini": round(gini, 3), "per_type_gini": per_type,
            "n_works": len(allc), "n_cited": cited}


def wac_citations_vs_age(cap=4400):
    """Scatter of inbound citations vs publication year (accrual-vs-age — NOT a
    half-life claim), with a per-year median overlay."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT year, COALESCE(cited_by,0) c, type, title FROM wac_works
            WHERE year IS NOT NULL
              AND type IN ('journal-article','book-chapter','edited-book','monograph')
            ORDER BY year
        """).fetchall()
    points = [{"year": r["year"], "cited_by": r["c"], "type": r["type"],
               "title": r["title"]} for r in rows[:cap]]
    by_year = collections.defaultdict(list)
    for r in rows:
        by_year[r["year"]].append(r["c"])
    medians = []
    for y in sorted(by_year):
        v = sorted(by_year[y])
        medians.append({"year": y, "median": v[len(v) // 2]})
    return {"points": points, "medians": medians}


# ── collaboration trend + title vocabulary + spanish spotlight ───────────────

def wac_coauthorship_trend():
    """Per year: share of authored works that are multi-author, and mean authors
    per work. Uses n_authors counted from cleaned author lists."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT year, n_authors FROM wac_works
            WHERE year IS NOT NULL AND n_authors > 0
              AND type IN ('journal-article','book-chapter','edited-book','monograph')
        """).fetchall()
    by_year = collections.defaultdict(lambda: {"works": 0, "multi": 0, "authors": 0})
    for r in rows:
        y = by_year[r["year"]]
        y["works"] += 1
        y["authors"] += r["n_authors"]
        if r["n_authors"] >= 2:
            y["multi"] += 1
    years = sorted(by_year)
    return {
        "years": years,
        "pct_multi": [round(100 * by_year[y]["multi"] / by_year[y]["works"]) for y in years],
        "mean_authors": [round(by_year[y]["authors"] / by_year[y]["works"], 2) for y in years],
        "works": [by_year[y]["works"] for y in years],
    }


_DEFAULT_TERMS = ["writing", "rhetoric", "literacy", "assessment", "multilingual",
                  "genre", "identity", "digital", "race", "disciplines"]


def wac_title_term_series(terms=None):
    """Per-year share of titles containing each term (titles are the only text
    signal — no abstracts). Accepts a custom term list; defaults to a starter set."""
    terms = [t.strip().lower() for t in (terms or _DEFAULT_TERMS) if t and t.strip()][:8]
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT year, lower(title) t FROM wac_works
            WHERE title IS NOT NULL AND year IS NOT NULL
              AND type IN ('journal-article','book-chapter','edited-book','monograph')
        """).fetchall()
    by_year_total = collections.Counter()
    by_year_term = {t: collections.Counter() for t in terms}
    for r in rows:
        by_year_total[r["year"]] += 1
        for t in terms:
            if re.search(r"\b" + re.escape(t), r["t"]):
                by_year_term[t][r["year"]] += 1
    years = sorted(by_year_total)
    # bucket to 3-year windows to smooth sparse early years
    series = []
    for t in terms:
        series.append({
            "term": t,
            "counts": [by_year_term[t].get(y, 0) for y in years],
            "share": [round(100 * by_year_term[t].get(y, 0) / by_year_total[y], 1) for y in years],
            "total": sum(by_year_term[t].values()),
        })
    return {"years": years, "series": series}


def wac_spanish_spotlight():
    """The press's one cross-language venue: Revista Latinoamericana de Estudios
    de la Escritura. Output timeline + works list + share of the whole catalog."""
    JRNL = "Revista Latinoamericana de Estudios de la Escritura"
    with get_conn() as conn:
        works = conn.execute("""
            SELECT doi, title, year, cited_by, url FROM wac_works
            WHERE journal=? ORDER BY year DESC, title
        """, (JRNL,)).fetchall()
        out = []
        for w in works:
            d = dict(w)
            d["authors"] = "; ".join(_authors_for(conn, w["doi"])) or None
            out.append(d)
        total = conn.execute(
            "SELECT COUNT(*) FROM wac_works WHERE type IN "
            "('journal-article','book-chapter','edited-book','monograph')"
        ).fetchone()[0]
    by_year = collections.Counter(w["year"] for w in works if w["year"])
    years = sorted(by_year)
    return {
        "journal": JRNL,
        "n_works": len(works),
        "share_pct": round(100 * len(works) / total, 1) if total else 0,
        "timeline": {"years": years, "counts": [by_year[y] for y in years]},
        "works": out,
    }
