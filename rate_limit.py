"""rate_limit.py — Flask-Limiter configuration for Pinakes.

Tiered limits:
    default      60/minute   — every route except where overridden
    citations    20/minute   — /api/citations/* (graph computations)
    stats        20/minute   — /api/stats/* (aggregations)
    search      120/minute   — /api/articles/search (typeahead)
    fetch         6/hour     — /fetch (already auth-gated; this is belt and
                               suspenders, and skipped when auth would fail
                               so probes do not exhaust the operator's budget)

Storage:
    In-memory. This is correct for the current single-worker deployment.
    Multi-worker deployments must switch to a shared backend (Redis or
    Fly.io's redis add-on); each worker would otherwise track its own
    counts independently and the effective limits would multiply by N.
"""

from __future__ import annotations

import logging

from flask import request
from flask_limiter import Limiter

from auth import token_check_passes

log = logging.getLogger(__name__)

# Per-tier limit strings. Exposed as a dict so app.py decorates routes by
# name rather than re-stating the values, and so tests can read them back.
LIMITS: dict[str, str] = {
    "default":   "60 per minute",
    "citations": "20 per minute",
    "stats":     "20 per minute",
    "search":   "120 per minute",
    "fetch":      "6 per hour",
}


def client_ip_key() -> str:
    """Return a stable per-client identifier for rate-limit bucketing.

    Order of preference:
        1. Fly-Client-IP header (set by the Fly proxy; the only header we
           trust on production traffic).
        2. First comma-separated value of X-Forwarded-For (the original
           client; subsequent values are intermediate proxies).
        3. request.remote_addr.
        4. "127.0.0.1" if remote_addr is None (Flask test client).
    """
    fly = request.headers.get("Fly-Client-IP")
    if fly:
        return fly.strip()

    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()

    return request.remote_addr or "127.0.0.1"


def _is_static_endpoint() -> bool:
    """Default-limit exemption: never count static-asset requests."""
    return request.endpoint == "static"


def fetch_auth_failing() -> bool:
    """exempt_when callback for /fetch.

    Skip the rate-limit check when admin auth would reject the request,
    so unauthenticated probes don't exhaust the operator's 6/hour budget.
    """
    return not token_check_passes()


# Initialise without an app — app.py calls limiter.init_app(app) at startup.
limiter = Limiter(
    key_func=client_ip_key,
    default_limits=[LIMITS["default"]],
    default_limits_exempt_when=_is_static_endpoint,
    storage_uri="memory://",
    headers_enabled=True,
    swallow_errors=True,  # storage failures must not 500 a request
)
