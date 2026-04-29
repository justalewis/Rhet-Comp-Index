"""All HTTP responses must carry the security headers set by
app.set_security_headers — verifies CSP, X-Frame-Options, HSTS, nosniff,
referrer policy."""

import pytest


@pytest.mark.parametrize("path", [
    "/", "/health", "/about", "/api/articles", "/coverage",
])
def test_security_headers_present(client, path):
    resp = client.get(path)
    h = resp.headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "max-age=31536000" in h["Strict-Transport-Security"]
    assert "default-src 'self'" in h["Content-Security-Policy"]


def test_csp_allows_inline_scripts(client):
    """Current CSP allows 'unsafe-inline' for scripts and styles. Locking
    this down is on the security backlog (B-prompts)."""
    resp = client.get("/")
    csp = resp.headers["Content-Security-Policy"]
    # Characterizes current state — flip when inline scripts are removed.
    assert "'unsafe-inline'" in csp


def test_404_has_security_headers(client):
    resp = client.get("/nonexistent-path")
    assert resp.status_code == 404
    assert resp.headers["X-Frame-Options"] == "DENY"
