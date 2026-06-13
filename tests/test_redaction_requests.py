"""P3 tests: the public request flow, email verification, and the admin review
queue (audit-before-redact, auth gating, rate limiting)."""

import re

import pytest

import db as _db
import redaction
import blueprints.redaction as bp_redaction

JANE = "Jane Smith"
ADMIN_TOKEN = "test-admin-token-xyz"


@pytest.fixture
def admin_env(monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", ADMIN_TOKEN)
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture
def captured_emails(monkeypatch):
    """Capture outbound emails instead of sending; return the list."""
    sent = []
    monkeypatch.setattr(bp_redaction, "send_email",
                        lambda to, subject, body: sent.append((to, subject, body)) or True)
    return sent


# ── module-level flow ─────────────────────────────────────────────────────────

def test_request_lifecycle_module(seeded_db):
    rid, token = redaction.create_request(JANE, email="a@b.edu", variants=["J. Smith"])
    assert rid and token
    req = redaction.get_request(rid)
    assert req["verification_status"] == "pending"
    # token is stored only as a hash
    assert req["verification_token_hash"] and token not in req["verification_token_hash"]

    assert redaction.verify_request_by_token(token) == rid
    assert redaction.get_request(rid)["verification_status"] == "verified"
    # token burned: second use fails
    assert redaction.verify_request_by_token(token) is None

    # approve → audited BEFORE redaction → author redacted
    redaction.decide_request(rid, "approved", actor="admin@test")
    assert _db.get_author_articles(JANE) == []
    events = [a["event"] for a in redaction.get_audit(rid)]
    assert events == ["created", "verified", "approved"]


# ── HTTP flow ─────────────────────────────────────────────────────────────────

def test_public_submit_sends_verification_email(client, captured_emails):
    resp = client.post("/redaction-request", data={
        "author_name": JANE, "email": "a@b.edu", "variants": "J. Smith",
    })
    assert resp.status_code == 200
    assert "confirmation link is on its way" in resp.get_data(as_text=True)
    assert len(captured_emails) == 1
    to, subject, body = captured_emails[0]
    assert to == "a@b.edu"
    assert "/redaction-request/verify/" in body


def test_submit_requires_name_and_email(client, captured_emails):
    resp = client.post("/redaction-request", data={"author_name": "", "email": ""})
    assert resp.status_code == 400
    assert captured_emails == []


def test_verify_link_marks_verified(client, captured_emails):
    client.post("/redaction-request", data={"author_name": JANE, "email": "a@b.edu"})
    body = captured_emails[0][2]
    verify_path = re.search(r"/redaction-request/verify/(\S+)", body).group(0)
    resp = client.get(verify_path)
    assert resp.status_code == 200
    assert "verified" in resp.get_data(as_text=True).lower()


def test_admin_queue_and_approve_redacts(client, admin_env, captured_emails):
    # submit + verify
    client.post("/redaction-request", data={"author_name": JANE, "email": "a@b.edu"})
    token = re.search(r"/verify/(\S+)\b", captured_emails[0][2]).group(1)
    redaction.verify_request_by_token(token)

    # admin sees it in the queue
    q = client.get("/api/admin/redaction-requests", headers=admin_env)
    assert q.status_code == 200
    reqs = q.get_json()["requests"]
    assert any(r["author_name_claimed"] == JANE for r in reqs)
    rid = next(r["id"] for r in reqs if r["author_name_claimed"] == JANE)

    # approve → author redacted
    resp = client.post(f"/api/admin/redaction-request/{rid}/approve", headers=admin_env)
    assert resp.status_code == 200
    assert _db.get_author_articles(JANE) == []


def test_admin_endpoints_require_token(client):
    assert client.get("/api/admin/redaction-requests").status_code in (401, 403, 503)
    assert client.post("/api/admin/redaction-request/1/approve").status_code in (401, 403, 503)


def test_cannot_approve_unverified_request(client, admin_env):
    rid, _ = redaction.create_request(JANE, email="a@b.edu")  # pending, not verified
    resp = client.post(f"/api/admin/redaction-request/{rid}/approve", headers=admin_env)
    assert resp.status_code == 409
    assert _db.get_author_articles(JANE) != []  # untouched


def test_request_form_rate_limited(client, captured_emails):
    last = None
    for _ in range(7):
        last = client.post("/redaction-request", data={"author_name": JANE, "email": "a@b.edu"})
    assert last.status_code == 429  # "5 per hour" cap tripped


def test_about_page_links_to_request_form(client):
    resp = client.get("/about")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Author Privacy" in body
    assert "/redaction-request" in body


def test_request_form_renders(client):
    resp = client.get("/redaction-request")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Request Name Removal" in body
    assert 'name="author_name"' in body
