"""auth_datastories.py — Password-gate for the Datastories tools.

The Datastories tools are an in-progress monograph project that I want to
share with collaborators and editors but not the public web. This module
implements a single-shared-password gate.

Two ways to pass the gate:
  1. A signed cookie issued after the user POSTed the correct password to
     /datastories/login. Cookie TTL is 14 days, signed with the app's
     SECRET_KEY using itsdangerous.URLSafeTimedSerializer.
  2. The PINAKES_ADMIN_TOKEN bearer header. Reusing the existing admin
     auth saves the operator from juggling two secrets and means CI /
     scripted callers don't need a separate code path.

The password itself lives in the PINAKES_DATASTORIES_PASSWORD environment
variable. If unset, every gated request returns 503 (server misconfigured)
— there's no fallback "open" mode, so a forgotten env var fails closed
rather than leaking access.

Exports:
    verify_password(submitted)        — constant-time check
    is_authenticated()                — current request has cookie or admin-token
    issue_session_cookie(response)    — set the cookie (called by /login)
    clear_session_cookie(response)    — clear the cookie (called by /logout)
    require_datastories_auth(view)    — decorator returning the landing page
                                        (or 401 JSON for /api routes) on miss
"""

from __future__ import annotations

import hmac
import logging
import os
from functools import wraps

from flask import jsonify, redirect, request, current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import auth as _admin_auth

log = logging.getLogger(__name__)

ENV_VAR = "PINAKES_DATASTORIES_PASSWORD"
COOKIE_NAME = "pinakes_ds"
COOKIE_TTL_SECONDS = 14 * 24 * 60 * 60   # 14 days
SALT = "datastories-session-v1"


# ── Password verification ──────────────────────────────────────────────────

def password_configured() -> bool:
    return bool(os.environ.get(ENV_VAR))


def verify_password(submitted: str) -> bool:
    """Constant-time comparison of `submitted` against the configured
    password. Returns False if the env var is unset (so callers can't
    accidentally authenticate via a missing-config side-channel)."""
    expected = os.environ.get(ENV_VAR, "")
    if not expected or not submitted:
        return False
    return hmac.compare_digest(submitted, expected)


# ── Cookie token helpers ───────────────────────────────────────────────────

def _serializer() -> URLSafeTimedSerializer:
    """Build the signer using the app's SECRET_KEY. The serializer's salt
    is namespaced so a leaked cookie from another part of the app can't
    pass this gate, and vice versa."""
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=SALT)


def _token_valid(token: str) -> bool:
    """True iff `token` was issued by this app and hasn't expired."""
    if not token:
        return False
    try:
        _serializer().loads(token, max_age=COOKIE_TTL_SECONDS)
        return True
    except SignatureExpired:
        return False
    except BadSignature:
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("datastories cookie verify error: %s", exc)
        return False


def issue_session_cookie(response):
    """Set the auth cookie on `response`. Called by /datastories/login on a
    successful password submission. Cookie is httponly (no JS access) and
    Secure when served over HTTPS (Flask sets this from request.is_secure)."""
    token = _serializer().dumps({"v": 1})
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=COOKIE_TTL_SECONDS,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
    )
    return response


def clear_session_cookie(response):
    """Remove the auth cookie."""
    response.delete_cookie(COOKIE_NAME, samesite="Lax")
    return response


# ── Request-time auth check ────────────────────────────────────────────────

def is_authenticated() -> bool:
    """Two-path check:
        1. Signed cookie present and valid → pass.
        2. Authorization: Bearer <PINAKES_ADMIN_TOKEN> → pass.
    Returns False if neither holds, including when the password isn't
    configured at all."""
    if not password_configured():
        return False
    token = request.cookies.get(COOKIE_NAME, "")
    if _token_valid(token):
        return True
    # Fall through to admin token. This lets a script with the admin token
    # exercise Datastories APIs without juggling a second secret.
    return _admin_auth.token_check_passes()


# ── Decorators ─────────────────────────────────────────────────────────────

def require_datastories_auth(view):
    """For HTML routes — on missed auth, redirect to /datastories (the
    public landing page). 503 if the password isn't configured."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not password_configured():
            return jsonify({
                "error": "Datastories password not configured on this server. "
                         "Set PINAKES_DATASTORIES_PASSWORD."
            }), 503
        if is_authenticated():
            return view(*args, **kwargs)
        return redirect("/datastories")
    return wrapper


def require_datastories_auth_api(view):
    """For /api/datastories/* — on missed auth, return JSON 401 instead of
    redirecting to a page (an XHR client won't follow the redirect to the
    landing HTML usefully)."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not password_configured():
            return jsonify({"error": "Datastories password not configured"}), 503
        if is_authenticated():
            return view(*args, **kwargs)
        return jsonify({"error": "authentication required"}), 401
    return wrapper
