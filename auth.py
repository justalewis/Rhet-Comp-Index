"""auth.py — Lightweight admin token authentication for mutating endpoints.

Exports:
    require_admin_token — Flask route decorator
    admin_token_configured — boolean check used by the health endpoint
    token_check_passes — predicate used by rate_limit.exempt_when so
        unauthenticated requests do not consume a route's rate budget.

The token is read from the PINAKES_ADMIN_TOKEN environment variable on each
request rather than at import time, so tests can monkeypatch the env without
re-importing the module.
"""

from __future__ import annotations

import hmac
import logging
import os
from functools import wraps

from flask import jsonify, request

log = logging.getLogger(__name__)

ENV_VAR = "PINAKES_ADMIN_TOKEN"

# Status / error pairs returned by _check_token() for each failure mode.
_ERR_NOT_CONFIGURED = (503, "admin authentication is not configured on this server")
_ERR_NO_HEADER      = (401, "authentication required")
_ERR_BAD_TOKEN      = (403, "invalid credentials")


def admin_token_configured() -> bool:
    """True iff PINAKES_ADMIN_TOKEN is set to a non-empty value."""
    return bool(os.environ.get(ENV_VAR))


def _client_ip() -> str:
    """Best-effort source IP for log lines. Prefers Fly's edge header."""
    return (
        request.headers.get("Fly-Client-IP")
        or request.remote_addr
        or "unknown"
    )


def _truncate(token: str, n: int = 4) -> str:
    """Return token[:n] + '...'. Never log the full token."""
    if not token:
        return "<empty>"
    return f"{token[:n]}..."


def _check_token() -> tuple[int, str] | None:
    """Pure predicate: does the current request carry a valid admin token?

    Returns None if auth would pass; (status, error_message) tuple if it
    would fail. Used by both require_admin_token (to dispatch the response)
    and rate_limit.token_check_passes (to skip rate counting on requests
    that auth will reject anyway).

    Does NOT log — both callers handle their own logging concerns.
    """
    configured = os.environ.get(ENV_VAR, "")
    if not configured:
        return _ERR_NOT_CONFIGURED

    header = request.headers.get("Authorization", "")
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        return _ERR_NO_HEADER

    if not hmac.compare_digest(supplied, configured):
        return _ERR_BAD_TOKEN

    return None


def token_check_passes() -> bool:
    """True iff the current request would pass require_admin_token.
    Safe to call without a request context guard — caller must already be
    inside one (Flask-Limiter's exempt_when callbacks always are)."""
    return _check_token() is None


def require_admin_token(view):
    """Reject the request unless `Authorization: Bearer <PINAKES_ADMIN_TOKEN>`
    is supplied. 503 if the server is misconfigured, 401 if the header is
    missing or malformed, 403 if the token is wrong."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        result = _check_token()
        if result is None:
            return view(*args, **kwargs)

        status, msg = result
        # Tailored log per failure mode. Only the truncated token prefix is
        # ever logged — never the configured secret or the full supplied value.
        if status == 503:
            log.warning(
                "Admin endpoint hit but %s is not set: ip=%s path=%s",
                ENV_VAR, _client_ip(), request.path,
            )
        elif status == 401:
            log.warning(
                "Auth required: missing/malformed Authorization header. "
                "ip=%s path=%s",
                _client_ip(), request.path,
            )
        else:  # 403
            supplied = request.headers.get("Authorization", "").partition(" ")[2]
            log.warning(
                "Auth failed: wrong token. ip=%s path=%s token=%s",
                _client_ip(), request.path, _truncate(supplied),
            )
        return jsonify({"error": msg}), status

    return wrapper
