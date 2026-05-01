"""datastories_cache.py — server-side memoization for slow Datastories tools.

Several Datastories analyses (Hummon-Doreian main paths, Louvain across
decades, exact betweenness on the full corpus) take 30-90 seconds end to
end — too slow to compute on every request. Flask-Limiter's HTTP cache is
client-side only, so we add a simple disk-backed function cache keyed by
(function name, args) and invalidated when articles.db's mtime changes.

Cache files live at /data/datastories_cache/<key>.json. The DB-mtime
fingerprint means a daily fetch automatically invalidates everything; no
manual cache-bust step is needed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from functools import wraps
from pathlib import Path

log = logging.getLogger(__name__)


def _cache_dir() -> Path:
    """Locate the cache dir. Prefer /data (Fly volume) when present, else
    repo-relative ./data/datastories_cache for local dev."""
    fly = Path("/data")
    if fly.is_dir() and os.access(fly, os.W_OK):
        out = fly / "datastories_cache"
    else:
        out = Path(__file__).resolve().parent / "data" / "datastories_cache"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _db_fingerprint() -> str:
    """A short token that changes when the DB file changes. Combines mtime
    and size so a swap-in restore doesn't return stale results."""
    from db import DB_PATH
    try:
        st = os.stat(DB_PATH)
        return f"{int(st.st_mtime)}-{st.st_size}"
    except OSError:
        return "0-0"


def _key(func_name: str, args: tuple, kwargs: dict) -> str:
    payload = json.dumps(
        {"f": func_name, "a": args, "k": sorted(kwargs.items())},
        sort_keys=True, default=str,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _path_for(func_name: str, key: str) -> Path:
    return _cache_dir() / f"{func_name}-{key}.json"


def cached(func_name: str | None = None, max_entries: int = 24):
    """Decorator: memoize the function's return value to disk. Cache is
    invalidated when the DB fingerprint changes. The wrapped function MUST
    return a JSON-serialisable object (dict/list/scalars).

    `max_entries` caps how many cache files we keep per function name (LRU
    by mtime). The customization layer lets users hit a heavy tool with
    arbitrary (cluster, journals, year-from, year-to) combinations; without
    a cap the cache directory grows unbounded.
    """
    def decorator(fn):
        name = func_name or fn.__name__

        @wraps(fn)
        def wrapped(*args, **kwargs):
            fp = _db_fingerprint()
            key = _key(name, args, kwargs)
            path = _path_for(name, key)
            if path.is_file():
                try:
                    with path.open("r", encoding="utf-8") as f:
                        blob = json.load(f)
                    if blob.get("_fingerprint") == fp:
                        # Touch mtime so LRU eviction skips this entry on the
                        # next write. atime semantics on Windows are unreliable
                        # so we use mtime as the recency signal.
                        try:
                            os.utime(path, None)
                        except OSError:
                            pass
                        return blob["data"]
                except (OSError, json.JSONDecodeError):
                    pass  # fall through and recompute

            t0 = time.time()
            data = fn(*args, **kwargs)
            elapsed = time.time() - t0
            try:
                tmp = path.with_suffix(".tmp")
                with tmp.open("w", encoding="utf-8") as f:
                    json.dump({"_fingerprint": fp, "data": data}, f)
                tmp.replace(path)
                log.info("datastories_cache: stored %s (%.2fs, %s)",
                         name, elapsed, path.name)
                _evict_if_over(name, max_entries)
            except OSError as exc:
                log.warning("datastories_cache: write failed for %s: %s", name, exc)
            return data

        return wrapped
    return decorator


def _evict_if_over(func_name: str, max_entries: int):
    """Drop the oldest cache files for `func_name` when the count exceeds
    `max_entries`. Best-effort; errors are swallowed."""
    try:
        files = sorted(
            _cache_dir().glob(f"{func_name}-*.json"),
            key=lambda p: p.stat().st_mtime,
        )
        excess = len(files) - max_entries
        if excess <= 0:
            return
        for old in files[:excess]:
            try:
                old.unlink()
            except OSError:
                pass
        log.info("datastories_cache: evicted %d entries for %s (LRU)", excess, func_name)
    except OSError:
        pass


def clear_all():
    """Wipe every cache entry. Exposed for an admin endpoint, future use."""
    n = 0
    for p in _cache_dir().glob("*.json"):
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def get_if_cached(func_name: str, *args, **kwargs):
    """Return the cached result for (func_name, args, kwargs) if it exists
    AND its fingerprint matches the current DB state, else None. Never
    triggers a compute.

    Used by `ds_books_everyone_reads` (the master-list aggregator) to skip
    underlying heavy tools whose cache is cold — better to return a
    partial master list quickly than to chain 60–90s computes that exceed
    gunicorn's worker timeout."""
    fp = _db_fingerprint()
    key = _key(func_name, args, kwargs)
    path = _path_for(func_name, key)
    if not path.is_file():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            blob = json.load(f)
        if blob.get("_fingerprint") == fp:
            return blob["data"]
    except (OSError, json.JSONDecodeError):
        pass
    return None
