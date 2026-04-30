"""articles Blueprint — article API + article detail page."""

import logging

from flask import Blueprint, request, render_template, jsonify

from db import (
    get_articles, get_total_count,
    get_article_by_id, get_related_articles,
    get_article_citations, get_article_all_references,
    get_new_article_count,
    get_article_affiliations,
    get_coverage_stats,
    search_articles_autocomplete,
)
from journals import UNAVAILABLE_JOURNALS
from rate_limit import limiter, LIMITS
from web_helpers import _safe_int

log = logging.getLogger(__name__)

bp = Blueprint("articles", __name__)


def _get_sidebar():
    from app import _get_sidebar as _impl
    return _impl()


@bp.route("/api/articles")
def api_articles():
    journal   = request.args.get("journal",   "")
    source    = request.args.get("source",    "")
    q         = request.args.get("q",         "")
    year_from = request.args.get("year_from", "")
    year_to   = request.args.get("year_to",   "")
    tag       = request.args.get("tag",       "")
    limit     = _safe_int(request.args.get("limit", 50), 50, lo=1, hi=200)
    offset    = _safe_int(request.args.get("offset", 0), 0, lo=0)

    articles = get_articles(
        journal=journal or None,
        source=source or None,
        q=q or None,
        year_from=year_from or None,
        year_to=year_to or None,
        tag=tag or None,
        limit=limit,
        offset=offset,
    )
    total = get_total_count(
        journal=journal or None,
        source=source or None,
        q=q or None,
        year_from=year_from or None,
        year_to=year_to or None,
        tag=tag or None,
    )
    return jsonify({"articles": articles, "total": total})


@bp.route("/article/<int:article_id>")
def article_detail(article_id):
    """Full detail page for a single article."""
    article = get_article_by_id(article_id)
    if article is None:
        return "Article not found", 404

    related             = get_related_articles(article_id, limit=5)
    cited_by            = get_article_citations(article_id)
    all_refs            = get_article_all_references(article_id)
    cites               = [r for r in all_refs if r["in_index"]]
    outside_refs        = [r for r in all_refs if not r["in_index"]]
    outside_count       = len(outside_refs)
    author_affiliations = get_article_affiliations(article_id)
    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    new_count = get_new_article_count(days=7)
    coverage_stats = get_coverage_stats()
    journal_coverage = next(
        (c for c in coverage_stats if c["journal"] == article.get("journal")), None
    )

    return render_template(
        "article.html",
        article=article,
        related=related,
        cited_by=cited_by,
        cites=cites,
        outside_refs=outside_refs,
        outside_count=outside_count,
        author_affiliations=author_affiliations,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
        journal_coverage=journal_coverage,
    )


@bp.route("/api/articles/search")
@limiter.limit(LIMITS["search"])
def api_article_search():
    """JSON: autocomplete article search for reading path seed selection."""
    q = request.args.get("q", "").strip()
    limit = _safe_int(request.args.get("limit", 10), 10, lo=1, hi=20)
    if not q:
        return jsonify([])
    return jsonify(search_articles_autocomplete(q, limit=limit))

