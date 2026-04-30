"""institutions Blueprint — institution detail page."""

import logging

from flask import Blueprint, render_template

from db import (
    get_institution_by_id, get_institution_article_count,
    get_institution_articles, get_institution_top_authors,
    get_new_article_count,
)
from journals import UNAVAILABLE_JOURNALS
from web_helpers import cache_response

log = logging.getLogger(__name__)

bp = Blueprint("institutions", __name__)


def _get_sidebar():
    from app import _get_sidebar as _impl
    return _impl()


@bp.route("/institution/<int:institution_id>")
@cache_response(seconds=3600)
def institution_detail(institution_id):
    """Institution detail page: metadata, article list, top authors."""
    institution = get_institution_by_id(institution_id)
    if not institution:
        return "Institution not found", 404

    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    new_count    = get_new_article_count(days=7)
    article_count = get_institution_article_count(institution_id)
    articles     = get_institution_articles(institution_id, limit=200)
    top_authors  = get_institution_top_authors(institution_id, limit=10)

    return render_template(
        "institution.html",
        institution=institution,
        articles=articles,
        article_count=article_count,
        top_authors=top_authors,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )

