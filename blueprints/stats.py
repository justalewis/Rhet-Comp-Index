"""stats Blueprint — corpus statistics API + /most-cited HTML page."""

import logging

from flask import Blueprint, request, render_template, jsonify

from db import (
    get_timeline_data, get_tag_cooccurrence, get_author_network,
    get_most_cited, get_top_institutions_v2, get_institution_timeline_v2,
    get_all_tags, get_year_range, get_coverage_stats,
    get_new_article_count,
)
from journals import UNAVAILABLE_JOURNALS
from rate_limit import limiter, LIMITS
from web_helpers import _safe_int, cache_response

log = logging.getLogger(__name__)

bp = Blueprint("stats", __name__)


def _get_sidebar():
    from app import _get_sidebar as _impl
    return _impl()


@bp.route("/api/stats/timeline")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=3600)
def api_timeline():
    """JSON: article counts per year per journal, 1990–present."""
    raw = get_timeline_data()

    # Collect all years and journals
    years_set = set()
    journal_year_count: dict[str, dict[str, int]] = {}
    for row in raw:
        y, j, c = row["year"], row["journal"], row["count"]
        years_set.add(y)
        if j not in journal_year_count:
            journal_year_count[j] = {}
        journal_year_count[j][y] = c

    years = sorted(years_set)
    series = []
    for journal, year_map in sorted(journal_year_count.items()):
        series.append({
            "journal": journal,
            "counts": [year_map.get(y, 0) for y in years],
        })

    return jsonify({"years": years, "series": series})


@bp.route("/api/stats/tag-cooccurrence")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=3600)
def api_tag_cooccurrence():
    """JSON: tag co-occurrence matrix."""
    return jsonify(get_tag_cooccurrence())


@bp.route("/api/stats/author-network")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_author_network():
    """JSON: author co-authorship network nodes and links.
    Accepts: ?min_papers=3&top_n=150
    """
    min_papers = _safe_int(request.args.get("min_papers", 3), 3, lo=2, hi=25)
    top_n      = _safe_int(request.args.get("top_n",      150), 150, lo=25, hi=500)
    return jsonify(get_author_network(min_papers=min_papers, top_n=top_n))


@bp.route("/api/stats/most-cited")
@limiter.limit(LIMITS["stats"])
def api_most_cited():
    """JSON: top articles by internal citation count, with optional filters."""
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()
    journal   = request.args.get("journal",   "").strip()
    tag       = request.args.get("tag",       "").strip()
    limit     = _safe_int(request.args.get("limit", 50), 50, lo=1, hi=100)

    results = get_most_cited(
        year_from=year_from or None,
        year_to=year_to or None,
        journal=journal or None,
        tag=tag or None,
        limit=limit,
    )
    return jsonify(results)


@bp.route("/api/stats/institutions")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=3600)
def api_institutions():
    """JSON: top institutions by article count + top-10 timeline."""
    top = get_top_institutions_v2(limit=25)
    institutions = [
        {
            "id":      r.get("id"),
            "name":    r.get("display_name") or r.get("name", ""),
            "count":   r.get("article_count") or r.get("count", 0),
            "country": r.get("country_code"),
            "type":    r.get("type"),
        }
        for r in top
    ]
    timeline = get_institution_timeline_v2(top_n=10)
    return jsonify({"institutions": institutions, "top10_timeline": timeline})


@bp.route("/most-cited")
@cache_response(seconds=1800)
def most_cited_page():
    """Most-cited articles page with filter controls and grouped views."""
    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    new_count = get_new_article_count(days=7)
    all_tags  = get_all_tags()
    min_year, max_year = get_year_range()
    coverage  = get_coverage_stats()

    # Filter params
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()
    journal   = request.args.get("journal",   "").strip()
    tag       = request.args.get("tag",       "").strip()
    view      = request.args.get("view",      "all").strip()

    articles = get_most_cited(
        year_from=year_from or None,
        year_to=year_to or None,
        journal=journal or None,
        tag=tag or None,
        limit=200,
    )

    # For grouped views, sort by group then citation count within group
    if view == "decade":
        def _decade_sort_key(a):
            try:
                decade = int(a["pub_date"][:4]) // 10 * 10
            except (TypeError, ValueError, KeyError):
                decade = 0
            return (-decade, -(a.get("internal_cited_by_count") or 0))
        articles = sorted(articles, key=_decade_sort_key)
    elif view == "journal":
        articles = sorted(articles,
                          key=lambda a: (a.get("journal") or "",
                                         -(a.get("internal_cited_by_count") or 0)))
    elif view == "topic":
        def _topic_sort_key(a):
            tags = a.get("tags") or ""
            first = tags.strip("|").split("|")[0] if tags else "\xff"
            return (first, -(a.get("internal_cited_by_count") or 0))
        articles = sorted(articles, key=_topic_sort_key)

    return render_template(
        "most-cited.html",
        articles=articles,
        view=view,
        year_from=year_from,
        year_to=year_to,
        sel_journal=journal,
        sel_tag=tag,
        all_tags=all_tags,
        min_year=min_year,
        max_year=max_year,
        coverage=coverage,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )

