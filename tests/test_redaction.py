"""Tests for the author-redaction spine (redaction.py + ingest choke-points).

The seed's "Jane Smith" is the ideal subject: she is primary author of
articles 1/14/27/39, has an authors-table row (ORCID + U of Iowa), affiliation
+ institution rows, and authors book 1 — so redacting her exercises every
table the name string lives in. Her sole co-author across those papers is
"George Hu", giving a hand-verifiable co-authorship edge to check for metric
invariance.
"""

import json

import pytest

import db as _db
import redaction
from redaction import (
    apply_suppression, is_redaction_token, mint_token, redact_author,
    resweep_all, unredact_author, _normalize,
)

JANE = "Jane Smith"


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_mint_token_is_deterministic_and_unique():
    t1 = mint_token(JANE, "salt-A")
    t2 = mint_token(JANE, "salt-A")
    assert t1 == t2                       # deterministic
    assert is_redaction_token(t1)
    assert "#" not in t1                  # URL-safe (no fragment delimiter)
    assert mint_token(JANE, "salt-B") != t1   # salt changes the token
    assert mint_token("John Adams", "salt-A") != t1   # injective per name


def test_normalize_folds_case_and_whitespace_only():
    assert _normalize("  Jane   Smith ") == _normalize("jane smith")
    # Conservative: does NOT fold distinct real-name forms together.
    assert _normalize("Smith, A.") != _normalize("Alice Smith")


def test_apply_suppression_replaces_exact_element_only(seeded_db):
    redact_author(JANE)
    smap = redaction._suppression_map()
    token = smap[_normalize(JANE)]
    # Co-author preserved; only Jane's element swapped.
    assert apply_suppression("Jane Smith; George Hu", smap=smap) == f"{token}; George Hu"
    # Substring safety: "Jane Smithson" must NOT match "Jane Smith".
    assert apply_suppression("Jane Smithson; Bob Lee", smap=smap) == "Jane Smithson; Bob Lee"
    # Nothing to do.
    assert apply_suppression("George Hu; Bob Lee", smap=smap) == "George Hu; Bob Lee"
    assert apply_suppression(None, smap=smap) is None


# ── full-sweep correctness ───────────────────────────────────────────────────

def _name_anywhere(conn, name):
    """Count every row in any table whose name field still contains *name*."""
    like = f"%{name}%"
    counts = {}
    counts["articles"] = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE authors LIKE ?", (like,)).fetchone()[0]
    counts["authors"] = conn.execute(
        "SELECT COUNT(*) FROM authors WHERE name LIKE ?", (like,)).fetchone()[0]
    counts["affiliations"] = conn.execute(
        "SELECT COUNT(*) FROM author_article_affiliations WHERE author_name LIKE ?",
        (like,)).fetchone()[0]
    counts["institutions"] = conn.execute(
        "SELECT COUNT(*) FROM article_author_institutions WHERE author_name LIKE ?",
        (like,)).fetchone()[0]
    counts["books"] = conn.execute(
        "SELECT COUNT(*) FROM books WHERE authors LIKE ? OR editors LIKE ?",
        (like, like)).fetchone()[0]
    return counts


def test_redaction_removes_name_from_every_table(seeded_db):
    with _db.get_conn() as conn:
        before = _name_anywhere(conn, JANE)
    assert before["articles"] > 0 and before["authors"] > 0 and before["books"] > 0

    result = redact_author(JANE)
    token = result["token"]

    with _db.get_conn() as conn:
        after = _name_anywhere(conn, JANE)
        assert all(v == 0 for v in after.values()), f"name still present: {after}"
        # Token took the name's place in each table.
        assert conn.execute(
            "SELECT COUNT(*) FROM articles WHERE authors LIKE ?", (f"%{token}%",)
        ).fetchone()[0] == before["articles"]
        # ORCID / OpenAlex id scrubbed on the de-identified authors row.
        row = conn.execute(
            "SELECT orcid, openalex_id, institution_name FROM authors WHERE name = ?",
            (token,)).fetchone()
        assert row["orcid"] is None and row["openalex_id"] is None
        assert row["institution_name"] is None
        # Institution METRICS preserved: the affiliation row kept its institution_id.
        inst = conn.execute(
            "SELECT institution_id FROM article_author_institutions WHERE author_name = ?",
            (token,)).fetchone()
        assert inst is not None and inst["institution_id"] is not None


def test_redaction_preserves_coauthorship_network_structure(seeded_db):
    """Metric invariance — the single most important property. Replacing the
    name with a stable token must leave the network's shape identical."""
    def signature(net):
        nodes = sorted(n["count"] for n in net["nodes"])
        links = sorted(l["value"] for l in net["links"])
        return nodes, links

    before = _db.get_author_network(min_papers=3, top_n=150)
    before_sig = signature(before)
    assert any(n["id"] == JANE for n in before["nodes"])
    jane_count = next(n["count"] for n in before["nodes"] if n["id"] == JANE)

    token = redact_author(JANE)["token"]

    after = _db.get_author_network(min_papers=3, top_n=150)
    assert signature(after) == before_sig, "network structure changed under redaction"
    assert not any(n["id"] == JANE for n in after["nodes"])
    tok_node = next((n for n in after["nodes"] if n["id"] == token), None)
    assert tok_node is not None and tok_node["count"] == jane_count


def test_author_lookups_follow_the_token(seeded_db):
    token = redact_author(JANE)["token"]
    # Real name no longer resolves; token does, with the same article set.
    assert _db.get_author_articles(JANE) == []
    assert len(_db.get_author_articles(token)) >= 3
    co_before_name = _db.get_author_coauthors(JANE)
    assert co_before_name["nodes"] == []          # name is gone
    co_token = _db.get_author_coauthors(token)
    assert co_token["center"] == token
    assert any(n["id"] == "George Hu" for n in co_token["nodes"])


# ── resurrection / idempotency / reversal ────────────────────────────────────

def test_new_publication_is_suppressed_on_ingest(seeded_db):
    token = redact_author(JANE)["token"]
    # Simulate tomorrow's fetch handing the real name back on a NEW article.
    _db.upsert_article(
        url="https://doi.org/10.9999/new.0001", doi="10.9999/new.0001",
        title="A brand new paper", authors="Jane Smith; Fresh Coauthor",
        abstract="x", pub_date="2026-06-01", journal="College English",
        source="crossref",
    )
    row = _db.get_article_by_id(
        next(a["id"] for a in _db.get_author_articles(token)
             if a["url"].endswith("new.0001")))
    assert "Jane Smith" not in row["authors"]
    assert token in row["authors"] and "Fresh Coauthor" in row["authors"]


def test_resweep_is_idempotent(seeded_db):
    token = redact_author(JANE)["token"]
    with _db.get_conn() as conn:
        snap1 = conn.execute(
            "SELECT id, authors FROM articles WHERE authors LIKE ?",
            (f"%{token}%",)).fetchall()
        snap1 = {r["id"]: r["authors"] for r in snap1}
    resweep_all()
    resweep_all()
    with _db.get_conn() as conn:
        snap2 = conn.execute(
            "SELECT id, authors FROM articles WHERE authors LIKE ?",
            (f"%{token}%",)).fetchall()
        snap2 = {r["id"]: r["authors"] for r in snap2}
        # No duplicate token rows in the authors table after repeated sweeps.
        assert conn.execute(
            "SELECT COUNT(*) FROM authors WHERE name = ?", (token,)).fetchone()[0] == 1
    assert snap1 == snap2


def test_unredaction_restores_the_name(seeded_db):
    token = redact_author(JANE)["token"]
    assert _db.get_author_articles(JANE) == []
    out = unredact_author(token)
    assert out["restored"] is True and out["name"] == JANE
    # Name is back in the free-text fields and the ledger row is gone.
    assert len(_db.get_author_articles(JANE)) >= 3
    with _db.get_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM redaction_ledger WHERE token = ?", (token,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM articles WHERE authors LIKE ?", (f"%{token}%",)
        ).fetchone()[0] == 0


def test_variant_forms_are_all_suppressed(seeded_db):
    # Insert an article using a variant form of the name.
    _db.upsert_article(
        url="https://doi.org/10.9999/var.0001", doi="10.9999/var.0001",
        title="Variant form paper", authors="J. Smith; Someone Else",
        abstract="x", pub_date="2025-01-01", journal="College English",
        source="crossref",
    )
    token = redact_author(JANE, variants=["J. Smith"])["token"]
    with _db.get_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM articles WHERE authors LIKE '%J. Smith%'"
        ).fetchone()[0] == 0


# ── import-completeness (the jflowAbbrev lesson) ─────────────────────────────

@pytest.mark.parametrize("module_path", [
    "db/articles.py", "db/books.py", "enrich_openalex.py",
])
def test_ingest_paths_reference_suppression(module_path):
    """Every author-writing path that bypasses or is the choke-point must
    reference the suppression helper, so a future refactor can't silently drop
    the guard (cf. jflowAbbrev breaking Half-Life/Main-Path for months)."""
    import pathlib
    text = pathlib.Path(module_path).read_text(encoding="utf-8")
    assert "apply_suppression" in text, f"{module_path} dropped the redaction guard"
