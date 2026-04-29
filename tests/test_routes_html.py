"""Route smoke tests for HTML pages — every Jinja-rendered route returns 200,
sets Content-Type: text/html, and includes a page-specific heading. Route
parameters use seeded entity ids."""

import pytest

from tests._route_schemas import HTML_ROUTE_HEADINGS

# Static routes (no URL params) listed from HTML_ROUTE_HEADINGS keys.
PARAM_ROUTES_HTML = [
    ("/article/1",          "essay"),       # article detail — title contains "essay"
    ("/author/Jane Smith",  "Jane Smith"),
    ("/book/1",             "Composition Pedagogy"),
    ("/institution/1",      "U of Iowa"),
    ("/citations?article=1", "essay"),  # ego network page; article=1 has "essay" in title
]


@pytest.mark.parametrize("path,heading", list(HTML_ROUTE_HEADINGS.items()))
def test_html_route_returns_200_and_heading(client, path, heading):
    resp = client.get(path)
    assert resp.status_code == 200
    ctype = resp.headers.get("Content-Type", "")
    assert ctype.startswith("text/html"), f"{path}: unexpected Content-Type {ctype}"
    body = resp.get_data(as_text=True)
    assert heading in body, f"{path}: heading {heading!r} missing from body"


@pytest.mark.parametrize("path,marker", PARAM_ROUTES_HTML)
def test_html_param_route(client, path, marker):
    resp = client.get(path)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert marker in body, f"{path}: marker {marker!r} missing"


def test_index_pagination(client):
    resp = client.get("/?page=2")
    assert resp.status_code == 200


def test_index_filter_by_journal(client):
    resp = client.get("/?journal=College%20English")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "College English" in body


def test_index_search_query(client):
    resp = client.get("/?q=composition")
    assert resp.status_code == 200


def test_index_safe_int_clamps_negative_page(client):
    """page=-5 should clamp to 1 (per _safe_int lo=1) — no 5xx."""
    resp = client.get("/?page=-5")
    assert resp.status_code == 200


def test_index_invalid_page_text(client):
    """Non-numeric ?page= falls back to default 1."""
    resp = client.get("/?page=not-a-number")
    assert resp.status_code == 200


def test_authors_letter_filter(client):
    resp = client.get("/authors?letter=S")
    assert resp.status_code == 200


def test_book_detail_unknown_returns_404(client):
    resp = client.get("/book/99999")
    # The route returns "Book not found" with 404.
    assert resp.status_code == 404


def test_institution_detail_unknown_returns_404(client):
    resp = client.get("/institution/99999")
    assert resp.status_code == 404


def test_404_renders_custom_error_template(client):
    """An unknown URL hits the custom 404 handler, which renders error.html."""
    resp = client.get("/this-path-does-not-exist")
    assert resp.status_code == 404
    body = resp.get_data(as_text=True)
    # error.html includes "Page not found" message
    assert "404" in body or "not found" in body.lower()


def test_coverage_with_since_filter(client):
    resp = client.get("/coverage?since=2020")
    assert resp.status_code == 200


def test_coverage_with_invalid_since(client):
    """Invalid ?since= falls through to None (the route validates against
    COVERAGE_SINCE_PRESETS)."""
    resp = client.get("/coverage?since=banana")
    assert resp.status_code == 200


def test_most_cited_view_modes(client):
    for view in ("all", "decade", "journal", "topic"):
        resp = client.get(f"/most-cited?view={view}")
        assert resp.status_code == 200, f"view={view}"


def test_books_filters(client):
    resp = client.get("/books?publisher=WAC%20Clearinghouse")
    assert resp.status_code == 200
    resp = client.get("/books?type=monograph")
    assert resp.status_code == 200


# ── Route count + completeness ────────────────────────────────────────────────


def test_route_count_matches_expected(client):
    """A removed or added route is caught immediately."""
    rules = [r for r in client.application.url_map.iter_rules()
             if r.endpoint != "static"]
    # 44 user-defined routes after Prompt B3 added /health/ready and
    # /health/deep (was 42 before).
    assert len(rules) == 44, (
        f"Expected 44 routes, got {len(rules)}. "
        "If you intentionally added/removed a route, update this test."
    )
