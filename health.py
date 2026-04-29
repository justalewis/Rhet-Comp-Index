"""health.py — Layered health checks.

Three levels modelled on Kubernetes liveness/readiness probes:

    /health        — liveness  (no DB, < 50ms; what Fly hits constantly)
    /health/ready  — readiness (lightweight DB query, < 500ms; deploy gate)
    /health/deep   — diagnostic (admin-protected, comprehensive, < 5s)

Module-level state (start time, integrity-check cache) is captured once at
import and survives the lifetime of the gunicorn worker.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from auth import admin_token_configured

log = logging.getLogger(__name__)

# ── Module-level state ──────────────────────────────────────────────────────

START_TIME = time.time()

try:
    APP_VERSION = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"],
        stderr=subprocess.DEVNULL,
    ).decode().strip()
except Exception:
    APP_VERSION = "dev"

# PRAGMA integrity_check is expensive on a large DB. Cache for 6 hours.
_INTEGRITY_CACHE: dict = {"ts": 0.0, "result": None}
_INTEGRITY_TTL = 6 * 3600

# Heartbeat staleness threshold: scheduler runs every 24h, allow 1h slack.
HEARTBEAT_STALE_SECONDS = 25 * 3600


# ── Helpers ─────────────────────────────────────────────────────────────────


def _db_path() -> str:
    """Read DB_PATH at call time (not import) so test monkeypatches work."""
    return os.environ.get("DB_PATH", "articles.db")


def _data_dir() -> str:
    """Directory containing the SQLite file. On Fly this is /data; locally
    it's the repo root or a tmp_path during tests."""
    return os.path.dirname(os.path.abspath(_db_path()))


def heartbeat_path() -> str:
    """Where scheduler.py writes its liveness marker."""
    return os.path.join(_data_dir(), "scheduler.heartbeat")


def write_heartbeat() -> None:
    """Write the current ISO timestamp to the heartbeat file. Failures are
    swallowed — heartbeat write must never crash the scheduler."""
    try:
        path = heartbeat_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(datetime.now(timezone.utc).isoformat())
    except Exception as exc:  # noqa: BLE001
        log.warning("heartbeat write failed: %s", exc)


def _heartbeat_age_seconds() -> float | None:
    """Seconds since the heartbeat file was last written, or None if absent."""
    path = heartbeat_path()
    if not os.path.exists(path):
        return None
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return None


def _run_integrity_check() -> list[str]:
    """Run PRAGMA integrity_check. Returns the result rows ('ok' on healthy
    DBs; otherwise a list of inconsistencies). Mocked in tests."""
    with sqlite3.connect(_db_path(), timeout=2.0) as conn:
        return [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]


def _cached_integrity_check() -> list[str]:
    """Cached wrapper — re-runs the integrity check at most every 6 hours."""
    now = time.time()
    cached = _INTEGRITY_CACHE.get("result")
    if cached is not None and now - _INTEGRITY_CACHE["ts"] < _INTEGRITY_TTL:
        return cached
    try:
        result = _run_integrity_check()
    except Exception as exc:  # noqa: BLE001
        result = [f"integrity check failed: {exc}"]
    _INTEGRITY_CACHE["result"] = result
    _INTEGRITY_CACHE["ts"] = now
    return result


def clear_integrity_cache() -> None:
    """Reset the cached integrity-check result. Used by test fixtures."""
    _INTEGRITY_CACHE["result"] = None
    _INTEGRITY_CACHE["ts"] = 0.0


# ── Public probes ───────────────────────────────────────────────────────────


def liveness() -> dict:
    """Liveness probe. No DB query; should return in <1ms.

    Returns the canonical {status, version, uptime_seconds} plus admin_auth
    so external monitoring can verify the secret landed without holding the
    token (verifying auth-config state without auth is the whole point of
    surfacing it here)."""
    return {
        "status": "ok",
        "version": APP_VERSION,
        "uptime_seconds": round(time.time() - START_TIME, 3),
        "admin_auth": "configured" if admin_token_configured() else "missing",
    }


def readiness() -> tuple[dict, int]:
    """Readiness probe. Verifies the SQLite file exists and answers a trivial
    query. Connection is opened with a 250ms busy-timeout so a locked DB
    fails fast rather than blocking Fly's health-check loop."""
    path = _db_path()
    if not os.path.exists(path):
        return ({"status": "degraded", "db": f"missing: {path}"}, 503)

    try:
        with sqlite3.connect(path, timeout=0.25) as conn:
            conn.execute("SELECT 1 FROM articles LIMIT 1").fetchone()
    except Exception as exc:  # noqa: BLE001
        return ({"status": "degraded", "db": str(exc)}, 503)

    return ({"status": "ok", "db": "reachable"}, 200)


def deep_diagnostic() -> dict:
    """Comprehensive operational view. Admin-protected at the route layer."""
    db_path = _db_path()

    counts = {"articles": None, "books": None, "authors": None}
    last_fetched_at = None
    db_error = None

    try:
        with sqlite3.connect(db_path, timeout=1.0) as conn:
            counts["articles"] = conn.execute(
                "SELECT COUNT(*) FROM articles"
            ).fetchone()[0]
            counts["books"] = conn.execute(
                "SELECT COUNT(*) FROM books WHERE record_type='book'"
            ).fetchone()[0]
            counts["authors"] = conn.execute(
                "SELECT COUNT(*) FROM authors"
            ).fetchone()[0]
            row = conn.execute(
                "SELECT MAX(fetched_at) FROM articles"
            ).fetchone()
            last_fetched_at = row[0] if row else None
    except Exception as exc:  # noqa: BLE001
        db_error = str(exc)

    # Disk space on the data volume.
    try:
        usage = shutil.disk_usage(_data_dir())
        disk = {
            "total_gb": round(usage.total / 1024**3, 2),
            "free_gb":  round(usage.free  / 1024**3, 2),
            "used_pct": round(usage.used  / usage.total * 100, 1),
        }
    except Exception as exc:  # noqa: BLE001
        disk = {"error": str(exc)}

    # Scheduler heartbeat
    age = _heartbeat_age_seconds()
    scheduler = {
        "heartbeat_age_seconds": round(age, 1) if age is not None else None,
        "scheduler_healthy": age is not None and age < HEARTBEAT_STALE_SECONDS,
        "heartbeat_path": heartbeat_path(),
    }

    integrity = _cached_integrity_check()

    return {
        "status": "ok" if db_error is None else "degraded",
        "version": APP_VERSION,
        "uptime_seconds": round(time.time() - START_TIME, 3),
        "counts": counts,
        "last_fetched_at": last_fetched_at,
        "db_error": db_error,
        "disk": disk,
        "scheduler": scheduler,
        "integrity_check": integrity,
        "auth": {
            "admin_auth": "configured" if admin_token_configured() else "missing",
        },
        "security_headers": {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self' 'unsafe-inline' "
                "cdn.jsdelivr.net gc.zgo.at; style-src 'self' 'unsafe-inline' "
                "fonts.googleapis.com; font-src fonts.gstatic.com; "
                "img-src 'self' data:; connect-src 'self'"
            ),
        },
    }
