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


@bp.route("/api/admin/run-backup", methods=["POST"])
@require_admin_token
def run_backup_now():
    """Run the SQLite backup pipeline synchronously: snapshot → zstd →
    age-encrypt → upload to S3-compatible bucket → prune retention.

    Called by .github/workflows/cron.yml at 03:00 UTC daily, hitting
    pinakes.xyz with the PINAKES_ADMIN_TOKEN GitHub secret. Synchronous
    so the GitHub Action sees real success/failure and so the resulting
    JSON response carries the full summary for log/audit.

    Writes /data/scheduler.heartbeat on success — that file is what
    /health/deep reads to report `scheduler_healthy`. With the
    standalone scheduler.py removed (see refactor-notes/13), the
    heartbeat is now the cron job's signal."""
    from backup import run_backup
    summary = run_backup()
    if summary.get("success"):
        try:
            _health.write_heartbeat()
        except Exception as exc:  # noqa: BLE001
            log.warning("heartbeat write after backup failed: %s", exc)
    status = 200 if summary.get("success") else 500
    return jsonify(summary), status

