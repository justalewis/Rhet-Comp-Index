"""
app.py — Flask web server for Rhet-Comp Index.

Routes:
  GET  /                        — main article index (HTML)
  GET  /api/articles            — JSON article feed
  POST /fetch                   — trigger incremental fetch (background thread)
  GET  /export                  — BibTeX / RIS export
  GET  /authors                 — alphabetical author list
  GET  /author/<name>           — articles by one author
  GET  /article/<id>            — article detail page
  GET  /explore                 — data visualisation page
  GET  /citations               — per-article ego network visualisation
  GET  /api/stats/timeline      — JSON: publication timeline by year+journal
  GET  /api/stats/tag-cooccurrence — JSON: tag co-occurrence matrix
  GET  /api/stats/author-network   — JSON: author co-authorship network
  GET  /api/stats/most-cited       — JSON: top articles by internal citation count
  GET  /api/citations/network      — JSON: force-graph nodes + edges for global citation network
  GET  /api/citations/ego          — JSON: 2-degree ego network around a specific article
  GET  /api/citations/cocitation    — JSON: co-citation network (undirected, weighted)
  GET  /api/citations/centrality   — JSON: citation network with eigenvector + betweenness centrality
  GET  /api/stats/citation-trends  — JSON: avg internal citations per article per year
  GET  /new                     — articles fetched in last 7 days
  GET  /books                   — monograph and edited-collection index
  GET  /book/<id>               — single book / edited collection detail with chapter list
  GET  /institution/<id>        — institution detail page
"""

import os
import re
import time
import threading
import logging
from functools import wraps
from urllib.parse import urlencode

from flask import Flask, render_template, request, jsonify, Response, redirect, make_response
from flask_compress import Compress

from db import (
    init_db, get_articles, get_article_counts, get_total_count,
    get_all_tags, get_year_range,
    get_article_by_id, get_related_articles,
    get_article_citations, get_article_all_references,
    get_timeline_data, get_tag_cooccurrence, get_author_network,
    get_new_articles, get_new_article_count,
    get_all_authors, get_author_articles,
    get_most_cited,
    get_citation_network,
    get_cocitation_network,
    get_bibcoupling_network,
    get_citation_centrality,
    get_sleeping_beauties,
    get_journal_citation_flow,
    get_journal_half_life,
    get_community_detection,
    get_main_path,
    get_temporal_network_evolution,
    get_citation_trends,
    get_ego_network,
    search_articles_autocomplete,
    get_reading_path,
    get_author_cocitation_network,
    get_author_cocitation_partners,
    get_coverage_stats,
    get_detailed_coverage,
    get_article_affiliations,
    get_author_by_name,
    get_all_authors_with_institutions,
    get_authors_by_letter,
    get_top_institutions,
    get_institution_timeline,
    get_top_institutions_v2,
    get_institution_timeline_v2,
    get_institution_by_id,
    get_institution_article_count,
    get_institution_articles,
    get_institution_top_authors,
    get_author_affiliations_per_article,
    get_author_institution_summary,
    get_author_books,
    get_author_timeline,
    get_author_coauthors,
    get_author_topics,
    # books
    get_books, get_book_count, get_book_by_id, get_book_chapters,
    get_book_publishers,
)
from journals import CROSSREF_JOURNALS, RSS_JOURNALS, SCRAPE_JOURNALS, MANUAL_JOURNALS, UNAVAILABLE_JOURNALS, JOURNAL_GROUPS
from auth import require_admin_token, admin_token_configured
from rate_limit import limiter, LIMITS, fetch_auth_failing

log = logging.getLogger(__name__)
app = Flask(__name__)
Compress(app)
limiter.init_app(app)


# ── Input-validation helpers ──────────────────────────────────────────────────

def _safe_int(val, default, lo=None, hi=None):
    """Convert *val* to int, returning *default* on failure.  Clamp to [lo, hi]."""
    try:
        n = int(val)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n


def _safe_float(val, default, lo=None, hi=None):
    """Convert *val* to float, returning *default* on failure.  Clamp to [lo, hi]."""
    try:
        n = float(val)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n

# ── Static asset versioning ────────────────────────────────────────────────────
import subprocess as _sp
try:
    APP_VERSION = _sp.check_output(
        ["git", "rev-parse", "--short", "HEAD"], stderr=_sp.DEVNULL
    ).decode().strip()
except Exception:
    APP_VERSION = "dev"


@app.context_processor
def inject_globals():
    """Make version string available to all templates for cache-busting."""
    return {"version": APP_VERSION}


# Initialise DB at import time so gunicorn workers find the schema on startup.
init_db()

# Surface a missing admin token at startup. Read endpoints continue to work;
# mutating endpoints will reject all requests with 503 until the secret is set.
if not admin_token_configured():
    log.critical(
        "PINAKES_ADMIN_TOKEN is not set; mutating endpoints will reject all requests"
    )

# Tag articles from known gold-OA journals (fast, no API calls).
from db import backfill_oa_status as _backfill_oa
_oa_result = _backfill_oa()
if _oa_result["tagged"] > 0:
    import logging as _log_mod
    _log_mod.getLogger(__name__).info(
        "OA backfill: tagged %d articles as gold OA", _oa_result["tagged"]
    )

# Pre-warm the SQLite page cache so the first HTTP request isn't slow.
# On Fly.io, the persistent volume is cold on startup; the first query that
# touches the 89 MB DB can take 10-15 s while the OS loads pages from disk.
# Running the most expensive main-page queries here (at import/preload time)
# fills the OS page cache before any user traffic arrives.
try:
    _t0 = time.time()
    get_articles(limit=50, offset=0)
    get_total_count()
    get_article_counts()
    get_all_tags()
    get_new_article_count()
    log.info("DB page cache warmed in %.2f s", time.time() - _t0)
except Exception as _e:
    log.warning("DB warmup failed (non-fatal): %s", _e)


# ── Error handlers ─────────────────────────────────────────────────────────────

@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500, message="Internal server error"), 500


@app.errorhandler(429)
def rate_limit_exceeded(e):
    """Friendly 429 for rate-limit hits. JSON for /api/* and explicit
    JSON Accept; HTML otherwise."""
    # Compute retry_after: prefer the limit's reset_at; fall back to 60s.
    retry_after = 60
    try:
        if getattr(e, "retry_after", None):
            retry_after = int(e.retry_after)
        elif getattr(e, "limit", None) is not None:
            reset = int(e.limit.reset_at - time.time())
            retry_after = max(1, reset)
    except Exception:
        pass

    log.debug(
        "rate limit hit: ip=%s path=%s description=%s",
        request.headers.get("Fly-Client-IP") or request.remote_addr,
        request.path, getattr(e, "description", ""),
    )

    wants_json = (
        request.path.startswith("/api/")
        or "application/json" in request.headers.get("Accept", "")
    )
    if wants_json:
        body = jsonify({"error": "rate limit exceeded", "retry_after": retry_after})
        body.headers["Retry-After"] = str(retry_after)
        return body, 429

    response = make_response(
        render_template(
            "error.html",
            code=429,
            message=f"Rate limit exceeded — please retry in {retry_after} seconds.",
        ),
        429,
    )
    response.headers["Retry-After"] = str(retry_after)
    return response


# ── HTTP security headers ─────────────────────────────────────────────────────

@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net gc.zgo.at; "
        "style-src 'self' 'unsafe-inline' fonts.googleapis.com; "
        "font-src fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )
    return response


# ── HTTP cache decorator ────────────────────────────────────────────────────────

def cache_response(seconds=300):
    """Add Cache-Control: public, max-age=N to a route's response."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            resp = make_response(f(*args, **kwargs))
            resp.headers["Cache-Control"] = f"public, max-age={seconds}"
            return resp
        return wrapped
    return decorator


# ── Sidebar cache ───────────────────────────────────────────────────────────────

_sidebar_cache = None
_sidebar_ts = 0.0
_SIDEBAR_TTL = 300  # seconds (5 minutes)


def _get_sidebar():
    """Return cached sidebar data, rebuilding at most every 5 minutes."""
    global _sidebar_cache, _sidebar_ts
    if _sidebar_cache is None or time.time() - _sidebar_ts > _SIDEBAR_TTL:
        _sidebar_cache = _build_sidebar()
        _sidebar_ts = time.time()
    return _sidebar_cache

@app.before_request
def redirect_www():
    """Redirect www.pinakes.xyz → pinakes.xyz (301 permanent)."""
    if request.host.startswith("www."):
        return redirect(request.url.replace("www.", "", 1), code=301)


# ── Template helpers ───────────────────────────────────────────────────────────

MONTHS = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


@app.template_filter("format_period")
def format_period(period):
    """Convert 'YYYY-MM' → 'Month YYYY', 'YYYY' → 'YYYY', else pass through."""
    if not period or period == "Undated":
        return "Undated"
    parts = period.split("-")
    if len(parts) >= 2:
        try:
            year, month = int(parts[0]), int(parts[1])
            return f"{MONTHS[month]} {year}"
        except (ValueError, IndexError):
            pass
    return period


@app.template_filter("display_date")
def display_date(pub_date):
    """Convert ISO date string to a short human-readable form."""
    if not pub_date:
        return ""
    parts = pub_date.split("-")
    try:
        if len(parts) >= 2:
            year, month = int(parts[0]), int(parts[1])
            return f"{MONTHS[month][:3]} {year}"
        return parts[0]
    except (ValueError, IndexError):
        return pub_date


# ── Sidebar data builder ───────────────────────────────────────────────────────

def _build_sidebar():
    counts_raw = get_article_counts()
    count_map = {r["journal"]: r["count"] for r in counts_raw}

    print_journals = [
        {
            "name":   j["name"],
            "source": "crossref",
            "count":  count_map.get(j["name"], 0),
        }
        for j in CROSSREF_JOURNALS
    ]

    web_journals = []
    for j in RSS_JOURNALS:
        web_journals.append({
            "name":   j["name"],
            "source": "rss",
            "count":  count_map.get(j["name"], 0),
        })
    for j in SCRAPE_JOURNALS:
        web_journals.append({
            "name":   j["name"],
            "source": "scrape",
            "count":  count_map.get(j["name"], 0),
        })
    for j in MANUAL_JOURNALS:
        web_journals.append({
            "name":   j["name"],
            "source": "manual",
            "count":  count_map.get(j["name"], 0),
        })

    all_journals = sorted(
        print_journals + web_journals,
        key=lambda x: x["name"].lower()
    )

    # Build grouped structure for sidebar navigation
    journal_map = {j["name"]: j for j in all_journals}
    assigned = set()
    journal_groups = []
    for group_label, names in JOURNAL_GROUPS:
        members = sorted([journal_map[n] for n in names if n in journal_map], key=lambda j: j["name"].lower())
        if members:
            journal_groups.append({"label": group_label, "journals": members})
            assigned.update(n for n in names if n in journal_map)
    # Catch any journals not assigned to a group
    ungrouped = [j for j in all_journals if j["name"] not in assigned]
    if ungrouped:
        journal_groups.append({"label": "Other", "journals": ungrouped})

    return print_journals, web_journals, all_journals, journal_groups


# ── Export helpers ─────────────────────────────────────────────────────────────

def _bibtex_key(article):
    """Generate a BibTeX key: firstauthorlastname + year + firsttitleword."""
    authors = article.get("authors") or ""
    first_author = authors.split(";")[0].strip() if authors else "unknown"
    last_word = re.sub(r"[^a-z0-9]", "", first_author.split()[-1].lower()) if first_author.split() else "unknown"

    year = ""
    pub_date = article.get("pub_date") or ""
    if pub_date:
        year = pub_date[:4]

    title = article.get("title") or ""
    first_title_word = re.sub(r"[^a-z0-9]", "", title.split()[0].lower()) if title.split() else "untitled"

    return f"{last_word}{year}{first_title_word}"


def _to_bibtex(articles):
    """Render a list of article dicts as a BibTeX string."""
    lines = []
    for a in articles:
        key = _bibtex_key(a)
        authors_raw = a.get("authors") or ""
        # Convert "First Last; First Last" → "First Last and First Last"
        bibtex_authors = " and ".join(
            p.strip() for p in authors_raw.split(";") if p.strip()
        ) if authors_raw else ""

        year = (a.get("pub_date") or "")[:4]
        title = (a.get("title") or "").replace("{", "{{").replace("}", "}}")
        journal = a.get("journal") or ""
        doi = a.get("doi") or ""
        url = a.get("url") or ""

        lines.append(f"@article{{{key},")
        if bibtex_authors:
            lines.append(f"  author  = {{{bibtex_authors}}},")
        lines.append(f"  title   = {{{title}}},")
        lines.append(f"  journal = {{{journal}}},")
        if year:
            lines.append(f"  year    = {{{year}}},")
        if doi:
            lines.append(f"  doi     = {{{doi}}},")
        if url:
            lines.append(f"  url     = {{{url}}},")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def _to_ris(articles):
    """Render a list of article dicts as a RIS string."""
    lines = []
    for a in articles:
        lines.append("TY  - JOUR")
        authors_raw = a.get("authors") or ""
        for author in (p.strip() for p in authors_raw.split(";") if p.strip()):
            lines.append(f"AU  - {author}")
        title = a.get("title") or ""
        lines.append(f"TI  - {title}")
        journal = a.get("journal") or ""
        lines.append(f"JO  - {journal}")
        year = (a.get("pub_date") or "")[:4]
        if year:
            lines.append(f"PY  - {year}")
        doi = a.get("doi") or ""
        if doi:
            lines.append(f"DO  - {doi}")
        url = a.get("url") or ""
        if url:
            lines.append(f"UR  - {url}")
        lines.append("ER  - ")
        lines.append("")
    return "\n".join(lines)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
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


@app.route("/api/articles")
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


def _run_background_fetch():
    """Background-thread target for /fetch. Module-level so tests can patch
    it directly rather than mocking threading.Thread (which would also
    intercept Flask-Limiter's internal Timer use)."""
    try:
        # Tag any untagged articles from known gold-OA journals
        from db import backfill_oa_status
        backfill_oa_status()

        from fetcher     import fetch_all as crossref_fetch
        from rss_fetcher import fetch_all as rss_fetch
        from scraper     import fetch_all as scrape_fetch
        crossref_fetch(incremental=True)
        rss_fetch()
        scrape_fetch()
    except Exception as e:
        log.error("Background fetch error: %s", e)


@app.route("/fetch", methods=["POST"])
@limiter.limit(LIMITS["fetch"], exempt_when=fetch_auth_failing)
@require_admin_token
def trigger_fetch():
    """Kick off an incremental fetch of all sources in a background thread.
    Requires `Authorization: Bearer <PINAKES_ADMIN_TOKEN>`."""
    t = threading.Thread(target=_run_background_fetch, daemon=True)
    t.start()
    return jsonify({"status": "fetch started"})


@app.route("/export")
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


@app.route("/authors")
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


@app.route("/author/<path:name>")
def author_detail(name):
    """All articles by a specific author."""
    print_journals, web_journals, all_journals, journal_groups = _get_sidebar()
    new_count = get_new_article_count(days=7)
    articles = get_author_articles(name)
    author_record = get_author_by_name(name)
    affiliations_by_article = get_author_affiliations_per_article(name)
    institution_summary = get_author_institution_summary(name)
    author_books = get_author_books(name)

    return render_template(
        "author.html",
        author_name=name,
        articles=articles,
        author_books=author_books,
        author_record=author_record,
        affiliations_by_article=affiliations_by_article,
        institution_summary=institution_summary,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        journal_groups=journal_groups,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )


@app.route("/api/author/<path:name>/timeline")
def author_timeline_api(name):
    """Publication timeline for a single author: articles by year+journal plus books."""
    return jsonify(get_author_timeline(name))


@app.route("/api/author/<path:name>/coauthors")
def author_coauthors_api(name):
    """Co-authorship mini-network for a single author."""
    return jsonify(get_author_coauthors(name))


@app.route("/api/author/<path:name>/topics")
def author_topics_api(name):
    """Topic tag distribution for a single author."""
    return jsonify(get_author_topics(name))


@app.route("/article/<int:article_id>")
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


@app.route("/explore")
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


@app.route("/tools")
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


@app.route("/citations")
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


@app.route("/api/citations/ego")
@limiter.limit(LIMITS["citations"])
def api_ego_network():
    """JSON: 2-degree ego network around a specific article."""
    article_id = request.args.get("article", type=int)
    if not article_id:
        return jsonify({"error": "article parameter required"}), 400
    return jsonify(get_ego_network(article_id))


@app.route("/api/stats/timeline")
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


@app.route("/api/stats/tag-cooccurrence")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=3600)
def api_tag_cooccurrence():
    """JSON: tag co-occurrence matrix."""
    return jsonify(get_tag_cooccurrence())


@app.route("/api/stats/author-network")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=600)
def api_author_network():
    """JSON: author co-authorship network nodes and links.
    Accepts: ?min_papers=3&top_n=150
    """
    min_papers = _safe_int(request.args.get("min_papers", 3), 3, lo=2, hi=25)
    top_n      = _safe_int(request.args.get("top_n",      150), 150, lo=25, hi=350)
    return jsonify(get_author_network(min_papers=min_papers, top_n=top_n))


@app.route("/api/stats/citation-trends")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=3600)
def api_citation_trends():
    """JSON: avg internal citations per article per year, filtered by optional journal."""
    journal = request.args.get("journal", "").strip()
    return jsonify(get_citation_trends(journal=journal or None))


@app.route("/api/citations/network")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_citations_network():
    """JSON: force-graph nodes and directed edges for the citation network."""
    min_citations = _safe_int(request.args.get("min_citations", 5), 5, lo=1)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_citation_network(
        min_citations=min_citations,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
    )
    return jsonify(data)


@app.route("/api/citations/cocitation")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_cocitation_network():
    """JSON: co-citation network — undirected weighted graph of articles co-cited together."""
    min_cocitations = _safe_int(request.args.get("min_cocitations", 3), 3, lo=1)
    max_nodes       = _safe_int(request.args.get("max_nodes", 400), 400, lo=50, hi=600)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_cocitation_network(
        min_cocitations=min_cocitations,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
        max_nodes=max_nodes,
    )
    return jsonify(data)


@app.route("/api/citations/bibcoupling")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_bibcoupling_network():
    """JSON: bibliographic coupling network — articles linked by shared references."""
    min_coupling = _safe_int(request.args.get("min_coupling", 3), 3, lo=1)
    max_nodes    = _safe_int(request.args.get("max_nodes", 400), 400, lo=50, hi=600)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_bibcoupling_network(
        min_coupling=min_coupling,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
        max_nodes=max_nodes,
    )
    return jsonify(data)


@app.route("/api/citations/centrality")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_citation_centrality():
    """JSON: citation network with eigenvector and betweenness centrality scores."""
    min_citations = _safe_int(request.args.get("min_citations", 2), 2, lo=1)
    max_nodes     = _safe_int(request.args.get("max_nodes", 600), 600, lo=50, hi=800)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_citation_centrality(
        min_citations=min_citations,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
        max_nodes=max_nodes,
    )
    return jsonify(data)


@app.route("/api/citations/sleeping-beauties")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_sleeping_beauties():
    """JSON: articles with delayed citation recognition (Sleeping Beauties)."""
    min_citations = _safe_int(request.args.get("min_citations", 5), 5, lo=3)
    max_results   = _safe_int(request.args.get("max_results", 50), 50, lo=10, hi=100)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_sleeping_beauties(
        min_total_citations=min_citations,
        max_results=max_results,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
    )
    return jsonify(data)


@app.route("/api/citations/journal-flow")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_journal_citation_flow():
    """JSON: journal-to-journal citation flow matrix for chord diagram."""
    min_citations = _safe_int(request.args.get("min_citations", 1), 1, lo=1)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_journal_citation_flow(
        min_citations=min_citations,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
    )
    return jsonify(data)


@app.route("/api/citations/half-life")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_citation_half_life():
    """JSON: citing and cited half-life per journal."""
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()
    include_distribution = request.args.get("distribution", "").lower() in ("1", "true")
    include_timeseries   = request.args.get("timeseries",   "").lower() in ("1", "true")

    data = get_journal_half_life(
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
        include_distribution=include_distribution,
        include_timeseries=include_timeseries,
    )
    return jsonify(data)


@app.route("/api/citations/communities")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_citation_communities():
    """JSON: community detection via Louvain modularity optimization."""
    min_citations = _safe_int(request.args.get("min_citations", 2), 2, lo=1)
    max_nodes     = _safe_int(request.args.get("max_nodes", 600), 600, lo=50, hi=800)
    resolution    = _safe_float(request.args.get("resolution", 1.0), 1.0, lo=0.1, hi=3.0)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_community_detection(
        min_citations=min_citations,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
        max_nodes=max_nodes,
        resolution=resolution,
    )
    return jsonify(data)


@app.route("/api/citations/main-path")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_main_path():
    """JSON: main path analysis — the backbone of knowledge flow."""
    min_citations = _safe_int(request.args.get("min_citations", 2), 2, lo=1)
    max_nodes     = _safe_int(request.args.get("max_nodes", 800), 800, lo=50, hi=1000)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_main_path(
        min_citations=min_citations,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
        max_nodes=max_nodes,
    )
    return jsonify(data)


@app.route("/api/citations/temporal-evolution")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_temporal_evolution():
    """JSON: temporal network evolution — structural metrics over time windows."""
    min_citations = _safe_int(request.args.get("min_citations", 1), 1, lo=1)
    max_nodes     = _safe_int(request.args.get("max_nodes", 500), 500, lo=50, hi=800)
    window_size   = _safe_int(request.args.get("window_size", 1), 1, lo=1, hi=10)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()
    snapshot_year = request.args.get("snapshot_year", "").strip()

    data = get_temporal_network_evolution(
        min_citations=min_citations,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
        window_size=window_size,
        max_nodes_per_window=max_nodes,
        snapshot_year=int(snapshot_year) if snapshot_year else None,
    )
    return jsonify(data)


@app.route("/api/articles/search")
@limiter.limit(LIMITS["search"])
def api_article_search():
    """JSON: autocomplete article search for reading path seed selection."""
    q = request.args.get("q", "").strip()
    limit = _safe_int(request.args.get("limit", 10), 10, lo=1, hi=20)
    if not q:
        return jsonify([])
    return jsonify(search_articles_autocomplete(q, limit=limit))


@app.route("/api/citations/reading-path")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_reading_path():
    """JSON: reading path around a seed article — cites, cited-by, co-citation, bib coupling."""
    article_id = request.args.get("article", type=int)
    if not article_id:
        return jsonify({"error": "article parameter required"}), 400
    result = get_reading_path(article_id)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/author-cocitation")
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


@app.route("/api/author/<path:name>/cocitation-partners")
def api_author_cocitation_partners(name):
    """JSON: top co-citation partners for a specific author."""
    limit = _safe_int(request.args.get("limit", 10), 10, lo=1, hi=20)
    return jsonify(get_author_cocitation_partners(name, limit=limit))


@app.route("/api/stats/most-cited")
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


@app.route("/api/stats/institutions")
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


@app.route("/new")
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


@app.route("/health")
@limiter.exempt
def health():
    """Lightweight health check — no DB queries, returns immediately.
    Reports admin-token configuration so deployment status is observable."""
    return jsonify({
        "status": "ok",
        "admin_auth": "configured" if admin_token_configured() else "missing",
    }), 200


@app.route("/about")
def about():
    """About page — project background, values, developer info."""
    return render_template("about.html")


COVERAGE_SINCE_PRESETS = (None, 2000, 2010, 2020)


@app.route("/coverage")
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


@app.route("/most-cited")
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


@app.route("/books")
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


@app.route("/book/<int:book_id>")
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


@app.route("/institution/<int:institution_id>")
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


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug)
