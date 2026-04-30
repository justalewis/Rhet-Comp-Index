"""admin Blueprint — auth-gated mutating endpoints and the layered health checks."""

import logging
import threading

from flask import Blueprint, jsonify

from auth import require_admin_token
from rate_limit import limiter, LIMITS, fetch_auth_failing
import health as _health

log = logging.getLogger(__name__)

bp = Blueprint("admin", __name__)


@bp.route("/fetch", methods=["POST"])
@limiter.limit(LIMITS["fetch"], exempt_when=fetch_auth_failing)
@require_admin_token
def trigger_fetch():
    """Kick off an incremental fetch of all sources in a background thread.
    Requires `Authorization: Bearer <PINAKES_ADMIN_TOKEN>`.

    Resolves _run_background_fetch through the app module on each call so
    `patch("app._run_background_fetch", mock)` in tests still intercepts."""
    import app as _app
    t = threading.Thread(target=_app._run_background_fetch, daemon=True)
    t.start()
    return jsonify({"status": "fetch started"})


@bp.route("/health")
@limiter.exempt
def health():
    """Liveness probe — process is up. No DB query; returns in <1ms.
    This is what Fly hits every 15 seconds. Stays unauthenticated because
    Fly's checker can't carry tokens."""
    return jsonify(_health.liveness()), 200


@bp.route("/health/ready")
@limiter.exempt
def health_ready():
    """Readiness probe — DB reachable. Used by Fly as a deployment gate
    and by external monitoring. 503 if the SQLite file is missing or a
    `SELECT 1 FROM articles LIMIT 1` doesn't return within 250ms."""
    body, status = _health.readiness()
    return jsonify(body), status


@bp.route("/health/deep")
@require_admin_token
def health_deep():
    """Full diagnostic: counts, last-fetch, disk, scheduler heartbeat,
    integrity check (cached for 6h), security-header configuration.
    Admin-protected because it exposes operational metadata."""
    return jsonify(_health.deep_diagnostic()), 200

