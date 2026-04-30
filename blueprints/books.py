"""books Blueprint — books index and book detail page."""

import logging

from flask import Blueprint, request, render_template

from db import (
    get_books, get_book_count, get_book_publishers,
    get_book_by_id, get_book_chapters,
    get_new_article_count,
)
from journals import UNAVAILABLE_JOURNALS
from web_helpers import _safe_int, cache_response

log = logging.getLogger(__name__)

bp = Blueprint("books", __name__)


def _get_sidebar():
    from app import _get_sidebar as _impl
    return _impl()


@bp.route("/books")
@cache_response(seconds=600)
def books():
    """Monograph and edited-collection index with publisher/type/year filters."""
    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    new_count   = get_new_article_count(days=7)
    publishers  = get_book_publishers()

    # Filter params
    pub       = request.args.get("publisher", "").strip()
    book_type = request.args.get("type",      "").strip()
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()
    q         = request.args.get("q",         "").strip()
    page      = _safe_int(request.args.get("page", 1), 1, lo=1)
    per_page  = 48

    total = get_book_count(
        publisher=pub or None,
        book_type=book_type or None,
        year_from=year_from or None,
        year_to=year_to or None,
        q=q or None,
    )
    book_list = get_books(
        publisher=pub or None,
        book_type=book_type or None,
        year_from=year_from or None,
        year_to=year_to or None,
        q=q or None,
        limit=per_page,
        offset=(page - 1) * per_page,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "books.html",
        book_list=book_list,
        publishers=publishers,
        total=total,
        page=page,
        total_pages=total_pages,
        sel_publisher=pub,
        sel_type=book_type,
        year_from=year_from,
        year_to=year_to,
        q=q,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )


@bp.route("/book/<int:book_id>")
@cache_response(seconds=600)
def book_detail(book_id):
    """Single book detail page: metadata + chapter list for edited collections."""
    book = get_book_by_id(book_id)
    if not book:
        return "Book not found", 404

    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    new_count = get_new_article_count(days=7)
    chapters  = []
    if book.get("book_type") == "edited-collection":
        chapters = get_book_chapters(book_id)

    return render_template(
        "book.html",
        book=book,
        chapters=chapters,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )

