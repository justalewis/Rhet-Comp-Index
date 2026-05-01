"""admin Blueprint — auth-gated mutating endpoints and the layered health checks."""

import json
import logging
import os
import threading

from flask import Blueprint, jsonify, request

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


def _run_backup_in_background():
    """Background-thread target for /api/admin/run-backup. Runs the full
    pipeline (snapshot → zstd → age → B2 upload → retention prune), writes
    the heartbeat on success, and logs the full summary at INFO so the
    operator can audit via `fly logs`. Failures are logged at ERROR; if
    Sentry is wired the failure will surface there too.

    Kept as a module-level function (not a closure) so it pickles cleanly
    and so tests can mock it without re-implementing the threading wrapper."""
    try:
        from backup import run_backup
        summary = run_backup()
        if summary.get("success"):
            try:
                _health.write_heartbeat()
            except Exception as exc:  # noqa: BLE001
                log.warning("heartbeat write after backup failed: %s", exc)
            log.info("Backup succeeded: %s", summary)
        else:
            log.error("Backup failed: %s", summary)
    except Exception as exc:  # noqa: BLE001
        log.exception("Backup raised unhandled exception: %s", exc)


@bp.route("/api/admin/run-backup", methods=["POST"])
@require_admin_token
def run_backup_now():
    """Kick off the SQLite backup pipeline asynchronously and return 200
    immediately. The pipeline (snapshot → zstd → age → B2 upload → retention
    prune) takes 2–5 minutes for the current ~190 MB DB on a 1-CPU Fly
    machine — longer than gunicorn's 120-second worker timeout, so a
    synchronous endpoint returns 502 (worker killed mid-pipeline). Going
    async sidesteps the timeout entirely.

    Called by .github/workflows/cron.yml at 03:00 UTC daily, hitting
    pinakes.xyz with the PINAKES_ADMIN_TOKEN GitHub secret. The cron
    workflow trusts the 200; the actual success/failure of the backup
    surfaces via:

      - Fly logs (INFO line "Backup succeeded: {...}" with the summary;
        ERROR line on failure with the failure dict)
      - /data/scheduler.heartbeat (touched on success; /health/deep reads
        this file to report scheduler_healthy)
      - Sentry (if SENTRY_DSN is set, unhandled exceptions surface there)

    If you need a richer success/failure signal back from the cron, add
    a polling step that hits /health/deep and checks the heartbeat
    timestamp. Not necessary for current operations; daily-snapshot
    monitoring via the storage bucket's "last modified" timestamp is
    sufficient."""
    t = threading.Thread(target=_run_backup_in_background, daemon=True)
    t.start()
    return jsonify({"status": "backup started"}), 200


# ── Disciplinary Calendar add-event ────────────────────────────────────────
# Appends a {year, type, title} entry to data/disciplinary_events.json.
# The Datastories ds_disciplinary_calendar() function reads this file at
# request time (merged with the code-curated seed list) so additions show
# up on the next page load, no restart required.

_DISCIPLINARY_EVENT_TYPES = {
    "journal_founded", "landmark_article",
    "external_crisis", "special_issue",
}


@bp.route("/api/admin/disciplinary-event", methods=["POST"])
@require_admin_token
def add_disciplinary_event():
    """Append one event to data/disciplinary_events.json. JSON body:
        {"year": 2024, "type": "landmark_article", "title": "Foo bar"}

    Idempotent on (year, title) — duplicate posts are silently dropped.
    Type must be one of: journal_founded, landmark_article,
    external_crisis, special_issue. Returns the full updated user-events
    list."""
    body = request.get_json(silent=True) or {}
    try:
        year = int(body.get("year"))
    except (TypeError, ValueError):
        return jsonify({"error": "year must be an integer"}), 400
    title = (body.get("title") or "").strip()
    ev_type = (body.get("type") or "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    if ev_type not in _DISCIPLINARY_EVENT_TYPES:
        return jsonify({
            "error": "type must be one of: " + ", ".join(sorted(_DISCIPLINARY_EVENT_TYPES))
        }), 400
    if year < 1900 or year > 2100:
        return jsonify({"error": "year out of plausible range (1900-2100)"}), 400

    here = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.normpath(os.path.join(here, "..", "data", "disciplinary_events.json"))
    os.makedirs(os.path.dirname(json_path), exist_ok=True)

    existing = []
    if os.path.exists(json_path):
        try:
            with open(json_path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                existing = loaded
        except Exception as exc:  # noqa: BLE001
            log.warning("disciplinary events JSON malformed; rewriting: %s", exc)
            existing = []

    key = (year, title.lower())
    seen = {(int(e.get("year", 0)), (e.get("title") or "").lower()) for e in existing if isinstance(e, dict)}
    if key in seen:
        return jsonify({"status": "duplicate", "events": existing}), 200

    new_event = {"year": year, "type": ev_type, "title": title}
    existing.append(new_event)
    existing.sort(key=lambda e: (e.get("year", 0), e.get("title", "")))
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    # Bust the ds_disciplinary_calendar disk cache so the new event shows
    # up immediately rather than waiting for the next DB-fingerprint change.
    # The cache stores files at <_cache_dir()>/<name>-<keyhash>.json.
    try:
        from datastories_cache import _cache_dir
        for f in _cache_dir().glob("ds_disciplinary_calendar-*.json"):
            try: f.unlink()
            except OSError: pass
    except Exception:
        pass

    return jsonify({"status": "added", "event": new_event, "events": existing}), 201

