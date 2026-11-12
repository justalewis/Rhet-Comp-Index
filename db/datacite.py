"""DataCite query functions (tables built by ingest_datacite.py).

Everything the public site shows is restricted to PUBLISHED_TIERS (A, B and
curator-approved). Tier C (candidates) and rejected records are visible only
through the admin curation queue. The curation table is the one writable
piece; all other tables are read-only to the web app.

Kept out of db/__init__.py on purpose: import directly with
`from db.datacite import ...`.
"""

import json
import os

from datacite_scope import PUBLISHED_TIERS, REVIEW_TIERS, WEAK_RELATIONS
from .core import get_conn

_REV = "(" + ",".join(f"'{t}'" for t in REVIEW_TIERS) + ")"
_PUB = "(" + ",".join(f"'{t}'" for t in PUBLISHED_TIERS) + ")"
# Relations that mean "the record's reference list mentions the article", not
# "the record sits behind the article". Left off article-facing views.
_WEAK = "(" + ",".join(f"'{r}'" for r in WEAK_RELATIONS) + ")"

PRECISION_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "datacite_precision.json")

# Relation types read from the DataCite record's point of view, flipped to
# how an article page should describe the object.
RELATION_LABELS = {
    "IsSupplementTo": "Supplement to this article",
    "IsReferencedBy": "References this article",
    "References": "Refers to this article",
    "Cites": "Cites this article",
    "IsCitedBy": "Cited by this article",
    "IsDocumentedBy": "Documents this article",
    "IsDescribedBy": "Describes this article",
    "IsSourceOf": "Derived from this article",
    "IsVersionOf": "Version of this article",
    "IsPartOf": "Part of this article",
    "IsNewVersionOf": "New version of this article",
    "IsPreviousVersionOf": "Previous version of this article",
}


def _rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params)]


def _collapse_versions(rows):
    """Zenodo/Figshare register a concept DOI plus one DOI per version. Show
    the version-less record when both are present."""
    by_title = {}
    for r in rows:
        key = (r["title"] or r["doi"], r["publisher"])
        cur = by_title.get(key)
        if cur is None or len(r["doi"]) < len(cur["doi"]):
            by_title[key] = r
    return list(by_title.values())


def _attach_creators(conn, rows, limit=6):
    for d in rows:
        d["creators"] = [
            c["name"] for c in conn.execute(
                "SELECT name FROM datacite_creators WHERE work_doi = ? AND role = 'creator' "
                "ORDER BY seq LIMIT ?", (d["doi"], limit))
        ]
    return rows


# ── article / author panels ──────────────────────────────────────────────────

def get_datacite_for_article(article_id, conn=None):
    """Published DataCite records tied to one article: records that declare a
    relation to it, plus records its reference list cites. Each item is a dict
    with the record's fields and `links`: a list of human-readable reasons."""
    own = conn is None
    conn = conn or get_conn()
    try:
        items = {}
        for r in conn.execute(
            f"""SELECT w.*, rel.relation_type
                  FROM datacite_relations rel
                  JOIN datacite_works w ON w.doi = rel.work_doi
                 WHERE rel.article_id = ? AND w.tier IN {_PUB}
                   AND rel.relation_type NOT IN {_WEAK}""",
            (article_id,),
        ):
            d = items.setdefault(r["doi"], dict(r) | {"links": []})
            label = RELATION_LABELS.get(r["relation_type"], r["relation_type"])
            if label not in d["links"]:
                d["links"].append(label)

        for r in conn.execute(
            f"""SELECT DISTINCT w.*
                  FROM citations c
                  JOIN datacite_works w ON w.doi = LOWER(c.target_doi)
                 WHERE c.source_article_id = ? AND w.tier IN {_PUB}""",
            (article_id,),
        ):
            d = items.setdefault(r["doi"], dict(r) | {"links": []})
            if "Cited in this article's references" not in d["links"]:
                d["links"].append("Cited in this article's references")

        rows = _attach_creators(conn, _collapse_versions(list(items.values())))
        rows.sort(key=lambda d: (d["resource_type_gen"] or "", d["title"] or ""))
        return rows
    finally:
        if own:
            conn.close()


def get_datacite_for_author(author_id, conn=None):
    """Published DataCite deposits whose creator/contributor ORCID matched this
    author. Powers the 'Research objects' section of the author page."""
    if not author_id:
        return []
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = _rows(
            conn,
            f"""SELECT DISTINCT w.*
                  FROM datacite_creators c
                  JOIN datacite_works w ON w.doi = c.work_doi
                 WHERE c.author_id = ? AND w.tier IN {_PUB}""",
            (author_id,),
        )
        rows = _attach_creators(conn, _collapse_versions(rows))
        rows.sort(key=lambda d: (-(d["year"] or 0), d["title"] or ""))
        return rows
    finally:
        if own:
            conn.close()


# ── curation queue ───────────────────────────────────────────────────────────

def get_curation_queue(limit=100, offset=0, conn=None):
    """Pending review candidates (tier C, plus B while B is held), strongest topical score first, each with its
    evidence so the curator can see *why* it surfaced."""
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = _rows(
            conn,
            f"""SELECT w.* FROM datacite_works w
                WHERE w.tier IN {_REV}
                  AND NOT EXISTS (SELECT 1 FROM datacite_curation k WHERE k.doi = w.doi)
                ORDER BY w.topic_score DESC, w.year DESC, w.doi
                LIMIT ? OFFSET ?""",
            (limit, offset),
        )
        for d in rows:
            d["evidence"] = _rows(
                conn,
                "SELECT method, matched_on, score FROM datacite_scope_evidence WHERE work_doi = ?",
                (d["doi"],))
        _attach_creators(conn, rows)
        return rows
    finally:
        if own:
            conn.close()


def queue_size(conn=None):
    own = conn is None
    conn = conn or get_conn()
    try:
        return conn.execute(
            f"SELECT COUNT(*) FROM datacite_works w WHERE w.tier IN {_REV} AND NOT EXISTS "
            "(SELECT 1 FROM datacite_curation k WHERE k.doi = w.doi)").fetchone()[0]
    finally:
        if own:
            conn.close()


def decide_curation(doi, decision, reason=None, actor=None):
    """Approve or reject a record. Rejections keep their reason and survive
    every re-ingest, so a rejected record is never suggested again. Returns the
    stored decision dict, or None if the DOI is unknown. Raises ValueError for a
    bad decision."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be 'approved' or 'rejected'")
    doi = (doi or "").strip().lower()
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM datacite_works WHERE doi = ?", (doi,)).fetchone():
            return None
        conn.execute(
            "INSERT INTO datacite_curation (doi, decision, reason, decided_by) VALUES (?,?,?,?) "
            "ON CONFLICT(doi) DO UPDATE SET decision=excluded.decision, reason=excluded.reason, "
            "decided_by=excluded.decided_by, decided_at=datetime('now')",
            (doi, decision, (reason or "").strip() or None, actor),
        )
        conn.execute("UPDATE datacite_works SET tier = ? WHERE doi = ?",
                     ("curated" if decision == "approved" else "rejected", doi))
        conn.commit()
        return dict(conn.execute("SELECT * FROM datacite_curation WHERE doi = ?", (doi,)).fetchone())


# ── dashboard analyses ───────────────────────────────────────────────────────

def overview(conn=None):
    own = conn is None
    conn = conn or get_conn()
    try:
        by_tier = {r["tier"]: r["n"] for r in conn.execute(
            "SELECT tier, COUNT(*) n FROM datacite_works GROUP BY tier")}
        pub = _rows(conn, f"SELECT COUNT(*) n, MIN(year) y0, MAX(year) y1 FROM datacite_works "
                          f"WHERE tier IN {_PUB}")[0]
        linked_articles = conn.execute(
            f"""SELECT COUNT(DISTINCT rel.article_id) FROM datacite_relations rel
                  JOIN datacite_works w ON w.doi = rel.work_doi
                 WHERE rel.article_id IS NOT NULL AND w.tier IN {_PUB}
                   AND rel.relation_type NOT IN {_WEAK}""").fetchone()[0]
        authors = conn.execute(
            f"""SELECT COUNT(DISTINCT c.author_id) FROM datacite_creators c
                  JOIN datacite_works w ON w.doi = c.work_doi
                 WHERE c.author_id IS NOT NULL AND w.tier IN {_PUB}""").fetchone()[0]
        # Records in only because an indexed article cites them. They are what
        # the field relies on, not necessarily Writing Studies work (a
        # general-purpose corpus, say), so the dashboard reports them apart.
        cited_only = conn.execute(
            f"""SELECT COUNT(*) FROM datacite_works w
                 WHERE w.tier IN {_PUB}
                   AND EXISTS (SELECT 1 FROM datacite_scope_evidence e
                                WHERE e.work_doi = w.doi AND e.method = 'cited_by_corpus')
                   AND NOT EXISTS (SELECT 1 FROM datacite_scope_evidence e
                                    WHERE e.work_doi = w.doi AND e.method != 'cited_by_corpus')"""
        ).fetchone()[0]
        return {
            "published": pub["n"], "year_min": pub["y0"], "year_max": pub["y1"],
            "cited_only": cited_only,
            "by_tier": by_tier, "queue": sum(by_tier.get(t, 0) for t in REVIEW_TIERS),
            "linked_articles": linked_articles, "orcid_authors": authors,
        }
    finally:
        if own:
            conn.close()


def type_composition(conn=None):
    """Resource-type mix of published records, by year."""
    own = conn is None
    conn = conn or get_conn()
    try:
        totals = _rows(conn, f"SELECT COALESCE(resource_type_gen,'Other') type, COUNT(*) n "
                             f"FROM datacite_works WHERE tier IN {_PUB} GROUP BY type ORDER BY n DESC")
        by_year = _rows(conn, f"SELECT year, COALESCE(resource_type_gen,'Other') type, COUNT(*) n "
                              f"FROM datacite_works WHERE tier IN {_PUB} AND year IS NOT NULL "
                              f"GROUP BY year, type ORDER BY year")
        return {"totals": totals, "by_year": by_year}
    finally:
        if own:
            conn.close()


def repositories(limit=20, conn=None):
    own = conn is None
    conn = conn or get_conn()
    try:
        # Repositories spell themselves inconsistently ("figshare"/"Figshare",
        # "Unknown"/"unknown"); fold case and show the capitalised spelling.
        return _rows(conn, f"SELECT MIN(COALESCE(NULLIF(TRIM(publisher),''),'(unknown)')) repository, "
                           f"COUNT(*) n FROM datacite_works WHERE tier IN {_PUB} "
                           f"GROUP BY LOWER(TRIM(COALESCE(publisher,''))) "
                           f"ORDER BY n DESC LIMIT ?", (limit,))
    finally:
        if own:
            conn.close()


def supplement_rate(by="journal", min_articles=None, limit=25, conn=None):
    """Share of DOI-bearing articles with at least one published linked object,
    by journal or by year. Denominator is articles with a DOI — an article
    without one cannot be matched by Method 2."""
    key = "a.journal" if by == "journal" else "SUBSTR(a.pub_date,1,4)"
    if min_articles is None:
        min_articles = 30 if by == "journal" else 100
    # By-year view starts at 2000: earlier years have too few DOI-bearing
    # articles for a share to mean anything.
    year_floor = "" if by == "journal" else " AND SUBSTR(a.pub_date,1,4) >= '2000'"
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = _rows(conn, f"""
            SELECT {key} AS label, COUNT(*) AS articles,
                   SUM(EXISTS(SELECT 1 FROM datacite_relations r
                                JOIN datacite_works w ON w.doi = r.work_doi
                               WHERE r.article_id = a.id AND w.tier IN {_PUB}
                                 AND r.relation_type NOT IN {_WEAK})) AS linked
              FROM articles a
             WHERE a.doi IS NOT NULL AND a.doi != '' AND {key} IS NOT NULL AND {key} != ''{year_floor}
             GROUP BY label HAVING articles >= ?""", (min_articles,))
        for r in rows:
            r["rate"] = round(100.0 * r["linked"] / r["articles"], 2) if r["articles"] else 0
        if by == "journal":
            rows = [r for r in rows if r["linked"]]
            rows.sort(key=lambda r: (-r["rate"], -r["articles"]))
            return rows[:limit]
        rows.sort(key=lambda r: r["label"])
        return rows
    finally:
        if own:
            conn.close()


def citations_beyond_articles(conn=None):
    """Registration agency of what the corpus cites, by citing-article year.
    Needs classify_citation_ra.py to have run."""
    own = conn is None
    conn = conn or get_conn()
    try:
        by_ra = _rows(conn, "SELECT target_ra ra, COUNT(*) n FROM citations "
                            "WHERE target_ra IS NOT NULL GROUP BY ra ORDER BY n DESC")
        by_year = _rows(conn, """
            SELECT SUBSTR(a.pub_date,1,4) year,
                   COUNT(*) total,
                   SUM(c.target_ra = 'datacite') datacite,
                   SUM(c.target_ra NOT IN ('crossref','datacite','unknown')) other
              FROM citations c JOIN articles a ON a.id = c.source_article_id
             WHERE c.target_ra IS NOT NULL AND a.pub_date IS NOT NULL
             GROUP BY year HAVING total >= 200 AND year >= '2000' ORDER BY year""")
        for r in by_year:
            r["datacite_pct"] = round(100.0 * (r["datacite"] or 0) / r["total"], 3)
        return {"by_ra": by_ra, "by_year": by_year}
    finally:
        if own:
            conn.close()


def infrastructure_builders(limit=20, conn=None):
    """Indexed authors (ORCID-matched) with published non-text deposits."""
    own = conn is None
    conn = conn or get_conn()
    try:
        return _rows(conn, f"""
            SELECT au.id, au.name, COUNT(DISTINCT w.doi) objects,
                   GROUP_CONCAT(DISTINCT w.resource_type_gen) types
              FROM datacite_creators c
              JOIN datacite_works w ON w.doi = c.work_doi
              JOIN authors au ON au.id = c.author_id
             WHERE w.tier IN {_PUB}
               AND w.resource_type_gen IN ('Software','Dataset','Collection','Workflow',
                                           'ComputationalNotebook','Model','InteractiveResource',
                                           'Audiovisual','Image','Sound','Service')
             GROUP BY au.id ORDER BY objects DESC, au.name LIMIT ?""", (limit,))
    finally:
        if own:
            conn.close()


def tool_lifecycles(limit=20, conn=None):
    """Software and interactive tools grouped by title: how many registered
    versions, and the first/last year. A tool whose last version is old has
    stopped being maintained, or stopped being deposited."""
    own = conn is None
    conn = conn or get_conn()
    try:
        return _rows(conn, f"""
            SELECT MIN(doi) doi, title, publisher, COUNT(*) versions,
                   MIN(year) first_year, MAX(year) last_year
              FROM datacite_works
             WHERE tier IN {_PUB} AND resource_type_gen IN ('Software','InteractiveResource','Workflow')
               AND title IS NOT NULL
             GROUP BY title, publisher
             ORDER BY versions DESC, last_year DESC LIMIT ?""", (limit,))
    finally:
        if own:
            conn.close()


def licenses(limit=12, conn=None):
    own = conn is None
    conn = conn or get_conn()
    try:
        rows = _rows(conn, f"SELECT rights, COUNT(*) n FROM datacite_works WHERE tier IN {_PUB} "
                           f"GROUP BY rights ORDER BY n DESC")
        out = {}
        for r in rows:
            label = (r["rights"] or "").split(";")[0].strip() or "(none stated)"
            out[label] = out.get(label, 0) + r["n"]
        return [{"license": k, "n": v} for k, v in sorted(out.items(), key=lambda kv: -kv[1])[:limit]]
    finally:
        if own:
            conn.close()


def funders(limit=15, conn=None):
    own = conn is None
    conn = conn or get_conn()
    try:
        counts = {}
        for r in conn.execute(f"SELECT funders FROM datacite_works WHERE tier IN {_PUB} "
                              f"AND funders IS NOT NULL"):
            for name in json.loads(r["funders"]):
                counts[name] = counts.get(name, 0) + 1
        return [{"funder": k, "n": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]]
    finally:
        if own:
            conn.close()


def usage_vs_citation(limit=15, conn=None):
    """Published objects with usage counts, beside how often the corpus cites
    them. Usage counts are sparse: only repositories that report them."""
    own = conn is None
    conn = conn or get_conn()
    try:
        cited = {r["d"]: r["n"] for r in conn.execute(
            "SELECT LOWER(target_doi) d, COUNT(*) n FROM citations "
            "WHERE target_ra = 'datacite' GROUP BY d")}
        rows = _rows(conn, f"""SELECT doi, title, resource_type_gen, view_count, download_count
                                 FROM datacite_works
                                WHERE tier IN {_PUB} AND (download_count > 0 OR view_count > 0)
                                ORDER BY download_count DESC, view_count DESC LIMIT ?""", (limit,))
        for r in rows:
            r["cited_by_corpus"] = cited.get(r["doi"], 0)
        return rows
    finally:
        if own:
            conn.close()


def shared_objects(limit=15, conn=None):
    """Objects linked to several indexed articles: the seed of an
    article-object network ('bibliographic coupling through infrastructure')."""
    own = conn is None
    conn = conn or get_conn()
    try:
        return _rows(conn, f"""
            SELECT w.doi, w.title, w.resource_type_gen,
                   COUNT(DISTINCT rel.article_id) articles
              FROM datacite_relations rel JOIN datacite_works w ON w.doi = rel.work_doi
             WHERE rel.article_id IS NOT NULL AND w.tier IN {_PUB}
               AND rel.relation_type NOT IN {_WEAK}
             GROUP BY w.doi HAVING articles > 1
             ORDER BY articles DESC LIMIT ?""", (limit,))
    finally:
        if own:
            conn.close()


def precision_report():
    """Hand-coded precision per tier, from data/datacite_precision.json
    (written by datacite_precision.py report). None if no study exists yet."""
    try:
        with open(PRECISION_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
