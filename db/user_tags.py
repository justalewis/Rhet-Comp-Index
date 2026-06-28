"""db.user_tags — community tag contributions: visitor-proposed tags
(`user_tags`) and 👍/👎 feedback on classifier tags (`tag_feedback`).

These tables are deliberately separate from articles.tags. The classifier owns
that column and retag.py rewrites it wholesale; user contributions kept there
would be clobbered. Approved user tags are unioned in at read time (the article
page render and the _build_where tag filter); feedback votes never touch
articles.tags — they're signal for hand-tuning tagger.py.
"""

import re
import sqlite3
import logging

from .core import get_conn
from tagger import is_vocab_tag

log = logging.getLogger(__name__)

# A user tag must be at least this many characters and no longer than this.
# 60 comfortably fits the longest classifier tag ("writing across the
# curriculum" = 31) while stopping someone pasting a paragraph into the field.
MIN_TAG_LEN = 2
MAX_TAG_LEN = 60
# Soft ceiling on the corroboration counter (see submit_user_tag). votes is an
# advisory badge in the admin queue, not a tamper-proof distinct-voter count, so
# we just cap it rather than build a per-voter ledger.
MAX_VOTES = 50


def normalize_tag(raw: str | None) -> str:
    """Canonical form for storage and comparison: lowercased, whitespace
    collapsed, with the pipe delimiter and any HTML/control characters removed.

    The pipe is the classifier's delimiter in articles.tags; even though user
    tags live in their own table, never letting a '|' into a stored tag keeps
    the two layers safe to concatenate anywhere downstream.

    Angle brackets and control/NUL bytes are stripped as defense in depth. Every
    render sink escapes user tags (Jinja autoescape, esc() in JS), so this isn't
    what stops XSS today — but sanitizing at the source means a stored, admin-
    approvable tag can never carry a raw HTML/script payload, independent of any
    one template or future sink remembering to escape it. Legitimate tags are
    lowercase words and spaces, so nothing real is harmed.
    """
    if not raw:
        return ""
    t = raw.replace("|", " ")
    t = re.sub(r"[\x00-\x1f\x7f<>]", "", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def _split_tags(tags: str | None) -> set[str]:
    """The set of normalized classifier tags on an article (from its pipe-
    delimited articles.tags string)."""
    if not tags:
        return set()
    return {t.strip().lower() for t in tags.strip("|").split("|") if t.strip()}


def _article_classifier_tags(conn, article_id) -> set[str] | None:
    """Return the article's classifier-tag set, or None if it doesn't exist."""
    row = conn.execute(
        "SELECT tags FROM articles WHERE id = ?", (article_id,)
    ).fetchone()
    if row is None:
        return None
    return _split_tags(row["tags"])


# ── Feedback on existing classifier tags ────────────────────────────────────────

def submit_tag_feedback(article_id, tag, vote, client_ip):
    """Record a 👍/👎 on a classifier tag that is actually on the article.

    One vote per (article, tag, client_ip): a re-POST flips the existing vote
    rather than stacking. Returns {tag, vote, up, down}. Raises ValueError on a
    bad vote or a tag the article doesn't carry; LookupError if no such article.
    """
    norm = normalize_tag(tag)
    if not norm:
        raise ValueError("Missing tag.")
    try:
        vote = int(vote)
    except (TypeError, ValueError):
        raise ValueError("Vote must be 1 or -1.")
    if vote not in (1, -1):
        raise ValueError("Vote must be 1 or -1.")

    with get_conn() as conn:
        classifier = _article_classifier_tags(conn, article_id)
        if classifier is None:
            raise LookupError("article not found")
        if norm not in classifier:
            raise ValueError("That isn't a classifier tag on this article.")

        conn.execute(
            "INSERT INTO tag_feedback (article_id, tag, vote, client_ip) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(article_id, tag, client_ip) "
            "DO UPDATE SET vote = excluded.vote, created_at = datetime('now')",
            (article_id, norm, vote, client_ip or ""),
        )
        conn.commit()
        row = conn.execute(
            "SELECT "
            "  SUM(CASE WHEN vote = 1  THEN 1 ELSE 0 END) AS up, "
            "  SUM(CASE WHEN vote = -1 THEN 1 ELSE 0 END) AS down "
            "FROM tag_feedback WHERE article_id = ? AND tag = ?",
            (article_id, norm),
        ).fetchone()
        return {"tag": norm, "vote": vote,
                "up": row["up"] or 0, "down": row["down"] or 0}


def get_tag_feedback_summary(article_id):
    """{tag: {"up": n, "down": n}} across all feedback on this article (admin)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT tag, "
            "  SUM(CASE WHEN vote = 1  THEN 1 ELSE 0 END) AS up, "
            "  SUM(CASE WHEN vote = -1 THEN 1 ELSE 0 END) AS down "
            "FROM tag_feedback WHERE article_id = ? GROUP BY tag",
            (article_id,),
        ).fetchall()
        return {r["tag"]: {"up": r["up"] or 0, "down": r["down"] or 0}
                for r in rows}


# ── Visitor-proposed tags (moderated) ───────────────────────────────────────────

def submit_user_tag(article_id, tag, client_ip):
    """Propose a topic tag for an article. Enters a pending moderation queue.

    Returns {"status": ..., "tag": norm} where status is:
      - "exists"   the tag is already a classifier tag, or an already-approved
                   user tag — nothing to do.
      - "approved" / "rejected"  a prior identical suggestion was already decided.
      - "bumped"   a pending suggestion already existed and a *different* IP
                   corroborated it (votes incremented).
      - "pending"  a new suggestion was queued (or an identical pending one from
                   the same IP was left untouched).

    Raises ValueError on an empty/too-long tag; LookupError if no such article.
    """
    norm = normalize_tag(tag)
    if len(norm) < MIN_TAG_LEN:
        raise ValueError("Please enter a topic of at least two characters.")
    if len(norm) > MAX_TAG_LEN:
        raise ValueError(f"Topics must be {MAX_TAG_LEN} characters or fewer.")
    in_vocab = 1 if is_vocab_tag(norm) else 0

    with get_conn() as conn:
        classifier = _article_classifier_tags(conn, article_id)
        if classifier is None:
            raise LookupError("article not found")
        if norm in classifier:
            return {"status": "exists", "tag": norm}

        existing = conn.execute(
            "SELECT id, status, client_ip FROM user_tags "
            "WHERE article_id = ? AND tag = ?",
            (article_id, norm),
        ).fetchone()
        if existing is not None:
            if existing["status"] == "pending":
                # votes is a SOFT corroboration signal for the admin queue, not a
                # tamper-proof distinct-voter count: it only compares against the
                # most-recent submitter and is capped at MAX_VOTES. Moderation,
                # not the count, gates publication, so an inflated badge is at
                # worst queue noise. (One-vote-per-IP, the way tag_feedback
                # enforces it, would need a per-voter table — not worth it here.)
                if client_ip and client_ip != existing["client_ip"]:
                    conn.execute(
                        "UPDATE user_tags SET votes = min(votes + 1, ?), "
                        "client_ip = ? WHERE id = ?",
                        (MAX_VOTES, client_ip, existing["id"]),
                    )
                    conn.commit()
                    return {"status": "bumped", "tag": norm}
                return {"status": "pending", "tag": norm}
            # approved or rejected — already adjudicated; don't resurface
            return {"status": existing["status"], "tag": norm}

        try:
            conn.execute(
                "INSERT INTO user_tags "
                "  (article_id, tag, in_vocab, status, votes, client_ip) "
                "VALUES (?, ?, ?, 'pending', 1, ?)",
                (article_id, norm, in_vocab, client_ip),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # A concurrent request inserted the same (article, tag) first.
            return {"status": "pending", "tag": norm}
        return {"status": "pending", "tag": norm}


def get_approved_user_tags(article_id):
    """Approved community tags for an article, as a list of strings (render)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT tag FROM user_tags "
            "WHERE article_id = ? AND status = 'approved' ORDER BY tag",
            (article_id,),
        ).fetchall()
        return [r["tag"] for r in rows]


# ── Moderation (admin) ──────────────────────────────────────────────────────────

def list_user_tag_queue(status=None):
    """Suggestion queue joined to article title/journal, newest first. Pass a
    status ('pending'|'approved'|'rejected') to filter; default returns all."""
    where, params = "", []
    if status:
        where = "WHERE u.status = ?"
        params.append(status)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT u.id, u.article_id, u.tag, u.in_vocab, u.status, u.votes, "
            "       u.created_at, u.decided_at, u.decided_by, "
            "       a.title AS article_title, a.journal AS article_journal "
            "FROM user_tags u JOIN articles a ON a.id = u.article_id "
            f"{where} ORDER BY u.created_at DESC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]


def decide_user_tag(user_tag_id, decision, actor=None):
    """Approve or reject a suggestion. Returns the updated row dict, or None if
    no such id. `decision` is 'approve' or 'reject'."""
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be 'approve' or 'reject'")
    status = "approved" if decision == "approve" else "rejected"
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE user_tags SET status = ?, decided_at = datetime('now'), "
            "decided_by = ? WHERE id = ?",
            (status, actor, user_tag_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT id, article_id, tag, status, in_vocab, votes "
            "FROM user_tags WHERE id = ?",
            (user_tag_id,),
        ).fetchone()
        return dict(row) if row else None


def get_user_tag_counts():
    """{status: count} over the suggestion queue (admin badge)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM user_tags GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}
