"""feeds Blueprint — Atom feeds for new arrivals.

    GET /feed.xml                    all journals
    GET /feed/<slug>.xml             one journal
    GET /feed/group/<gslug>.xml      one section, merged
    GET /feed/select/<code>.xml      a reader's own selection, merged
    GET /feed/<...>.opml             the same set as separate subscriptions
    GET /feed/<...>                  landing page for any of the above
    GET /feeds                       directory + selection builder

Why the URL set stays effectively bounded
-----------------------------------------
Feed readers poll on their own schedule, ignore robots.txt, and never stop, so
an unbounded feed URL space is an unbounded cache-miss space aimed at a
single-worker box. That is the shape of the `/explore?seed=N` traffic that
exhausted the worker in the 2026-06-20 incident.

The all-index, per-journal, and per-section feeds are 63 fixed URLs. Custom
selections are open-ended in principle, but the encoding is deterministic —
the same journals always produce the same code, in sorted order — so the set
of URLs in circulation is the set of selections people actually made, not the
combinatorial space of selections they could make. Two readers who pick the
same six journals share one cache entry. Nothing is stored server-side.

Landing pages
-------------
Nothing in the interface links straight to a .xml. Chrome 148 dropped
declarative XSLT, so a browser shows raw markup for a feed URL and tells the
person nothing about what to do with it; every feed therefore has an HTML page
that hands over the address instead.
"""

import hashlib
import logging
import os
from urllib.parse import quote
from email.utils import format_datetime, parsedate_to_datetime

from flask import Blueprint, Response, request, render_template, abort, redirect, url_for

from db import get_feed_articles, get_new_article_count
from feeds import render_atom, render_opml, feed_updated, FEED_MIMETYPE
from journals import (
    SLUG_TO_JOURNAL, JOURNAL_TO_SLUG, JOURNAL_GROUPS,
    ALL_JOURNAL_NAMES, UNAVAILABLE_JOURNALS,
    GROUP_SLUG_TO_LABEL, GROUP_SLUG_TO_JOURNALS,
    JOURNAL_TO_CODE, CODE_TO_JOURNAL, JOURNAL_CODE_LEN,
)

log = logging.getLogger(__name__)

bp = Blueprint("feeds", __name__)

# Entries per feed. Enough that a reader polling weekly on a busy journal
# misses nothing; small enough that the document stays a few KB gzipped.
FEED_LIMIT = 50

# Edge and browser cache lifetime. The daily fetch runs once at 03:23 UTC, so
# a 30-minute window costs a subscriber nothing in freshness and absorbs
# essentially all reader polling at Cloudflare.
FEED_MAX_AGE = 1800

SITE_URL = os.environ.get("PINAKES_SITE_URL", "https://pinakes.wacclearinghouse.org")

OPML_MIMETYPE = "text/x-opml"

# Upper bound on a custom selection, so a hand-edited URL cannot ask for an
# arbitrarily long IN (...) clause. Selecting every journal is still allowed.
MAX_SELECTION = len(ALL_JOURNAL_NAMES)


def _get_sidebar():
    from app import _get_sidebar as _impl
    return _impl()


# ── Selection codes ─────────────────────────────────────────────────────────

def encode_selection(names):
    """Journal names → the code used in a custom feed URL.

    Sorted, so the code depends on which journals were picked and not on the
    order the boxes happened to be ticked. static/feed-select.js builds the
    same string in the browser from the codes embedded in the page, which is
    why the URL can appear instantly without asking the server.
    """
    return "".join(sorted(JOURNAL_TO_CODE[n] for n in names if n in JOURNAL_TO_CODE))


def decode_selection(code):
    """Custom feed code → journal names, or None if the code is malformed.

    Returns None rather than silently dropping unknown chunks: a selection that
    quietly loses a journal would give somebody a feed missing the title they
    subscribed for, with nothing to indicate it.
    """
    code = (code or "").strip().lower()
    if not code or len(code) % JOURNAL_CODE_LEN:
        return None
    chunks = [code[i:i + JOURNAL_CODE_LEN]
              for i in range(0, len(code), JOURNAL_CODE_LEN)]
    if len(chunks) > MAX_SELECTION or len(set(chunks)) != len(chunks):
        return None
    names = []
    for chunk in chunks:
        name = CODE_TO_JOURNAL.get(chunk)
        if not name:
            return None
        names.append(name)
    return sorted(names)


# ── Response helpers ────────────────────────────────────────────────────────

def _conditional(body, mimetype, last_modified):
    """Serve `body`, or a 304 when the client already has this exact bytes.

    ETag is computed over the rendered body rather than over the query inputs,
    so two responses share an ETag exactly when they are byte-identical.
    """
    etag = '"%s"' % hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]

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
            if last_modified.replace(microsecond=0) <= parsedate_to_datetime(ims):
                return _headers(Response(status=304))
        except (TypeError, ValueError):
            pass  # Malformed header — just serve the body.

    return _headers(Response(body, mimetype=mimetype))


def _feed_response(journals, *, title, self_path, subtitle=None):
    """Render an Atom feed for one journal, a list of them, or all of them."""
    articles = get_feed_articles(journal=journals, limit=FEED_LIMIT)
    body = render_atom(
        articles,
        title=title,
        subtitle=subtitle,
        self_url=SITE_URL + self_path,
        site_url=SITE_URL,
    )
    return _conditional(body, FEED_MIMETYPE, feed_updated(articles))


def _opml_response(names, *, title, filename):
    """Render an OPML subscription list for a set of journals."""
    entries = [
        (n,
         f"{SITE_URL}/feed/{JOURNAL_TO_SLUG[n]}.xml",
         f"{SITE_URL}/feed/{JOURNAL_TO_SLUG[n]}")
        for n in names
    ]
    body = render_opml(entries, title=title)
    resp = Response(body, mimetype=OPML_MIMETYPE)
    # OPML is imported from disk, so it is one of the few things here that
    # genuinely wants to be a download rather than a page.
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    resp.headers["Cache-Control"] = f"public, max-age={FEED_MAX_AGE}"
    return resp


def _landing(**ctx):
    """Render the shared feed landing page with the usual sidebar context."""
    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    return render_template(
        "feed_landing.html",
        feed_limit=FEED_LIMIT,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=get_new_article_count(days=7),
        active_nav="feeds",
        **ctx,
    )


# ── All journals ────────────────────────────────────────────────────────────

@bp.route("/feed.xml")
def feed_all():
    """New arrivals across every journal in the index."""
    return _feed_response(
        None,
        title="Pinakes — new in rhetoric and composition",
        subtitle="Newly indexed articles across every journal Pinakes tracks.",
        self_path="/feed.xml",
    )


@bp.route("/feed.opml")
def feed_all_opml():
    """Every journal as a separate subscription."""
    return _opml_response(
        sorted(ALL_JOURNAL_NAMES),
        title="Pinakes — all journals",
        filename="pinakes-all-journals.opml",
    )


# ── One journal ─────────────────────────────────────────────────────────────

@bp.route("/feed/<slug>.xml")
def feed_journal(slug):
    journal = SLUG_TO_JOURNAL.get(slug)
    if not journal:
        abort(404)
    return _feed_response(
        journal,
        title=f"Pinakes — {journal}",
        subtitle=f"Newly indexed articles from {journal}.",
        self_path=f"/feed/{slug}.xml",
    )


@bp.route("/feed/<slug>")
def feed_landing(slug):
    """Human-facing page for one journal's feed."""
    journal = SLUG_TO_JOURNAL.get(slug)
    if not journal:
        abort(404)
    return _landing(
        heading=journal,
        kicker="Journal feed",
        description=(
            f"A feed reader watches this address and shows you new articles "
            f"from {journal} as Pinakes indexes them."
        ),
        feed_url=f"{SITE_URL}/feed/{slug}.xml",
        raw_path=f"/feed/{slug}.xml",
        opml_path=None,
        browse_url=f"/?journal={quote(journal)}",
        browse_label=f"Browse {journal} on Pinakes",
        empty_subject=journal,
        articles=get_feed_articles(journal=journal, limit=10),
    )


# ── One section ─────────────────────────────────────────────────────────────

@bp.route("/feed/group/<gslug>.xml")
def feed_group(gslug):
    """Every journal in one section of the sidebar, merged into one feed."""
    label = GROUP_SLUG_TO_LABEL.get(gslug)
    if not label:
        abort(404)
    names = GROUP_SLUG_TO_JOURNALS[gslug]
    return _feed_response(
        names,
        title=f"Pinakes — {label}",
        subtitle=(f"Newly indexed articles across the {len(names)} "
                  f"{label} journals Pinakes tracks."),
        self_path=f"/feed/group/{gslug}.xml",
    )


@bp.route("/feed/group/<gslug>.opml")
def feed_group_opml(gslug):
    label = GROUP_SLUG_TO_LABEL.get(gslug)
    if not label:
        abort(404)
    return _opml_response(
        GROUP_SLUG_TO_JOURNALS[gslug],
        title=f"Pinakes — {label}",
        filename=f"pinakes-{gslug}.opml",
    )


@bp.route("/feed/group/<gslug>")
def feed_group_landing(gslug):
    label = GROUP_SLUG_TO_LABEL.get(gslug)
    if not label:
        abort(404)
    names = GROUP_SLUG_TO_JOURNALS[gslug]
    return _landing(
        heading=label,
        kicker="Section feed",
        description=(
            f"One feed carrying new articles from all {len(names)} "
            f"{label} journals in the index."
        ),
        feed_url=f"{SITE_URL}/feed/group/{gslug}.xml",
        raw_path=f"/feed/group/{gslug}.xml",
        opml_path=f"/feed/group/{gslug}.opml",
        included=[(n, JOURNAL_TO_SLUG[n]) for n in names],
        included_label="Journals in this feed",
        browse_url="/feeds",
        browse_label="All feeds",
        empty_subject=label,
        articles=get_feed_articles(journal=names, limit=10),
    )


# ── A reader's own selection ────────────────────────────────────────────────

@bp.route("/feed/select/<code>.xml")
def feed_selection(code):
    names = decode_selection(code)
    if not names:
        abort(404)
    return _feed_response(
        names,
        title=f"Pinakes — {len(names)} selected journals",
        subtitle="Newly indexed articles from a chosen set of journals.",
        self_path=f"/feed/select/{code}.xml",
    )


@bp.route("/feed/select/<code>.opml")
def feed_selection_opml(code):
    names = decode_selection(code)
    if not names:
        abort(404)
    return _opml_response(
        names,
        title=f"Pinakes — {len(names)} selected journals",
        filename="pinakes-selected-journals.opml",
    )


@bp.route("/feed/select/<code>")
def feed_selection_landing(code):
    names = decode_selection(code)
    if not names:
        abort(404)
    return _landing(
        heading=f"{len(names)} selected journals",
        kicker="Custom feed",
        description=(
            "One feed carrying new articles from the journals listed below. "
            "The address depends only on which journals are in it, so this "
            "same link will always mean this same set. Bookmark it or pass it "
            "to a colleague."
        ),
        feed_url=f"{SITE_URL}/feed/select/{code}.xml",
        raw_path=f"/feed/select/{code}.xml",
        opml_path=f"/feed/select/{code}.opml",
        included=[(n, JOURNAL_TO_SLUG[n]) for n in names],
        included_label="Journals in this feed",
        browse_url="/feeds",
        browse_label="Change this selection",
        empty_subject="these journals",
        articles=get_feed_articles(journal=names, limit=10),
    )


# ── Selection form target ───────────────────────────────────────────────────

@bp.route("/feeds/select")
def build_selection():
    """Target of the "build your own" form on /feeds.

    static/feed-select.js builds the same URL in the browser and never submits,
    so this runs only when JavaScript is off or broken. Keeping it means the
    selection form is a working HTML form rather than a pile of checkboxes that
    do nothing — which is also what makes it usable from a screen reader that
    is driving the page rather than the script.
    """
    codes = request.args.getlist("j")
    names = [CODE_TO_JOURNAL[c] for c in codes if c in CODE_TO_JOURNAL]
    if not names:
        return redirect(url_for("feeds.feeds_index"))
    return redirect(f"/feed/select/{encode_selection(names)}")


# ── Directory ───────────────────────────────────────────────────────────────

@bp.route("/feeds")
def feeds_index():
    """Directory of every feed, plus the build-your-own selection form."""
    grouped_in_nav = {n for _, names in JOURNAL_GROUPS for n in names}
    groups = [
        {
            "label": label,
            "slug": gslug,
            "journals": [
                (n, JOURNAL_TO_SLUG[n], JOURNAL_TO_CODE[n])
                for n in GROUP_SLUG_TO_JOURNALS[gslug]
            ],
        }
        for gslug, label in GROUP_SLUG_TO_LABEL.items()
    ]
    ungrouped = sorted(
        (n, JOURNAL_TO_SLUG[n], JOURNAL_TO_CODE[n])
        for n in ALL_JOURNAL_NAMES
        if n not in grouped_in_nav
    )

    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    return render_template(
        "feeds.html",
        groups=groups,
        ungrouped=ungrouped,
        feed_limit=FEED_LIMIT,
        site_url=SITE_URL,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=get_new_article_count(days=7),
        active_nav="feeds",
    )
