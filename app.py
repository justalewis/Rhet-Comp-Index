"""app.py — Pinakes Flask application factory.

Wires Blueprints from blueprints/, shared helpers from web_helpers.py,
and the cross-cutting concerns: Sentry init, rate-limiter init, gzip
compression, Jinja filters, security headers, www-redirect, error
handlers, and DB warmup at startup.

The module-level `app = create_app()` instance exists for gunicorn's
`app:app` import path. Tests can call create_app() directly if they
need an isolated instance.

Module-resident helpers (kept here on purpose — tests reach into these
via `app.X` patterns):
    _run_background_fetch       (test_routes_export, test_auth, test_rate_limit)
    _get_sidebar / _build_sidebar / _sidebar_cache / _sidebar_ts
                                (conftest invalidates the cache)

Cross-module re-exports for `app.X` access from tests:
    _safe_int, _safe_float, _bibtex_key, _to_bibtex, _to_ris,
    format_period
"""

from __future__ import annotations

import logging
import os
import time

from flask import Flask, redirect, request
from flask_compress import Compress

from monitoring import init_sentry
from rate_limit import limiter
from auth import admin_token_configured

from db import (
    init_db, get_articles, get_article_counts, get_total_count,
    get_all_tags, get_new_article_count,
)
from db import backfill_oa_status as _backfill_oa

# Re-exports for tests that use `app.X` patterns:
from web_helpers import (  # noqa: F401
    _safe_int, _safe_float,
    _bibtex_key, _to_bibtex, _to_ris,
    format_period, display_date,
    cache_response,
    inject_globals,
    register_error_handlers,
    not_found, server_error, rate_limit_exceeded,
    set_security_headers, redirect_www,
)

log = logging.getLogger(__name__)


# ── Sidebar cache (process-wide, mutated by tests' conftest) ────────────────

_sidebar_cache = None
_sidebar_ts = 0.0
_SIDEBAR_TTL = 300  # seconds


def _get_sidebar():
    """Return cached sidebar data, rebuilding at most every 5 minutes.
    Cache state lives here in app.py so conftest can invalidate it via
    `_app_module._sidebar_cache = None` between tests."""
    global _sidebar_cache, _sidebar_ts
    if _sidebar_cache is None or time.time() - _sidebar_ts > _SIDEBAR_TTL:
        from web_helpers import build_sidebar
        _sidebar_cache = build_sidebar()
        _sidebar_ts = time.time()
    return _sidebar_cache


# ── Background-fetch worker (called by /fetch admin route) ──────────────────

def _run_background_fetch():
    """Background-thread target for /fetch. Module-level so tests can patch
    it directly rather than mocking threading.Thread (which would also
    intercept Flask-Limiter's internal Timer use)."""
    try:
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


# ── Factory ─────────────────────────────────────────────────────────────────

def create_app() -> Flask:
    """Build and configure the Flask app. Imported once at module load."""
    init_sentry("web")

    flask_app = Flask(__name__)
    Compress(flask_app)
    limiter.init_app(flask_app)

    # Initialise DB at app construction so gunicorn workers find the schema.
    init_db()

    # Surface a missing admin token at startup. Read endpoints continue to
    # work; mutating endpoints will reject all requests with 503 until set.
    if not admin_token_configured():
        log.critical(
            "PINAKES_ADMIN_TOKEN is not set; mutating endpoints will reject all requests"
        )

    # Tag articles from known gold-OA journals (fast, no API calls).
    _oa_result = _backfill_oa()
    if _oa_result["tagged"] > 0:
        log.info("OA backfill: tagged %d articles as gold OA", _oa_result["tagged"])

    # Pre-warm the SQLite page cache so the first HTTP request isn't slow.
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

    # Jinja filters
    flask_app.jinja_env.filters["format_period"] = format_period
    flask_app.jinja_env.filters["display_date"]  = display_date

    # Context
    flask_app.context_processor(inject_globals)

    # Middleware
    flask_app.before_request(redirect_www)
    flask_app.after_request(set_security_headers)

    # Error handlers
    register_error_handlers(flask_app)

    # Blueprints — registered last so all middleware is in place.
    from blueprints.main         import bp as main_bp
    from blueprints.articles     import bp as articles_bp
    from blueprints.authors      import bp as authors_bp
    from blueprints.citations    import bp as citations_bp
    from blueprints.stats        import bp as stats_bp
    from blueprints.books        import bp as books_bp
    from blueprints.institutions import bp as institutions_bp
    from blueprints.admin        import bp as admin_bp

    flask_app.register_blueprint(main_bp)
    flask_app.register_blueprint(articles_bp)
    flask_app.register_blueprint(authors_bp)
    flask_app.register_blueprint(citations_bp)
    flask_app.register_blueprint(stats_bp)
    flask_app.register_blueprint(books_bp)
    flask_app.register_blueprint(institutions_bp)
    flask_app.register_blueprint(admin_bp)

    return flask_app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(host="0.0.0.0", port=port, debug=debug)
