"""Route smoke tests for JSON API endpoints — each /api/* route must return
200, application/json, parseable JSON, and a top-level shape that matches
the contract in tests/_route_schemas.py."""

import json
import pytest

from tests._route_schemas import JSON_ROUTE_SCHEMAS


# Routes that need URL parameters substituted at test time. Each entry maps
# the schema-key URL to the actual URL the test client should hit.
URL_OVERRIDES = {
    "/api/citations/ego": "/api/citations/ego?article=1",
    "/api/articles/search": "/api/articles/search?q=composition",
    "/api/citations/reading-path": "/api/citations/reading-path?article=1",
}


@pytest.mark.parametrize("schema_url,required_keys", list(JSON_ROUTE_SCHEMAS.items()))
def test_api_route_returns_json_with_required_keys(client, schema_url, required_keys):
    url = URL_OVERRIDES.get(schema_url, schema_url)
    resp = client.get(url)
    assert resp.status_code == 200, f"{url}: status {resp.status_code}, body={resp.get_data(as_text=True)[:200]}"
    ctype = resp.headers.get("Content-Type", "")
    assert ctype.startswith("application/json"), f"{url}: ctype={ctype}"
    body = json.loads(resp.get_data(as_text=True))
    if required_keys:
        # Body might be a dict at top level
        assert isinstance(body, dict), f"{url}: not a dict"
        missing = required_keys - set(body)
        assert not missing, f"{url}: missing keys {missing}"


def test_api_articles_pagination(client):
    resp = client.get("/api/articles?limit=10&offset=10")
    body = resp.get_json()
    assert len(body["articles"]) == 10
    assert body["total"] == 50


def test_api_articles_filter_by_journal(client):
    resp = client.get("/api/articles?journal=College%20English")
    body = resp.get_json()
    assert all(a["journal"] == "College English" for a in body["articles"])


def test_api_articles_clamps_limit(client):
    """limit clamped to [1, 200] by _safe_int."""
    resp = client.get("/api/articles?limit=99999")
    body = resp.get_json()
    assert len(body["articles"]) <= 200


def test_api_citations_ego_unknown_article(client):
    resp = client.get("/api/citations/ego?article=99999")
    # Returns JSON; nodes list may be empty
    assert resp.status_code == 200
    body = resp.get_json()
    assert "focal_id" in body


def test_api_articles_search_returns_list(client):
    resp = client.get("/api/articles/search?q=composition")
    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body, (list, dict))  # search returns a list of articles


def test_api_stats_most_cited(client):
    resp = client.get("/api/stats/most-cited")
    assert resp.status_code == 200


def test_api_routes_have_cache_control(client):
    """Routes decorated with @cache_response set Cache-Control: public, max-age=...
    Sample one of them."""
    resp = client.get("/api/stats/timeline")
    assert resp.status_code == 200
    cc = resp.headers.get("Cache-Control", "")
    assert "max-age=" in cc
