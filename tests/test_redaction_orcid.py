"""P4 tests: the ORCID OAuth verification path."""

import urllib.parse

import pytest

import db as _db
import redaction
import orcid_oauth
import blueprints.redaction as bp_redaction

JANE = "Jane Smith"


@pytest.fixture
def orcid_configured(monkeypatch):
    monkeypatch.setenv("ORCID_CLIENT_ID", "APP-TEST")
    monkeypatch.setenv("ORCID_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ORCID_ENV", "sandbox")


def test_orcid_button_hidden_when_unconfigured(client, monkeypatch):
    monkeypatch.delenv("ORCID_CLIENT_ID", raising=False)
    resp = client.get("/redaction-request")
    assert resp.status_code == 200
    assert "Verify with ORCID" not in resp.get_data(as_text=True)


def test_orcid_method_redirects_to_orcid(client, orcid_configured):
    resp = client.post("/redaction-request", data={
        "author_name": JANE, "method": "orcid",
    })
    assert resp.status_code == 302
    loc = resp.headers["Location"]
    assert "sandbox.orcid.org/oauth/authorize" in loc
    assert "scope=%2Fauthenticate" in loc or "scope=/authenticate" in loc
    # A pending ORCID request was created.
    reqs = redaction.list_requests(status="pending")
    assert any(r["verification_method"] == "orcid" for r in reqs)


def test_orcid_method_unconfigured_errors(client, monkeypatch):
    monkeypatch.delenv("ORCID_CLIENT_ID", raising=False)
    resp = client.post("/redaction-request", data={"author_name": JANE, "method": "orcid"})
    assert resp.status_code == 400
    assert "available right now" in resp.get_data(as_text=True)


def test_orcid_callback_verifies_request(client, orcid_configured, monkeypatch):
    # Kick off ORCID to mint a signed state bound to the request.
    resp = client.post("/redaction-request", data={"author_name": JANE, "method": "orcid"})
    state = urllib.parse.parse_qs(urllib.parse.urlparse(resp.headers["Location"]).query)["state"][0]

    # Mock the token exchange (no real network).
    monkeypatch.setattr(orcid_oauth, "exchange_code",
                        lambda code, redirect_uri: {"orcid": "0000-0001-1111-1111", "name": JANE})

    cb = client.get(f"/redaction-request/orcid/callback?code=abc&state={urllib.parse.quote(state)}")
    assert cb.status_code == 200
    assert "verified" in cb.get_data(as_text=True).lower()

    reqs = redaction.list_requests(status="verified")
    req = next(r for r in reqs if r["author_name_claimed"] == JANE)
    assert req["requester_orcid"] == "0000-0001-1111-1111"
    # Audit recorded the verified name + loose-match signal.
    detail = " ".join(a["detail"] or "" for a in redaction.get_audit(req["id"]))
    assert "name_match=True" in detail


def test_orcid_callback_rejects_bad_state(client, orcid_configured):
    cb = client.get("/redaction-request/orcid/callback?code=abc&state=forged")
    assert cb.status_code == 400
    assert "isn't valid" in cb.get_data(as_text=True)


def test_loose_name_match():
    assert redaction._loose_name_match("Jane Q. Smith", "Jane Smith") is True
    assert redaction._loose_name_match("Jane Smith", "Robert Jones") is False
