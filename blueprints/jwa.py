"""jwa Blueprint — serves the gated interactive webtext companion for the Pinakes
article in *The Journal of Writing Analytics*, at ``/jwa/``.

The webtext is a self-contained static bundle in ``jwa-webtext/`` (one HTML file +
a ``figures/`` folder + the linked walkthrough). It is gated behind HTTP Basic
Auth — a single user/pass shared with the journal editors — so it is not a public
page. Credentials come from the ``PINAKES_JWA_USER`` / ``PINAKES_JWA_PASSWORD``
environment (Fly secrets in production); if either is unset the route fails closed
with 503 rather than serving unprotected.

Basic Auth (not the Datastories session gate) is deliberate here: it is the
simplest thing to hand an external reviewer — "go to this URL, enter this user and
password" — and needs no login page.
"""

import hmac
import mimetypes
import os
from pathlib import Path

from flask import Blueprint, Response, redirect, request

bp = Blueprint("jwa", __name__)

# Repo-root/jwa-webtext — the committed static bundle.
_JWA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "jwa-webtext")
# ASCII only — HTTP header values must be Latin-1 encodable, or gunicorn rejects
# the response with InvalidHeader (an em-dash here 500'd the 401 auth prompt).
_REALM = 'Basic realm="Pinakes JWA preview"'


def _authorized() -> bool:
    user = os.environ.get("PINAKES_JWA_USER")
    pw = os.environ.get("PINAKES_JWA_PASSWORD")
    if not user or not pw:
        return False  # not configured — caller turns this into a 503
    a = request.authorization
    if not a or (a.type or "").lower() != "basic":
        return False
    # constant-time compares to avoid leaking credential length/prefix via timing
    return (hmac.compare_digest(a.username or "", user)
            and hmac.compare_digest(a.password or "", pw))


@bp.before_request
def _gate():
    """Guard every /jwa route (page and assets alike) with Basic Auth."""
    if not os.environ.get("PINAKES_JWA_USER") or not os.environ.get("PINAKES_JWA_PASSWORD"):
        return Response("The JWA preview is not configured.", 503)
    if not _authorized():
        return Response("Authentication required.", 401, {"WWW-Authenticate": _REALM})
    return None


@bp.route("/jwa")
def jwa_root():
    # Redirect to the trailing-slash form so the browser resolves the bundle's
    # relative asset paths (figures/…, the walkthrough) against /jwa/.
    return redirect("/jwa/", code=308)


@bp.route("/jwa/", defaults={"filename": "index.html"})
@bp.route("/jwa/<path:filename>")
def jwa_file(filename):
    base = Path(_JWA_DIR).resolve()
    try:
        target = (base / filename).resolve()
        target.relative_to(base)          # reject path traversal outside the bundle
    except (ValueError, OSError):
        return Response("Not found.", 404)
    if not target.is_file():
        return Response("Not found.", 404)
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return Response(target.read_bytes(), mimetype=mime)
