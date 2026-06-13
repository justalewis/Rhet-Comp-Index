"""Render-layer tests for author redaction: no real name reaches any HTML
surface, the friendly tag shows, old name URLs 404, FTS forgets the name.

Uses the Flask test client against the seeded DB with Jane Smith redacted, so
a template Jinja error (from the new filters/conditionals) fails the test too.
"""

import urllib.parse

import pytest

import db as _db
from redaction import redact_author, DISPLAY_TEXT

JANE = "Jane Smith"


@pytest.fixture
def redacted_client(client):
    """Seeded test client with Jane Smith redacted. Yields (client, token)."""
    token = redact_author(JANE)["token"]
    return client, token


def test_author_token_page_renders_without_name(redacted_client):
    client, token = redacted_client
    resp = client.get("/author/" + urllib.parse.quote(token))
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert JANE not in body
    assert DISPLAY_TEXT in body
    # The name-based CompPile-by-author search is hidden for redacted authors.
    assert "Search for" not in body or "in CompPile" not in body


def test_old_name_url_404s(redacted_client):
    client, _ = redacted_client
    resp = client.get("/author/" + urllib.parse.quote(JANE))
    assert resp.status_code == 404
    assert JANE not in resp.get_data(as_text=True)


def test_article_page_has_no_name_or_citation_meta(redacted_client):
    client, token = redacted_client
    # Article 1 is Jane's hub paper.
    resp = client.get("/article/1")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert JANE not in body
    assert DISPLAY_TEXT in body
    # No citation-manager metadata emitted for the redacted author.
    assert f'citation_author" content="{token}"' not in body
    assert f'DC.creator" content="{token}"' not in body
    assert JANE not in body  # belt and suspenders


def test_index_and_authors_pages_have_no_name(redacted_client):
    client, _ = redacted_client
    for path in ("/", "/authors?letter=S", "/most-cited"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert JANE not in resp.get_data(as_text=True), path


def test_fts_search_forgets_the_name(redacted_client):
    client, _ = redacted_client
    with _db.get_conn() as conn:
        rows = conn.execute(
            "SELECT a.id FROM articles a "
            "WHERE a.id IN (SELECT rowid FROM articles_fts WHERE articles_fts MATCH ?)",
            ('"Jane Smith"',),
        ).fetchall()
    assert rows == [], "FTS index still matches the redacted name"


def test_autocomplete_api_has_no_name(redacted_client):
    client, _ = redacted_client
    resp = client.get("/api/articles/search?q=Jane")
    assert resp.status_code == 200
    assert JANE not in resp.get_data(as_text=True)
