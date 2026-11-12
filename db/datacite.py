"""DataCite query functions (read-only; tables built by ingest_datacite.py).

Kept out of db/__init__.py on purpose: import directly with
`from db.datacite import ...`.
"""

from .core import get_conn

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


def get_datacite_for_article(article_id, conn=None):
    """DataCite records tied to one article: records that declare a relation
    to it, plus records its reference list cites. Each item is a dict with the
    record's fields and `links`: a list of human-readable reasons."""
    own = conn is None
    conn = conn or get_conn()
    try:
        items = {}

        for r in conn.execute(
            """SELECT w.*, rel.relation_type
                 FROM datacite_relations rel
                 JOIN datacite_works w ON w.doi = rel.work_doi
                WHERE rel.article_id = ?""",
            (article_id,),
        ):
            d = items.setdefault(r["doi"], dict(r) | {"links": []})
            label = RELATION_LABELS.get(r["relation_type"], r["relation_type"])
            if label not in d["links"]:
                d["links"].append(label)

        for r in conn.execute(
            """SELECT DISTINCT w.*
                 FROM citations c
                 JOIN datacite_works w ON w.doi = LOWER(c.target_doi)
                WHERE c.source_article_id = ?""",
            (article_id,),
        ):
            d = items.setdefault(r["doi"], dict(r) | {"links": []})
            if "Cited in this article's references" not in d["links"]:
                d["links"].append("Cited in this article's references")

        rows = _collapse_versions(list(items.values()))
        for d in rows:
            d["creators"] = [
                c["name"] for c in conn.execute(
                    "SELECT name FROM datacite_creators WHERE work_doi = ? AND role = 'creator' "
                    "ORDER BY seq LIMIT 6", (d["doi"],))
            ]
        rows.sort(key=lambda d: (d["resource_type_gen"] or "", d["title"] or ""))
        return rows
    finally:
        if own:
            conn.close()
