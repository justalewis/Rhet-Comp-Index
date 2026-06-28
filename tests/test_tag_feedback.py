"""Community tags: public 👍/👎 feedback + tag suggestions, the moderation
queue, and the approved-tag render/filter union. The load-bearing invariant
under test is that none of this ever mutates articles.tags."""

import json

import pytest

import db as _db
from tagger import TAG_NAMES

ADMIN_TOKEN = "test-admin-token-xyz"


@pytest.fixture
def admin_headers(monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", ADMIN_TOKEN)
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _an_article_with_tags():
    """(id, [classifier tags]) for a seeded article that carries tags."""
    with _db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, tags FROM articles "
            "WHERE tags IS NOT NULL AND tags != '' LIMIT 1"
        ).fetchone()
    assert row is not None, "seed should include tagged articles"
    tags = [t.strip() for t in row["tags"].strip("|").split("|") if t.strip()]
    return row["id"], tags


def _post(client, url, body, headers=None):
    return client.post(url, data=json.dumps(body),
                       content_type="application/json", headers=headers or {})


# ── Feedback ────────────────────────────────────────────────────────────────────

def test_feedback_records_vote(client):
    aid, tags = _an_article_with_tags()
    r = _post(client, f"/api/articles/{aid}/tag-feedback", {"tag": tags[0], "vote": 1})
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"
    assert data["up"] == 1 and data["down"] == 0


def test_feedback_reposting_flips_not_stacks(client):
    aid, tags = _an_article_with_tags()
    _post(client, f"/api/articles/{aid}/tag-feedback", {"tag": tags[0], "vote": 1})
    data = _post(client, f"/api/articles/{aid}/tag-feedback",
                 {"tag": tags[0], "vote": -1}).get_json()
    assert data["up"] == 0 and data["down"] == 1
    with _db.get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM tag_feedback WHERE article_id=? AND tag=?",
            (aid, tags[0])).fetchone()[0]
    assert n == 1  # one row per (article, tag, ip), flipped in place


def test_feedback_rejects_tag_not_on_article(client):
    aid, _ = _an_article_with_tags()
    r = _post(client, f"/api/articles/{aid}/tag-feedback",
              {"tag": "not a real classifier tag", "vote": 1})
    assert r.status_code == 400


def test_feedback_rejects_bad_vote(client):
    aid, tags = _an_article_with_tags()
    r = _post(client, f"/api/articles/{aid}/tag-feedback", {"tag": tags[0], "vote": 5})
    assert r.status_code == 400


def test_feedback_unknown_article_404(client):
    r = _post(client, "/api/articles/999999/tag-feedback", {"tag": "anything", "vote": 1})
    assert r.status_code == 404


# ── Suggestions ─────────────────────────────────────────────────────────────────

def test_suggest_novel_tag_is_pending_and_flagged_novel(client):
    aid, _ = _an_article_with_tags()
    r = _post(client, f"/api/articles/{aid}/suggest-tag", {"tag": "Sentimental Rhetoric"})
    assert r.status_code == 200
    assert r.get_json()["status"] == "pending"
    with _db.get_conn() as conn:
        row = conn.execute(
            "SELECT tag, in_vocab, status FROM user_tags WHERE article_id=?",
            (aid,)).fetchone()
    assert row["tag"] == "sentimental rhetoric"   # normalized
    assert row["in_vocab"] == 0
    assert row["status"] == "pending"


def test_suggest_in_vocab_tag_is_flagged(client):
    aid, tags = _an_article_with_tags()
    target = next(t for t in TAG_NAMES if t not in tags)
    assert _post(client, f"/api/articles/{aid}/suggest-tag",
                 {"tag": target}).get_json()["status"] == "pending"
    with _db.get_conn() as conn:
        row = conn.execute(
            "SELECT in_vocab FROM user_tags WHERE article_id=? AND tag=?",
            (aid, target)).fetchone()
    assert row["in_vocab"] == 1


def test_suggest_existing_classifier_tag_returns_exists(client):
    aid, tags = _an_article_with_tags()
    assert _post(client, f"/api/articles/{aid}/suggest-tag",
                 {"tag": tags[0]}).get_json()["status"] == "exists"
    with _db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM user_tags WHERE article_id=?",
                         (aid,)).fetchone()[0]
    assert n == 0  # nothing queued


def test_suggest_strips_pipe_and_normalizes(client):
    aid, _ = _an_article_with_tags()
    _post(client, f"/api/articles/{aid}/suggest-tag", {"tag": "  A|B|C  "})
    with _db.get_conn() as conn:
        tag = conn.execute("SELECT tag FROM user_tags WHERE article_id=?",
                           (aid,)).fetchone()["tag"]
    assert "|" not in tag
    assert tag == "a b c"


def test_suggest_rejects_overlong(client):
    aid, _ = _an_article_with_tags()
    assert _post(client, f"/api/articles/{aid}/suggest-tag",
                 {"tag": "x" * 80}).status_code == 400


def test_suggest_rejects_empty(client):
    aid, _ = _an_article_with_tags()
    assert _post(client, f"/api/articles/{aid}/suggest-tag",
                 {"tag": "   "}).status_code == 400


def test_corroborating_suggestion_from_new_ip_bumps_votes(client):
    aid, _ = _an_article_with_tags()
    h1 = {"X-Forwarded-For": "203.0.113.1"}
    h2 = {"X-Forwarded-For": "203.0.113.2"}
    _post(client, f"/api/articles/{aid}/suggest-tag", {"tag": "Civic Listening"}, headers=h1)
    r = _post(client, f"/api/articles/{aid}/suggest-tag", {"tag": "Civic Listening"}, headers=h2)
    assert r.get_json()["status"] == "bumped"
    with _db.get_conn() as conn:
        votes = conn.execute(
            "SELECT votes FROM user_tags WHERE article_id=? AND tag=?",
            (aid, "civic listening")).fetchone()["votes"]
    assert votes == 2


# ── Moderation ──────────────────────────────────────────────────────────────────

def test_admin_queue_requires_auth(client):
    # No PINAKES_ADMIN_TOKEN configured in the bare client → 503 (not configured).
    assert client.get("/api/admin/user-tags").status_code in (401, 503)


def test_admin_queue_lists_pending(client, admin_headers):
    aid, _ = _an_article_with_tags()
    _post(client, f"/api/articles/{aid}/suggest-tag", {"tag": "Queue Me"})
    data = client.get("/api/admin/user-tags", headers=admin_headers).get_json()
    assert any(u["tag"] == "queue me" for u in data["user_tags"])


def test_decide_invalid_decision_400(client, admin_headers):
    aid, _ = _an_article_with_tags()
    _post(client, f"/api/articles/{aid}/suggest-tag", {"tag": "Decide Me"})
    with _db.get_conn() as conn:
        utid = conn.execute("SELECT id FROM user_tags WHERE article_id=?",
                            (aid,)).fetchone()["id"]
    r = _post(client, f"/api/admin/user-tags/{utid}/decide",
              {"decision": "maybe"}, headers=admin_headers)
    assert r.status_code == 400


def test_decide_missing_id_404(client, admin_headers):
    r = _post(client, "/api/admin/user-tags/999999/decide",
              {"decision": "approve"}, headers=admin_headers)
    assert r.status_code == 404


def test_admin_page_renders_shell(client):
    r = client.get("/admin/user-tags")
    assert r.status_code == 200
    assert b"Tag Suggestions" in r.data


# ── End-to-end: approve → visible + filterable, and the core invariant ──────────

def test_approve_makes_tag_visible_and_filterable(client, admin_headers):
    aid, _ = _an_article_with_tags()
    _post(client, f"/api/articles/{aid}/suggest-tag", {"tag": "Borderlands Rhetoric"})
    with _db.get_conn() as conn:
        utid = conn.execute(
            "SELECT id FROM user_tags WHERE article_id=? AND tag=?",
            (aid, "borderlands rhetoric")).fetchone()["id"]

    r = _post(client, f"/api/admin/user-tags/{utid}/decide",
              {"decision": "approve"}, headers=admin_headers)
    assert r.status_code == 200

    assert "borderlands rhetoric" in _db.get_approved_user_tags(aid)

    page = client.get(f"/article/{aid}")
    assert b"borderlands rhetoric" in page.data
    # ...rendered as a community tag, not folded into the classifier-tag loop.
    assert b"article-tag--community" in page.data

    api = client.get("/api/articles?tag=borderlands%20rhetoric").get_json()
    assert any(a["id"] == aid for a in api["articles"])
    assert api["total"] >= 1

    # Filterable case-insensitively, like classifier tags (hand-typed URL).
    mixed = client.get("/api/articles?tag=Borderlands%20Rhetoric").get_json()
    assert any(a["id"] == aid for a in mixed["articles"])


def test_rejected_tag_is_not_visible_or_filterable(client, admin_headers):
    aid, _ = _an_article_with_tags()
    _post(client, f"/api/articles/{aid}/suggest-tag", {"tag": "Spurious Topic"})
    with _db.get_conn() as conn:
        utid = conn.execute("SELECT id FROM user_tags WHERE article_id=?",
                            (aid,)).fetchone()["id"]
    _post(client, f"/api/admin/user-tags/{utid}/decide",
          {"decision": "reject"}, headers=admin_headers)

    assert "spurious topic" not in _db.get_approved_user_tags(aid)
    api = client.get("/api/articles?tag=spurious%20topic").get_json()
    assert api["total"] == 0


def test_user_contributions_never_mutate_articles_tags(client, admin_headers):
    """The load-bearing invariant: feedback and approved suggestions live in
    their own tables; articles.tags (classifier-owned, retag-rewritten) is
    untouched."""
    aid, tags = _an_article_with_tags()
    with _db.get_conn() as conn:
        before = conn.execute("SELECT tags FROM articles WHERE id=?",
                             (aid,)).fetchone()["tags"]

    _post(client, f"/api/articles/{aid}/tag-feedback", {"tag": tags[0], "vote": 1})
    _post(client, f"/api/articles/{aid}/suggest-tag", {"tag": "New Concept"})
    with _db.get_conn() as conn:
        utid = conn.execute("SELECT id FROM user_tags WHERE article_id=?",
                            (aid,)).fetchone()["id"]
    _post(client, f"/api/admin/user-tags/{utid}/decide",
          {"decision": "approve"}, headers=admin_headers)

    with _db.get_conn() as conn:
        after = conn.execute("SELECT tags FROM articles WHERE id=?",
                            (aid,)).fetchone()["tags"]
    assert after == before


def _approve_suggestion(client, admin_headers, aid, tag):
    """Suggest `tag` on `aid` and approve it; return the normalized stored tag."""
    _post(client, f"/api/articles/{aid}/suggest-tag", {"tag": tag})
    with _db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, tag FROM user_tags WHERE article_id=? ORDER BY id DESC LIMIT 1",
            (aid,)).fetchone()
    _post(client, f"/api/admin/user-tags/{row['id']}/decide",
          {"decision": "approve"}, headers=admin_headers)
    return row["tag"]


# ── XSS hardening (normalize_tag strips angle brackets at the source) ────────────

def test_html_payload_tag_is_stripped_and_never_renders_live(client, admin_headers):
    aid, _ = _an_article_with_tags()
    _post(client, f"/api/articles/{aid}/suggest-tag",
          {"tag": "<img src=x onerror=alert(1)>"})
    with _db.get_conn() as conn:
        stored = conn.execute("SELECT tag FROM user_tags WHERE article_id=?",
                             (aid,)).fetchone()["tag"]
    # Angle brackets (and any control bytes) are gone — no HTML tag can form.
    assert "<" not in stored and ">" not in stored
    utid = None
    with _db.get_conn() as conn:
        utid = conn.execute("SELECT id FROM user_tags WHERE article_id=?",
                            (aid,)).fetchone()["id"]
    _post(client, f"/api/admin/user-tags/{utid}/decide",
          {"decision": "approve"}, headers=admin_headers)
    page = client.get(f"/article/{aid}")
    # The live payload never appears in the rendered page (article.html has no
    # <img>, so this substring would only be present if the payload survived).
    assert b"<img" not in page.data


# ── Rate-limit tiers actually gate the public routes ────────────────────────────

def test_suggest_tag_rate_limit_trips_at_cap(client):
    aid, _ = _an_article_with_tags()
    codes = [_post(client, f"/api/articles/{aid}/suggest-tag",
                   {"tag": f"novel topic {i}"}).status_code for i in range(11)]
    assert codes[:10] == [200] * 10   # tag_suggestion = 10/hour
    assert codes[10] == 429


def test_tag_feedback_rate_limit_trips_at_cap(client):
    aid, tags = _an_article_with_tags()
    # The limiter counts before the view, so repeats (which just flip the vote)
    # still consume budget. tag_feedback = 40/hour.
    codes = [_post(client, f"/api/articles/{aid}/tag-feedback",
                   {"tag": tags[0], "vote": 1 if i % 2 == 0 else -1}).status_code
             for i in range(41)]
    assert codes[:40] == [200] * 40
    assert codes[40] == 429


# ── The approved-tag union doesn't disturb other queries ────────────────────────

def test_approval_leaves_unfiltered_and_journal_results_unchanged(client, admin_headers):
    before_total = _db.get_total_count()
    before_rows = len(_db.get_articles(limit=100000))
    aid, _ = _an_article_with_tags()
    with _db.get_conn() as conn:
        journal = conn.execute("SELECT journal FROM articles WHERE id=?",
                             (aid,)).fetchone()["journal"]
    jbefore = _db.get_total_count(journal=journal)

    _approve_suggestion(client, admin_headers, aid, "Quiet Approval")

    assert _db.get_total_count() == before_total
    assert len(_db.get_articles(limit=100000)) == before_rows
    assert _db.get_total_count(journal=journal) == jbefore


def test_count_and_list_agree_for_user_tag_only_match(client, admin_headers):
    aid, _ = _an_article_with_tags()
    _approve_suggestion(client, admin_headers, aid, "Unique Crowd Tag")
    assert _db.get_total_count(tag="unique crowd tag") == 1
    rows = _db.get_articles(tag="unique crowd tag")
    assert len(rows) == 1 and rows[0]["id"] == aid


def test_combined_journal_and_community_tag_filter_binds_correctly(client, admin_headers):
    aid, _ = _an_article_with_tags()
    with _db.get_conn() as conn:
        journal = conn.execute("SELECT journal FROM articles WHERE id=?",
                             (aid,)).fetchone()["journal"]
    _approve_suggestion(client, admin_headers, aid, "Combo Tag")
    total = _db.get_total_count(journal=journal, tag="combo tag")
    rows = _db.get_articles(journal=journal, tag="combo tag")
    assert total == len(rows)
    assert any(r["id"] == aid for r in rows)


# ── Migration idempotency + multi-IP feedback tally ─────────────────────────────

def test_v14_migration_is_idempotent(seeded_db):
    import sqlite3
    _db.init_db()   # always-on v14 migration re-runs on an already-migrated DB
    with _db.get_conn() as conn:
        conn.execute("INSERT INTO user_tags (article_id, tag, in_vocab) VALUES (1, 'dup', 0)")
        conn.commit()
        raised = False
        try:
            conn.execute("INSERT INTO user_tags (article_id, tag, in_vocab) VALUES (1, 'dup', 0)")
            conn.commit()
        except sqlite3.IntegrityError:
            raised = True
    assert raised   # UNIQUE(article_id, tag) survived the second migration pass


def test_feedback_tally_across_two_ips_then_flip(client):
    aid, tags = _an_article_with_tags()
    A = {"X-Forwarded-For": "203.0.113.10"}
    B = {"X-Forwarded-For": "203.0.113.11"}
    _post(client, f"/api/articles/{aid}/tag-feedback", {"tag": tags[0], "vote": 1}, headers=A)
    d = _post(client, f"/api/articles/{aid}/tag-feedback",
              {"tag": tags[0], "vote": -1}, headers=B).get_json()
    assert d["up"] == 1 and d["down"] == 1
    with _db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM tag_feedback WHERE article_id=? AND tag=?",
                        (aid, tags[0])).fetchone()[0]
    assert n == 2   # one row per IP

    d2 = _post(client, f"/api/articles/{aid}/tag-feedback",
               {"tag": tags[0], "vote": -1}, headers=A).get_json()
    assert d2["up"] == 0 and d2["down"] == 2   # A flipped in place
    with _db.get_conn() as conn:
        n2 = conn.execute("SELECT COUNT(*) FROM tag_feedback WHERE article_id=? AND tag=?",
                         (aid, tags[0])).fetchone()[0]
    assert n2 == 2
