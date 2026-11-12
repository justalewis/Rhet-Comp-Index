"""datacite_scope.py — how a DataCite record earns its place in Pinakes.

Shared by datacite_fetcher.py (discovery, pre-filtering) and
ingest_datacite.py (tiering). Three pieces:

  * Topical scoring against the tagger vocabulary. The same 62-term controlled
    vocabulary that scopes the article index scopes this layer, so a trigger is
    strong evidence of a Writing Studies topic. Score, don't gate: each distinct
    tag counts once per field, weighted subject > title > description.
  * The curated seed file (data/datacite_seeds.tsv).
  * The tier rule that combines evidence methods with the topical score.

Relations come in two strengths. A STRONG relation (IsSupplementTo,
IsDocumentedBy, IsReferencedBy, IsVersionOf, ...) says the record belongs with
the article: data or code behind it, documentation of it, a copy of it. A WEAK
relation (Cites, References, IsRelatedTo) only says the record's own reference
list mentions the article. Publishers deposit whole bibliographies that way
(every "Additional file" of a BMC paper "References" every work that paper
cites), so a weak relation is not evidence the record is Writing Studies work:
it must clear the topical threshold like any other candidate, or it is dropped.

Tiers
    A        found by Method 1 (cited by corpus) or Method 2 through a STRONG
             relation
    B        found by Method 3 (ORCID of an indexed author) or Method 4 (seed)
             AND topical score >= TIER_B_MIN
    C        Method 4 without topical support; Method 3 at AUTHOR_MIN..B-1
             (below AUTHOR_MIN it is dropped); Method 5 (topical
             discovery) alone at >= DISCOVERY_MIN — goes to the curation queue
    curated  a curator approved a Tier C record (applied in ingest)
    rejected a curator rejected it; stored with a reason, never re-suggested
"""

import csv
import html
import os
import re

from tagger import VOCAB, _COMPILED_VOCAB

W_SUBJECT = 3
W_TITLE = 2
W_DESC = 1

# Calibrated on the 2026-11 live harvest: the tagger vocabulary also fires on
# general education / applied-linguistics / NLP work, so low scores are mostly
# out-of-field. B needs a real topical match; author-only deposits with almost no
# match are dropped (an indexed author's rTMS dataset is not Writing Studies);
# Method 5 alone is a weak signal and only the strongest ~1% reach the queue.
TIER_B_MIN = 8
AUTHOR_MIN = 3
DISCOVERY_MIN = 20

PUBLISHED_TIERS = ("A", "B", "curated")

# Relation types (from the DataCite record's point of view) that only mean the
# record's reference list mentions the corpus work. See the module docstring.
WEAK_RELATIONS = ("Cites", "References", "IsRelatedTo")

SEEDS_DEFAULT = os.path.join(os.path.dirname(__file__), "data", "datacite_seeds.tsv")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(s):
    """Strip markup/entities some repositories leave in titles and abstracts."""
    if not s:
        return s
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", s))).strip()


def matched_tags(text):
    """Set of vocabulary tags whose triggers appear in `text`."""
    if not text:
        return set()
    return {tag for tag, pats in _COMPILED_VOCAB.items() if any(p.search(text) for p in pats)}


def score_fields(title, description, subjects):
    """(score, sorted tag list). `subjects` is an iterable of subject strings."""
    s_tags = matched_tags(" ; ".join(subjects or []))
    t_tags = matched_tags(clean_text(title))
    d_tags = matched_tags(clean_text(description))
    score = W_SUBJECT * len(s_tags) + W_TITLE * len(t_tags) + W_DESC * len(d_tags)
    return float(score), sorted(s_tags | t_tags | d_tags)


def score_attrs(attrs):
    """Score a DataCite `attributes` object."""
    title = next((t.get("title") for t in attrs.get("titles") or [] if t.get("title")), None)
    descs = attrs.get("descriptions") or []
    desc = " ".join(d.get("description") or "" for d in descs)
    subjects = [s.get("subject") for s in attrs.get("subjects") or [] if s.get("subject")]
    return score_fields(title, desc, subjects)


def discovery_terms():
    """Multi-word tagger triggers, used as Method 5 queries. Single words are
    excluded: bare 'rhetoric' or 'composition' collide with music, chemistry
    and materials science."""
    return sorted({p for phrases in VOCAB.values() for p in phrases if " " in p})


def load_seeds(path=SEEDS_DEFAULT):
    """{kind: [(value, query, note), ...]} from the curated seed TSV.

    Columns: kind, value, query, note. Kinds:
        orcid   an ORCID outside the `authors` table whose deposits to include
        client  a DataCite repository id (e.g. cos.osf); `query` narrows it
        doi     a single hand-picked DOI
        term    an extra Method 5 discovery phrase
    Lines starting with '#' and blank lines are ignored.
    """
    seeds = {}
    if not os.path.exists(path):
        return seeds
    with open(path, encoding="utf-8", newline="") as f:
        rows = (ln for ln in f if ln.strip() and not ln.lstrip().startswith("#"))
        for row in csv.DictReader(rows, delimiter="\t"):
            kind = (row.get("kind") or "").strip().lower()
            value = (row.get("value") or "").strip()
            if kind and value:
                seeds.setdefault(kind, []).append(
                    (value, (row.get("query") or "").strip(), (row.get("note") or "").strip())
                )
    return seeds


def effective_methods(evidence):
    """Method names for a record's evidence rows, with a Method 2 hit through
    only WEAK relations reported as "weak_relation" instead. A relation of
    unknown type is treated as strong."""
    methods = set()
    strong_relation = False
    for e in evidence:
        if e["method"] == "relation_to_corpus":
            if e.get("relation") in WEAK_RELATIONS:
                methods.add("weak_relation")
            else:
                strong_relation = True
        else:
            methods.add(e["method"])
    if strong_relation:
        methods.add("relation_to_corpus")
    return methods


def tier_for(methods, score):
    """Tier letter for a set of evidence methods and a topical score, or None
    if the record should not enter the index at all."""
    methods = set(methods)
    if methods & {"cited_by_corpus", "relation_to_corpus"}:
        return "A"
    if "seed" in methods:
        return "B" if score >= TIER_B_MIN else "C"
    if "author_in_corpus" in methods:
        if score >= TIER_B_MIN:
            return "B"
        return "C" if score >= AUTHOR_MIN else None
    if "weak_relation" in methods:
        return "B" if score >= TIER_B_MIN else None
    if "topical" in methods and score >= DISCOVERY_MIN:
        return "C"
    return None
