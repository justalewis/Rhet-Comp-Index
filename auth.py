"""auth.py — Lightweight admin token authentication for mutating endpoints.

Exports:
    require_admin_token — Flask route decorator
    admin_token_configured — boolean check used by the health endpoint

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


def require_admin_token(view):
    """Reject the request unless `Authorization: Bearer <PINAKES_ADMIN_TOKEN>`
    is supplied. 503 if the server is misconfigured, 401 if the header is
    missing or malformed, 403 if the token is wrong."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        configured = os.environ.get(ENV_VAR, "")
        if not configured:
            log.warning(
                "Admin endpoint hit but %s is not set: ip=%s path=%s",
                ENV_VAR, _client_ip(), request.path,
            )
            return jsonify({
                "error": "admin authentication is not configured on this server"
            }), 503

        header = request.headers.get("Authorization", "")
        scheme, _, supplied = header.partition(" ")
        if scheme.lower() != "bearer" or not supplied:
            log.warning(
                "Auth required: missing/malformed Authorization header. "
                "ip=%s path=%s",
                _client_ip(), request.path,
            )
            return jsonify({"error": "authentication required"}), 401

        if not hmac.compare_digest(supplied, configured):
            log.warning(
                "Auth failed: wrong token. ip=%s path=%s token=%s",
                _client_ip(), request.path, _truncate(supplied),
            )
            return jsonify({"error": "invalid credentials"}), 403

        return view(*args, **kwargs)

    return wrapper
