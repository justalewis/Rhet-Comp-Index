"""wac Blueprint — the WAC Clearinghouse publisher dashboard.

A public, intentionally-unlinked page at /wac that profiles the WAC Clearinghouse
AS A PRESS (everything under DOI prefix 10.37514): cross-format author portfolios,
editor brokerage, institutional feeders, the cited canon, the book program, and
field texture. Reads the denormalized wac_works / wac_authors tables (db.wac).

No citation-network analysis — WAC deposits almost no outbound references. Every
view here is built from authors, editors, affiliations, dates, types, venues,
parent-book linkage, and inbound citation counts, with the data's limits stated
in the UI.

Not registered in the nav; reachable only by URL.
"""

from __future__ import annotations

import logging

from flask import Blueprint, render_template, jsonify, request, abort

from db import wac
from rate_limit import limiter, LIMITS
from web_helpers import _safe_int, cache_response

log = logging.getLogger(__name__)

bp = Blueprint("wac", __name__)

_PAGE_CACHE = 1800
_API_CACHE = 3600


# ── page ─────────────────────────────────────────────────────────────────────

@bp.route("/wac")
@cache_response(seconds=_PAGE_CACHE)
def wac_page():
    """The publisher dashboard. Server-renders the hero KPIs; everything else
    lazy-loads from /api/wac/* as the reader scrolls."""
    return render_template("wac.html", overview=wac.wac_overview())


# ── JSON API ─────────────────────────────────────────────────────────────────
# All endpoints are cached (the underlying tables are static between re-ingests)
# and rate-limited. None are auth-gated — the page is public-but-unlinked.

def _j(payload):
    return jsonify(payload)


@bp.route("/api/wac/overview")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_overview():
    return _j(wac.wac_overview())


@bp.route("/api/wac/timeline")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_timeline():
    return _j(wac.wac_timeline())


@bp.route("/api/wac/format-composition")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_format_composition():
    return _j(wac.wac_format_composition())


@bp.route("/api/wac/journals")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_journals():
    return _j(wac.wac_journals())


@bp.route("/api/wac/journal-lifelines")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_journal_lifelines():
    return _j(wac.wac_journal_lifelines())


@bp.route("/api/wac/most-cited")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_most_cited():
    limit = _safe_int(request.args.get("limit", 40), 40, lo=5, hi=100)
    wtype = (request.args.get("type") or "").strip() or None
    return _j(wac.wac_most_cited(limit=limit, work_type=wtype))


@bp.route("/api/wac/house-authors")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_house_authors():
    limit = _safe_int(request.args.get("limit", 30), 30, lo=10, hi=80)
    return _j(wac.wac_house_authors(limit=limit))


@bp.route("/api/wac/cross-format-authors")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_cross_format_authors():
    limit = _safe_int(request.args.get("limit", 40), 40, lo=10, hi=120)
    min_types = _safe_int(request.args.get("min_types", 2), 2, lo=2, hi=4)
    return _j(wac.wac_cross_format_authors(limit=limit, min_types=min_types))


@bp.route("/api/wac/book-journal-crossover")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_book_journal_crossover():
    return _j(wac.wac_book_journal_crossover())


@bp.route("/api/wac/author-spans")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_author_spans():
    min_works = _safe_int(request.args.get("min_works", 5), 5, lo=3, hi=20)
    return _j(wac.wac_author_spans(min_works=min_works))


@bp.route("/api/wac/coauthorship")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=_API_CACHE)
def api_coauthorship():
    min_works = _safe_int(request.args.get("min_works", 2), 2, lo=2, hi=10)
    top_n = _safe_int(request.args.get("top_n", 160), 160, lo=40, hi=400)
    return _j(wac.wac_coauthorship(min_works=min_works, top_n=top_n))


@bp.route("/api/wac/lasting-partnerships")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_lasting_partnerships():
    return _j(wac.wac_lasting_partnerships())


@bp.route("/api/wac/editor-network")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=_API_CACHE)
def api_editor_network():
    return _j(wac.wac_editor_network())


@bp.route("/api/wac/editor-brokers")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_editor_brokers():
    limit = _safe_int(request.args.get("limit", 40), 40, lo=10, hi=80)
    return _j(wac.wac_editor_brokers(limit=limit))


@bp.route("/api/wac/editor-author-overlap")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_editor_author_overlap():
    return _j(wac.wac_editor_author_overlap())


@bp.route("/api/wac/copresence")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=_API_CACHE)
def api_copresence():
    min_shared = _safe_int(request.args.get("min_shared", 1), 1, lo=1, hi=5)
    top_n = _safe_int(request.args.get("top_n", 180), 180, lo=40, hi=400)
    return _j(wac.wac_copresence(min_shared=min_shared, top_n=top_n))


@bp.route("/api/wac/collections")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_collections():
    return _j(wac.wac_collections())


@bp.route("/api/wac/collection/<path:doi>")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_collection(doi):
    data = wac.wac_collection_chapters(doi)
    if data is None:
        abort(404)
    return _j(data)


@bp.route("/api/wac/collection-anatomy")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_collection_anatomy():
    return _j(wac.wac_collection_anatomy())


@bp.route("/api/wac/institutions")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_institutions():
    limit = _safe_int(request.args.get("limit", 30), 30, lo=10, hi=60)
    return _j(wac.wac_institutions(limit=limit))


@bp.route("/api/wac/institution-journal")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_institution_journal():
    return _j(wac.wac_institution_journal())


@bp.route("/api/wac/affiliation-coverage")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_affiliation_coverage():
    return _j(wac.wac_affiliation_coverage())


@bp.route("/api/wac/citation-lorenz")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_citation_lorenz():
    return _j(wac.wac_citation_lorenz())


@bp.route("/api/wac/citations-vs-age")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_citations_vs_age():
    return _j(wac.wac_citations_vs_age())


@bp.route("/api/wac/coauthorship-trend")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_coauthorship_trend():
    return _j(wac.wac_coauthorship_trend())


@bp.route("/api/wac/team-size")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_team_size():
    return _j(wac.wac_team_size())


@bp.route("/api/wac/title-terms")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_title_terms():
    return _j(wac.wac_title_terms())


@bp.route("/api/wac/title-term-series")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_title_term_series():
    raw = (request.args.get("terms") or "").strip()
    terms = [t for t in (s.strip() for s in raw.split(",")) if t] or None
    return _j(wac.wac_title_term_series(terms=terms))


@bp.route("/api/wac/topics")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_topics():
    return _j(wac.wac_topics())


@bp.route("/api/wac/topic-trends")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_topic_trends():
    return _j(wac.wac_topic_trends())


@bp.route("/api/wac/spanish-spotlight")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=_API_CACHE)
def api_spanish_spotlight():
    return _j(wac.wac_spanish_spotlight())
