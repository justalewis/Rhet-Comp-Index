"""Digest sender tests.

Two failure modes drive this file, both of which reach real people and neither
of which any later code path can undo:

  * a first digest that carries the whole back catalogue, and
  * a week of scholarship silently skipped because a watermark advanced past
    mail that never went out.
"""

import pytest

import alerts
import digest


@pytest.fixture(autouse=True)
def _alerts_on(monkeypatch):
    monkeypatch.setenv("PINAKES_ALERTS_ENABLED", "1")
    # Sends are exercised through a stub; never touch a real SMTP server.
    monkeypatch.setattr(digest, "SEND_DELAY_SECONDS", 0)


@pytest.fixture
def sent(monkeypatch):
    """Capture what send_email would have sent."""
    box = []

    def _fake_send(to, subject, body, html=None, headers=None):
        box.append({"to": to, "subject": subject, "body": body,
                    "html": html, "headers": headers or {}})
        return True

    monkeypatch.setattr(digest, "send_email", _fake_send)
    monkeypatch.setattr(digest, "email_configured", lambda: True)
    return box


def _add_articles(n, journal="College English", start_title="Brand New"):
    """Insert n articles that are newer than everything already indexed."""
    from db import get_conn
    ids = []
    with get_conn() as conn:
        for i in range(n):
            cur = conn.execute(
                "INSERT INTO articles (url, doi, title, authors, abstract, "
                "pub_date, journal, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (f"https://example.org/new/{start_title}/{i}",
                 f"10.9999/new.{start_title}.{i}",
                 f"{start_title} {i}", "New Author", "New abstract.",
                 "2026-05-01", journal, "crossref"),
            )
            ids.append(cur.lastrowid)
        conn.commit()
    return ids


def _active_sub(email="a@b.co", filters=None):
    sub_id, verify, unsub, _ = alerts.create_subscription(email, filters or {})
    alerts.verify_by_token(verify)
    return sub_id, unsub


# ── The back-catalogue guard ─────────────────────────────────────────────────

def test_first_digest_contains_only_articles_indexed_after_signup(seeded_db, sent):
    """The feature's worst possible bug, pinned. A brand-new subscriber must
    not receive the 50 seeded articles that predate their subscription."""
    _active_sub()
    _add_articles(3)

    summary = digest.run()

    assert summary["sent"] == 1
    assert len(sent) == 1
    assert "3 new articles" in sent[0]["subject"]
    body = sent[0]["body"]
    assert "Brand New 0" in body
    assert "Jane Smith" not in body  # a seeded author; nothing pre-signup


def test_subscription_with_nothing_new_sends_nothing(seeded_db, sent):
    """Most saved searches are quiet most weeks. A '0 new articles' email every
    Friday is how a useful alert becomes a filtered one."""
    _active_sub()
    summary = digest.run()
    assert sent == []
    assert summary["sent"] == 0 and summary["skipped_empty"] == 1


def test_watermark_advances_so_the_next_run_is_quiet(seeded_db, sent):
    _active_sub()
    _add_articles(2)

    digest.run()
    assert len(sent) == 1

    # Second run, nothing new since.
    from db import get_conn
    with get_conn() as conn:
        conn.execute("UPDATE alert_subscriptions SET last_sent_at = NULL")
        conn.commit()
    digest.run()
    assert len(sent) == 1  # unchanged


def test_filters_are_respected(seeded_db, sent):
    _active_sub(filters={"journal": ["Pre/Text"]})
    _add_articles(2, journal="College English")   # should not match
    _add_articles(1, journal="Pre/Text", start_title="Matching")

    digest.run()

    assert len(sent) == 1
    assert "Matching 0" in sent[0]["body"]
    assert "Brand New" not in sent[0]["body"]


def test_overflow_links_back_to_the_filtered_index(seeded_db, sent):
    _active_sub(filters={"journal": ["College English"]})
    _add_articles(digest.MAX_ITEMS_PER_EMAIL + 5)

    digest.run()

    body = sent[0]["body"]
    assert f"...and 5 more" in body
    assert "journal=College" in body


def test_digest_carries_one_click_unsubscribe_headers(seeded_db, sent):
    """Gmail and Yahoo require these on bulk mail; without them the digest is
    filtered regardless of content."""
    _, unsub = _active_sub()
    _add_articles(1)

    digest.run()

    headers = sent[0]["headers"]
    assert headers["List-Unsubscribe"] == f"<{digest.SITE_URL}/alerts/unsubscribe/{unsub}>"
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert unsub in sent[0]["body"]


def test_unsubscribe_link_in_the_email_actually_works(seeded_db, sent, client):
    """End-to-end: the token in a delivered digest resolves at the route."""
    _, unsub = _active_sub()
    _add_articles(1)
    digest.run()

    assert client.get(f"/alerts/unsubscribe/{unsub}").status_code == 200
    assert alerts.stats().get("unsubscribed") == 1


def test_html_and_text_parts_both_present(seeded_db, sent):
    _active_sub()
    _add_articles(1)
    digest.run()
    assert sent[0]["html"] and sent[0]["body"]
    assert "Brand New 0" in sent[0]["html"]


def test_html_escapes_titles(seeded_db, sent):
    _active_sub()
    from db import get_conn
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO articles (url, doi, title, authors, pub_date, journal, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("https://example.org/x", "10.9999/x",
             "Rhetoric & <script>alert(1)</script>", "A", "2026-05-01",
             "College English", "crossref"),
        )
        conn.commit()
    digest.run()
    assert "<script>" not in sent[0]["html"]
    assert "&amp;" in sent[0]["html"]


# ── Failure handling ─────────────────────────────────────────────────────────

def test_failed_send_leaves_the_watermark_alone(seeded_db, monkeypatch):
    """So the next run retries. A duplicate is recoverable; a skipped week is
    scholarship the subscriber never learns about."""
    monkeypatch.setattr(digest, "email_configured", lambda: True)
    monkeypatch.setattr(digest, "send_email",
                        lambda **kw: False)
    sub_id, _ = _active_sub()
    _add_articles(2)

    from db import get_conn
    with get_conn() as conn:
        before = conn.execute("SELECT watermark_id FROM alert_subscriptions "
                              "WHERE id = ?", (sub_id,)).fetchone()[0]

    summary = digest.run()

    with get_conn() as conn:
        after = conn.execute("SELECT watermark_id FROM alert_subscriptions "
                             "WHERE id = ?", (sub_id,)).fetchone()[0]
    assert after == before
    assert summary["failed"] == 1


def test_one_failing_subscriber_does_not_stop_the_others(seeded_db, monkeypatch):
    """Without this, subscriber #1's dead domain silently cancels everyone
    else's mail."""
    monkeypatch.setattr(digest, "email_configured", lambda: True)
    delivered = []

    def _selective(to, subject, body, html=None, headers=None):
        if to == "boom@b.co":
            raise RuntimeError("SMTP exploded")
        delivered.append(to)
        return True

    monkeypatch.setattr(digest, "send_email", _selective)

    _active_sub(email="boom@b.co")
    _active_sub(email="fine@b.co")
    _add_articles(1)

    summary = digest.run()

    assert delivered == ["fine@b.co"]
    assert summary["failed"] == 1 and summary["sent"] == 1


def test_run_refuses_when_smtp_is_unconfigured(seeded_db, monkeypatch):
    """send_email 'succeeds' by logging when SMTP is unset. Running anyway
    would advance every watermark and silently consume the backlog, so the
    articles would never go out once SMTP was fixed."""
    monkeypatch.setattr(digest, "email_configured", lambda: False)
    sub_id, _ = _active_sub()
    _add_articles(2)

    summary = digest.run()

    assert summary == {"skipped": "smtp-unconfigured"}
    from db import get_conn
    with get_conn() as conn:
        assert conn.execute("SELECT send_count FROM alert_subscriptions "
                            "WHERE id = ?", (sub_id,)).fetchone()[0] == 0


def test_run_is_a_no_op_when_the_feature_is_disabled(seeded_db, monkeypatch, sent):
    monkeypatch.setenv("PINAKES_ALERTS_ENABLED", "0")
    _active_sub()
    _add_articles(1)
    assert digest.run() == {"skipped": "disabled"}
    assert sent == []


def test_dry_run_sends_nothing_and_advances_nothing(seeded_db, sent):
    sub_id, _ = _active_sub()
    _add_articles(3)

    summary = digest.run(dry_run=True)

    assert sent == []
    assert summary["sent"] == 1 and summary["dry_run"] is True
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT watermark_id, send_count FROM "
                           "alert_subscriptions WHERE id = ?", (sub_id,)).fetchone()
    assert row["send_count"] == 0


def test_query_is_capped(seeded_db, sent, monkeypatch):
    """A subscription unsent through a large backfill must not try to render
    thousands of rows."""
    monkeypatch.setattr(digest, "MAX_QUERY", 5)
    _active_sub()
    _add_articles(12)

    digest.run()

    assert "5 new articles" in sent[0]["subject"]


# ── Admin endpoint ───────────────────────────────────────────────────────────

def test_send_digests_requires_the_admin_token(client):
    assert client.post("/api/admin/send-digests").status_code in (401, 503)


def test_send_digests_accepts_a_valid_token(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", "test-token")
    resp = client.post("/api/admin/send-digests",
                       headers={"Authorization": "Bearer test-token"},
                       json={"dry_run": True})
    assert resp.status_code == 200
    assert resp.get_json()["dry_run"] is True
