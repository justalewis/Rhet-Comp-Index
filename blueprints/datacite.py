"""datacite Blueprint — research objects registered with DataCite.

Public, intentionally-unlinked dashboard at /datacite (like /wac), JSON under
/api/datacite/*, and a token-gated curation queue for candidate records:

    GET  /datacite                         the dashboard
    GET  /api/datacite/<analysis>          one analysis as JSON
    GET  /api/admin/datacite/queue         pending Tier C candidates   (Bearer admin token)
    POST /api/admin/datacite/decide        {"doi","decision","reason"} (Bearer admin token)
    GET  /admin/datacite                   review page shell (token entered client-side)

Only published tiers (A, B, curator-approved) appear on public surfaces.
"""

import logging

from flask import Blueprint, abort, jsonify, render_template, request

from auth import _client_ip, require_admin_token
from db import datacite as dc
from rate_limit import LIMITS, limiter
from web_helpers import _safe_int, cache_response

log = logging.getLogger(__name__)

bp = Blueprint("datacite", __name__)

_PAGE_CACHE = 1800
_API_CACHE = 3600

# name -> zero/one-argument callables returning JSON-serializable data
_ANALYSES = {
    "overview": dc.overview,
    "types": dc.type_composition,
    "repositories": dc.repositories,
    "supplement-by-journal": lambda: dc.supplement_rate("journal"),
    "supplement-by-year": lambda: dc.supplement_rate("year"),
    "cited-beyond-articles": dc.citations_beyond_articles,
    "builders": dc.infrastructure_builders,
    "tool-lifecycles": dc.tool_lifecycles,
    "licenses": dc.licenses,
    "funders": dc.funders,
    "usage-vs-citation": dc.usage_vs_citation,
    "shared-objects": dc.shared_objects,
}


@bp.route("/datacite")
@cache_response(seconds=_PAGE_CACHE)
def datacite_page():
    ctx = {name.replace("-", "_"): fn() for name, fn in _ANALYSES.items()}
    return render_template("datacite.html", precision=dc.precision_report(), **ctx)


@bp.route("/api/datacite/<name>")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_datacite(name):
    fn = _ANALYSES.get(name)
    if fn is None:
        abort(404)
    return jsonify(fn())


# ── curation (admin) ─────────────────────────────────────────────────────────

@bp.route("/api/admin/datacite/queue", methods=["GET"])
@require_admin_token
def admin_datacite_queue():
    limit = _safe_int(request.args.get("limit", 50), 50, lo=1, hi=200)
    offset = _safe_int(request.args.get("offset", 0), 0, lo=0, hi=1_000_000)
    return jsonify({"pending": dc.queue_size(), "items": dc.get_curation_queue(limit, offset)})


@bp.route("/api/admin/datacite/decide", methods=["POST"])
@require_admin_token
def admin_datacite_decide():
    body = request.get_json(silent=True) or {}
    actor = f"admin@{_client_ip()}"
    try:
        row = dc.decide_curation(body.get("doi"), (body.get("decision") or "").strip(),
                                 body.get("reason"), actor=actor)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if row is None:
        abort(404)
    log.info("DataCite %s %s by %s", row["doi"], row["decision"].upper(), actor)
    return jsonify({"status": "ok", "decision": row})


@bp.route("/admin/datacite", methods=["GET"])
def admin_datacite_page():
    """Review page shell; nothing loads until the admin pastes their token. No
    queue data is embedded in the HTML."""
    return render_template("admin_datacite.html")
