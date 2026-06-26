"""mem_watchdog.py — Self-healing memory guard for the single gunicorn worker.

Why this exists. The app runs one gunicorn worker (gthread, 8 threads) on a
small Fly machine. CPython does not hand freed heap arenas back to the OS, so
RSS ratchets upward over days as heavy endpoints (NetworkX graph builds, large
SQLite result sets) leave high-water marks. With no headroom that ends in a
hard OOM which wedges the box — incident 2026-06-26: ~3 days to climb the 1GB
ceiling, load average 8+ on one core, 0 MB available, a multi-hour outage that
also red-failed the daily cron with 503s.

The watchdog is a daemon thread that periodically reads this process's memory
footprint (resident + swapped) and, when it crosses a threshold, asks gunicorn
to recycle the worker: it SIGTERMs itself, the master respawns a fresh worker,
and RSS drops back to baseline. It is the job-aware cousin of gunicorn's
--max-requests — it NEVER recycles while a fetch/maintenance writer is running,
because it holds app._fetch_lock across the signal, so a long background job is
never interrupted mid-flight.

No-ops where it can't help: non-Linux (no /proc/self/status), or when the
threshold env is set to 0. Safe to import anywhere; start() is idempotent.

Tunables (env):
    PINAKES_MEM_WATCHDOG_MB        recycle threshold in MB (default 1400; 0 disables)
    PINAKES_MEM_WATCHDOG_INTERVAL  seconds between checks (default 120)
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time

log = logging.getLogger(__name__)

_started = False
_started_lock = threading.Lock()


def read_footprint_mb():
    """Resident + swapped memory of THIS process in MB, or None if unknown.

    VmRSS alone undercounts under memory pressure (pages swap out and leave
    RSS), so we add VmSwap to track the true footprint.
    """
    try:
        rss = swap = 0
        with open("/proc/self/status", "r") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1])      # kB
                elif line.startswith("VmSwap:"):
                    swap = int(line.split()[1])     # kB
        return (rss + swap) / 1024.0
    except (OSError, ValueError, IndexError):
        return None


def _recycle(footprint_mb, threshold_mb):
    """Hold the fetch lock (so no writer can start), then SIGTERM ourself.

    gunicorn's master replaces an exited worker, so the single-worker invariant
    holds and RSS returns to baseline. We deliberately do NOT release the lock:
    the process is on its way out, and keeping it shut closes the race where a
    fetch starts between the lock check and the signal. SIGTERM is graceful for
    a gunicorn worker — in-flight requests finish before it exits.

    Returns True if a recycle was triggered, False if it was deferred because a
    job is running.
    """
    import app as _app
    if not _app._fetch_lock.acquire(blocking=False):
        log.info("mem-watchdog: footprint %.0f MB over %.0f MB but a "
                 "fetch/maintenance job is running — deferring recycle.",
                 footprint_mb, threshold_mb)
        return False
    log.warning("mem-watchdog: footprint %.0f MB exceeded %.0f MB and no job "
                "is running — recycling worker (pid %d).",
                footprint_mb, threshold_mb, os.getpid())
    os.kill(os.getpid(), signal.SIGTERM)
    return True


def _loop(threshold_mb, interval_s):
    time.sleep(interval_s)  # let the worker finish importing before watching
    while True:
        try:
            fp = read_footprint_mb()
            if fp is not None and fp >= threshold_mb:
                _recycle(fp, threshold_mb)
        except Exception:
            log.exception("mem-watchdog: check failed")
        time.sleep(interval_s)


def start():
    """Start the watchdog thread once. No-op if disabled, unsupported, or
    already running."""
    global _started
    threshold_mb = float(os.environ.get("PINAKES_MEM_WATCHDOG_MB", "1400"))
    interval_s = float(os.environ.get("PINAKES_MEM_WATCHDOG_INTERVAL", "120"))
    if threshold_mb <= 0:
        log.info("mem-watchdog: disabled (PINAKES_MEM_WATCHDOG_MB <= 0).")
        return
    if read_footprint_mb() is None:
        log.info("mem-watchdog: /proc/self/status unavailable — not started.")
        return
    with _started_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, args=(threshold_mb, interval_s),
                     name="mem-watchdog", daemon=True).start()
    log.info("mem-watchdog: started (threshold %.0f MB, every %.0fs).",
             threshold_mb, interval_s)
