"""Route smoke tests for /export, /fetch, /health — file downloads, the
fetch trigger, and the cheap health check."""

import re
import time
from unittest.mock import patch, MagicMock

import pytest


# ── /export bibtex ──────────────────────────────────────────────────────────


def test_export_bibtex_default(client):
    resp = client.get("/export")
    assert resp.status_code == 200
    ctype = resp.headers["Content-Type"]
    assert ctype.startswith("application/x-bibtex")
    body = resp.get_data(as_text=True)
    # Body has @article entries
    assert "@article{" in body


def test_export_bibtex_filename_in_disposition(client):
    resp = client.get("/export?format=bibtex")
    cd = resp.headers["Content-Disposition"]
    assert "rhet-comp-export.bib" in cd
    assert "attachment" in cd


def test_export_bibtex_grammar(client):
    """Each @article block must close with } on its own line."""
    resp = client.get("/export?format=bibtex&journal=College%20English")
    body = resp.get_data(as_text=True)
    blocks = re.findall(r"@article\{[^@]+\}", body, flags=re.S)
    assert len(blocks) > 0
    for b in blocks:
        assert b.startswith("@article{")
        assert b.rstrip().endswith("}")


def test_export_ris_format(client):
    resp = client.get("/export?format=ris")
    assert resp.status_code == 200
    ctype = resp.headers["Content-Type"]
    assert ctype.startswith("application/x-research-info-systems")
    body = resp.get_data(as_text=True)
    assert "TY  - JOUR" in body
    assert "ER  -" in body


def test_export_ris_filename(client):
    resp = client.get("/export?format=ris")
    assert "rhet-comp-export.ris" in resp.headers["Content-Disposition"]


def test_export_single_article(client):
    resp = client.get("/export?article_id=1")
    body = resp.get_data(as_text=True)
    # Article 1's BibTeX key includes 'smith' (first author last name)
    assert "@article{" in body
    # Only one @article block for single-article export
    assert body.count("@article{") == 1


def test_export_unknown_article_id(client):
    resp = client.get("/export?article_id=99999")
    body = resp.get_data(as_text=True)
    # 0 blocks when the article doesn't exist
    assert body.count("@article{") == 0


# ── /fetch (POST) ───────────────────────────────────────────────────────────


def test_fetch_route_starts_thread_does_not_run(client, monkeypatch):
    """POST /fetch must spawn a daemon thread targeting _run, but the
    background work must NOT execute during the test (no network calls).
    Auth-specific behavior is covered in test_auth.py."""
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "test-token")
    fake_thread = MagicMock()
    with patch("app.threading.Thread", return_value=fake_thread) as ThreadCls:
        resp = client.post("/fetch", headers={"Authorization": "Bearer test-token"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {"status": "fetch started"}
    # Thread was constructed with target=_run, daemon=True
    ThreadCls.assert_called_once()
    kwargs = ThreadCls.call_args.kwargs
    assert kwargs.get("daemon") is True
    assert callable(kwargs.get("target"))
    # And start() was invoked
    fake_thread.start.assert_called_once()


def test_fetch_route_get_returns_405(client):
    resp = client.get("/fetch")
    assert resp.status_code == 405


# ── /health ─────────────────────────────────────────────────────────────────


def test_health_returns_json_status_quickly(client):
    t0 = time.time()
    resp = client.get("/health")
    elapsed_ms = (time.time() - t0) * 1000
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("application/json")
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["admin_auth"] in {"configured", "missing"}
    # The route does NO db queries, so it should be fast.
    assert elapsed_ms < 50, f"/health took {elapsed_ms:.1f}ms (>50ms)"
