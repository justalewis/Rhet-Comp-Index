"""Tests for mem_watchdog — the self-healing memory guard (incident 2026-06-26).

The watchdog recycles the gunicorn worker when its footprint crosses a
threshold, but ONLY when no fetch/maintenance job is running. These tests pin
the footprint parsing and, critically, the job-aware deferral that keeps a long
background writer from being killed mid-flight.
"""

import builtins
import io
import os
import signal

import pytest

import mem_watchdog


def test_read_footprint_sums_rss_and_swap(monkeypatch):
    fake = "VmPeak:\t100 kB\nVmRSS:\t1048576 kB\nVmSwap:\t524288 kB\n"
    monkeypatch.setattr(builtins, "open", lambda *a, **k: io.StringIO(fake))
    # (1048576 + 524288) kB = 1572864 kB = 1536 MB
    assert mem_watchdog.read_footprint_mb() == pytest.approx(1536.0)


def test_read_footprint_none_when_proc_unavailable(monkeypatch):
    def boom(*a, **k):
        raise OSError("no /proc here (e.g. Windows dev)")
    monkeypatch.setattr(builtins, "open", boom)
    assert mem_watchdog.read_footprint_mb() is None


def test_recycle_defers_while_a_job_holds_the_lock(monkeypatch):
    """The single most important behaviour: never SIGTERM the worker while a
    fetch/maintenance writer is running."""
    import app
    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))

    assert app._fetch_lock.acquire(blocking=False), "lock should be free at test start"
    try:
        triggered = mem_watchdog._recycle(9999, 1400)
    finally:
        app._fetch_lock.release()

    assert triggered is False
    assert killed == [], "must not signal the worker while a job is running"


def test_recycle_signals_sigterm_when_idle(monkeypatch):
    import app
    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append((pid, sig)))

    assert not app._fetch_lock.locked(), "lock should be free at test start"
    try:
        triggered = mem_watchdog._recycle(9999, 1400)
        assert triggered is True
        assert killed == [(os.getpid(), signal.SIGTERM)]
    finally:
        # _recycle intentionally holds the lock through the (mocked) signal so a
        # fetch can't start in the race window; release it so other tests run.
        if app._fetch_lock.locked():
            app._fetch_lock.release()


def test_start_is_disabled_when_threshold_zero(monkeypatch):
    monkeypatch.setenv("PINAKES_MEM_WATCHDOG_MB", "0")
    mem_watchdog._started = False
    mem_watchdog.start()
    assert mem_watchdog._started is False, "threshold 0 must disable the watchdog"
