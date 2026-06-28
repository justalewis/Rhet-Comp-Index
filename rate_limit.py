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
    In-memory. This is correct for the single-PROCESS deployment, including
    the gthread (one worker, many threads) config: threads share the
    process's memory, so the counters stay coherent. A multi-PROCESS
    deployment (gunicorn --workers >1) would need a shared backend (Redis or
    Fly.io's redis add-on); each process would otherwise track its own
    counts independently and the effective limits would multiply by N.

    Note: in-memory counters reset whenever the worker process restarts. A
    runaway worker-timeout/restart loop therefore defeats the limiter — the
    gthread config exists in part to prevent that loop (see Dockerfile).
"""

from __future__ import annotations

import ipaddress
import logging

from flask import request
from flask_limiter import Limiter

from auth import token_check_passes

log = logging.getLogger(__name__)

# Cloudflare's published edge ranges (cloudflare.com/ips — a stable list).
# When the site is fronted by Cloudflare, the TCP peer Fly reports in
# Fly-Client-IP is one of these, and the real visitor IP is in the
# CF-Connecting-IP header. We trust CF-Connecting-IP ONLY when the peer is
# genuinely Cloudflare, so a client hitting the Fly origin directly can't spoof
# that header to dodge the IP block or poison rate-limit buckets.
_CLOUDFLARE_CIDRS = [ipaddress.ip_network(c) for c in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
)]


def _is_cloudflare_peer(ip: str) -> bool:
    """True if `ip` is in Cloudflare's edge ranges."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _CLOUDFLARE_CIDRS)

# Per-tier limit strings. Exposed as a dict so app.py decorates routes by
# name rather than re-stating the values, and so tests can read them back.
LIMITS: dict[str, str] = {
    "default":   "60 per minute",
    "citations": "20 per minute",
    "stats":     "20 per minute",
    "search":   "120 per minute",
    "fetch":      "6 per hour",
    # Public author-redaction request form. Low cap so a script can't flood the
    # admin review queue or trigger verification-email spam.
    "redaction_request": "5 per hour",
    # Community tags. Votes are cheap and low-stakes (never change anything on
    # their own), so a more generous cap; suggestions enter a human review queue,
    # so a tighter cap that still lets a genuine reader tag a few articles.
    "tag_feedback":   "40 per hour",
    "tag_suggestion": "10 per hour",
}


def client_ip_key() -> str:
    """Return a stable per-client identifier for rate-limit bucketing, the IP
    denylist, and access attribution.

    Order of preference:
        1. CF-Connecting-IP (the real visitor IP) — trusted ONLY when the
           request actually arrived from a Cloudflare edge, i.e. Fly-Client-IP
           is in Cloudflare's ranges. This prevents a direct-to-origin client
           from spoofing the header.
        2. Fly-Client-IP — the TCP peer Fly saw (a direct visitor, or the
           Cloudflare edge IP when CF-Connecting-IP isn't trusted).
        3. First comma-separated value of X-Forwarded-For.
        4. request.remote_addr, else "127.0.0.1" (Flask test client).
    """
    fly = (request.headers.get("Fly-Client-IP") or "").strip()
    cf = (request.headers.get("CF-Connecting-IP") or "").strip()
    if cf and fly and _is_cloudflare_peer(fly):
        return cf
    if fly:
        return fly

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
