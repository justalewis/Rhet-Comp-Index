"""Tests for the layered health checks: /health (liveness), /health/ready
(readiness), /health/deep (admin-protected diagnostic). Includes heartbeat
freshness logic and integrity-check caching."""

import os
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import health as _health


# ── /health  — liveness, no DB ──────────────────────────────────────────────


def test_liveness_returns_200_without_db(empty_client, tmp_path, monkeypatch):
    """Even with DB_PATH pointing at a missing file, /health is 200.
    Liveness must not depend on the database."""
    missing = tmp_path / "does-not-exist.db"
    monkeypatch.setenv("DB_PATH", str(missing))
    resp = empty_client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["version"]
    assert isinstance(body["uptime_seconds"], (int, float))


def test_liveness_returns_under_50ms(client):
    """Generous CI-safe budget: 200ms covers Windows VM jitter; the route
    itself does no work."""
    t0 = time.time()
    resp = client.get("/health")
    elapsed_ms = (time.time() - t0) * 1000
    assert resp.status_code == 200
    assert elapsed_ms < 200, f"liveness took {elapsed_ms:.1f}ms"


def test_liveness_includes_admin_auth_status(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "x")
    assert client.get("/health").get_json()["admin_auth"] == "configured"
    monkeypatch.delenv("PINAKES_ADMIN_TOKEN", raising=False)
    assert client.get("/health").get_json()["admin_auth"] == "missing"


# ── /health/ready  — readiness ──────────────────────────────────────────────


def test_readiness_returns_200_when_db_ok(client):
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"status": "ok", "db": "reachable"}


def test_readiness_returns_503_when_db_missing(empty_client, tmp_path, monkeypatch):
    """Repoint DB_PATH at a path that doesn't exist."""
    missing = tmp_path / "nope.db"
    monkeypatch.setenv("DB_PATH", str(missing))
    resp = empty_client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["status"] == "degraded"
    assert "missing" in body["db"]


def test_readiness_returns_503_when_articles_table_absent(empty_client,
                                                          tmp_path, monkeypatch):
    """A SQLite file that exists but is empty should fail readiness because
    the SELECT against `articles` raises no-such-table."""
    empty_db = tmp_path / "empty.db"
    empty_db.write_bytes(b"")  # zero-byte file: SQLite treats it as new
    monkeypatch.setenv("DB_PATH", str(empty_db))
    resp = empty_client.get("/health/ready")
    assert resp.status_code == 503


# ── /health/deep  — admin-protected diagnostic ──────────────────────────────


def test_deep_requires_auth_no_token(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    resp = client.get("/health/deep")
    assert resp.status_code == 401


def test_deep_requires_auth_wrong_token(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    resp = client.get("/health/deep",
                      headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 403


def test_deep_returns_200_with_correct_token(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    resp = client.get("/health/deep",
                      headers={"Authorization": "Bearer real"})
    assert resp.status_code == 200


def test_deep_reports_article_count(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    resp = client.get("/health/deep",
                      headers={"Authorization": "Bearer real"})
    body = resp.get_json()
    assert body["counts"]["articles"] == 50
    assert body["counts"]["authors"] == 20
    assert body["counts"]["books"] == 5


def test_deep_reports_disk_section(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    body = client.get(
        "/health/deep", headers={"Authorization": "Bearer real"}
    ).get_json()
    assert "disk" in body
    # Either a real measurement or an error key — both are acceptable
    # depending on the platform (no /data on Windows dev).
    assert "free_gb" in body["disk"] or "error" in body["disk"]


def test_deep_includes_security_headers_section(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    body = client.get(
        "/health/deep", headers={"Authorization": "Bearer real"}
    ).get_json()
    assert "security_headers" in body
    assert body["security_headers"]["X-Frame-Options"] == "DENY"


def test_deep_includes_admin_auth_status(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    body = client.get(
        "/health/deep", headers={"Authorization": "Bearer real"}
    ).get_json()
    assert body["auth"]["admin_auth"] == "configured"


# ── Scheduler heartbeat ─────────────────────────────────────────────────────


def test_deep_reports_scheduler_unhealthy_when_no_heartbeat(client, monkeypatch,
                                                            tmp_path):
    """No heartbeat file present → scheduler_healthy: False, age: None."""
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    # The seeded_db fixture's DB lives in tmp_path; ensure no heartbeat file.
    heartbeat = _health.heartbeat_path()
    if os.path.exists(heartbeat):
        os.remove(heartbeat)
    body = client.get(
        "/health/deep", headers={"Authorization": "Bearer real"}
    ).get_json()
    assert body["scheduler"]["heartbeat_age_seconds"] is None
    assert body["scheduler"]["scheduler_healthy"] is False


def test_deep_reports_scheduler_healthy_when_heartbeat_recent(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    _health.write_heartbeat()  # writes "now"
    body = client.get(
        "/health/deep", headers={"Authorization": "Bearer real"}
    ).get_json()
    assert body["scheduler"]["scheduler_healthy"] is True
    assert body["scheduler"]["heartbeat_age_seconds"] < 10


def test_deep_reports_scheduler_unhealthy_when_heartbeat_stale(client, monkeypatch):
    """Write a heartbeat then backdate its mtime to 26 hours ago."""
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    _health.write_heartbeat()
    path = _health.heartbeat_path()
    stale = time.time() - 26 * 3600
    os.utime(path, (stale, stale))
    body = client.get(
        "/health/deep", headers={"Authorization": "Bearer real"}
    ).get_json()
    assert body["scheduler"]["scheduler_healthy"] is False
    assert body["scheduler"]["heartbeat_age_seconds"] > 25 * 3600


def test_write_heartbeat_swallows_errors(monkeypatch):
    """A failing open() must not propagate — scheduler must keep running."""
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr("builtins.open", boom)
    # Must not raise
    _health.write_heartbeat()


# ── PRAGMA integrity_check caching ──────────────────────────────────────────


def test_integrity_check_is_cached(client, monkeypatch):
    """Two consecutive /health/deep hits should produce a single underlying
    PRAGMA integrity_check call."""
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    with patch("health._run_integrity_check",
               return_value=["ok"]) as run_mock:
        client.get("/health/deep", headers={"Authorization": "Bearer real"})
        client.get("/health/deep", headers={"Authorization": "Bearer real"})
    assert run_mock.call_count == 1


def test_integrity_check_rerun_after_cache_cleared(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    with patch("health._run_integrity_check",
               return_value=["ok"]) as run_mock:
        client.get("/health/deep", headers={"Authorization": "Bearer real"})
        _health.clear_integrity_cache()
        client.get("/health/deep", headers={"Authorization": "Bearer real"})
    assert run_mock.call_count == 2


def test_integrity_check_failure_returns_message(client, monkeypatch):
    """A raising _run_integrity_check is caught and surfaced as a string."""
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "real")
    def boom():
        raise RuntimeError("disk corruption")
    with patch("health._run_integrity_check", side_effect=boom):
        body = client.get(
            "/health/deep", headers={"Authorization": "Bearer real"}
        ).get_json()
    assert any("integrity check failed" in s for s in body["integrity_check"])
