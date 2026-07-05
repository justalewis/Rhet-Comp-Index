"""Load-shedding on the expensive read endpoints (web_helpers.shed_expensive_load).

Backstop for the 2026-07-05 IP-rotating scrape flood: cap concurrent requests
to /explore, /export, /citations, /article/, /author/ below the worker thread
count so a flood can't occupy every thread. When the cap is full, expensive
requests get an instant 503 + Retry-After; cheap paths and health are never
shed; and a served request must free its slot so the next one gets through.
"""

import threading

import pytest

import web_helpers


@pytest.mark.parametrize("path,expensive", [
    ("/explore", True),
    ("/export", True),
    ("/citations", True),
    ("/article/5", True),
    ("/author/Jane%20Doe", True),
    # Cheap siblings and unrelated paths must NOT be shed.
    ("/articles", False),
    ("/authors", False),
    ("/api/citations/ego", False),
    ("/api/articles", False),
    ("/", False),
    ("/health", False),
])
def test_path_classification(path, expensive):
    assert web_helpers._is_expensive_path(path) is expensive


def test_sheds_expensive_path_when_cap_full(client, monkeypatch):
    """With every slot taken, an expensive request is refused instantly."""
    drained = threading.BoundedSemaphore(1)
    drained.acquire()  # cap now full
    monkeypatch.setattr(web_helpers, "_EXPENSIVE_SEMAPHORE", drained)

    resp = client.get("/explore")
    assert resp.status_code == 503
    # Retry-After is present and a positive integer. (Flask-Limiter's
    # headers_enabled pass may set it to the limit window rather than our 5.)
    assert int(resp.headers["Retry-After"]) > 0


def test_does_not_shed_cheap_path_when_cap_full(client, monkeypatch):
    """A saturated expensive-endpoint cap must not touch cheap paths."""
    drained = threading.BoundedSemaphore(1)
    drained.acquire()
    monkeypatch.setattr(web_helpers, "_EXPENSIVE_SEMAPHORE", drained)

    assert client.get("/health").status_code == 200
    assert client.get("/api/articles").status_code == 200


def test_slot_is_released_after_each_request(client, monkeypatch):
    """cap=1 + serial requests: every one must go through, proving the slot is
    freed in teardown. If release leaked, the 2nd request would 503."""
    monkeypatch.setattr(
        web_helpers, "_EXPENSIVE_SEMAPHORE", threading.BoundedSemaphore(1)
    )
    for _ in range(3):
        # 404 (no such article) is fine — it still runs the view and frees the
        # slot; the point is it is never a 503.
        assert client.get("/article/999999").status_code != 503
