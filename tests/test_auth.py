"""Tests for the admin-token decorator (auth.py) and its integration with
/fetch and /health. Covers each branch of the decorator: missing token env
var (503), missing/malformed header (401), wrong token (403), and the
happy path (the wrapped view runs)."""

from unittest.mock import patch, MagicMock

import pytest
from flask import Flask, jsonify

import auth


# ── Tiny throwaway Flask app for decorator-unit tests ────────────────────────


@pytest.fixture
def decorated_client():
    """A standalone Flask app with one route decorated by require_admin_token.
    Used to exercise the decorator in isolation from the main app."""
    test_app = Flask(__name__)

    @test_app.route("/private", methods=["POST"])
    @auth.require_admin_token
    def private():
        return jsonify({"ok": True})

    test_app.config["TESTING"] = True
    return test_app.test_client()


# ── Decorator unit tests ─────────────────────────────────────────────────────


def test_require_admin_token_missing_env_var_returns_503(decorated_client, monkeypatch):
    monkeypatch.delenv("PINAKES_ADMIN_TOKEN", raising=False)
    resp = decorated_client.post("/private",
                                 headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 503
    assert "configured" in resp.get_json()["error"]


def test_require_admin_token_empty_env_var_returns_503(decorated_client, monkeypatch):
    """An empty string is treated the same as missing."""
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "")
    resp = decorated_client.post("/private",
                                 headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 503


def test_require_admin_token_no_header_returns_401(decorated_client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "secret")
    resp = decorated_client.post("/private")
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "authentication required"}


def test_require_admin_token_wrong_scheme_returns_401(decorated_client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "secret")
    resp = decorated_client.post("/private",
                                 headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert resp.status_code == 401


def test_require_admin_token_bearer_without_value_returns_401(decorated_client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "secret")
    resp = decorated_client.post("/private",
                                 headers={"Authorization": "Bearer"})
    assert resp.status_code == 401


def test_require_admin_token_wrong_token_returns_403(decorated_client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "secret")
    resp = decorated_client.post("/private",
                                 headers={"Authorization": "Bearer not-the-secret"})
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "invalid credentials"}


def test_require_admin_token_correct_token_calls_view(decorated_client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "the-real-secret")
    resp = decorated_client.post("/private",
                                 headers={"Authorization": "Bearer the-real-secret"})
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


def test_require_admin_token_uses_constant_time_compare(decorated_client, monkeypatch):
    """Token comparison must use hmac.compare_digest, not ==, to defeat
    timing side-channels. Patch it and assert it was the comparator."""
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "secret")
    with patch("auth.hmac.compare_digest", return_value=True) as cmp_mock:
        resp = decorated_client.post("/private",
                                     headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 200
    cmp_mock.assert_called_once()
    # Both arguments are present and the configured value is one of them.
    args = cmp_mock.call_args.args
    assert "secret" in args


# ── Logging behavior ─────────────────────────────────────────────────────────


def test_auth_failure_logs_truncated_token(decorated_client, monkeypatch, caplog):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "the-correct-secret")
    full = "leaked-attacker-token-1234567890"
    with caplog.at_level("WARNING", logger="auth"):
        decorated_client.post("/private",
                              headers={"Authorization": f"Bearer {full}"})
    # The first four chars MAY appear; the full token MUST NOT.
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert full not in joined, "auth log leaked the full supplied token"
    assert "leak..." in joined  # truncated form (4 chars + ...)


def test_auth_no_header_log_does_not_include_token(decorated_client, monkeypatch, caplog):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "secret")
    with caplog.at_level("WARNING", logger="auth"):
        decorated_client.post("/private")
    joined = "\n".join(r.getMessage() for r in caplog.records)
    # Sanity: log mentions "missing/malformed"; never mentions the configured token.
    assert "secret" not in joined


# ── admin_token_configured ──────────────────────────────────────────────────


def test_admin_token_configured_true_when_set(monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "x")
    assert auth.admin_token_configured() is True


def test_admin_token_configured_false_when_missing(monkeypatch):
    monkeypatch.delenv("PINAKES_ADMIN_TOKEN", raising=False)
    assert auth.admin_token_configured() is False


def test_admin_token_configured_false_when_empty(monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "")
    assert auth.admin_token_configured() is False


# ── /fetch integration ──────────────────────────────────────────────────────


def test_fetch_endpoint_requires_auth_no_header(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    resp = client.post("/fetch")
    assert resp.status_code == 401


def test_fetch_endpoint_requires_auth_wrong_token(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    resp = client.post("/fetch", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 403


def test_fetch_endpoint_503_when_token_missing(client, monkeypatch):
    monkeypatch.delenv("PINAKES_ADMIN_TOKEN", raising=False)
    resp = client.post("/fetch", headers={"Authorization": "Bearer anything"})
    assert resp.status_code == 503


def test_fetch_endpoint_succeeds_with_correct_token(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    with patch("app._run_background_fetch", MagicMock()):
        resp = client.post("/fetch", headers={"Authorization": "Bearer real"})
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "fetch started"}


# ── /health reports admin_auth status ───────────────────────────────────────


def test_health_reports_admin_auth_configured(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "anything")
    resp = client.get("/health")
    assert resp.get_json()["admin_auth"] == "configured"


def test_health_reports_admin_auth_missing(client, monkeypatch):
    monkeypatch.delenv("PINAKES_ADMIN_TOKEN", raising=False)
    resp = client.get("/health")
    assert resp.get_json()["admin_auth"] == "missing"
