"""usage/capture.py — record each request, cheaply, without slowing it down.

after_request builds one row and appends it to an in-memory queue; a daemon
thread writes the queue to usage.db every few seconds. A request never waits
on a disk write, and a usage.db problem can never fail a page: every step is
wrapped, and the worst case is a lost row.

Recording is off unless PINAKES_USAGE_TRACKING is truthy, so this code can
ship before the privacy notice and the Clearinghouse's sign-off are in place.
"""

from __future__ import annotations

import atexit
import collections
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from urllib.parse import urlsplit

from flask import g, request

from . import store
from .tools import classify, should_skip
from .ua import parse as parse_ua

log = logging.getLogger(__name__)

ENABLE_ENV = "PINAKES_USAGE_TRACKING"
SYNC_ENV = "PINAKES_USAGE_SYNC"      # tests: write in the request instead of the thread
FLUSH_SECONDS = 5.0

# Query parameters never stored: OAuth codes, tokens, contact details, and
# cache-busters.
_DENY_PARAMS = frozenset({
    "code", "state", "token", "email", "password", "key", "secret",
    "access_token", "id_token", "_cb", "cb", "_",
})
# Path variables kept as the `object` of a hit (an article id, an author's
# name). Tokens in alerts and redaction URLs are deliberately absent.
_OBJECT_KEYS = ("article_id", "book_id", "institution_id", "name", "slug",
                "gslug", "doi", "filename")
# Hosts that are Pinakes itself; a referrer from one of these is internal.
_OWN_HOSTS = {"pinakes.wacclearinghouse.org", "testpinakes.wacclearinghouse.org",
              "pinakes.xyz", "www.pinakes.xyz", "rhet-comp-index.fly.dev"}

_queue: collections.deque = collections.deque(maxlen=20000)
_flusher: threading.Thread | None = None
_flusher_lock = threading.Lock()
_wake = threading.Event()

_HIT_COLUMNS = (
    "ts", "day", "host", "method", "endpoint", "rule", "area", "tool", "object",
    "params", "query", "status", "ms", "is_api", "ref_host", "ref_path", "lang",
    "ua_family", "ua_os", "ua_device", "bot", "bot_kind", "feed_subs",
    "visitor", "gpc", "ip", "cf_country", "cf_region", "cf_city",
)


def enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _sync() -> bool:
    return os.environ.get(SYNC_ENV, "").strip().lower() in ("1", "true", "yes")


# ── Hooks ────────────────────────────────────────────────────────────────────

def before_request():
    g._usage_t0 = time.perf_counter()


def after_request(response):
    try:
        if enabled() and _should_record(response):
            row = _build_hit(response)
            if row is not None:
                _enqueue("hit", row)
    except Exception:  # noqa: BLE001 — recording must never break a page
        log.debug("usage capture failed", exc_info=True)
    return response


def _should_record(response) -> bool:
    if request.method in ("HEAD", "OPTIONS"):
        return False
    endpoint = request.endpoint
    if endpoint is None:
        return False            # 404 probes for wp-login.php and friends
    if should_skip(endpoint) or request.path.startswith(("/admin/", "/api/admin/")):
        return False
    if response.status_code == 403:
        return False            # denylisted networks and origin-lock refusals
    if endpoint == "jwa.jwa_file":
        filename = (request.view_args or {}).get("filename", "index.html")
        if not filename.endswith(".html"):
            return False        # the webtext's own scripts and images
    return True


# ── Building a row ───────────────────────────────────────────────────────────

def client_context() -> dict:
    """The per-request facts that hits and browser events share: who (as a
    daily visitor id), when, and the raw IP for the resolver."""
    from rate_limit import client_ip_key
    now = store.utc_now()
    day = now.date().isoformat()
    ip = client_ip_key()
    ua = request.headers.get("User-Agent", "")
    host = (request.host or "").split(":")[0].lower()
    visitor = hmac.new(store.salt_for(day), f"{ip}|{ua}|{host}".encode("utf-8"),
                       hashlib.sha256).hexdigest()[:16]
    gpc = request.headers.get("Sec-GPC") == "1" or request.headers.get("DNT") == "1"
    return {"ts": now.isoformat(timespec="seconds"), "day": day, "host": host,
            "ip": ip, "ua": ua, "visitor": visitor, "gpc": 1 if gpc else 0}


def _params() -> tuple[str | None, str | None]:
    kept = {}
    for k in list(request.args.keys())[:20]:
        if k.lower() in _DENY_PARAMS or len(k) > 40:
            continue
        v = request.args.get(k, "")
        if v == "":
            continue
        kept[k] = v[:120]
    q = (request.args.get("q") or "").strip()[:160] or None
    kept.pop("q", None)
    return (json.dumps(kept, sort_keys=True, ensure_ascii=False) if kept else None), q


def _object() -> str | None:
    va = request.view_args or {}
    for k in _OBJECT_KEYS:
        if k in va and va[k] not in (None, ""):
            return str(va[k])[:200]
    if request.endpoint == "main.export" and request.args.get("article_id"):
        return request.args.get("article_id", "")[:20]
    return None


def _referrer(own_host: str) -> tuple[str | None, str | None]:
    ref = request.headers.get("Referer", "")
    if not ref:
        return None, None
    try:
        parts = urlsplit(ref)
    except ValueError:
        return None, None
    host = (parts.hostname or "").lower()
    if not host:
        return None, None
    if host == own_host or host in _OWN_HOSTS:
        return None, (parts.path or "/")[:200]
    if host.startswith("www."):
        host = host[4:]
    return host[:120], None


def _lang() -> str | None:
    raw = request.headers.get("Accept-Language", "")
    first = raw.split(",")[0].split(";")[0].strip()
    if not first or first == "*":
        return None
    parts = first.split("-")
    out = parts[0].lower()[:3]
    if len(parts) > 1 and parts[1]:
        out += "-" + parts[1].upper()[:3]
    return out


def _build_hit(response) -> dict | None:
    ctx = client_context()
    endpoint = request.endpoint
    t = classify(endpoint, request.path)
    tool_key, area = t.tool, t.area
    if endpoint == "main.export":
        fmt = (request.args.get("format") or "bibtex").lower()
        tool_key = "export-ris" if fmt == "ris" else "export-bibtex"
    params, q = _params()
    if endpoint == "main.index" and q:
        area = "search"
    ref_host, ref_path = _referrer(ctx["host"])
    ua = parse_ua(ctx["ua"])
    t0 = getattr(g, "_usage_t0", None)
    ms = int((time.perf_counter() - t0) * 1000) if t0 else None
    rule = request.url_rule.rule if request.url_rule is not None else None
    return {
        "ts": ctx["ts"], "day": ctx["day"], "host": ctx["host"],
        "method": request.method, "endpoint": endpoint, "rule": rule,
        "area": area, "tool": tool_key, "object": _object(),
        "params": params, "query": q, "status": response.status_code, "ms": ms,
        "is_api": 1 if request.path.startswith("/api/") else 0,
        "ref_host": ref_host, "ref_path": ref_path, "lang": _lang(),
        "ua_family": ua["family"], "ua_os": ua["os"], "ua_device": ua["device"],
        "bot": ua["bot"], "bot_kind": ua["bot_kind"], "feed_subs": ua["feed_subs"],
        "visitor": ctx["visitor"], "gpc": ctx["gpc"], "ip": ctx["ip"],
        "cf_country": _cf("CF-IPCountry", 2), "cf_region": _cf("cf-region", 80),
        "cf_city": _cf("cf-ipcity", 80),
    }


def _cf(header: str, n: int) -> str | None:
    v = (request.headers.get(header) or "").strip()
    if not v or v in ("XX", "T1"):     # unknown / Tor, in Cloudflare's codes
        return None
    return v[:n]


# ── Queue and writer ─────────────────────────────────────────────────────────

def _enqueue(kind: str, row: dict) -> None:
    if _sync():
        _write([(kind, row)])
        return
    _queue.append((kind, row))
    _ensure_flusher()


def _ensure_flusher() -> None:
    global _flusher
    if _flusher is not None and _flusher.is_alive():
        return
    with _flusher_lock:
        if _flusher is not None and _flusher.is_alive():
            return
        _flusher = threading.Thread(target=_flush_loop, name="usage-flusher", daemon=True)
        _flusher.start()


def _flush_loop() -> None:
    while True:
        _wake.wait(FLUSH_SECONDS)
        _wake.clear()
        try:
            flush()
        except Exception:  # noqa: BLE001
            log.warning("usage flush failed", exc_info=True)


def flush() -> int:
    """Write everything queued so far. Returns the number of rows written."""
    batch = []
    while _queue and len(batch) < 5000:
        batch.append(_queue.popleft())
    if batch:
        _write(batch)
    return len(batch)


def _write(batch: list[tuple[str, dict]]) -> None:
    hits = [tuple(r.get(c) for c in _HIT_COLUMNS) for k, r in batch if k == "hit"]
    events = [r for k, r in batch if k == "event"]
    conn = store.connect()
    try:
        with conn:
            if hits:
                conn.executemany(
                    f"INSERT INTO hits ({', '.join(_HIT_COLUMNS)}) "
                    f"VALUES ({', '.join('?' * len(_HIT_COLUMNS))})",
                    hits,
                )
            if events:
                conn.executemany(
                    "INSERT INTO events (ts, day, visitor, type, page, area, tool, target, value, pv) "
                    "VALUES (:ts, :day, :visitor, :type, :page, :area, :tool, :target, :value, :pv)",
                    events,
                )
    finally:
        conn.close()


def record_event(row: dict) -> None:
    """Queue one browser event (built by the /api/e endpoint)."""
    _enqueue("event", row)


@atexit.register
def _flush_at_exit() -> None:
    try:
        flush()
    except Exception:  # noqa: BLE001
        pass
