"""main Blueprint — top-level HTML pages: index, /export, /tools, /explore,
/citations (ego-network HTML), /new, /about, /coverage."""

import logging
from urllib.parse import urlencode

from flask import Blueprint, request, render_template, jsonify, Response, redirect, make_response

from db import (
    get_articles, get_article_counts, get_total_count,
    get_all_tags, get_year_range,
    get_article_by_id, get_related_articles,
    get_article_citations, get_article_all_references,
    get_new_articles, get_new_article_count,
    get_detailed_coverage, get_coverage_stats,
)
from journals import UNAVAILABLE_JOURNALS
from web_helpers import _safe_int, _to_bibtex, _to_ris

log = logging.getLogger(__name__)

bp = Blueprint("main", __name__)

# Module-level constant validated by the /coverage view's `since` param.
COVERAGE_SINCE_PRESETS = (None, 2000, 2010, 2020)


def _get_sidebar():
    """Lazy proxy to app._get_sidebar so its sidebar cache stays a single
    process-wide source of truth (and so conftest can invalidate it)."""
    from app import _get_sidebar as _impl
    return _impl()


@bp.route("/")
def index():
    # Read all filter params
    journals  = request.args.getlist("journal")
    source    = request.args.get("source",    "").strip()
    q         = request.args.get("q",         "").strip()
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()
    tag       = request.args.get("tag",       "").strip()
    page      = _safe_int(request.args.get("page", 1), 1, lo=1)
    per_page  = 50

    # Build a query-string fragment preserving all active filters except 'page'.
    filter_params = {}
    if journals:  filter_params["journal"]   = journals
    if source:    filter_params["source"]    = source
    if q:         filter_params["q"]         = q
    if year_from: filter_params["year_from"] = year_from
    if year_to:   filter_params["year_to"]   = year_to
    if tag:       filter_params["tag"]       = tag
    filter_qs = urlencode(filter_params, doseq=True)

    offset  = (page - 1) * per_page
    articles = get_articles(
        journal=journals or None,
        source=source or None,
        q=q or None,
        year_from=year_from or None,
        year_to=year_to or None,
        tag=tag or None,
        limit=per_page,
        offset=offset,
    )
    total = get_total_count(
        journal=journals or None,
        source=source or None,
        q=q or None,
        year_from=year_from or None,
        year_to=year_to or None,
        tag=tag or None,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Group articles by year-month period
    grouped = []
    current_period = None
    current_group  = []
    for a in articles:
        raw    = a.get("pub_date") or ""
        period = raw[:7] if len(raw) >= 7 else (raw or "Undated")
        if period != current_period:
            if current_group:
                grouped.append((current_period, current_group))
            current_period = period
            current_group  = [a]
        else:
            current_group.append(a)
    if current_group:
        grouped.append((current_period, current_group))

    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    all_tags = get_all_tags(journal=journals[0] if len(journals)==1 else None, source=source or None)
    min_year, max_year = get_year_range()
    new_count = get_new_article_count(days=7)

    return render_template(
        "index.html",
        grouped=grouped,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        active_journals=journals,
        selected_source=source,
        active_q=q,
        active_year_from=year_from,
        active_year_to=year_to,
        active_tag=tag,
        all_tags=all_tags,
        min_year=min_year,
        max_year=max_year,
        filter_qs=filter_qs,
        page=page,
        total=total,
        total_pages=total_pages,
        per_page=per_page,
        new_count=new_count,
    )


@bp.route("/export")
def export():
    """Export all matching articles as BibTeX or RIS."""
    article_id = request.args.get("article_id", "").strip()
    journal   = request.args.get("journal",   "").strip()
    source    = request.args.get("source",    "").strip()
    q         = request.args.get("q",         "").strip()
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()
    tag       = request.args.get("tag",       "").strip()
    fmt       = request.args.get("format", "bibtex").strip().lower()

    # Single-article export
    if article_id:
        article = get_article_by_id(int(article_id))
        articles = [article] if article else []
    else:
        articles = get_articles(
            journal=journal or None,
            source=source or None,
            q=q or None,
            year_from=year_from or None,
            year_to=year_to or None,
            tag=tag or None,
            limit=5000,
            offset=0,
        )

    if fmt == "ris":
        content = _to_ris(articles)
        mimetype = "application/x-research-info-systems"
        filename = "rhet-comp-export.ris"
    else:
        content = _to_bibtex(articles)
        mimetype = "application/x-bibtex"
        filename = "rhet-comp-export.bib"

    return Response(
        content,
        mimetype=mimetype,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@bp.route("/explore")
def explore():
    """Data exploration page: timeline, tag co-occurrence, author network, citations."""
    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    new_count = get_new_article_count(days=7)
    all_tags = get_all_tags()
    min_year, max_year = get_year_range()
    coverage = get_coverage_stats()

    return render_template(
        "explore.html",
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
        all_tags=all_tags,
        min_year=min_year,
        max_year=max_year,
        coverage=coverage,
    )


@bp.route("/tools")
def tools():
    """Browse all tools page — flat grid of every analytical tool."""
    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    new_count = get_new_article_count(days=7)

    return render_template(
        "tools.html",
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )


@bp.route("/citations")
def citation_network_page():
    """Per-article ego-network visualisation page."""
    article_id = request.args.get("article", type=int)
    if not article_id:
        return redirect("/explore?tab=citnet")

    article = get_article_by_id(article_id)
    if article is None:
        return "Article not found", 404

    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    new_count = get_new_article_count(days=7)

    return render_template(
        "citations.html",
        article=article,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )


@bp.route("/new")
def new_articles():
    """Articles fetched within the last 7 days."""
    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    new_count = get_new_article_count(days=7)
    articles = get_new_articles(days=7)

    # Group by journal for display on this page
    new_by_journal: dict[str, list] = {}
    for a in articles:
        j = a.get("journal") or "Unknown"
        if j not in new_by_journal:
            new_by_journal[j] = []
        new_by_journal[j].append(a)

    return render_template(
        "new.html",
        new_by_journal=new_by_journal,
        total=len(articles),
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )


@bp.route("/about")
def about():
    """About page — project background, values, developer info."""
    return render_template("about.html")


@bp.route("/coverage")
def coverage():
    """Index coverage page — what's fully indexed, what's partial, what's missing.
    Accepts an optional `?since=YYYY` filter that scopes the per-journal
    table to articles published in [YYYY, ∞)."""
    raw = request.args.get("since", "").strip()
    try:
        since = int(raw) if raw else None
    except ValueError:
        since = None
    if since not in COVERAGE_SINCE_PRESETS:
        since = None
    detailed = get_detailed_coverage(year_min=since)
    return render_template(
        "coverage.html",
        detailed=detailed,
        since=since,
        since_presets=COVERAGE_SINCE_PRESETS,
    )

