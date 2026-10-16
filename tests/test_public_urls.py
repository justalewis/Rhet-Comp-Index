"""Absolute URLs that leave the site: emailed verification links and the ORCID
OAuth redirect_uri.

On the Clearinghouse box a request reaches Flask from IIS/ARR on
127.0.0.1:8080 with no forwarding headers, because Waitress 3.x deletes
X-Forwarded-Host, X-Forwarded-Proto, X-Forwarded-For and Forwarded first.
ProxyFix then has nothing to read, and a URL built from the request reads
http://127.0.0.1:8080/... . The `iis` fixture reproduces that request shape;
with PINAKES_SITE_URL set, every outbound link must use the public host anyway.
"""

import pathlib
import re
import urllib.parse

import pytest

import blueprints.alerts as bp_alerts
import blueprints.redaction as bp_redaction
import orcid_oauth

PUBLIC = "https://pinakes.wacclearinghouse.org"
JANE = "Jane Smith"
ADMIN_TOKEN = "test-admin-token-xyz"


@pytest.fixture(autouse=True)
def _features_on(monkeypatch):
    """The redaction form and alerts both ship off; these tests need them on."""
    monkeypatch.setenv("PINAKES_REDACTION_FORM_ENABLED", "1")
    monkeypatch.setenv("PINAKES_ALERTS_ENABLED", "1")


@pytest.fixture
def iis(monkeypatch):
    """What Waitress hands Flask on the Clearinghouse box: IIS's own
    connection to 127.0.0.1:8080 over plain http, no X-Forwarded-*, and the
    service environment's PINAKES_SITE_URL. Returns the request kwargs."""
    monkeypatch.setenv("PINAKES_SITE_URL", PUBLIC)
    return {"base_url": "http://127.0.0.1:8080",
            "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}


@pytest.fixture
def emails(monkeypatch):
    """Capture every outbound email from both blueprints."""
    sent = []

    def _capture(to, subject, body):
        sent.append((to, subject, body))
        return True

    monkeypatch.setattr(bp_redaction, "send_email", _capture)
    monkeypatch.setattr(bp_alerts, "send_email", _capture)
    return sent


@pytest.fixture
def orcid_configured(monkeypatch):
    monkeypatch.setenv("ORCID_CLIENT_ID", "APP-TEST")
    monkeypatch.setenv("ORCID_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ORCID_ENV", "sandbox")


def _links(body):
    return re.findall(r"https?://\S+", body)


def _redirect_uri(location):
    query = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    return query["redirect_uri"][0], query["state"][0]


# ── The IIS case ─────────────────────────────────────────────────────────────

def test_verification_email_link_uses_public_host(client, iis, emails):
    resp = client.post("/redaction-request",
                       data={"author_name": JANE, "email": "a@b.edu"}, **iis)
    assert resp.status_code == 200
    (_, _, body), = emails
    links = _links(body)
    assert len(links) == 1
    assert links[0].startswith(f"{PUBLIC}/redaction-request/verify/")
    assert "127.0.0.1" not in body


def test_verification_link_from_the_email_actually_verifies(client, iis, emails):
    client.post("/redaction-request",
                data={"author_name": JANE, "email": "a@b.edu"}, **iis)
    link, = _links(emails[0][2])
    path = urllib.parse.urlparse(link).path
    assert client.get(path, **iis).status_code == 200


def test_orcid_redirect_uri_uses_public_host(client, iis, orcid_configured):
    resp = client.post("/redaction-request",
                       data={"author_name": JANE, "method": "orcid"}, **iis)
    assert resp.status_code == 302
    redirect_uri, _ = _redirect_uri(resp.headers["Location"])
    assert redirect_uri == f"{PUBLIC}/redaction-request/orcid/callback"


def test_orcid_token_exchange_sends_the_same_redirect_uri(
        client, iis, orcid_configured, monkeypatch):
    """ORCID rejects a token exchange whose redirect_uri differs from the one
    the authorize step used, so the callback must rebuild it identically."""
    resp = client.post("/redaction-request",
                       data={"author_name": JANE, "method": "orcid"}, **iis)
    authorize_uri, state = _redirect_uri(resp.headers["Location"])

    seen = []

    def _exchange(code, redirect_uri):
        seen.append(redirect_uri)
        return {"orcid": "0000-0001-1111-1111", "name": JANE}

    monkeypatch.setattr(orcid_oauth, "exchange_code", _exchange)
    cb = client.get("/redaction-request/orcid/callback?code=abc&state="
                    + urllib.parse.quote(state), **iis)
    assert cb.status_code == 200
    assert seen == [authorize_uri] == [f"{PUBLIC}/redaction-request/orcid/callback"]


def test_admin_review_notice_uses_public_host(client, iis, emails, monkeypatch):
    monkeypatch.setenv("REDACTION_NOTIFY_EMAIL", "admin@b.edu")
    client.post("/redaction-request",
                data={"author_name": JANE, "email": "a@b.edu"}, **iis)
    link, = _links(emails[0][2])
    client.get(urllib.parse.urlparse(link).path, **iis)

    notice = next(body for to, _, body in emails if to == "admin@b.edu")
    assert f"{PUBLIC}/admin/redactions" in notice
    assert "127.0.0.1" not in notice


def test_alert_confirmation_link_uses_public_host(client, iis, emails):
    resp = client.post("/alerts/subscribe",
                       data={"email": "a@b.co", "journal": "College English"},
                       **iis)
    assert resp.status_code == 200
    (_, _, body), = emails
    link, = _links(body)
    assert link.startswith(f"{PUBLIC}/alerts/verify/")


def test_client_ip_check_reports_public_external_url(client, iis, monkeypatch):
    """What deploy/windows/Test-PinakesClientIp.ps1 reads for its
    "Absolute URLs use the public host" check."""
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", ADMIN_TOKEN)
    resp = client.get("/api/admin/client-ip",
                      headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}, **iis)
    body = resp.get_json()
    assert body["external_url"] == f"{PUBLIC}/health"
    assert body["site_url"] == PUBLIC
    assert body["request_host_url"] == "http://127.0.0.1:8080/"


def test_forged_forwarded_host_cannot_steer_emailed_links(client, iis, emails):
    """PINAKES_SITE_URL wins over anything in the request, so a forged
    X-Forwarded-Host can't send a victim's token to another domain."""
    client.post("/redaction-request",
                data={"author_name": JANE, "email": "a@b.edu"},
                headers={"X-Forwarded-Host": "evil.example",
                         "X-Forwarded-Proto": "https"}, **iis)
    body = emails[0][2]
    assert "evil.example" not in body
    assert f"{PUBLIC}/redaction-request/verify/" in body


def test_trailing_slash_on_site_url_is_tolerated(client, iis, emails, monkeypatch):
    monkeypatch.setenv("PINAKES_SITE_URL", PUBLIC + "/")
    client.post("/redaction-request",
                data={"author_name": JANE, "email": "a@b.edu"}, **iis)
    assert f"{PUBLIC}/redaction-request/verify/" in emails[0][2]


# ── Without PINAKES_SITE_URL: the request decides ───────────────────────────

def test_without_site_url_the_iis_request_gives_waitress_address(
        client, iis, emails, monkeypatch):
    """The bug this guards against, reproduced: the fixture's request shape is
    faithful enough that dropping PINAKES_SITE_URL brings it back."""
    monkeypatch.delenv("PINAKES_SITE_URL")
    client.post("/redaction-request",
                data={"author_name": JANE, "email": "a@b.edu"}, **iis)
    assert "http://127.0.0.1:8080/redaction-request/verify/" in emails[0][2]


def test_fly_behaviour_unchanged_without_site_url(
        client, emails, orcid_configured, monkeypatch):
    """Fly leaves PINAKES_SITE_URL unset and its proxy's X-Forwarded-Proto and
    -Host reach ProxyFix, so links still come from the request."""
    monkeypatch.delenv("PINAKES_SITE_URL", raising=False)
    fly = {"base_url": "http://172.19.0.2:8080",
           "headers": {"X-Forwarded-Proto": "https",
                       "X-Forwarded-Host": "pinakes.xyz"}}
    client.post("/redaction-request",
                data={"author_name": JANE, "email": "a@b.edu"}, **fly)
    assert "https://pinakes.xyz/redaction-request/verify/" in emails[0][2]

    resp = client.post("/redaction-request",
                       data={"author_name": JANE, "method": "orcid"}, **fly)
    redirect_uri, _ = _redirect_uri(resp.headers["Location"])
    assert redirect_uri == "https://pinakes.xyz/redaction-request/orcid/callback"


# ── Guard ────────────────────────────────────────────────────────────────────

_REPO = pathlib.Path(__file__).resolve().parent.parent
_EXTERNAL_FLAG = re.compile(r"_external\s*=\s*True")


def test_absolute_urls_go_through_public_url_for():
    """url_for's _external flag builds from the request, which is wrong on the
    Clearinghouse box. Outbound links must use web_helpers.public_url_for."""
    files = [*_REPO.glob("*.py"), *_REPO.glob("blueprints/*.py"),
             *_REPO.glob("templates/**/*.html")]
    offenders = []
    for path in files:
        if path.name == "web_helpers.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if not line.lstrip().startswith("#") and _EXTERNAL_FLAG.search(line):
                offenders.append(f"{path.relative_to(_REPO)}:{n}")
    assert offenders == []
