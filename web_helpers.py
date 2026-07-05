"""web_helpers.py — shared helpers, Jinja filters, response middleware,
and error handlers extracted from the original monolithic app.py during the
prompt-F1 split. The application factory in app.py wires these onto the
Flask instance."""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import time
from functools import wraps

import threading

from flask import (
    g, jsonify, make_response, redirect, render_template, request,
)

from health import APP_VERSION

log = logging.getLogger(__name__)

MONTHS = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

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


def inject_globals():
    """Make version string available to all templates for cache-busting.
    Also surfaces the Datastories flags so _feature_nav.html can decide
    whether to render the Datastories nav item, and whether to expose
    the full chapter outline (authed) vs a single link to the landing
    page (not authed)."""
    from app import datastories_enabled
    from auth_datastories import is_authenticated
    return {
        "version": APP_VERSION,
        "datastories_enabled": datastories_enabled(),
        # Truthy when the current request carries a valid session cookie
        # OR a valid PINAKES_ADMIN_TOKEN bearer header. Tools, API, and
        # the chapter-outline dropdown are gated on this. Always evaluated
        # in a request context — inject_globals runs as a context processor.
        "datastories_authed": is_authenticated(),
    }


def not_found(e):
    return render_template("error.html", code=404, message="Page not found"), 404


def server_error(e):
    return render_template("error.html", code=500, message="Internal server error"), 500


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


# ── IP denylist ─────────────────────────────────────────────────────────────
# Hard block for abusive networks, applied before any routing or DB work so a
# flood can't tie up the (small, single-machine) worker pool. The default entry
# is the Alibaba Cloud range that ran a distributed, User-Agent-rotating
# scraper across the whole /21 — walking /article, /export, /citations, and the
# infinite /explore?seed=N space — and repeatedly exhausted the worker
# (incident 2026-06-20). Per-IP rate limiting can't catch it (hundreds of IPs,
# each under the cap) and UA blocking can't (rotating fake browser UAs), so the
# range is denied wholesale; real traffic from cloud-hosting IPs is ~nil for a
# scholarly index. Extend without a code change via PINAKES_BLOCKED_CIDRS
# (comma-separated CIDRs); this is a stopgap until Cloudflare fronts the site.
_DEFAULT_BLOCKED_CIDRS = "47.79.200.0/21"


def _load_blocked_networks():
    raw = os.environ.get("PINAKES_BLOCKED_CIDRS", _DEFAULT_BLOCKED_CIDRS)
    nets = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            nets.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            log.warning("Ignoring invalid CIDR in blocklist: %r", chunk)
    return nets


_BLOCKED_NETWORKS = _load_blocked_networks()


def block_denied_ips():
    """before_request hook: 403 any client in a denied network, cheaply, before
    rate limiting / routing / DB access. Registered first so abusive traffic
    costs essentially nothing. No-op when the blocklist is empty."""
    if not _BLOCKED_NETWORKS:
        return None
    from rate_limit import client_ip_key
    try:
        addr = ipaddress.ip_address(client_ip_key())
    except ValueError:
        return None
    for net in _BLOCKED_NETWORKS:
        if addr in net:
            return make_response("Forbidden", 403)
    return None


def require_cloudflare_origin():
    """When PINAKES_CLOUDFLARE_ONLY is truthy, 403 any PUBLIC client that
    reaches the Fly origin directly instead of through Cloudflare — closing
    the bypass where an attacker hits rhet-comp-index.fly.dev (or the origin
    IP) to skip Cloudflare's bot/rate protection.

    Always exempt, so this can never take the site down:
      - the /health* probes (Fly's checker must always reach them);
      - internal Fly traffic, which carries no Fly-Client-IP header;
      - loopback / private peers (health checks, in-machine curl).

    Read per-request so it can be toggled via `fly secrets set` without a
    code change. Default off until explicitly enabled."""
    if os.environ.get("PINAKES_CLOUDFLARE_ONLY", "").lower() not in ("1", "true", "yes"):
        return None
    if request.path.startswith("/health"):
        return None
    peer = (request.headers.get("Fly-Client-IP") or "").strip()
    if not peer:
        return None
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return None
    if addr.is_private or addr.is_loopback:
        return None
    from rate_limit import _is_cloudflare_peer
    if _is_cloudflare_peer(peer):
        return None
    return make_response("Forbidden", 403)


def redirect_www():
    """Redirect www.pinakes.xyz → pinakes.xyz (301 permanent)."""
    if request.host.startswith("www."):
        return redirect(request.url.replace("www.", "", 1), code=301)


# ── Load-shedding for the expensive read endpoints ──────────────────────────
# A distributed, IP-rotating botnet can't be caught by per-IP rate limiting or
# the CIDR denylist (incident 2026-07-05): it walked the expensive read
# endpoints — /explore?seed=N, /article/N, /author/, /citations, /export, each
# 5–18 s of graph/DB work — from a fresh IP per request until every gthread
# worker thread was stuck and Fly's proxy 503'd *everything*, health checks
# included. Cloudflare "Under Attack Mode" is the edge fix, but it's a manual
# toggle. This is the always-on backstop: cap how many expensive requests run
# concurrently at fewer than the worker's thread count, so no flood — from any
# source — can occupy every thread. Cheap pages, the JSON API, /health, static,
# and the admin/cron routes are never shed; they always find a free thread.
# When the cap is full, an expensive request is refused instantly with 503 +
# Retry-After instead of queuing behind seconds of work.
#
# One process (gunicorn --workers 1), so this module-level semaphore governs
# all threads. The cap is read once at import; change it via the
# PINAKES_MAX_EXPENSIVE_INFLIGHT env var + a restart. Keep it below the gthread
# thread count (currently 8) so cheap paths and health always have headroom.

# Trailing slashes on /article/ and /author/ so the detail routes match but the
# cheap list pages (/articles, /authors) do NOT. /citations and /explore and
# /export have no cheaper sibling to worry about; the /api/citations/* graph
# routes start with /api and so never match here.
_EXPENSIVE_PREFIXES = ("/explore", "/export", "/citations", "/article/", "/author/")

_MAX_EXPENSIVE_INFLIGHT = _safe_int(
    os.environ.get("PINAKES_MAX_EXPENSIVE_INFLIGHT"), 6, lo=1, hi=1000
)
_EXPENSIVE_SEMAPHORE = threading.BoundedSemaphore(_MAX_EXPENSIVE_INFLIGHT)


def _is_expensive_path(path):
    return path.startswith(_EXPENSIVE_PREFIXES)


def shed_expensive_load():
    """before_request hook: bound concurrency on the expensive read endpoints.
    Acquire a slot without blocking; if the cap is full, refuse instantly with
    503 + Retry-After rather than queue behind long graph/DB work and starve
    the worker. Non-expensive paths pass through untouched. The slot is freed
    in release_expensive_slot (teardown), which runs on every outcome."""
    if not _is_expensive_path(request.path):
        return None
    if _EXPENSIVE_SEMAPHORE.acquire(blocking=False):
        g._loadshed_held = True
        return None
    log.warning(
        "load-shed 503: path=%s ip=%s",
        request.path,
        request.headers.get("Fly-Client-IP") or request.remote_addr,
    )
    resp = make_response(
        "<!doctype html><meta charset=utf-8><title>Busy</title>"
        "<p>The server is briefly overloaded. Please retry in a few seconds.</p>",
        503,
    )
    # Sensible default; Flask-Limiter (headers_enabled) may overwrite this with
    # the current limit window in its after_request pass. Either value is fine.
    resp.headers["Retry-After"] = "5"
    return resp


def release_expensive_slot(exc=None):
    """teardown_request hook: release the slot acquired by shed_expensive_load,
    if this request held one. teardown runs on success and on exception, so a
    slot is never leaked. The g flag makes double-release impossible."""
    if g.pop("_loadshed_held", False):
        try:
            _EXPENSIVE_SEMAPHORE.release()
        except ValueError:
            # Defensive: g flag already guarantees one release per acquire.
            pass


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


def redact_authors(value):
    """Jinja filter: render redaction tokens as the friendly display phrase.

    Accepts a single author string or a ``;``-joined list and replaces any
    stored redaction token ("Redacted Author 7f3a2c") with the human phrase
    "Name Redacted by Author Request", passing real names through untouched.

    The DATA layer already swapped redacted names for tokens everywhere, so a
    token rendering raw is not a name leak — this filter is the display polish
    that makes redacted bylines read the way an author was promised. Apply it
    to the *visible text* of an author byline; leave the token in the ``href``
    so the (preserved) author page stays reachable at its token URL."""
    from redaction import is_redaction_token, DISPLAY_TEXT
    if not value:
        return value
    parts = [p.strip() for p in str(value).split(";")]
    out = [DISPLAY_TEXT if is_redaction_token(p) else p for p in parts if p]
    return "; ".join(out)


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


def register_error_handlers(app):
    """Register 404 / 500 / 429 handlers on *app*. Called by create_app()."""
    app.register_error_handler(404, not_found)
    app.register_error_handler(500, server_error)
    app.register_error_handler(429, rate_limit_exceeded)


# ── Sidebar builder ─────────────────────────────────────────────────────────
#
# build_sidebar is the cache MISS path; the cache itself and the cached
# wrapper (_get_sidebar) live in app.py so conftest can invalidate via
# `_app_module._sidebar_cache = None`. build_sidebar holds no state and is
# safe to live here.

def build_sidebar():
    """Compute sidebar journal-list data. Pure function over the journals
    constants and the live article counts; called by app._get_sidebar on
    cache miss."""
    from db import get_article_counts
    from journals import (
        CROSSREF_JOURNALS, RSS_JOURNALS, SCRAPE_JOURNALS, MANUAL_JOURNALS,
        JOURNAL_GROUPS,
    )

    counts_raw = get_article_counts()
    count_map = {r["journal"]: r["count"] for r in counts_raw}

    print_journals = [
        {"name": j["name"], "source": "crossref",
         "count": count_map.get(j["name"], 0)}
        for j in CROSSREF_JOURNALS
    ]

    web_journals = []
    for j in RSS_JOURNALS:
        web_journals.append({"name": j["name"], "source": "rss",
                             "count": count_map.get(j["name"], 0)})
    for j in SCRAPE_JOURNALS:
        web_journals.append({"name": j["name"], "source": "scrape",
                             "count": count_map.get(j["name"], 0)})
    for j in MANUAL_JOURNALS:
        web_journals.append({"name": j["name"], "source": "manual",
                             "count": count_map.get(j["name"], 0)})

    all_journals = sorted(print_journals + web_journals,
                          key=lambda x: x["name"].lower())

    journal_map = {j["name"]: j for j in all_journals}
    assigned = set()
    journal_groups = []
    for group_label, names in JOURNAL_GROUPS:
        members = sorted(
            [journal_map[n] for n in names if n in journal_map],
            key=lambda j: j["name"].lower(),
        )
        if members:
            journal_groups.append({"label": group_label, "journals": members})
            assigned.update(n for n in names if n in journal_map)
    ungrouped = [j for j in all_journals if j["name"] not in assigned]
    if ungrouped:
        journal_groups.append({"label": "Other", "journals": ungrouped})

    return print_journals, web_journals, all_journals, journal_groups
