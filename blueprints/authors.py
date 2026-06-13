"""authors Blueprint — author index and author-centric API endpoints."""

import logging

from flask import Blueprint, request, render_template, jsonify, abort

from db import (
    get_authors_by_letter, get_all_authors_with_institutions,
    get_author_by_name, get_author_articles,
    get_author_affiliations_per_article, get_author_books,
    get_author_institution_summary,
    get_author_timeline, get_author_coauthors, get_author_topics,
    get_author_cocitation_network, get_author_cocitation_partners,
    get_author_citing_venues,
    get_new_article_count,
)
from journals import UNAVAILABLE_JOURNALS
from rate_limit import limiter, LIMITS
from web_helpers import _safe_int, cache_response

log = logging.getLogger(__name__)

bp = Blueprint("authors", __name__)


def _get_sidebar():
    from app import _get_sidebar as _impl
    return _impl()


@bp.route("/authors")
def authors_list():
    """Browse all authors by last-name initial."""
    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    new_count = get_new_article_count(days=7)

    selected_letter = request.args.get("letter", "").upper()
    if selected_letter and len(selected_letter) == 1 and selected_letter.isalpha():
        letter_authors = get_authors_by_letter(selected_letter)
    else:
        selected_letter = ""
        letter_authors = []

    all_letters = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

    return render_template(
        "authors.html",
        selected_letter=selected_letter,
        letter_authors=letter_authors,
        all_letters=all_letters,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )


@bp.route("/author/<path:name>")
def author_detail(name):
    """All articles by a specific author.

    Redaction handling: a redacted author's page is served at their *token*
    URL (e.g. /author/Redacted%20Author%207f3a2c) and renders under the
    "Name Redacted by Author Request" tag. A request for the author's old
    *real-name* URL is 404'd rather than redirected — a redirect would echo
    the suppressed name in the Location header and the logs."""
    from redaction import apply_suppression, is_redaction_token

    is_redacted = is_redaction_token(name)
    # Old real-name URL of a now-redacted author → 404 (don't leak the name).
    if not is_redacted and apply_suppression(name) != name:
        abort(404)

    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    new_count = get_new_article_count(days=7)
    articles = get_author_articles(name)
    author_record = get_author_by_name(name)
    affiliations_by_article = get_author_affiliations_per_article(name)
    institution_summary = get_author_institution_summary(name)
    author_books = get_author_books(name)
    citing_venues = get_author_citing_venues(name, top_journals=10)

    return render_template(
        "author.html",
        author_name=name,
        is_redacted=is_redacted,
        articles=articles,
        author_books=author_books,
        author_record=author_record,
        affiliations_by_article=affiliations_by_article,
        institution_summary=institution_summary,
        citing_venues=citing_venues,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )


@bp.route("/api/author/<path:name>/timeline")
def author_timeline_api(name):
    """Publication timeline for a single author: articles by year+journal plus books."""
    return jsonify(get_author_timeline(name))


@bp.route("/api/author/<path:name>/coauthors")
def author_coauthors_api(name):
    """Co-authorship mini-network for a single author."""
    return jsonify(get_author_coauthors(name))


@bp.route("/api/author/<path:name>/topics")
def author_topics_api(name):
    """Topic tag distribution for a single author."""
    return jsonify(get_author_topics(name))


@bp.route("/api/author-cocitation")
@cache_response(seconds=600)
def api_author_cocitation():
    """JSON: author co-citation network — which scholars the field cites together."""
    min_cocitations = _safe_int(request.args.get("min_cocitations", 3), 3, lo=1)
    max_authors     = _safe_int(request.args.get("max_authors", 200), 200, lo=25, hi=500)
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()
    journals  = request.args.getlist("journal")

    data = get_author_cocitation_network(
        min_cocitations=min_cocitations,
        max_authors=max_authors,
        year_from=year_from or None,
        year_to=year_to or None,
        journals=journals or None,
    )
    return jsonify(data)


@bp.route("/api/author/<path:name>/cocitation-partners")
def api_author_cocitation_partners(name):
    """JSON: top co-citation partners for a specific author."""
    limit = _safe_int(request.args.get("limit", 10), 10, lo=1, hi=20)
    return jsonify(get_author_cocitation_partners(name, limit=limit))

