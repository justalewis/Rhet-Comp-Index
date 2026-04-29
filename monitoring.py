"""monitoring.py — Sentry error monitoring configuration.

Initialised once at app and scheduler startup. Disabled when SENTRY_DSN
is unset or when running under pytest. Tags errors by component so the
Sentry dashboard can separate web errors from background-fetch errors.

Public surface:
    init_sentry(component)      — call once at process startup
    capture_fetcher_error(src, journal, exc) — for ingestion scripts
"""

from __future__ import annotations

import logging
import os
import re

log = logging.getLogger(__name__)

DSN_ENV_VAR = "SENTRY_DSN"

_initialised = False  # set True once init_sentry has actually called sentry_sdk.init


def _is_test_env() -> bool:
    """True iff we're running under pytest. Conftest sets FLASK_ENV=testing
    at module import; we additionally check PYTEST_CURRENT_TEST (set by
    pytest while a test is active) for belt-and-suspenders."""
    if os.environ.get("FLASK_ENV", "").lower() == "testing":
        return True
    if "PYTEST_CURRENT_TEST" in os.environ:
        return True
    return False


def _scrub_pii(event: dict, hint) -> dict | None:
    """before_send hook: strip auth headers, cookies, token-bearing query
    params, and request bodies from outgoing Sentry events.

    The function is defensive about event shape — non-HTTP events
    (scheduler errors, captured exceptions from ingestion) won't have a
    `request` key.
    """
    request = event.get("request") or {}

    # Headers — Authorization is the obvious leak; Cookie defensively
    # (Pinakes sets none, but third-party tooling could).
    headers = request.get("headers")
    if isinstance(headers, dict):
        for h in ("Authorization", "Cookie"):
            if h in headers:
                headers[h] = "[FILTERED]"

    # Query string — redact any param whose name contains "token".
    qs = request.get("query_string")
    if isinstance(qs, str) and qs:
        request["query_string"] = re.sub(
            r"([^&=]*token[^&=]*)=([^&]*)",
            r"\1=[FILTERED]",
            qs,
            flags=re.IGNORECASE,
        )
    elif isinstance(qs, list):
        request["query_string"] = [
            (k, "[FILTERED]") if isinstance(k, str) and "token" in k.lower() else (k, v)
            for k, v in qs
        ]

    # Request body — not interesting; possibly sensitive (search queries).
    if "data" in request:
        request["data"] = None

    if request:
        event["request"] = request
    return event


def init_sentry(component: str) -> bool:
    """Initialise Sentry for one process. Idempotent. Returns True if
    Sentry was actually initialised, False if skipped (no DSN, test env).

    `component` is one of "web", "scheduler", "cli". Tagged on every
    event so the Sentry dashboard can split them.
    """
    global _initialised
    if _initialised:
        return True

    dsn = os.environ.get(DSN_ENV_VAR, "").strip()
    if not dsn:
        log.debug("Sentry: %s unset, skipping init.", DSN_ENV_VAR)
        return False
    if _is_test_env():
        log.debug("Sentry: test environment detected, skipping init.")
        return False

    # Lazy import — keeps this module cheap to import in tests where the
    # DSN check short-circuits.
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration

    sentry_sdk.init(
        dsn=dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.01,    # 1% performance sampling
        profiles_sample_rate=0.0,   # off — preserves quota
        send_default_pii=False,
        release=os.environ.get("FLY_RELEASE_VERSION", "dev"),
        environment=os.environ.get("FLY_APP_NAME", "local"),
        before_send=_scrub_pii,
    )
    sentry_sdk.set_tag("component", component)
    _initialised = True
    log.info("Sentry initialised for component=%s", component)
    return True


def capture_fetcher_error(source: str, journal: str | None, exc: Exception) -> None:
    """Report an ingestion error to Sentry with source/journal tags.

    No-op when Sentry hasn't been initialised (DSN missing or test env).
    Callers always log the exception themselves — this is supplementary
    structured reporting, not a replacement for log.error.

    `source`  — one of "crossref", "rss", "scrape", "openalex", "citations".
    `journal` — the journal name where known, else None (e.g., for the
                citation backfill which iterates articles, not journals).
    """
    if not _initialised:
        return

    try:
        import sentry_sdk
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("source", source)
            if journal:
                scope.set_tag("journal", journal)
            sentry_sdk.capture_exception(exc)
    except Exception as inner:  # noqa: BLE001 — Sentry failures must not crash callers
        log.debug("Sentry capture failed: %s", inner)
