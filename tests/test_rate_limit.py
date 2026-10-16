"""Tests for rate_limit.client_ip_key, the per-tier limits applied in app.py,
and the custom 429 handler. Determinism: the autouse _reset_rate_limiter
fixture in conftest.py clears the limiter's in-memory storage before and
after each test, so counts never leak between tests."""

import pytest

from rate_limit import client_ip_key, resolve_client_ip, LIMITS, limiter


# ── client_ip_key ────────────────────────────────────────────────────────────


@pytest.fixture
def on_fly(monkeypatch):
    """Fly sets FLY_APP_NAME on every machine; only then is Fly-Client-IP
    believed."""
    monkeypatch.setenv("FLY_APP_NAME", "rhet-comp-index")


def test_client_ip_key_prefers_fly_header(app, on_fly):
    with app.test_request_context(headers={"Fly-Client-IP": "1.2.3.4"}):
        assert client_ip_key() == "1.2.3.4"


def test_client_ip_key_strips_fly_header_whitespace(app, on_fly):
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


def test_client_ip_key_fly_header_overrides_xff(app, on_fly):
    headers = {"Fly-Client-IP": "1.2.3.4", "X-Forwarded-For": "5.6.7.8"}
    with app.test_request_context(headers=headers):
        assert client_ip_key() == "1.2.3.4"


def test_client_ip_key_trusts_cf_when_peer_is_cloudflare(app, on_fly):
    # Peer (Fly-Client-IP) is a Cloudflare edge IP → trust CF-Connecting-IP
    # as the real visitor.
    headers = {"Fly-Client-IP": "162.158.0.1",
               "CF-Connecting-IP": "203.0.113.55"}
    with app.test_request_context(headers=headers):
        assert client_ip_key() == "203.0.113.55"


def test_client_ip_key_ignores_spoofed_cf_from_non_cloudflare_peer(app, on_fly):
    # Peer is NOT Cloudflare → CF-Connecting-IP is untrusted (spoof attempt);
    # fall back to the actual peer so the block / rate limit can't be evaded.
    headers = {"Fly-Client-IP": "47.79.205.10",
               "CF-Connecting-IP": "8.8.8.8"}
    with app.test_request_context(headers=headers):
        assert client_ip_key() == "47.79.205.10"


def test_client_ip_key_falls_back_to_remote_addr(app):
    with app.test_request_context(environ_base={"REMOTE_ADDR": "192.0.2.99"}):
        assert client_ip_key() == "192.0.2.99"


def test_client_ip_key_falls_back_to_localhost_when_remote_addr_none(app):
    """Flask's app.test_request_context defaults remote_addr to 127.0.0.1
    so we have to explicitly delete it to exercise the None branch."""
    with app.test_request_context() as ctx:
        ctx.request.environ["REMOTE_ADDR"] = None
        assert client_ip_key() == "127.0.0.1"


# ── PINAKES_TRUSTED_IP_HEADER (reverse proxy on the same machine) ────────────
# The WAC Clearinghouse chain is Cloudflare → IIS/ARR → Waitress, so every
# request reaches Flask from 127.0.0.1 and the visitor is in a header.
# Visitor IPs here are real public addresses: ipaddress counts the RFC 5737
# documentation ranges as private, and private hops are skipped.

_VIA_LOCAL_PROXY = {"REMOTE_ADDR": "127.0.0.1"}


def test_trusted_cf_connecting_ip_from_local_proxy(app, monkeypatch):
    monkeypatch.setenv("PINAKES_TRUSTED_IP_HEADER", "CF-Connecting-IP")
    with app.test_request_context(headers={"CF-Connecting-IP": "81.2.69.160"},
                                  environ_base=_VIA_LOCAL_PROXY):
        assert resolve_client_ip() == ("81.2.69.160", "CF-Connecting-IP")


def test_trusted_header_ignored_when_peer_is_public(app, monkeypatch):
    # Waitress is bound to 0.0.0.0 on the Windows box. A visitor who reaches
    # it directly must not be able to name their own IP.
    monkeypatch.setenv("PINAKES_TRUSTED_IP_HEADER", "CF-Connecting-IP")
    with app.test_request_context(headers={"CF-Connecting-IP": "8.8.8.8"},
                                  environ_base={"REMOTE_ADDR": "9.9.9.9"}):
        assert client_ip_key() == "9.9.9.9"


def test_trusted_header_absent_uses_remote_addr_not_xff(app, monkeypatch):
    # Once a trusted header is configured, the spoofable first-XFF fallback
    # is off.
    monkeypatch.setenv("PINAKES_TRUSTED_IP_HEADER", "CF-Connecting-IP")
    with app.test_request_context(headers={"X-Forwarded-For": "5.6.7.8"},
                                  environ_base=_VIA_LOCAL_PROXY):
        assert resolve_client_ip() == ("127.0.0.1", "remote_addr")


def test_trusted_xff_is_read_from_the_right(app, monkeypatch):
    # A client-invented entry on the left, then the visitor Cloudflare saw,
    # then the Cloudflare edge that ARR appended with its port.
    monkeypatch.setenv("PINAKES_TRUSTED_IP_HEADER", "X-Forwarded-For")
    headers = {"X-Forwarded-For": "6.6.6.6, 81.2.69.160, 162.158.1.2:51234"}
    with app.test_request_context(headers=headers, environ_base=_VIA_LOCAL_PROXY):
        assert client_ip_key() == "81.2.69.160"


def test_trusted_xff_parses_bracketed_ipv6_with_port(app, monkeypatch):
    monkeypatch.setenv("PINAKES_TRUSTED_IP_HEADER", "X-Forwarded-For")
    headers = {"X-Forwarded-For": "[2600:1700::7]:443"}
    with app.test_request_context(headers=headers, environ_base=_VIA_LOCAL_PROXY):
        assert client_ip_key() == "2600:1700::7"


def test_trusted_xff_of_only_cloudflare_hops_uses_cf_connecting_ip(app, monkeypatch):
    monkeypatch.setenv("PINAKES_TRUSTED_IP_HEADER", "X-Forwarded-For")
    headers = {"X-Forwarded-For": "162.158.1.2:51234",
               "CF-Connecting-IP": "81.2.69.160"}
    with app.test_request_context(headers=headers, environ_base=_VIA_LOCAL_PROXY):
        assert client_ip_key() == "81.2.69.160"


def test_trusted_xff_stops_at_an_unparseable_entry(app, monkeypatch):
    monkeypatch.setenv("PINAKES_TRUSTED_IP_HEADER", "X-Forwarded-For")
    headers = {"X-Forwarded-For": "81.2.69.160, not-an-ip"}
    with app.test_request_context(headers=headers, environ_base=_VIA_LOCAL_PROXY):
        assert client_ip_key() == "127.0.0.1"


def test_fly_header_still_wins_when_trusted_header_set(app, monkeypatch, on_fly):
    monkeypatch.setenv("PINAKES_TRUSTED_IP_HEADER", "CF-Connecting-IP")
    headers = {"Fly-Client-IP": "47.79.205.10", "CF-Connecting-IP": "8.8.8.8"}
    with app.test_request_context(headers=headers):
        assert client_ip_key() == "47.79.205.10"


def test_forged_xff_cannot_pose_as_the_local_proxy(client, monkeypatch):
    # ProxyFix rewrites remote_addr from X-Forwarded-For, so a direct visitor
    # sending "X-Forwarded-For: 127.0.0.1" would look local if the check read
    # remote_addr. It reads the socket peer ProxyFix saved instead.
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real-secret")
    monkeypatch.setenv("PINAKES_TRUSTED_IP_HEADER", "CF-Connecting-IP")
    resp = client.get("/api/admin/client-ip",
                      environ_base={"REMOTE_ADDR": "9.9.9.9"},
                      headers={"Authorization": "Bearer real-secret",
                               "X-Forwarded-For": "127.0.0.1",
                               "CF-Connecting-IP": "81.2.69.160"})
    body = resp.get_json()
    assert body["remote_addr"] == "127.0.0.1"
    assert body["socket_peer"] == "9.9.9.9"
    assert body["ip"] == "9.9.9.9"


def test_forged_fly_header_is_ignored_off_fly(app, monkeypatch):
    # The Clearinghouse chain passes custom headers straight through, so a
    # visitor's own Fly-Client-IP must count for nothing there.
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.setenv("PINAKES_TRUSTED_IP_HEADER", "CF-Connecting-IP")
    headers = {"Fly-Client-IP": "9.9.9.9", "CF-Connecting-IP": "81.2.69.160"}
    with app.test_request_context(headers=headers, environ_base=_VIA_LOCAL_PROXY):
        assert resolve_client_ip() == ("81.2.69.160", "CF-Connecting-IP")


def test_forged_fly_and_cloudflare_pair_is_ignored_off_fly(app, monkeypatch):
    # A Cloudflare-range Fly-Client-IP plus a CF-Connecting-IP of the
    # visitor's choosing must not be read as "came through Cloudflare".
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.delenv("PINAKES_TRUSTED_IP_HEADER", raising=False)
    headers = {"Fly-Client-IP": "162.158.0.1", "CF-Connecting-IP": "8.8.8.8"}
    with app.test_request_context(headers=headers, environ_base={"REMOTE_ADDR": "9.9.9.9"}):
        assert client_ip_key() == "9.9.9.9"


def test_forged_fly_header_gets_no_fresh_budget_off_fly(client, monkeypatch):
    monkeypatch.delenv("FLY_APP_NAME", raising=False)
    monkeypatch.setenv("PINAKES_TRUSTED_IP_HEADER", "CF-Connecting-IP")
    for i in range(60):
        client.get("/api/articles", headers={"CF-Connecting-IP": "81.2.69.1",
                                             "Fly-Client-IP": f"9.9.9.{i % 250}"})
    assert client.get("/api/articles", headers={"CF-Connecting-IP": "81.2.69.1",
                                                "Fly-Client-IP": "9.9.9.251"}).status_code == 429


def test_trusted_header_feeds_the_rate_limiter(client, monkeypatch):
    # Two visitors behind the same local proxy get separate budgets.
    monkeypatch.setenv("PINAKES_TRUSTED_IP_HEADER", "CF-Connecting-IP")
    for _ in range(60):
        client.get("/api/articles", headers={"CF-Connecting-IP": "81.2.69.1"})
    assert client.get("/api/articles",
                      headers={"CF-Connecting-IP": "81.2.69.1"}).status_code == 429
    assert client.get("/api/articles",
                      headers={"CF-Connecting-IP": "81.2.69.2"}).status_code == 200


# ── /api/admin/client-ip ─────────────────────────────────────────────────────


def test_client_ip_check_requires_token(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real-secret")
    assert client.get("/api/admin/client-ip").status_code == 401


def test_client_ip_check_reports_resolution(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real-secret")
    monkeypatch.setenv("PINAKES_TRUSTED_IP_HEADER", "CF-Connecting-IP")
    resp = client.get("/api/admin/client-ip", headers={
        "Authorization": "Bearer real-secret",
        "CF-Connecting-IP": "81.2.69.160",
        "X-Forwarded-For": "6.6.6.6",
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ip"] == "81.2.69.160"
    assert body["source"] == "CF-Connecting-IP"
    assert body["trusted_ip_header"] == "CF-Connecting-IP"
    assert body["socket_peer_is_local_proxy"] is True
    assert body["headers"]["X-Forwarded-For"] == "6.6.6.6"
    assert body["candidates"]["CF-Connecting-IP"] == "81.2.69.160"
    assert body["candidates"]["X-Forwarded-For"] == "6.6.6.6"
    assert body["external_url"].endswith("/health")


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
    assert set(LIMITS) == {"default", "citations", "stats", "search", "fetch",
                           "redaction_request", "tag_feedback", "tag_suggestion",
                           "alert_signup", "usage_survey"}


def test_limits_values_are_strings():
    for k, v in LIMITS.items():
        assert isinstance(v, str)
        assert "per" in v
