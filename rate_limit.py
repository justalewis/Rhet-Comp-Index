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
import os

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
    # Saved-search alert signup. Each accepted POST sends a verification email
    # to an address the submitter typed, so an uncapped form is a way to make
    # Pinakes mail strangers on someone else's behalf. Low cap, and the digest
    # purges never-confirmed rows after a week.
    "alert_signup": "5 per hour",
    # The optional "how you use Pinakes" form. Responses go to a person who
    # reads them, so a script should not be able to bury real ones.
    "usage_survey": "5 per hour",
}


# Behind a reverse proxy on the same machine (IIS/ARR → Waitress at the WAC
# Clearinghouse) there is no Fly-Client-IP, and every request reaches Flask
# from the proxy's loopback address. PINAKES_TRUSTED_IP_HEADER names the header
# that carries the real visitor IP there. On the Clearinghouse box that has to
# be CF-Connecting-IP, not X-Forwarded-For: Waitress 3.x deletes X-Forwarded-*
# and Forwarded before the app sees them unless it is started with
# --trusted-proxy, so an X-Forwarded-For setting finds nothing. Cloudflare
# answers a client-supplied CF-Connecting-IP with a 403 at the edge, so the
# header can only be forged by a request that skips Cloudflare and reaches the
# origin directly; restricting the origin to Cloudflare's ranges closes that.
TRUSTED_IP_HEADER_ENV = "PINAKES_TRUSTED_IP_HEADER"


def _parse_hop(entry: str) -> str | None:
    """One forwarding-header entry as a bare IP string, or None if it isn't
    one. Proxies decorate entries: IIS ARR appends `ip:port`, and IPv6 can
    arrive bracketed as `[2001:db8::1]:443`."""
    v = entry.strip().strip('"')
    if v.startswith("["):
        v = v[1:].split("]", 1)[0]
    elif v.count(":") == 1:
        v = v.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(v))
    except ValueError:
        return None


def _socket_peer() -> str | None:
    """The address that actually connected to us. app.py's ProxyFix leaves
    x_for at Werkzeug's default of 1, so request.remote_addr is rewritten from
    the last X-Forwarded-For entry whenever one arrives; ProxyFix keeps the
    original under werkzeug.proxy_fix.orig."""
    orig = request.environ.get("werkzeug.proxy_fix.orig") or {}
    return orig.get("REMOTE_ADDR") or request.remote_addr


def _is_local_peer(addr: str | None) -> bool:
    """True if `addr` is loopback or private, i.e. the request was handed to
    us by a proxy on this machine or network rather than by a visitor. The
    Windows install binds Waitress to 0.0.0.0, so a visitor who reaches port
    8080 directly must not have their forwarding headers believed."""
    try:
        ip = ipaddress.ip_address(addr or "")
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _ip_from_header(name: str) -> str | None:
    """The visitor IP carried in header `name`, read from the right.

    Single-value headers (CF-Connecting-IP) yield their one entry. For a chain
    like X-Forwarded-For, entries are appended left to right, so everything to
    the right of the visitor was written by a proxy we trust and everything to
    the left came from the client and may be invented. Walk from the right,
    skipping Cloudflare edges and private hops; the first other address is the
    visitor. If the chain holds nothing but Cloudflare edges, the request came
    through Cloudflare and CF-Connecting-IP is the visitor.
    """
    raw = request.headers.get(name) or ""
    saw_cloudflare = False
    for entry in reversed(raw.split(",")):
        if not entry.strip():
            continue
        hop = _parse_hop(entry)
        if hop is None:
            return None
        if _is_cloudflare_peer(hop):
            saw_cloudflare = True
            continue
        if _is_local_peer(hop):
            continue
        return hop
    if saw_cloudflare:
        return _parse_hop(request.headers.get("CF-Connecting-IP") or "")
    return None


def resolve_client_ip() -> tuple[str, str]:
    """The client IP for this request, and which rule produced it.

    Order of preference:
        1. On Fly only: CF-Connecting-IP (the real visitor IP), trusted ONLY
           when the request actually arrived from a Cloudflare edge, i.e.
           Fly-Client-IP is in Cloudflare's ranges. This prevents a
           direct-to-origin client from spoofing the header.
        2. On Fly only: Fly-Client-IP, the TCP peer Fly saw (a direct
           visitor, or the Cloudflare edge IP when CF-Connecting-IP isn't
           trusted).
        3. The PINAKES_TRUSTED_IP_HEADER header, when that is set and the
           request came from a local proxy. If it is set but yields nothing,
           the answer is the socket peer; step 4 is skipped.
        4. First comma-separated value of X-Forwarded-For.
        5. request.remote_addr, else "127.0.0.1" (Flask test client).

    "On Fly" means FLY_APP_NAME is set, which Fly does on every machine.
    Fly's proxy overwrites any Fly-Client-IP a visitor sends; nothing on the
    Clearinghouse chain does. Cloudflare, ARR and Waitress all pass a custom
    header through untouched, so trusting it there let any visitor name their
    own address: a fresh rate-limit budget per request, a way around the
    denylist, and as many "readers" as they liked in the tag votes and the
    usage statistics.

    The second element names the rule, for the /api/admin/client-ip check.
    """
    if os.environ.get("FLY_APP_NAME"):
        fly = (request.headers.get("Fly-Client-IP") or "").strip()
        cf = (request.headers.get("CF-Connecting-IP") or "").strip()
        if cf and fly and _is_cloudflare_peer(fly):
            return cf, "CF-Connecting-IP via Fly"
        if fly:
            return fly, "Fly-Client-IP"

    trusted = (os.environ.get(TRUSTED_IP_HEADER_ENV) or "").strip()
    if trusted:
        peer = _socket_peer()
        if _is_local_peer(peer):
            ip = _ip_from_header(trusted)
            if ip:
                return ip, trusted
        return peer or "127.0.0.1", "remote_addr"

    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip(), "X-Forwarded-For (first entry, untrusted)"

    return request.remote_addr or "127.0.0.1", "remote_addr"


def client_ip_key() -> str:
    """Return a stable per-client identifier for rate-limit bucketing, the IP
    denylist, and access attribution. See resolve_client_ip for the rules."""
    return resolve_client_ip()[0]


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
