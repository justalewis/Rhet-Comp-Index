"""usage Blueprint — the browser-event endpoint, the /admin/usage dashboard,
and the token-gated JSON, report and job endpoints behind it.

Nothing in this blueprint is itself recorded (usage.tools.SKIP_BLUEPRINTS).
"""

import datetime as _dt
import logging
import os
import threading

from flask import Blueprint, Response, abort, jsonify, render_template, request

from auth import require_admin_token
from rate_limit import LIMITS, TRUSTED_IP_HEADER_ENV, limiter

log = logging.getLogger(__name__)

bp = Blueprint("usage", __name__)


@bp.route("/api/e", methods=["POST"])
@limiter.limit("120 per minute")
def usage_event():
    """One event from static/usage.js. Always 204: the browser has nothing
    useful to do with an error, and a refusal tells a prober nothing."""
    from usage import beacon, capture
    if capture.enabled():
        try:
            beacon.handle()
        except Exception:  # noqa: BLE001 — never 500 a beacon
            log.debug("usage event rejected", exc_info=True)
    return Response(status=204)


def _survey_page(**ctx):
    """Render /tell-us with the context the sidebar needs."""
    from app import _get_sidebar
    from db import get_new_article_count
    from journals import UNAVAILABLE_JOURNALS
    from usage.survey import ROLES, USES
    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    return render_template(
        "usage_survey.html", roles=ROLES, uses=USES,
        print_journals=print_journals, web_journals=web_journals,
        all_journals=all_journals, journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=get_new_article_count(days=7, back_catalog=False),
        active_nav="about", **ctx)


@bp.route("/tell-us", methods=["GET", "POST"])
@limiter.limit(LIMITS["usage_survey"], methods=["POST"])
def tell_us():
    """The optional "how you use Pinakes" form. 404 unless PINAKES_USAGE_SURVEY
    is set, so it can ship before anyone is ready to read the responses."""
    from usage import survey
    if not survey.enabled():
        abort(404)
    if request.method == "GET":
        return _survey_page(form={}, came_from=request.args.get("from", ""))
    # Honeypot: a field hidden from people. A bot that fills it gets the
    # ordinary thank-you page and nothing is stored.
    if (request.form.get("website") or "").strip():
        return _survey_page(sent=True)
    try:
        data = survey.clean(request.form)
    except survey.Invalid as e:
        return _survey_page(form=request.form, error=str(e),
                            came_from=request.form.get("came_from", "")), 400
    survey.save(data, came_from=request.form.get("came_from"))
    return _survey_page(sent=True)


@bp.route("/api/admin/usage/responses")
@require_admin_token
def usage_responses():
    """Every form response in the range, contact addresses included."""
    from usage.report import default_range
    from usage.survey import list_responses
    start, end = default_range(30)
    start = request.args.get("from") or start
    end = request.args.get("to") or end
    try:
        _dt.date.fromisoformat(start)
        _dt.date.fromisoformat(end)
    except ValueError:
        return jsonify({"error": "dates must be YYYY-MM-DD"}), 400
    return jsonify({"responses": list_responses(start, end)})


@bp.route("/api/admin/usage/responses/<int:response_id>/delete", methods=["POST"])
@require_admin_token
def usage_response_delete(response_id):
    """Delete one response, for a respondent who asks or for spam."""
    from usage.survey import delete
    if not delete(response_id):
        return jsonify({"error": "no such response"}), 404
    return jsonify({"status": "deleted"})


@bp.route("/admin/usage")
def admin_usage_page():
    """The dashboard shell. It holds no data; the page's script asks for the
    admin token and calls the endpoints below."""
    return render_template("admin_usage.html")


def _range_args():
    from usage.report import PEOPLE, default_range
    start, end = default_range(30)
    start = request.args.get("from") or start
    end = request.args.get("to") or end
    _dt.date.fromisoformat(start)
    _dt.date.fromisoformat(end)
    classes = tuple(c.strip() for c in (request.args.get("classes") or "").split(",") if c.strip())
    public = request.args.get("public") in ("1", "true", "yes")
    try:
        min_count = max(1, min(50, int(request.args.get("min_count") or 3)))
    except ValueError:
        min_count = 3
    return start, end, classes or PEOPLE, public, min_count


@bp.route("/api/admin/usage/summary")
@require_admin_token
def usage_summary():
    from usage.report import build_summary
    try:
        start, end, classes, public, min_count = _range_args()
    except ValueError:
        return jsonify({"error": "dates must be YYYY-MM-DD"}), 400
    return jsonify(build_summary(start, end, classes=classes, public=public, min_count=min_count))


@bp.route("/api/admin/usage/report")
@require_admin_token
def usage_report():
    """The grant report as a download: format=html (printable), zip (CSVs and
    JSON) or json."""
    from usage.report import build_summary, render_html, to_zip
    try:
        start, end, classes, public, min_count = _range_args()
    except ValueError:
        return jsonify({"error": "dates must be YYYY-MM-DD"}), 400
    summary = build_summary(start, end, classes=classes, public=public, min_count=min_count)
    fmt = (request.args.get("format") or "html").lower()
    stem = f"pinakes-usage-{start}-to-{end}{'-public' if public else ''}"
    if fmt == "zip":
        return Response(to_zip(summary), mimetype="application/zip",
                        headers={"Content-Disposition": f"attachment; filename={stem}.zip"})
    if fmt == "json":
        return jsonify(summary)
    return Response(render_html(summary), mimetype="text/html",
                    headers={"Content-Disposition": f"attachment; filename={stem}.html"})


@bp.route("/api/admin/usage/status")
@require_admin_token
def usage_status():
    """Is recording on, is the IP chain right, and how fresh is everything."""
    from usage import capture, store, survey
    from usage.refresh import data_status
    from usage.resolve import JOB_LOCK
    path = store.usage_db_path()
    out = {
        "tracking_enabled": capture.enabled(),
        "survey_enabled": survey.enabled(),
        "trusted_ip_header": os.environ.get(TRUSTED_IP_HEADER_ENV) or None,
        "usage_db": path,
        "usage_db_mb": round(os.path.getsize(path) / 1e6, 2) if os.path.exists(path) else 0,
        "data_dir": store.data_dir(),
        "data_files": data_status(),
        "job_running": JOB_LOCK.locked(),
        "queued_rows": len(capture._queue),
    }
    conn = store.connect()
    try:
        out["hits"] = conn.execute("SELECT COUNT(*) FROM hits").fetchone()[0]
        out["unresolved_hits"] = conn.execute(
            "SELECT COUNT(*) FROM hits WHERE resolved = 0").fetchone()[0]
        out["raw_addresses_held"] = conn.execute(
            "SELECT COUNT(DISTINCT ip) FROM hits WHERE ip IS NOT NULL").fetchone()[0]
        out["oldest_raw_address"] = conn.execute(
            "SELECT MIN(ts) FROM hits WHERE ip IS NOT NULL").fetchone()[0]
        out["events"] = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        out["institutions"] = conn.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]
        out["networks_cached"] = conn.execute("SELECT COUNT(*) FROM networks").fetchone()[0]
        out["first_day"] = conn.execute("SELECT MIN(day) FROM hits").fetchone()[0]
        out["runs"] = [dict(r) for r in conn.execute(
            "SELECT job, started_at, finished_at, ok, summary FROM runs ORDER BY id DESC LIMIT 8")]
    finally:
        conn.close()
    return jsonify(out)


def _start_job(target, **kwargs):
    from usage.resolve import JOB_LOCK
    if JOB_LOCK.locked():
        return jsonify({"status": "a usage job is already running"}), 409
    threading.Thread(target=target, kwargs=kwargs, daemon=True, name="usage-job").start()
    return jsonify({"status": "started"}), 202


@bp.route("/api/admin/usage/resolve", methods=["POST"])
@require_admin_token
def usage_resolve():
    """Nightly: resolve addresses, rebuild visitor-days, purge raw IPs."""
    from usage.resolve import run
    return _start_job(run)


@bp.route("/api/admin/usage/refresh-data", methods=["POST"])
@require_admin_token
def usage_refresh_data():
    """Download any IP database or institution list that is missing or stale.
    {"force": true} re-downloads everything."""
    from usage import capture
    from usage.refresh import refresh
    body = request.get_json(silent=True) or {}
    force = bool(body.get("force"))
    if not capture.enabled() and not force:
        # The weekly task should not pull 65 MB of IP databases for a feature
        # that is switched off. {"force": true} still works, to stage the data
        # before turning recording on.
        return jsonify({"status": "recording is off; nothing downloaded"}), 200
    return _start_job(refresh, force=force)
