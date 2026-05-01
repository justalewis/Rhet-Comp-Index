"""datastories Blueprint — chapter-organized analytical tools that originally
shipped as standalone CLI scripts producing book-figure JSON. Each tool is now
a JSON API endpoint plus an interactive panel on /datastories.

Routes:
    GET  /datastories                          HTML page (accordion by chapter)
    GET  /api/datastories/<tool-slug>          JSON for each tool

Every API route accepts the universal filter set:
    cluster=<slug>           (one of the 7 sidebar clusters)
    journal=<name>           (repeatable; overrides cluster)
    year_from=<YYYY>         (inclusive)
    year_to=<YYYY>           (inclusive)

Tool-specific knobs are documented per-route below.
"""

from __future__ import annotations

import logging

from flask import Blueprint, render_template, jsonify, request, redirect, make_response

from db import get_year_range, get_new_article_count, get_coverage_stats
from db import datastories as ds
from journal_groups import get_clusters
from journals import UNAVAILABLE_JOURNALS
from rate_limit import limiter, LIMITS
from web_helpers import _safe_int, cache_response
from auth_datastories import (
    is_authenticated, verify_password, password_configured,
    issue_session_cookie, clear_session_cookie,
    require_datastories_auth, require_datastories_auth_api,
    COOKIE_TTL_SECONDS,
)

log = logging.getLogger(__name__)

bp = Blueprint("datastories", __name__)


def _get_sidebar():
    from app import _get_sidebar as _impl
    return _impl()


# ── Universal filter parsing ───────────────────────────────────────────────

def _parse_filters(req):
    """Pull (cluster, journals, year_from, year_to) from a request, normalising
    empty strings to None. Returns a dict ready to splat into a ds_* call."""
    cluster = (req.args.get("cluster") or "").strip() or None
    journals = req.args.getlist("journal") or None
    year_from = (req.args.get("year_from") or "").strip() or None
    year_to   = (req.args.get("year_to")   or "").strip() or None

    # Validate years (silently ignore garbage rather than 400; the tool will
    # just compute the unfiltered slice on that axis)
    def _safe_year(v):
        if not v: return None
        try:
            n = int(v)
            return n if 1900 <= n <= 2100 else None
        except (TypeError, ValueError):
            return None

    return {
        "cluster":   cluster,
        "journals":  journals,
        "year_from": _safe_year(year_from),
        "year_to":   _safe_year(year_to),
    }


# ── HTML pages ─────────────────────────────────────────────────────────────
#
# /datastories         public landing page; doubles as the login screen.
# /datastories/tools   the actual tool surface, password-gated.
# /datastories/login   POST password, set cookie, redirect to /tools.
# /datastories/logout  POST clears cookie.

_TTL_DAYS = COOKIE_TTL_SECONDS // (24 * 60 * 60)


@bp.route("/datastories")
def datastories_landing():
    """Public landing page describing the project. Always reachable."""
    return render_template(
        "datastories_landing.html",
        active_nav="datastories",
        authed=is_authenticated(),
        ttl_days=_TTL_DAYS,
        login_error=None,
    )


@bp.route("/datastories/login", methods=["POST"])
@limiter.limit("12 per minute")
def datastories_login():
    """Validate the submitted password. On success, set the auth cookie
    and redirect to /datastories/tools. On failure, re-render the landing
    page with an inline error. Rate-limited tighter than the default to
    discourage brute-force."""
    if not password_configured():
        return render_template(
            "datastories_landing.html",
            active_nav="datastories",
            authed=False,
            ttl_days=_TTL_DAYS,
            login_error=(
                "This server has not been configured for Datastories access "
                "(PINAKES_DATASTORIES_PASSWORD is unset). Contact the operator."
            ),
        ), 503

    submitted = (request.form.get("password") or "").strip()
    if not verify_password(submitted):
        log.warning(
            "Datastories login failed: ip=%s",
            request.headers.get("Fly-Client-IP") or request.remote_addr or "unknown",
        )
        return render_template(
            "datastories_landing.html",
            active_nav="datastories",
            authed=False,
            ttl_days=_TTL_DAYS,
            login_error="That password didn't match. If you should have access, contact Justin.",
        ), 401

    response = make_response(redirect("/datastories/tools"))
    issue_session_cookie(response)
    return response


@bp.route("/datastories/logout", methods=["POST"])
def datastories_logout():
    response = make_response(redirect("/datastories"))
    clear_session_cookie(response)
    return response


@bp.route("/datastories/tools")
@require_datastories_auth
def datastories_page():
    """The full tool surface — accordion of chapters and panels. Auth-gated."""
    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    new_count = get_new_article_count(days=7)
    min_year, max_year = get_year_range()
    clusters, cluster_order = get_clusters()
    cluster_options = [
        {"slug": slug, "label": clusters[slug]["label"]} for slug in cluster_order
    ]

    return render_template(
        "datastories.html",
        active_nav="datastories",
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
        min_year=min_year,
        max_year=max_year,
        cluster_options=cluster_options,
    )


# ── Chapter 3 ──────────────────────────────────────────────────────────────

@bp.route("/api/datastories/ch3-braided-path")
@require_datastories_auth_api
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_ch3_braided_path():
    return jsonify(ds.ds_braided_path(**_parse_filters(request)))


@bp.route("/api/datastories/ch3-branching-traditions")
@require_datastories_auth_api
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_ch3_branching_traditions():
    f = _parse_filters(request)
    return jsonify(ds.ds_branching_traditions(year_from=f["year_from"], year_to=f["year_to"]))


@bp.route("/api/datastories/ch3-origins-frontiers")
@require_datastories_auth_api
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_ch3_origins_frontiers():
    return jsonify(ds.ds_origins_frontiers(**_parse_filters(request)))


# ── Chapter 4 ──────────────────────────────────────────────────────────────

@bp.route("/api/datastories/ch4-shifting-currents")
@require_datastories_auth_api
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_ch4_shifting_currents():
    return jsonify(ds.ds_shifting_currents(**_parse_filters(request)))


@bp.route("/api/datastories/ch4-speed-of-influence")
@require_datastories_auth_api
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_ch4_speed_of_influence():
    return jsonify(ds.ds_speed_of_influence(**_parse_filters(request)))


@bp.route("/api/datastories/ch4-border-crossers")
@require_datastories_auth_api
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_ch4_border_crossers():
    return jsonify(ds.ds_border_crossers(**_parse_filters(request)))


@bp.route("/api/datastories/ch4-two-way-street")
@require_datastories_auth_api
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_ch4_two_way_street():
    return jsonify(ds.ds_two_way_street(**_parse_filters(request)))


# ── Chapter 5 ──────────────────────────────────────────────────────────────

@bp.route("/api/datastories/ch5-shape-of-influence")
@require_datastories_auth_api
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_ch5_shape_of_influence():
    f = _parse_filters(request)
    # Backward-compat singular `journal=` query is folded into journals filter
    legacy_j = (request.args.get("journal") or "").strip() or None
    if legacy_j and not f["journals"]:
        f["journals"] = [legacy_j]
    return jsonify(ds.ds_shape_of_influence(**f))


@bp.route("/api/datastories/ch5-long-tail")
@require_datastories_auth_api
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_ch5_long_tail():
    top_n = _safe_int(request.args.get("top_n", 50), 50, lo=10, hi=200)
    return jsonify(ds.ds_long_tail(top_n=top_n, **_parse_filters(request)))


@bp.route("/api/datastories/ch5-fair-ranking")
@require_datastories_auth_api
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_ch5_fair_ranking():
    exclude_recent = _safe_int(request.args.get("exclude_recent", 2), 2, lo=0, hi=10)
    top_n = _safe_int(request.args.get("top_n", 50), 50, lo=10, hi=200)
    return jsonify(ds.ds_fair_ranking(
        exclude_recent_years=exclude_recent, top_n=top_n,
        **_parse_filters(request),
    ))


@bp.route("/api/datastories/ch5-shifting-canons")
@require_datastories_auth_api
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_ch5_shifting_canons():
    top_n = _safe_int(request.args.get("top_n", 25), 25, lo=10, hi=50)
    return jsonify(ds.ds_shifting_canons(top_n=top_n, **_parse_filters(request)))


@bp.route("/api/datastories/ch5-reach-of-citation")
@require_datastories_auth_api
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_ch5_reach_of_citation():
    top_n = _safe_int(request.args.get("top_n", 100), 100, lo=20, hi=200)
    return jsonify(ds.ds_reach_of_citation(top_n=top_n, **_parse_filters(request)))


@bp.route("/api/datastories/ch5-inside-outside")
@require_datastories_auth_api
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_ch5_inside_outside():
    return jsonify(ds.ds_inside_outside(**_parse_filters(request)))


# ── Chapter 6 ──────────────────────────────────────────────────────────────

@bp.route("/api/datastories/ch6-communities-time")
@require_datastories_auth_api
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_ch6_communities_time():
    return jsonify(ds.ds_communities_time(**_parse_filters(request)))


@bp.route("/api/datastories/ch6-walls-bridges")
@require_datastories_auth_api
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_ch6_walls_bridges():
    return jsonify(ds.ds_walls_bridges(**_parse_filters(request)))


@bp.route("/api/datastories/ch6-first-spark")
@require_datastories_auth_api
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_ch6_first_spark():
    return jsonify(ds.ds_first_spark(**_parse_filters(request)))


# ── Chapter 7 ──────────────────────────────────────────────────────────────

@bp.route("/api/datastories/ch7-shared-foundations")
@require_datastories_auth_api
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_ch7_shared_foundations():
    min_coupling = _safe_int(request.args.get("min_coupling", 3), 3, lo=2, hi=20)
    return jsonify(ds.ds_shared_foundations(min_coupling=min_coupling, **_parse_filters(request)))


@bp.route("/api/datastories/ch7-two-maps")
@require_datastories_auth_api
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_ch7_two_maps():
    return jsonify(ds.ds_two_maps(**_parse_filters(request)))


@bp.route("/api/datastories/ch7-books-everyone-reads")
@require_datastories_auth_api
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_ch7_books_everyone_reads():
    return jsonify(ds.ds_books_everyone_reads(**_parse_filters(request)))


@bp.route("/api/datastories/ch7-uneven-debts")
@require_datastories_auth_api
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_ch7_uneven_debts():
    return jsonify(ds.ds_uneven_debts(**_parse_filters(request)))


# ── Chapter 8 ──────────────────────────────────────────────────────────────

@bp.route("/api/datastories/ch8-solo-to-squad")
@require_datastories_auth_api
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_ch8_solo_to_squad():
    return jsonify(ds.ds_solo_to_squad(**_parse_filters(request)))


@bp.route("/api/datastories/ch8-academic-lineages")
@require_datastories_auth_api
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_ch8_academic_lineages():
    min_gap = _safe_int(request.args.get("min_gap", 10), 10, lo=5, hi=25)
    return jsonify(ds.ds_academic_lineages(min_gap=min_gap, **_parse_filters(request)))


@bp.route("/api/datastories/ch8-lasting-partnerships")
@require_datastories_auth_api
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_ch8_lasting_partnerships():
    return jsonify(ds.ds_lasting_partnerships(**_parse_filters(request)))


# ── Chapter 9 ──────────────────────────────────────────────────────────────

@bp.route("/api/datastories/ch9-prince-network")
@require_datastories_auth_api
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_ch9_prince_network():
    return jsonify(ds.ds_prince_network(**_parse_filters(request)))


@bp.route("/api/datastories/ch9-disciplinary-calendar")
@require_datastories_auth_api
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_ch9_disciplinary_calendar():
    return jsonify(ds.ds_disciplinary_calendar(**_parse_filters(request)))


@bp.route("/api/datastories/ch9-unread-canon")
@require_datastories_auth_api
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_ch9_unread_canon():
    return jsonify(ds.ds_unread_canon(**_parse_filters(request)))
