"""admin Blueprint — auth-gated mutating endpoints and the layered health checks."""

import json
import logging
import os
import threading

from flask import Blueprint, jsonify, request, render_template

from auth import require_admin_token, _client_ip
from rate_limit import limiter, LIMITS, fetch_auth_failing
import health as _health

log = logging.getLogger(__name__)

bp = Blueprint("admin", __name__)


@bp.route("/fetch", methods=["POST"])
@limiter.limit(LIMITS["fetch"], exempt_when=fetch_auth_failing)
@require_admin_token
def trigger_fetch():
    """Kick off a fetch of all sources in a background thread.
    Requires `Authorization: Bearer <PINAKES_ADMIN_TOKEN>`.

    Default: incremental fetch (new articles since each journal's last
    fetch). With a JSON body of {"deep": true} (or ?deep=1): full corpus
    revalidation — per-journal CrossRef coverage audit, complete catalog
    walk with metadata fill on existing rows, then RSS/OAI and scraper
    re-runs. See deep_refresh.py.

    Resolves _run_background_fetch through the app module on each call so
    `patch("app._run_background_fetch", mock)` in tests still intercepts."""
    body = request.get_json(silent=True) or {}
    deep = bool(body.get("deep")) or request.args.get("deep") == "1"
    import app as _app
    # Don't start a second fetch on top of a running one — overlapping writer
    # threads against the same SQLite file caused "database is locked".
    if _app._fetch_lock.locked():
        return jsonify({"status": "fetch already in progress; ignored"}), 409
    t = threading.Thread(target=_app._run_background_fetch,
                         kwargs={"deep": deep}, daemon=True)
    t.start()
    return jsonify({"status": "deep refresh started" if deep else "fetch started"})


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
    machine — longer than gunicorn's worker timeout (300s per the
    Dockerfile CMD), so a synchronous endpoint returns 502 (worker killed
    mid-pipeline). Going async sidesteps the timeout entirely.

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


# ── Maintenance pipeline (citations, enrichment, retag) ───────────────────

def _run_maintenance_in_background():
    """Background-thread target for /api/admin/run-maintenance. Runs the
    enrichment half of weekly_maintenance (the cron's daily /fetch already
    covers article ingest, steps 1-3): citation harvesting, OpenAlex
    enrichment, LiCS reference scrape, retag + FTS rebuild, OA backfill,
    OpenAlex citation counts. cite_fetcher resyncs the denormalized
    internal_cited_by_count / internal_cites_count columns at the end of
    its run, so rankings stay consistent with the citations table.

    Shares app._fetch_lock with the fetch/deep-refresh writers. The weekly
    run (Sundays 04:00) used to overlap the daily fetch's multi-hour scraper
    phase, and both are heavy SQLite writers — that contention surfaced as
    "database is locked" in the scraper's upserts (incident 2026-06-21).
    Maintenance now WAITS for any in-flight fetch to finish rather than
    running a second concurrent writer; a generous timeout guards against a
    wedged fetch holding the lock forever."""
    import app as _app
    if not _app._fetch_lock.acquire(timeout=6 * 3600):
        log.error("Maintenance skipped — fetch lock held >6h (fetch stuck?).")
        return
    try:
        from weekly_maintenance import run_pipeline
        rc = run_pipeline(steps=(4, 5, 6, 7, 8, 9))
        log.info("Maintenance pipeline finished (exit status %s).", rc)
    except Exception:  # noqa: BLE001
        log.exception("Maintenance pipeline raised unhandled exception")
    finally:
        _app._fetch_lock.release()


@bp.route("/api/admin/run-maintenance", methods=["POST"])
@require_admin_token
def run_maintenance_now():
    """Kick off the citation/enrichment maintenance pipeline asynchronously
    and return 200 immediately (the pipeline runs for tens of minutes —
    far beyond the worker timeout). Called weekly by
    .github/workflows/cron.yml; safe to trigger manually after large
    ingests (deep refresh, new journal). Success/failure surfaces in fly
    logs as the per-step summary lines."""
    t = threading.Thread(target=_run_maintenance_in_background, daemon=True)
    t.start()
    return jsonify({"status": "maintenance started"}), 200


# ── Datastories cache pre-warm ─────────────────────────────────────────────

def _run_prewarm_in_background():
    """Background-thread target for /api/admin/prewarm. Recomputes the
    default-parameter cache entries for every disk-cached heavy analysis so
    the first human visitor after a data change gets disk-cache speed
    instead of a multi-minute compute (or a worker timeout). Order matters:
    ds_books_everyone_reads aggregates the other tools' cached results, so
    it runs last. Calls the compute functions directly — not over HTTP — so
    no request timeout applies."""
    from db import datastories as ds
    from db.citations import (
        get_author_cocitation_network, get_temporal_network_evolution,
    )
    jobs = [
        ("speed_of_influence",   lambda: ds.ds_speed_of_influence()),
        ("shifting_currents",    lambda: ds.ds_shifting_currents()),
        ("two_maps",             lambda: ds.ds_two_maps()),
        ("walls_bridges",        lambda: ds.ds_walls_bridges()),
        ("communities_time",     lambda: ds.ds_communities_time()),
        ("shared_foundations",   lambda: ds.ds_shared_foundations()),
        ("uneven_debts",         lambda: ds.ds_uneven_debts()),
        ("first_spark",          lambda: ds.ds_first_spark()),
        ("border_crossers",      lambda: ds.ds_border_crossers()),
        ("author_cocitation",    lambda: get_author_cocitation_network()),
        ("temporal_evolution",   lambda: get_temporal_network_evolution()),
        ("books_everyone_reads", lambda: ds.ds_books_everyone_reads()),
    ]
    import time as _time
    for name, job in jobs:
        t0 = _time.time()
        try:
            job()
            log.info("prewarm: %s done in %.1fs", name, _time.time() - t0)
        except Exception:  # noqa: BLE001
            log.exception("prewarm: %s failed after %.1fs", name, _time.time() - t0)
    log.info("prewarm: all jobs attempted")


@bp.route("/api/admin/prewarm", methods=["POST"])
@require_admin_token
def prewarm_now():
    """Kick off the Datastories/Explore cache pre-warm asynchronously.
    Called nightly by cron.yml after the fetch lands, so the day's first
    visitors never pay the heavy computes."""
    t = threading.Thread(target=_run_prewarm_in_background, daemon=True)
    t.start()
    return jsonify({"status": "prewarm started"}), 200


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


# ── Article curation (edit / suppress) ────────────────────────────────────────
#
# A sysadmin surface for cleaning up individual records: fix HTML/encoding
# artifacts in a title, correct authors, or durably remove junk (test deposits,
# spam) via the suppression blocklist. Same gated pattern as /admin/redactions:
# the page shell is public HTML; it does nothing until the admin pastes their
# PINAKES_ADMIN_TOKEN, which is sent as a Bearer header to these endpoints.

# Fields the curation UI may edit. Kept in one place so the API and the page
# agree on what is editable.
_EDITABLE_FIELDS = (
    "title", "authors", "abstract", "pub_date", "journal",
    "doi", "oa_url", "oa_status", "keywords", "tags",
)


@bp.route("/api/admin/article/<int:article_id>", methods=["GET"])
@require_admin_token
def admin_get_article(article_id):
    """Full record for the editor to load."""
    import db
    art = db.get_article_by_id(article_id)
    if not art:
        return jsonify({"error": "not found"}), 404
    return jsonify({"article": art, "editable_fields": list(_EDITABLE_FIELDS)})


@bp.route("/api/admin/article-search", methods=["GET"])
@require_admin_token
def admin_search_articles():
    """Look up candidates for curation by id, DOI, or title/author text.

    Numeric q is treated as an id first; otherwise a DOI exact match, then a
    title/author LIKE. Small result cap — this is a lookup, not a browse."""
    import db
    from db.core import get_conn
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})
    results = []
    with get_conn() as conn:
        if q.isdigit():
            row = conn.execute(
                "SELECT id, title, authors, journal, pub_date, doi "
                "FROM articles WHERE id = ?", (int(q),)
            ).fetchone()
            if row:
                results.append(dict(row))
        if not results:
            like = f"%{q}%"
            rows = conn.execute(
                "SELECT id, title, authors, journal, pub_date, doi FROM articles "
                "WHERE doi = ? OR title LIKE ? OR authors LIKE ? "
                "ORDER BY pub_date DESC LIMIT 25",
                (q, like, like),
            ).fetchall()
            results = [dict(r) for r in rows]
    return jsonify({"results": results})


@bp.route("/api/admin/article/<int:article_id>", methods=["PUT"])
@require_admin_token
def admin_update_article(article_id):
    """Edit whitelisted fields on one article. JSON body of {field: value}."""
    import db
    body = request.get_json(silent=True) or {}
    fields = {k: v for k, v in body.items() if k in _EDITABLE_FIELDS}
    if not fields:
        return jsonify({"error": "no editable fields supplied",
                        "editable_fields": list(_EDITABLE_FIELDS)}), 400
    if not db.update_article(article_id, fields):
        return jsonify({"error": "not found"}), 404
    log.info("Article #%s edited by admin@%s: fields=%s",
             article_id, _client_ip(), sorted(fields))
    return jsonify({"status": "updated", "article": db.get_article_by_id(article_id)})


@bp.route("/api/admin/article/<int:article_id>/suppress", methods=["POST"])
@require_admin_token
def admin_suppress_article(article_id):
    """Durably remove an article: blocklist its DOI/URL, then delete it, so the
    next fetch cannot resurrect it. JSON body may carry {"reason": "..."}."""
    import db
    body = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip() or None
    result = db.suppress_article(
        article_id, reason=reason, actor=f"admin@{_client_ip()}")
    if not result.get("ok"):
        return jsonify(result), 400
    if not result.get("deleted_ids"):
        return jsonify({**result, "warning": "article id not found; "
                        "DOI/URL still blocklisted if supplied"}), 404
    log.info("Article #%s SUPPRESSED by admin@%s (doi=%s reason=%r)",
             article_id, _client_ip(), result.get("doi"), reason)
    return jsonify({"status": "suppressed", **result})


@bp.route("/api/admin/suppressed", methods=["GET"])
@require_admin_token
def admin_list_suppressed():
    """The current blocklist, for review and un-suppression."""
    import db
    return jsonify({"suppressed": db.list_suppressed()})


@bp.route("/api/admin/unsuppress", methods=["POST"])
@require_admin_token
def admin_unsuppress():
    """Remove a DOI/URL from the blocklist so it can be re-ingested. JSON body
    {"doi": "..."} and/or {"url": "..."}. Does not re-fetch."""
    import db
    body = request.get_json(silent=True) or {}
    doi = (body.get("doi") or "").strip() or None
    url = (body.get("url") or "").strip() or None
    if not doi and not url:
        return jsonify({"error": "supply doi and/or url"}), 400
    removed = db.unsuppress(doi=doi, url=url)
    log.info("Unsuppress by admin@%s (doi=%s url=%s) -> %s",
             _client_ip(), doi, url, removed)
    return jsonify({"status": "unsuppressed" if removed else "not-on-blocklist",
                    "removed": removed})


@bp.route("/admin/curate", methods=["GET"])
def admin_curate_page():
    """The curation console shell. Public HTML; loads nothing until the admin
    pastes their token (held in sessionStorage, sent as Bearer on every API
    call). No article data is embedded server-side."""
    return render_template("admin_curate.html")

