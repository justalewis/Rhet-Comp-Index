"""Tests for rate_limit.client_ip_key, the per-tier limits applied in app.py,
and the custom 429 handler. Determinism: the autouse _reset_rate_limiter
fixture in conftest.py clears the limiter's in-memory storage before and
after each test, so counts never leak between tests."""

import pytest

from rate_limit import client_ip_key, LIMITS, limiter


# ── client_ip_key ────────────────────────────────────────────────────────────


def test_client_ip_key_prefers_fly_header(app):
    with app.test_request_context(headers={"Fly-Client-IP": "1.2.3.4"}):
        assert client_ip_key() == "1.2.3.4"


def test_client_ip_key_strips_fly_header_whitespace(app):
    with app.test_request_context(headers={"Fly-Client-IP": "  1.2.3.4  "}):
        assert client_ip_key() == "1.2.3.4"


def test_client_ip_key_falls_back_to_xff(app):
    headers = {"X-Forwarded-For": "5.6.7.8, 9.10.11.12"}
    with app.test_request_context(headers=headers):
        assert client_ip_key() == "5.6.7.8"


def test_client_ip_key_xff_strips_whitespace(app):
    headers = {"X-Forwarded-For": "  5.6.7.8  ,  9.10.11.12"}
    with app.test_request_context(headers=headers):
        assert client_ip_key() == "5.6.7.8"


def test_client_ip_key_fly_header_overrides_xff(app):
    headers = {"Fly-Client-IP": "1.2.3.4", "X-Forwarded-For": "5.6.7.8"}
    with app.test_request_context(headers=headers):
        assert client_ip_key() == "1.2.3.4"


def test_client_ip_key_falls_back_to_remote_addr(app):
    with app.test_request_context(environ_base={"REMOTE_ADDR": "192.0.2.99"}):
        assert client_ip_key() == "192.0.2.99"


def test_client_ip_key_falls_back_to_localhost_when_remote_addr_none(app):
    """Flask's app.test_request_context defaults remote_addr to 127.0.0.1
    so we have to explicitly delete it to exercise the None branch."""
    with app.test_request_context() as ctx:
        ctx.request.environ["REMOTE_ADDR"] = None
        assert client_ip_key() == "127.0.0.1"


# ── Default tier (60/minute) ────────────────────────────────────────────────


def test_default_limit_applies(client):
    """61 rapid GETs to /api/articles (no per-tier override; uses default
    60/min). The 61st request returns 429."""
    last_status = None
    tripped_at = None
    for i in range(1, 70):
        resp = client.get("/api/articles")
        last_status = resp.status_code
        if resp.status_code == 429:
            tripped_at = i
            break
    assert tripped_at is not None, "default limit never tripped"
    assert tripped_at == 61, f"default tripped at request {tripped_at}, expected 61"


# ── Citations tier (20/minute) ──────────────────────────────────────────────


def test_citations_endpoint_has_stricter_limit(client):
    tripped_at = None
    for i in range(1, 30):
        resp = client.get("/api/citations/network")
        if resp.status_code == 429:
            tripped_at = i
            break
    assert tripped_at == 21, f"citations tripped at {tripped_at}, expected 21"


# ── Stats tier (20/minute) ──────────────────────────────────────────────────


def test_stats_endpoint_has_stricter_limit(client):
    tripped_at = None
    for i in range(1, 30):
        resp = client.get("/api/stats/timeline")
        if resp.status_code == 429:
            tripped_at = i
            break
    assert tripped_at == 21


# ── Search tier (120/minute) ────────────────────────────────────────────────


def test_search_endpoint_has_relaxed_limit(client):
    """Typeahead — 120/min. Verify 60 rapid requests do NOT trip."""
    for i in range(60):
        resp = client.get("/api/articles/search?q=composition")
        assert resp.status_code != 429, (
            f"search incorrectly tripped at {i+1}"
        )


# ── /health exemption ───────────────────────────────────────────────────────


def test_health_is_exempt(client):
    """200 rapid /health calls — all should succeed."""
    statuses = set()
    for _ in range(200):
        resp = client.get("/health")
        statuses.add(resp.status_code)
    assert statuses == {200}


# ── 429 response shape ──────────────────────────────────────────────────────


def test_429_returns_retry_after_header(client):
    """Trip the limit and inspect the 429 response."""
    for _ in range(25):
        resp = client.get("/api/citations/network")
        if resp.status_code == 429:
            break
    assert resp.status_code == 429
    retry_after = resp.headers.get("Retry-After")
    assert retry_after is not None
    assert int(retry_after) > 0


def test_429_json_response_for_api_routes(client):
    for _ in range(25):
        resp = client.get("/api/citations/network")
        if resp.status_code == 429:
            break
    assert resp.status_code == 429
    assert resp.headers["Content-Type"].startswith("application/json")
    body = resp.get_json()
    assert body["error"] == "rate limit exceeded"
    assert isinstance(body["retry_after"], int)
    assert body["retry_after"] > 0


def test_429_html_response_renders_error_template(client):
    """A browser request (Accept: text/html, non-/api/ path) past its limit
    receives the HTML error template instead of JSON."""
    for _ in range(70):
        resp = client.get("/", headers={"Accept": "text/html"})
        if resp.status_code == 429:
            break
    assert resp.status_code == 429
    assert resp.headers["Content-Type"].startswith("text/html")
    body = resp.get_data(as_text=True)
    assert "429" in body
    assert "Rate limit" in body or "rate limit" in body
    assert resp.headers.get("Retry-After") is not None


# ── /static/ exempt from default limit ──────────────────────────────────────


def test_static_endpoint_exempt_from_default_limit(client):
    """/static/ requests must not count against the 60/min default budget.
    Make 80 rapid static requests; none should 429."""
    for _ in range(80):
        resp = client.get("/static/style.css")
        try:
            # 200 if asset exists, 304/404 acceptable too — anything but 429.
            assert resp.status_code != 429
        finally:
            resp.close()  # release Flask's static-file handle


# ── /fetch limit + auth interaction ─────────────────────────────────────────


def test_fetch_unauthenticated_does_not_consume_budget(client, monkeypatch):
    """Unauthenticated /fetch requests should be exempt from the 6/hour
    counter (handled via exempt_when=fetch_auth_failing). Hammer it 30 times
    with no auth, then a single authenticated request must still succeed."""
    from unittest.mock import patch, MagicMock
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real-secret")
    # Probes — never reach the limiter because exempt_when returns True.
    for _ in range(30):
        resp = client.post("/fetch")
        assert resp.status_code == 401, f"unexpected status {resp.status_code}"

    # Now an authenticated request — budget should be untouched.
    with patch("app._run_background_fetch", MagicMock()):
        resp = client.post("/fetch", headers={"Authorization": "Bearer real-secret"})
    assert resp.status_code == 200


def test_fetch_authenticated_burst_eventually_429s(client, monkeypatch):
    """7 authenticated /fetch calls in rapid succession — the 7th must 429
    (limit is 6/hour)."""
    from unittest.mock import patch, MagicMock
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real-secret")
    tripped_at = None
    with patch("app._run_background_fetch", MagicMock()):
        for i in range(1, 10):
            resp = client.post("/fetch",
                               headers={"Authorization": "Bearer real-secret"})
            if resp.status_code == 429:
                tripped_at = i
                break
    assert tripped_at == 7, f"fetch tripped at {tripped_at}, expected 7"


# ── LIMITS dict sanity ──────────────────────────────────────────────────────


def test_limits_dict_has_all_tiers():
    assert set(LIMITS) == {"default", "citations", "stats", "search", "fetch"}


def test_limits_values_are_strings():
    for k, v in LIMITS.items():
        assert isinstance(v, str)
        assert "per" in v
