"""feeds Blueprint — Atom feeds for new arrivals.

    GET /feed.xml            all journals, 50 most recent arrivals
    GET /feed/<slug>.xml     one journal
    GET /feeds               HTML directory of every feed

Why the URL set is bounded
--------------------------
These feeds take no filter parameters, even though the index's filter grammar
(journal/tag/q/year) would make arbitrary saved-search feeds easy to build.
Feed readers poll on their own schedule, ignore robots.txt, and never stop —
so an unbounded feed URL space is an unbounded cache-miss space pointed at a
single-worker box. That is the shape of the `/explore?seed=N` traffic that
exhausted the worker in the 2026-06-20 incident. 55 journals plus the firehose
is 56 URLs, all edge-cacheable, all warm.

Per-search feeds, if they are ever wanted, belong behind the same email-alert
signup as the digest — a verified human at the other end, not an open URL
space.

Conditional GET
---------------
Every response carries an ETag and Last-Modified, and honours If-None-Match /
If-Modified-Since with a 304. A reader polling hourly then costs one indexed
query and one hash instead of ~30KB on the wire. This is not premature
optimization: polling is what feed clients *do*, and it is the only meaningful
load this feature adds.
"""

import hashlib
import logging
from email.utils import format_datetime

from flask import Blueprint, Response, request, render_template, abort, url_for

from db import get_feed_articles, get_new_article_count
from feeds import render_atom, feed_updated, FEED_MIMETYPE
from journals import (
    SLUG_TO_JOURNAL, JOURNAL_TO_SLUG, JOURNAL_GROUPS,
    ALL_JOURNAL_NAMES, UNAVAILABLE_JOURNALS,
)

log = logging.getLogger(__name__)

bp = Blueprint("feeds", __name__)

# Entries per feed. Enough that a reader polling weekly on a busy journal
# misses nothing; small enough that the document stays a single-digit number
# of kilobytes gzipped.
FEED_LIMIT = 50

# Edge and browser cache lifetime. The daily fetch runs once at 03:23 UTC, so
# a 30-minute window costs a subscriber nothing in freshness and absorbs
# essentially all reader polling at Cloudflare.
FEED_MAX_AGE = 1800

SITE_URL = "https://pinakes.xyz"


def _get_sidebar():
    from app import _get_sidebar as _impl
    return _impl()


def _feed_response(articles, *, title, self_path, subtitle=None):
    """Render, then answer conditionally.

    ETag is computed over the rendered body rather than over the query inputs,
    so it is correct by construction: two responses share an ETag exactly when
    they are byte-identical.
    """
    self_url = SITE_URL + self_path
    body = render_atom(
        articles,
        title=title,
        subtitle=subtitle,
        self_url=self_url,
        site_url=SITE_URL,
    )
    etag = '"%s"' % hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]
    last_modified = feed_updated(articles)

    def _headers(resp):
        resp.headers["ETag"] = etag
        resp.headers["Last-Modified"] = format_datetime(last_modified, usegmt=True)
        resp.headers["Cache-Control"] = (
            f"public, max-age={FEED_MAX_AGE}, s-maxage={FEED_MAX_AGE}"
        )
        return resp

    # If-None-Match wins over If-Modified-Since per RFC 7232 §6.
    inm = request.headers.get("If-None-Match", "")
    if inm and etag in [t.strip() for t in inm.split(",")]:
        return _headers(Response(status=304))

    ims = request.headers.get("If-Modified-Since")
    if ims:
        try:
            from email.utils import parsedate_to_datetime
            if last_modified.replace(microsecond=0) <= parsedate_to_datetime(ims):
                return _headers(Response(status=304))
        except (TypeError, ValueError):
            pass  # Malformed header — just serve the body.

    return _headers(Response(body, mimetype=FEED_MIMETYPE))


@bp.route("/feed.xml")
def feed_all():
    """New arrivals across every journal in the index."""
    articles = get_feed_articles(limit=FEED_LIMIT)
    return _feed_response(
        articles,
        title="Pinakes — new in rhetoric and composition",
        subtitle="Newly indexed articles across every journal Pinakes tracks.",
        self_path="/feed.xml",
    )


@bp.route("/feed/<slug>.xml")
def feed_journal(slug):
    """New arrivals for one journal."""
    journal = SLUG_TO_JOURNAL.get(slug)
    if not journal:
        abort(404)
    articles = get_feed_articles(journal=journal, limit=FEED_LIMIT)
    return _feed_response(
        articles,
        title=f"Pinakes — {journal}",
        subtitle=f"Newly indexed articles from {journal}.",
        self_path=f"/feed/{slug}.xml",
    )


@bp.route("/feed/<slug>")
def feed_landing(slug):
    """Human-facing page for one journal's feed.

    This exists because a feed URL is not a page. Clicking one in Chrome shows
    a wall of raw XML: Chrome 148 no longer applies an xml-stylesheet
    processing instruction to render XML documents (the scripted XSLTProcessor
    API survives, the declarative path does not), so static/feed.xsl only helps
    in Firefox and Safari.

    Rather than depend on a browser feature that is being removed, nothing in
    the interface links straight to the .xml any more. The journal name goes
    here, this page hands over the address, and the raw feed stays exactly as
    it was for the software that actually consumes it.
    """
    journal = SLUG_TO_JOURNAL.get(slug)
    if not journal:
        abort(404)
    articles = get_feed_articles(journal=journal, limit=10)
    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    return render_template(
        "feed_landing.html",
        journal=journal,
        slug=slug,
        feed_url=f"{SITE_URL}/feed/{slug}.xml",
        articles=articles,
        feed_limit=FEED_LIMIT,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=get_new_article_count(days=7),
        active_nav="feeds",
    )


@bp.route("/feeds")
def feeds_index():
    """Human-facing directory of every feed, grouped like the sidebar."""
    grouped_in_nav = {n for _, names in JOURNAL_GROUPS for n in names}
    groups = [
        (label, [(n, JOURNAL_TO_SLUG[n]) for n in names if n in JOURNAL_TO_SLUG])
        for label, names in JOURNAL_GROUPS
    ]
    ungrouped = sorted(
        (n, JOURNAL_TO_SLUG[n])
        for n in ALL_JOURNAL_NAMES
        if n not in grouped_in_nav
    )

    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    return render_template(
        "feeds.html",
        groups=groups,
        ungrouped=ungrouped,
        feed_limit=FEED_LIMIT,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=get_new_article_count(days=7),
        active_nav="feeds",
    )
