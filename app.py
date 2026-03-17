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
  GET  /api/stats/citation-trends  — JSON: avg internal citations per article per year
  GET  /new                     — articles fetched in last 7 days
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
    get_citation_trends,
    get_ego_network,
    get_coverage_stats,
)
from journals import CROSSREF_JOURNALS, RSS_JOURNALS, SCRAPE_JOURNALS, UNAVAILABLE_JOURNALS

log = logging.getLogger(__name__)
app = Flask(__name__)
Compress(app)

# Initialise DB at import time so gunicorn workers find the schema on startup.
init_db()


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

    all_journals = sorted(
        print_journals + web_journals,
        key=lambda x: x["name"].lower()
    )
    return print_journals, web_journals, all_journals


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
    page      = max(1, int(request.args.get("page", 1)))
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

    print_journals, web_journals, all_journals = _get_sidebar()
    all_tags = get_all_tags(journal=journals[0] if len(journals)==1 else None, source=source or None)
    min_year, max_year = get_year_range()
    new_count = get_new_article_count(days=7)

    return render_template(
        "index.html",
        grouped=grouped,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
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
    limit     = min(200, int(request.args.get("limit", 50)))
    offset    = int(request.args.get("offset", 0))

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


@app.route("/fetch", methods=["POST"])
def trigger_fetch():
    """Kick off an incremental fetch of all sources in a background thread."""
    def _run():
        try:
            from fetcher     import fetch_all as crossref_fetch
            from rss_fetcher import fetch_all as rss_fetch
            from scraper     import fetch_all as scrape_fetch
            crossref_fetch(incremental=True)
            rss_fetch()
            scrape_fetch()
        except Exception as e:
            log.error("Background fetch error: %s", e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({"status": "fetch started"})


@app.route("/export")
def export():
    """Export all matching articles as BibTeX or RIS."""
    journal   = request.args.get("journal",   "").strip()
    source    = request.args.get("source",    "").strip()
    q         = request.args.get("q",         "").strip()
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()
    tag       = request.args.get("tag",       "").strip()
    fmt       = request.args.get("format", "bibtex").strip().lower()

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
@cache_response(seconds=3600)
def authors_list():
    """Alphabetical list of all authors with article counts."""
    print_journals, web_journals, all_journals = _get_sidebar()
    new_count = get_new_article_count(days=7)
    all_authors = get_all_authors(limit=500)

    # Group by first letter of last name (last word of full name)
    grouped = {}
    for name, count in all_authors:
        parts = name.strip().split()
        last = parts[-1] if parts else name
        letter = last[0].upper() if last else "#"
        if letter not in grouped:
            grouped[letter] = []
        grouped[letter].append((name, count))

    # Sort letters, put non-alpha at end
    letters = sorted(grouped.keys(), key=lambda c: (not c.isalpha(), c))

    return render_template(
        "authors.html",
        grouped=grouped,
        letters=letters,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )


@app.route("/author/<path:name>")
def author_detail(name):
    """All articles by a specific author."""
    print_journals, web_journals, all_journals = _get_sidebar()
    new_count = get_new_article_count(days=7)
    articles = get_author_articles(name)

    return render_template(
        "author.html",
        author_name=name,
        articles=articles,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )


@app.route("/article/<int:article_id>")
def article_detail(article_id):
    """Full detail page for a single article."""
    article = get_article_by_id(article_id)
    if article is None:
        return "Article not found", 404

    related      = get_related_articles(article_id, limit=5)
    cited_by     = get_article_citations(article_id)
    all_refs     = get_article_all_references(article_id)
    cites        = [r for r in all_refs if r["in_index"]]
    outside_refs = [r for r in all_refs if not r["in_index"]]
    outside_count = len(outside_refs)
    print_journals, web_journals, all_journals = _get_sidebar()
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
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
        journal_coverage=journal_coverage,
    )


@app.route("/explore")
def explore():
    """Data exploration page: timeline, tag co-occurrence, author network, citations."""
    print_journals, web_journals, all_journals = _get_sidebar()
    new_count = get_new_article_count(days=7)
    all_tags = get_all_tags()
    min_year, max_year = get_year_range()
    coverage = get_coverage_stats()

    return render_template(
        "explore.html",
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
        all_tags=all_tags,
        min_year=min_year,
        max_year=max_year,
        coverage=coverage,
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

    print_journals, web_journals, all_journals = _get_sidebar()
    new_count = get_new_article_count(days=7)

    return render_template(
        "citations.html",
        article=article,
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )


@app.route("/api/citations/ego")
def api_ego_network():
    """JSON: 2-degree ego network around a specific article."""
    article_id = request.args.get("article", type=int)
    if not article_id:
        return jsonify({"error": "article parameter required"}), 400
    return jsonify(get_ego_network(article_id))


@app.route("/api/stats/timeline")
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
@cache_response(seconds=3600)
def api_tag_cooccurrence():
    """JSON: tag co-occurrence matrix."""
    return jsonify(get_tag_cooccurrence())


@app.route("/api/stats/author-network")
@cache_response(seconds=3600)
def api_author_network():
    """JSON: author co-authorship network nodes and links."""
    return jsonify(get_author_network(min_papers=3, top_n=150))


@app.route("/api/stats/citation-trends")
@cache_response(seconds=3600)
def api_citation_trends():
    """JSON: avg internal citations per article per year, filtered by optional journal."""
    journal = request.args.get("journal", "").strip()
    return jsonify(get_citation_trends(journal=journal or None))


@app.route("/api/citations/network")
def api_citations_network():
    """JSON: force-graph nodes and directed edges for the citation network."""
    min_citations = max(1, int(request.args.get("min_citations", 5)))
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


@app.route("/api/stats/most-cited")
def api_most_cited():
    """JSON: top articles by internal citation count, with optional filters."""
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()
    journal   = request.args.get("journal",   "").strip()
    tag       = request.args.get("tag",       "").strip()
    limit     = min(100, int(request.args.get("limit", 50)))

    results = get_most_cited(
        year_from=year_from or None,
        year_to=year_to or None,
        journal=journal or None,
        tag=tag or None,
        limit=limit,
    )
    return jsonify(results)


@app.route("/new")
def new_articles():
    """Articles fetched within the last 7 days."""
    print_journals, web_journals, all_journals = _get_sidebar()
    new_count = get_new_article_count(days=7)
    articles = get_new_articles(days=7)

    # Group by journal
    journal_groups: dict[str, list] = {}
    for a in articles:
        j = a.get("journal") or "Unknown"
        if j not in journal_groups:
            journal_groups[j] = []
        journal_groups[j].append(a)

    return render_template(
        "new.html",
        journal_groups=journal_groups,
        total=len(articles),
        print_journals=print_journals,
        web_journals=web_journals,
        all_journals=all_journals,
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )


@app.route("/about")
def about():
    """About page — project background, values, developer info."""
    return render_template("about.html")


@app.route("/most-cited")
@cache_response(seconds=1800)
def most_cited_page():
    """Most-cited articles page with filter controls and grouped views."""
    print_journals, web_journals, all_journals = _get_sidebar()
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
        unavailable=UNAVAILABLE_JOURNALS,
        new_count=new_count,
    )


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug)
