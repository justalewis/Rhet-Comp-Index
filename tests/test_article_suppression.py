"""Tests for durable article suppression + the curation admin endpoints.

Covers the article-level analog of author redaction: the upsert_article
choke-point, delete/suppress/unsuppress, the post-fetch resweep, field edits,
and the token-gated /api/admin/article* surface.
"""

import db
from db import get_conn


def _insert(url, doi, title, authors="Ada Lovelace", journal="The WAC Journal"):
    """Insert one article and return its id."""
    db.upsert_article(url, doi, title, authors, None, "2026-01-15", journal,
                      "crossref")
    with get_conn() as conn:
        return conn.execute("SELECT id FROM articles WHERE url = ?", (url,)).fetchone()[0]


# ── upsert choke-point ────────────────────────────────────────────────────────

def test_upsert_skips_blocklisted_doi(fixture_db):
    db.suppress_article(doi="10.37514/wac-j.2026.2.1.91", reason="test deposit")
    inserted = db.upsert_article(
        "https://doi.org/10.37514/wac-j.2026.2.1.91",
        "10.37514/wac-j.2026.2.1.91", "ebizonTest7", "x", None,
        "2026-01-01", "The WAC Journal", "crossref")
    assert inserted == 0
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert n == 0


def test_upsert_skips_blocklisted_url(fixture_db):
    db.suppress_article(url="https://example.org/junk", reason="spam")
    inserted = db.upsert_article(
        "https://example.org/junk", None, "junk", "x", None,
        "2026-01-01", "J", "crossref")
    assert inserted == 0


def test_upsert_allows_unrelated_doi(fixture_db):
    db.suppress_article(doi="10.37514/wac-j.2026.2.1.91", reason="test")
    inserted = db.upsert_article(
        "https://doi.org/10.1/real", "10.1/real", "Real article", "x", None,
        "2026-01-01", "J", "crossref")
    assert inserted == 1


def test_doi_match_is_case_insensitive(fixture_db):
    db.suppress_article(doi="10.37514/WAC-J.2026.2.1.91", reason="test")
    inserted = db.upsert_article(
        "https://doi.org/x", "10.37514/wac-j.2026.2.1.91", "t", "x", None,
        "2026-01-01", "J", "crossref")
    assert inserted == 0


# ── suppress / delete / unsuppress ────────────────────────────────────────────

def test_suppress_existing_article_deletes_and_blocklists(fixture_db):
    aid = _insert("https://doi.org/10.37514/wac-j.2026.2.1.94",
                  "10.37514/wac-j.2026.2.1.94", "ebizonTest8")
    result = db.suppress_article(aid, reason="test deposit", actor="admin@ip")
    assert result["ok"] and aid in result["deleted_ids"]
    assert db.get_article_by_id(aid) is None
    bl = db.list_suppressed()
    assert any(r["doi"] == "10.37514/wac-j.2026.2.1.94" for r in bl)


def test_suppress_removes_from_fts(fixture_db):
    aid = _insert("https://doi.org/10.1/fts", "10.1/fts", "Findable Title Xyzzy")
    with get_conn() as conn:
        hit = conn.execute(
            "SELECT COUNT(*) FROM articles_fts WHERE articles_fts MATCH 'Xyzzy'"
        ).fetchone()[0]
    assert hit == 1
    db.suppress_article(aid, reason="junk")
    with get_conn() as conn:
        hit = conn.execute(
            "SELECT COUNT(*) FROM articles_fts WHERE articles_fts MATCH 'Xyzzy'"
        ).fetchone()[0]
    assert hit == 0


def test_suppress_cleans_dependent_rows(fixture_db):
    aid = _insert("https://doi.org/10.1/dep", "10.1/dep", "Has deps")
    with get_conn() as conn:
        conn.execute("INSERT INTO user_tags (article_id, tag, created_at) "
                     "VALUES (?, 'x', datetime('now'))", (aid,))
        conn.commit()
    db.suppress_article(aid, reason="junk")
    with get_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM user_tags WHERE article_id = ?", (aid,)
        ).fetchone()[0] == 0


def test_suppress_then_unsuppress_allows_reingest(fixture_db):
    aid = _insert("https://doi.org/10.1/round", "10.1/round", "Round trip")
    db.suppress_article(aid, reason="mistake")
    # blocked
    assert db.upsert_article("https://doi.org/10.1/round", "10.1/round", "t",
                             "x", None, "2026-01-01", "J", "crossref") == 0
    assert db.unsuppress(doi="10.1/round") is True
    # now allowed again
    assert db.upsert_article("https://doi.org/10.1/round", "10.1/round", "t",
                             "x", None, "2026-01-01", "J", "crossref") == 1


def test_suppress_by_doi_only_when_not_ingested(fixture_db):
    result = db.suppress_article(doi="10.1/future", reason="pre-empt")
    assert result["ok"] and result["deleted_ids"] == []
    assert any(r["doi"] == "10.1/future" for r in db.list_suppressed())


def test_delete_article_is_not_durable(fixture_db):
    """delete_article removes the row but does NOT blocklist — re-upsert works."""
    aid = _insert("https://doi.org/10.1/del", "10.1/del", "Deletable")
    assert db.delete_article(aid) is True
    assert db.upsert_article("https://doi.org/10.1/del", "10.1/del", "t", "x",
                             None, "2026-01-01", "J", "crossref") == 1


# ── resweep backstop ──────────────────────────────────────────────────────────

def test_resweep_purges_blocklisted_row(fixture_db):
    """A row that reaches the table by a path bypassing upsert is swept."""
    aid = _insert("https://doi.org/10.1/sneak", "10.1/sneak", "Snuck in")
    # Blocklist directly, leaving the row in place (simulating a bypass).
    with get_conn() as conn:
        conn.execute("INSERT INTO suppressed_articles (doi, reason) VALUES (?, ?)",
                     ("10.1/sneak", "test"))
        conn.commit()
    purged = db.resweep_suppressed_articles()
    assert purged == 1
    assert db.get_article_by_id(aid) is None


def test_resweep_noop_when_blocklist_empty(fixture_db):
    _insert("https://doi.org/10.1/keep", "10.1/keep", "Keeper")
    assert db.resweep_suppressed_articles() == 0


# ── field edits ───────────────────────────────────────────────────────────────

def test_update_article_edits_title_and_syncs_fts(fixture_db):
    aid = _insert("https://doi.org/10.1/edit", "10.1/edit", "Writing &amp; Rhetoric")
    assert db.update_article(aid, {"title": "Writing & Rhetoric"}) is True
    assert db.get_article_by_id(aid)["title"] == "Writing & Rhetoric"
    with get_conn() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM articles_fts WHERE articles_fts MATCH 'Rhetoric'"
        ).fetchone()[0] == 1


def test_update_article_ignores_unknown_fields(fixture_db):
    aid = _insert("https://doi.org/10.1/u", "10.1/u", "T")
    assert db.update_article(aid, {"nonsense": "x"}) is False


def test_update_article_missing_returns_false(fixture_db):
    assert db.update_article(999999, {"title": "x"}) is False


# ── admin endpoints (token-gated) ─────────────────────────────────────────────

TOKEN = "test-admin-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_get_article_requires_token(client, monkeypatch):
    # Token configured on the server, but the request carries no header -> 401.
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", TOKEN)
    assert client.get("/api/admin/article/1").status_code == 401


def test_get_article_returns_record(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", TOKEN)
    aid = _insert("https://doi.org/10.1/get", "10.1/get", "Gettable")
    resp = client.get(f"/api/admin/article/{aid}", headers=AUTH)
    assert resp.status_code == 200
    assert resp.get_json()["article"]["title"] == "Gettable"


def test_put_article_edits_field(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", TOKEN)
    aid = _insert("https://doi.org/10.1/put", "10.1/put", "Old &amp; busted")
    resp = client.put(f"/api/admin/article/{aid}", headers=AUTH,
                      json={"title": "New & shiny"})
    assert resp.status_code == 200
    assert db.get_article_by_id(aid)["title"] == "New & shiny"


def test_put_article_rejects_empty_edit(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", TOKEN)
    aid = _insert("https://doi.org/10.1/put2", "10.1/put2", "T")
    resp = client.put(f"/api/admin/article/{aid}", headers=AUTH,
                      json={"not_editable": "x"})
    assert resp.status_code == 400


def test_suppress_endpoint_removes_article(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", TOKEN)
    aid = _insert("https://doi.org/10.37514/wac-j.2026.2.1.99",
                  "10.37514/wac-j.2026.2.1.99", "vdf")
    resp = client.post(f"/api/admin/article/{aid}/suppress", headers=AUTH,
                       json={"reason": "test deposit"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "suppressed"
    assert db.get_article_by_id(aid) is None


def test_search_finds_by_id_and_title(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", TOKEN)
    aid = _insert("https://doi.org/10.1/search", "10.1/search", "Unique Zorp Title")
    by_id = client.get(f"/api/admin/article-search?q={aid}", headers=AUTH).get_json()
    assert by_id["results"][0]["id"] == aid
    by_title = client.get("/api/admin/article-search?q=Zorp", headers=AUTH).get_json()
    assert any(r["id"] == aid for r in by_title["results"])


def test_suppressed_list_and_unsuppress_endpoints(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", TOKEN)
    aid = _insert("https://doi.org/10.1/bl", "10.1/bl", "Blocklisted")
    client.post(f"/api/admin/article/{aid}/suppress", headers=AUTH, json={})
    listing = client.get("/api/admin/suppressed", headers=AUTH).get_json()
    assert any(r["doi"] == "10.1/bl" for r in listing["suppressed"])
    resp = client.post("/api/admin/unsuppress", headers=AUTH, json={"doi": "10.1/bl"})
    assert resp.get_json()["removed"] is True


def test_curate_page_renders(client):
    resp = client.get("/admin/curate")
    assert resp.status_code == 200
    assert b"Article Curation" in resp.data
