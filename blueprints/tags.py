"""tags Blueprint — community tag contributions.

Public (rate-limited, no auth — same posture as the redaction request form;
the writes are low-stakes: a vote, or a suggestion a human must approve before
it is ever public):
    POST /api/articles/<id>/tag-feedback   👍/👎 an existing classifier tag
    POST /api/articles/<id>/suggest-tag    propose a topic tag → pending queue

Admin (Bearer PINAKES_ADMIN_TOKEN):
    GET  /api/admin/user-tags              the moderation queue
    POST /api/admin/user-tags/<id>/decide  approve | reject a suggestion
    GET  /admin/user-tags                  the review page (token entered client-side)
"""

import logging

from flask import Blueprint, request, jsonify, render_template, abort

from db import (
    submit_tag_feedback,
    submit_user_tag,
    list_user_tag_queue,
    decide_user_tag,
)
from auth import require_admin_token, _client_ip
from rate_limit import limiter, LIMITS, client_ip_key

log = logging.getLogger(__name__)

bp = Blueprint("tags", __name__)


def _json_body():
    """Parsed JSON body, or {} — tolerant of missing/!json bodies so a bad
    request becomes a clean 400 from our own validation rather than a 415."""
    return request.get_json(silent=True) or {}


# ── Public ──────────────────────────────────────────────────────────────────────

@bp.route("/api/articles/<int:article_id>/tag-feedback", methods=["POST"])
@limiter.limit(LIMITS["tag_feedback"])
def tag_feedback(article_id):
    """Record a 👍/👎 on a classifier tag. Body: {"tag": str, "vote": 1|-1}."""
    body = _json_body()
    try:
        result = submit_tag_feedback(
            article_id, body.get("tag"), body.get("vote"), client_ip_key())
    except LookupError:
        abort(404)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"status": "ok", **result})


@bp.route("/api/articles/<int:article_id>/suggest-tag", methods=["POST"])
@limiter.limit(LIMITS["tag_suggestion"])
def suggest_tag(article_id):
    """Propose a topic tag for an article. Body: {"tag": str}. The suggestion
    enters a pending moderation queue; nothing is shown publicly until approved.
    """
    body = _json_body()
    try:
        result = submit_user_tag(article_id, body.get("tag"), client_ip_key())
    except LookupError:
        abort(404)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    log.info("Tag suggestion '%s' on article #%s → %s (ip=%s)",
             result.get("tag"), article_id, result.get("status"), _client_ip())
    return jsonify({"status": "ok", **result})


# ── Admin moderation ────────────────────────────────────────────────────────────

@bp.route("/api/admin/user-tags", methods=["GET"])
@require_admin_token
def admin_list_user_tags():
    """The suggestion queue. ?status= filters (pending|approved|rejected);
    default returns pending only (the actionable set)."""
    status = request.args.get("status", "pending")
    items = list_user_tag_queue(status=status or None)
    return jsonify({"user_tags": items})


@bp.route("/api/admin/user-tags/<int:user_tag_id>/decide", methods=["POST"])
@require_admin_token
def admin_decide_user_tag(user_tag_id):
    """Approve or reject a suggestion. Body: {"decision": "approve"|"reject"}."""
    body = _json_body()
    decision = (body.get("decision") or "").strip()
    actor = f"admin@{_client_ip()}"
    try:
        row = decide_user_tag(user_tag_id, decision, actor=actor)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if row is None:
        abort(404)
    log.info("User tag #%s %sED by %s (tag='%s', article #%s)",
             user_tag_id, decision.upper(), actor, row["tag"], row["article_id"])
    return jsonify({"status": "ok", "user_tag": row})


@bp.route("/admin/user-tags", methods=["GET"])
def admin_user_tags_page():
    """Review page shell. Shows nothing until the admin pastes their
    PINAKES_ADMIN_TOKEN (held in sessionStorage, sent as a Bearer header to the
    /api/admin/user-tags endpoints). No queue data is embedded in the HTML."""
    return render_template("admin_user_tags.html")
