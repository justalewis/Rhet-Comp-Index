"""Saved-search alert tests — canonicalization, tokens, and the enumeration
guarantee.

The signup form is public and the subscriber list is a list of named scholars,
so "does this endpoint reveal whether an address is subscribed" is a
first-class correctness question here, not a nicety.
"""

import pytest

import alerts


@pytest.fixture(autouse=True)
def _alerts_on(monkeypatch):
    """Every test in this module runs with the feature switched on."""
    monkeypatch.setenv("PINAKES_ALERTS_ENABLED", "1")


# ── Canonicalization ─────────────────────────────────────────────────────────

def test_journal_order_does_not_change_identity():
    a = alerts.canonicalize_filters({"journal": ["Kairos", "College English"]})
    b = alerts.canonicalize_filters({"journal": ["College English", "Kairos"]})
    assert a == b
    assert alerts.filters_hash(a) == alerts.filters_hash(b)


def test_empty_values_are_dropped():
    """`?q=&tag=` from a submitted-but-blank form must equal no filters at all,
    or the same saved search gets two different identities."""
    assert alerts.canonicalize_filters({"q": "", "tag": "  "}) == {}
    assert (alerts.filters_hash(alerts.canonicalize_filters({"q": ""}))
            == alerts.filters_hash({}))


def test_duplicate_journals_collapse():
    assert alerts.canonicalize_filters(
        {"journal": ["College English", "College English"]}
    ) == {"journal": ["College English"]}


def test_whitespace_is_stripped():
    assert alerts.canonicalize_filters({"tag": "  pedagogy  "}) == {"tag": "pedagogy"}


def test_canonicalize_accepts_werkzeug_multidict():
    from werkzeug.datastructures import MultiDict
    md = MultiDict([("journal", "Kairos"), ("journal", "College English"),
                    ("tag", "multimodal")])
    assert alerts.canonicalize_filters(md) == {
        "journal": ["College English", "Kairos"], "tag": "multimodal",
    }


def test_hash_is_deterministic_across_calls():
    f = {"journal": ["A", "B"], "tag": "x"}
    assert alerts.filters_hash(f) == alerts.filters_hash(dict(reversed(list(f.items()))))


@pytest.mark.parametrize("filters,expected_fragment", [
    ({}, "Everything"),
    ({"journal": ["College English"]}, "College English"),
    ({"journal": ["A", "B", "C"]}, "3 journals"),
    ({"tag": "pedagogy"}, "tagged pedagogy"),
    ({"q": "genre"}, 'matching "genre"'),
    ({"year_from": "2020", "year_to": "2024"}, "2020"),
])
def test_describe_filters(filters, expected_fragment):
    assert expected_fragment in alerts.describe_filters(filters)


# ── Email validation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("addr", [
    "a@b.co", "justin.lewis@olympic.edu", "a+pinakes@gmail.com",
])
def test_valid_emails(addr):
    assert alerts.valid_email(addr)


@pytest.mark.parametrize("addr", [
    "", "   ", "no-at-sign", "a@b", "a@@b.com", "a b@c.com", "a@b .com",
])
def test_invalid_emails(addr):
    assert not alerts.valid_email(addr)


def test_plus_addressing_is_not_collapsed():
    """a+x@ and a@ are different subscriptions on purpose."""
    assert alerts.normalize_email("A+X@Gmail.com") == "a+x@gmail.com"
    assert alerts.normalize_email("a+x@gmail.com") != alerts.normalize_email("a@gmail.com")


# ── Subscription lifecycle ───────────────────────────────────────────────────

def test_create_then_verify_activates(seeded_db):
    sub_id, verify, unsub, already = alerts.create_subscription(
        "a@b.co", {"journal": ["College English"]})
    assert not already and verify and unsub
    assert alerts.verify_by_token(verify) == sub_id

    from db import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM alert_subscriptions WHERE id = ?",
                           (sub_id,)).fetchone()
    assert row["status"] == "active"
    assert row["verify_token_hash"] is None


def test_verification_seeds_the_watermark_to_the_newest_article(seeded_db):
    """The single most consequential line in the feature. Without it the first
    digest is the entire back catalogue, mailed to a real person."""
    sub_id, verify, _, _ = alerts.create_subscription("a@b.co", {})
    alerts.verify_by_token(verify)

    from db import get_conn
    with get_conn() as conn:
        watermark = conn.execute(
            "SELECT watermark_id FROM alert_subscriptions WHERE id = ?",
            (sub_id,)).fetchone()[0]
        max_id = conn.execute("SELECT MAX(id) FROM articles").fetchone()[0]
    assert watermark == max_id > 0


def test_verify_token_burns(seeded_db):
    _, verify, _, _ = alerts.create_subscription("a@b.co", {})
    assert alerts.verify_by_token(verify)
    assert alerts.verify_by_token(verify) is None


def test_unknown_verify_token_returns_none(seeded_db):
    assert alerts.verify_by_token("not-a-token") is None


def test_duplicate_subscription_returns_already_active(seeded_db):
    filters = {"tag": "pedagogy"}
    sub_id, verify, _, _ = alerts.create_subscription("a@b.co", filters)
    alerts.verify_by_token(verify)

    again_id, again_verify, again_unsub, already = \
        alerts.create_subscription("a@b.co", filters)
    assert already is True
    assert again_id == sub_id
    assert again_verify is None and again_unsub is None

    from db import get_conn
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM alert_subscriptions WHERE email = 'a@b.co'"
        ).fetchone()[0]
    assert count == 1


def test_resubscribing_after_unsubscribe_reuses_the_row(seeded_db):
    filters = {"tag": "pedagogy"}
    sub_id, verify, unsub, _ = alerts.create_subscription("a@b.co", filters)
    alerts.verify_by_token(verify)
    alerts.unsubscribe_by_token(unsub)

    again_id, again_verify, _, already = alerts.create_subscription("a@b.co", filters)
    assert already is False
    assert again_id == sub_id
    assert again_verify  # a fresh confirmation is required


def test_different_filters_are_separate_subscriptions(seeded_db):
    a, _, _, _ = alerts.create_subscription("a@b.co", {"tag": "x"})
    b, _, _, _ = alerts.create_subscription("a@b.co", {"tag": "y"})
    assert a != b


def test_subscription_cap_is_enforced(seeded_db):
    for i in range(alerts.MAX_SUBSCRIPTIONS_PER_EMAIL):
        alerts.create_subscription("a@b.co", {"tag": f"t{i}"})
    with pytest.raises(alerts.TooManySubscriptions):
        alerts.create_subscription("a@b.co", {"tag": "one-too-many"})


def test_unsubscribe_is_idempotent(seeded_db):
    """Unsubscribe links live in mail archives forever; a second click years
    later must not error."""
    _, verify, unsub, _ = alerts.create_subscription("a@b.co", {})
    alerts.verify_by_token(verify)
    assert alerts.unsubscribe_by_token(unsub)
    assert alerts.unsubscribe_by_token(unsub)  # still resolves


def test_delete_removes_subscription_and_send_history(seeded_db):
    sub_id, verify, unsub, _ = alerts.create_subscription("a@b.co", {})
    alerts.verify_by_token(verify)
    alerts.record_send(sub_id, 3, 999, ok=True)

    assert alerts.delete_by_unsub_token(unsub) is True

    from db import get_conn
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM alert_subscriptions "
                            "WHERE id = ?", (sub_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM alert_sends "
                            "WHERE sub_id = ?", (sub_id,)).fetchone()[0] == 0


# ── Due selection and watermarks ─────────────────────────────────────────────

def test_only_active_subscriptions_are_due(seeded_db):
    alerts.create_subscription("pending@b.co", {"tag": "a"})  # never verified
    _, verify, _, _ = alerts.create_subscription("active@b.co", {"tag": "b"})
    alerts.verify_by_token(verify)

    emails = {s["email"] for s in alerts.due_subscriptions()}
    assert emails == {"active@b.co"}


def test_recently_sent_subscription_is_not_due(seeded_db):
    sub_id, verify, _, _ = alerts.create_subscription("a@b.co", {})
    alerts.verify_by_token(verify)
    alerts.record_send(sub_id, 1, 500, ok=True)
    assert alerts.due_subscriptions() == []


def test_failed_send_does_not_advance_the_watermark(seeded_db):
    """A duplicate digest is recoverable. A silently skipped week is not."""
    sub_id, verify, _, _ = alerts.create_subscription("a@b.co", {})
    alerts.verify_by_token(verify)

    from db import get_conn
    with get_conn() as conn:
        before = conn.execute("SELECT watermark_id FROM alert_subscriptions "
                              "WHERE id = ?", (sub_id,)).fetchone()[0]
    alerts.record_send(sub_id, 5, before + 100, ok=False)
    with get_conn() as conn:
        row = conn.execute("SELECT watermark_id, send_count FROM "
                           "alert_subscriptions WHERE id = ?", (sub_id,)).fetchone()
        logged = conn.execute("SELECT ok FROM alert_sends WHERE sub_id = ?",
                              (sub_id,)).fetchone()[0]
    assert row["watermark_id"] == before
    assert row["send_count"] == 0
    assert logged == 0  # the attempt is still recorded


def test_purge_stale_pending_leaves_active_alone(seeded_db):
    _, verify, _, _ = alerts.create_subscription("active@b.co", {"tag": "a"})
    alerts.verify_by_token(verify)
    alerts.create_subscription("pending@b.co", {"tag": "b"})

    from db import get_conn
    with get_conn() as conn:
        conn.execute("UPDATE alert_subscriptions SET created_at = "
                     "datetime('now', '-30 days') WHERE status = 'pending'")
        conn.commit()

    assert alerts.purge_stale_pending(days=7) == 1
    assert set(alerts.stats()) == {"active"}


def test_corrupt_filters_json_degrades_instead_of_raising(seeded_db):
    """Raising here would abort the digest loop and cancel everyone else's
    mail, so a broken row must fail soft."""
    sub_id, _, _, _ = alerts.create_subscription("a@b.co", {})
    from db import get_conn
    with get_conn() as conn:
        conn.execute("UPDATE alert_subscriptions SET filters_json = 'not json' "
                     "WHERE id = ?", (sub_id,))
        conn.commit()
        row = dict(conn.execute("SELECT * FROM alert_subscriptions WHERE id = ?",
                                (sub_id,)).fetchone())
    assert alerts.subscription_filters(row) == {}


# ── Routes ───────────────────────────────────────────────────────────────────

def test_signup_page_renders_with_filters(client):
    resp = client.get("/alerts/new?journal=College+English&tag=pedagogy")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "College English" in body and "pedagogy" in body


def test_routes_404_when_feature_disabled(client, monkeypatch):
    monkeypatch.setenv("PINAKES_ALERTS_ENABLED", "0")
    assert client.get("/alerts/new").status_code == 404
    assert client.post("/alerts/subscribe",
                       data={"email": "a@b.co"}).status_code == 404


def test_unsubscribe_still_works_when_feature_disabled(client, monkeypatch, seeded_db):
    """Turning the feature off must never strand somebody who wants out."""
    _, verify, unsub, _ = alerts.create_subscription("a@b.co", {})
    alerts.verify_by_token(verify)
    monkeypatch.setenv("PINAKES_ALERTS_ENABLED", "0")
    assert client.get(f"/alerts/unsubscribe/{unsub}").status_code == 200


def test_subscribe_creates_a_pending_subscription(client, seeded_db):
    resp = client.post("/alerts/subscribe",
                       data={"email": "new@b.co", "journal": "College English"})
    assert resp.status_code == 200
    assert "Check your email" in resp.get_data(as_text=True)
    assert alerts.stats().get("pending") == 1


def test_subscribe_response_is_identical_for_known_and_unknown_addresses(
        client, seeded_db):
    """The enumeration guarantee. If these two bodies ever differ, the form
    tells a stranger whether a named scholar subscribes to Pinakes."""
    filters = {"journal": ["College English"]}
    _, verify, _, _ = alerts.create_subscription("known@b.co", filters)
    alerts.verify_by_token(verify)

    known = client.post("/alerts/subscribe",
                        data={"email": "known@b.co", "journal": "College English"})
    unknown = client.post("/alerts/subscribe",
                          data={"email": "unknown@b.co", "journal": "College English"})

    assert known.status_code == unknown.status_code == 200
    # Normalize the one legitimate difference: the address echoed back.
    a = known.get_data(as_text=True).replace("known@b.co", "X")
    b = unknown.get_data(as_text=True).replace("unknown@b.co", "X")
    assert a == b


def test_subscribe_over_cap_looks_like_success(client, seeded_db):
    for i in range(alerts.MAX_SUBSCRIPTIONS_PER_EMAIL):
        alerts.create_subscription("full@b.co", {"tag": f"t{i}"})
    resp = client.post("/alerts/subscribe",
                       data={"email": "full@b.co", "tag": "over"})
    assert resp.status_code == 200
    assert "Check your email" in resp.get_data(as_text=True)


def test_honeypot_submission_creates_nothing(client, seeded_db):
    resp = client.post("/alerts/subscribe",
                       data={"email": "bot@b.co", "website": "http://spam"})
    assert resp.status_code == 200
    assert "Check your email" in resp.get_data(as_text=True)
    assert alerts.stats() == {}


def test_bad_email_is_rejected_visibly(client, seeded_db):
    """The one failure a submitter can see for themselves, so showing it leaks
    nothing."""
    resp = client.post("/alerts/subscribe", data={"email": "nope"})
    assert resp.status_code == 400
    assert "email address" in resp.get_data(as_text=True).lower()
    assert alerts.stats() == {}


def test_verify_route_activates(client, seeded_db):
    _, verify, _, _ = alerts.create_subscription("a@b.co", {})
    resp = client.get(f"/alerts/verify/{verify}")
    assert resp.status_code == 200
    assert "alert is on" in resp.get_data(as_text=True)
    assert alerts.stats().get("active") == 1


def test_reused_verify_link_explains_itself(client, seeded_db):
    _, verify, _, _ = alerts.create_subscription("a@b.co", {})
    client.get(f"/alerts/verify/{verify}")
    resp = client.get(f"/alerts/verify/{verify}")
    assert resp.status_code == 404
    assert "already been used" in resp.get_data(as_text=True)


def test_one_click_unsubscribe_post(client, seeded_db):
    """RFC 8058: mail providers POST here with no browser session."""
    _, verify, unsub, _ = alerts.create_subscription("a@b.co", {})
    alerts.verify_by_token(verify)
    resp = client.post(f"/alerts/unsubscribe/{unsub}")
    assert resp.status_code == 200
    assert alerts.stats().get("unsubscribed") == 1


def test_one_click_unsubscribe_post_is_200_even_for_unknown_tokens(client):
    """A mail provider can't act on a 404, and a non-200 risks the provider
    marking the unsubscribe mechanism broken."""
    assert client.post("/alerts/unsubscribe/garbage").status_code == 200


def test_manage_page_and_delete(client, seeded_db):
    sub_id, verify, unsub, _ = alerts.create_subscription("a@b.co", {"tag": "x"})
    alerts.verify_by_token(verify)

    page = client.get(f"/alerts/manage/{unsub}")
    assert page.status_code == 200
    assert "a@b.co" in page.get_data(as_text=True)

    resp = client.post(f"/alerts/manage/{unsub}/delete")
    assert resp.status_code == 200
    assert alerts.stats() == {}


def test_manage_unknown_token_404s(client):
    assert client.get("/alerts/manage/garbage").status_code == 404
