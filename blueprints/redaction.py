"""redaction Blueprint — the author opt-out ("Right to Be Forgotten") surface.

Public:
    GET  /redaction-request            the request form
    POST /redaction-request            create a request + send email verify link
    GET  /redaction-request/verify/<t> consume the one-time email token

Admin (Bearer PINAKES_ADMIN_TOKEN):
    GET  /api/admin/redaction-requests           review queue (incl. PII — gated)
    POST /api/admin/redaction-request/<id>/approve   audit → redact the author
    POST /api/admin/redaction-request/<id>/deny

Verification proves the requester controls the claimed identity (email now,
ORCID OAuth in the ORCID blueprint). Verified requests sit in a queue; an admin
approves before any redaction fires (admin-gated, per the design decision). The
real name and requester contact details are never exposed on a public surface.
"""

import logging

from flask import (
    Blueprint, request, render_template, jsonify, url_for, abort,
    redirect as flask_redirect, current_app,
)

import redaction
import orcid_oauth
from auth import require_admin_token, _client_ip
from notifications import send_email
from rate_limit import limiter, LIMITS

log = logging.getLogger(__name__)

bp = Blueprint("redaction", __name__)


def _state_serializer():
    """Sign the ORCID `state` (the request id) with the app secret so a
    callback can't be forged to verify an arbitrary request."""
    from itsdangerous import URLSafeTimedSerializer
    return URLSafeTimedSerializer(current_app.secret_key, salt="redaction-orcid")


# ── Public ────────────────────────────────────────────────────────────────────

@bp.route("/redaction-request", methods=["GET"])
def request_form():
    """Render the opt-out request form."""
    return render_template("redaction_request.html", stage="form",
                           orcid_available=orcid_oauth.is_configured())


def _parse_variants(variants_raw):
    return [
        v.strip() for v in variants_raw.replace(";", "\n").splitlines() if v.strip()
    ] or None


@bp.route("/redaction-request", methods=["POST"])
@limiter.limit(LIMITS["redaction_request"])
def submit_request():
    """Create a redaction request and start verification.

    Two paths, chosen by the submit button (`method`): an email round-trip
    (one-time link) or ORCID OAuth (the stronger proof). We always render the
    same neutral confirmation for the email path regardless of whether the
    address matches anything, so the form can't probe who is in the index."""
    author_name = (request.form.get("author_name") or "").strip()
    email = (request.form.get("email") or "").strip()
    orcid = (request.form.get("orcid") or "").strip()
    variants_raw = (request.form.get("variants") or "").strip()
    method = (request.form.get("method") or "email").strip()

    def _form_error(msg):
        return render_template(
            "redaction_request.html", stage="form", error=msg,
            orcid_available=orcid_oauth.is_configured(),
            author_name=author_name, email=email, orcid=orcid, variants=variants_raw,
        ), 400

    if not author_name:
        return _form_error("Please provide the name your work is published under.")

    variants = _parse_variants(variants_raw)

    # ── ORCID OAuth path ──────────────────────────────────────────────────
    if method == "orcid":
        if not orcid_oauth.is_configured():
            return _form_error("ORCID verification isn't available right now — "
                               "please verify by email instead.")
        rid, _ = redaction.create_request(
            author_name, email=email or None, orcid=orcid or None,
            method="orcid", variants=variants,
        )
        log.info("Redaction request #%s created via ORCID (ip=%s)", rid, _client_ip())
        state = _state_serializer().dumps(rid)
        redirect_uri = url_for("redaction.orcid_callback", _external=True)
        return flask_redirect(orcid_oauth.authorize_url(state, redirect_uri))

    # ── Email path (default) ──────────────────────────────────────────────
    if not email:
        return _form_error("Please provide an email address we can verify.")

    rid, raw_token = redaction.create_request(
        author_name, email=email, orcid=orcid or None,
        method="email", variants=variants,
    )
    log.info("Redaction request #%s created via email (ip=%s)", rid, _client_ip())

    verify_url = url_for("redaction.verify", token=raw_token, _external=True)
    send_email(
        email,
        "Confirm your Pinakes removal request",
        "You (or someone using this address) asked to have an author's name "
        "removed from the Pinakes index.\n\n"
        f"Requested name: {author_name}\n\n"
        "If this was you, confirm by opening this one-time link:\n\n"
        f"  {verify_url}\n\n"
        "Confirming verifies your email; a person then reviews the request "
        "before anything changes. If you didn't make this request, ignore "
        "this message and nothing will happen.\n",
    )

    return render_template("redaction_request.html", stage="submitted", email=email)


@bp.route("/redaction-request/orcid/callback", methods=["GET"])
def orcid_callback():
    """ORCID OAuth return: exchange the code, record the verified iD + name on
    the request, and mark it verified for admin review."""
    code = request.args.get("code")
    state = request.args.get("state")
    if not code or not state:
        return render_template("redaction_request.html", stage="invalid"), 400
    try:
        rid = _state_serializer().loads(state, max_age=1800)  # 30-min window
    except Exception:  # noqa: BLE001 — bad/expired/forged state
        return render_template("redaction_request.html", stage="invalid"), 400

    if not redaction.get_request(rid):
        abort(404)

    redirect_uri = url_for("redaction.orcid_callback", _external=True)
    try:
        result = orcid_oauth.exchange_code(code, redirect_uri)
    except Exception as exc:  # noqa: BLE001
        log.error("ORCID token exchange failed for request #%s: %s", rid, exc)
        return render_template("redaction_request.html", stage="invalid"), 400

    matches = redaction.attach_orcid_verification(
        rid, (result or {}).get("orcid"), (result or {}).get("name"))
    log.info("Redaction request #%s ORCID-verified (name_match=%s, ip=%s)",
             rid, matches, _client_ip())
    return render_template("redaction_request.html", stage="verified")


@bp.route("/redaction-request/verify/<token>", methods=["GET"])
def verify(token):
    """Consume the one-time email token, marking the request verified."""
    rid = redaction.verify_request_by_token(token)
    if rid is None:
        return render_template("redaction_request.html", stage="invalid"), 404
    log.info("Redaction request #%s email-verified (ip=%s)", rid, _client_ip())
    return render_template("redaction_request.html", stage="verified")


# ── Admin review queue ────────────────────────────────────────────────────────

@bp.route("/api/admin/redaction-requests", methods=["GET"])
@require_admin_token
def admin_list_requests():
    """Review queue. Returns requester email/ORCID and the claimed name — only
    ever to an authenticated admin, never on a public surface. ?status= filters
    (pending|verified|approved|denied); default returns verified + pending."""
    status = request.args.get("status")
    if status:
        items = redaction.list_requests(status=status)
    else:
        items = [r for r in redaction.list_requests()
                 if r["verification_status"] in ("pending", "verified")]
    return jsonify({"requests": items})


@bp.route("/api/admin/redaction-request/<int:rid>/approve", methods=["POST"])
@require_admin_token
def admin_approve(rid):
    """Approve a request: writes the audit row, then redacts the claimed author
    across the whole index. Only verified requests may be approved."""
    req = redaction.get_request(rid)
    if not req:
        abort(404)
    if req["verification_status"] != "verified":
        return jsonify({
            "error": f"request is '{req['verification_status']}', not 'verified'; "
                     "only verified requests can be approved",
        }), 409
    actor = f"admin@{_client_ip()}"
    redaction.decide_request(rid, "approved", actor=actor)
    log.info("Redaction request #%s APPROVED by %s", rid, actor)
    return jsonify({"status": "approved", "request_id": rid})


@bp.route("/api/admin/redaction-request/<int:rid>/deny", methods=["POST"])
@require_admin_token
def admin_deny(rid):
    """Deny a request (audited; no redaction)."""
    req = redaction.get_request(rid)
    if not req:
        abort(404)
    actor = f"admin@{_client_ip()}"
    redaction.decide_request(rid, "denied", actor=actor)
    log.info("Redaction request #%s DENIED by %s", rid, actor)
    return jsonify({"status": "denied", "request_id": rid})


@bp.route("/api/admin/redaction-request/<int:rid>/audit", methods=["GET"])
@require_admin_token
def admin_request_audit(rid):
    """The append-only audit trail for one request (created / verified /
    approved / denied), shown in the admin review page."""
    if not redaction.get_request(rid):
        abort(404)
    return jsonify({"audit": redaction.get_audit(rid)})


@bp.route("/admin/redactions", methods=["GET"])
def admin_redactions_page():
    """The admin review page for redaction requests.

    The page shell is public; it shows nothing until the admin pastes their
    PINAKES_ADMIN_TOKEN, which is held in sessionStorage and sent as a Bearer
    header on every fetch to the token-gated /api/admin/* endpoints above. No
    requester data is ever embedded in the HTML — it all loads client-side
    through the authenticated API."""
    return render_template("admin_redactions.html")
