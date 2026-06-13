"""orcid_oauth.py — minimal ORCID three-legged OAuth for identity verification.

The author-redaction request flow offers ORCID as the stronger verification
path (vs. an email round-trip): completing the OAuth dance proves the requester
controls a specific ORCID iD, and ORCID hands back that iD plus the person's
name. The admin still reviews before any redaction fires.

Env vars (read at call time):
    ORCID_CLIENT_ID, ORCID_CLIENT_SECRET   — the registered API credentials
    ORCID_ENV       — 'sandbox' to hit sandbox.orcid.org, else production
The client secret lives only in the environment (fly secrets / .env), never in
the repo — same discipline as PINAKES_ADMIN_TOKEN.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlencode

log = logging.getLogger(__name__)


def _base() -> str:
    if os.environ.get("ORCID_ENV", "").lower() == "sandbox":
        return "https://sandbox.orcid.org"
    return "https://orcid.org"


def is_configured() -> bool:
    """True iff ORCID OAuth credentials are present."""
    return bool(os.environ.get("ORCID_CLIENT_ID") and os.environ.get("ORCID_CLIENT_SECRET"))


def authorize_url(state: str, redirect_uri: str) -> str:
    """Build the ORCID authorization URL to redirect the requester to.
    The /authenticate scope returns the iD and name without profile write."""
    params = {
        "client_id": os.environ["ORCID_CLIENT_ID"],
        "response_type": "code",
        "scope": "/authenticate",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{_base()}/oauth/authorize?{urlencode(params)}"


def exchange_code(code: str, redirect_uri: str) -> dict | None:
    """Exchange the authorization code for a token. The ORCID token response
    includes the verified `orcid` iD and the person's `name`. Returns a dict
    {'orcid': ..., 'name': ...} or None on failure."""
    import requests

    resp = requests.post(
        f"{_base()}/oauth/token",
        data={
            "client_id": os.environ["ORCID_CLIENT_ID"],
            "client_secret": os.environ["ORCID_CLIENT_SECRET"],
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        headers={"Accept": "application/json"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"orcid": data.get("orcid"), "name": data.get("name")}
