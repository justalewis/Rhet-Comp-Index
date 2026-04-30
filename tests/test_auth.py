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


# /health's admin_auth surfacing is exercised in tests/test_health.py
# (test_liveness_includes_admin_auth_status). Don't duplicate here.


# ── /api/admin/run-backup integration ──────────────────────────────────────


def test_run_backup_endpoint_requires_auth_no_header(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    resp = client.post("/api/admin/run-backup")
    assert resp.status_code == 401


def test_run_backup_endpoint_requires_auth_wrong_token(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    resp = client.post("/api/admin/run-backup",
                       headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 403


def test_run_backup_endpoint_returns_summary(client, monkeypatch):
    """With auth + a stubbed run_backup, the endpoint returns the summary
    dict and writes a heartbeat on success."""
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    fake_summary = {
        "success": True,
        "snapshot_bytes": 12345,
        "compressed_bytes": 6789,
        "uploaded_to": "s3://test-bucket/2026/04/30/x.db.zst.age",
        "duration_seconds": 1.5,
        "pruned_keys": [],
        "error": None,
    }
    with patch("backup.run_backup", return_value=fake_summary), \
         patch("health.write_heartbeat") as wh:
        resp = client.post("/api/admin/run-backup",
                           headers={"Authorization": "Bearer real"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["uploaded_to"] == "s3://test-bucket/2026/04/30/x.db.zst.age"
    wh.assert_called_once()


def test_run_backup_endpoint_returns_500_on_failure_and_no_heartbeat(
    client, monkeypatch,
):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    fail_summary = {
        "success": False,
        "snapshot_bytes": None,
        "compressed_bytes": None,
        "uploaded_to": None,
        "duration_seconds": 0.1,
        "pruned_keys": [],
        "error": "missing env vars: ['PINAKES_BACKUP_BUCKET']",
    }
    with patch("backup.run_backup", return_value=fail_summary), \
         patch("health.write_heartbeat") as wh:
        resp = client.post("/api/admin/run-backup",
                           headers={"Authorization": "Bearer real"})
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["success"] is False
    assert "missing env vars" in body["error"]
    wh.assert_not_called()
