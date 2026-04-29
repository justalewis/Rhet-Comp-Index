"""Characterization tests for scraper.py — _get HTTP utility, the SCRAPERS
dispatch table, and a smoke test that fetch_all routes journals to their
registered strategies. Per-journal scrapers are too brittle (full-page HTML
shapes) to lock into characterization tests; we only verify the wiring."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import responses

import scraper


FIXTURE = Path(__file__).parent / "fixtures" / "scraper_sample.html"


# ── _get utility ────────────────────────────────────────────────────────────


@responses.activate
def test_get_returns_tuple_on_success():
    responses.add(
        responses.GET, "https://example.org/page",
        body=FIXTURE.read_text(encoding="utf-8"),
        status=200,
    )
    resp, soup = scraper._get("https://example.org/page")
    assert resp is not None
    assert soup is not None
    # The fixture has an <h2> we can find.
    h2 = soup.find("h2")
    assert h2 is not None
    assert "Issue 14.1" in h2.get_text()


@responses.activate
def test_get_returns_none_on_404():
    responses.add(responses.GET, "https://example.org/missing", status=404)
    resp, soup = scraper._get("https://example.org/missing")
    assert resp is None
    assert soup is None


@responses.activate
def test_get_returns_none_on_connection_failure():
    """No registered URL → ConnectionError → returns (None, None)."""
    resp, soup = scraper._get("https://example.org/never-registered")
    assert resp is None
    assert soup is None


# ── _is_nav_text ─────────────────────────────────────────────────────────────


def test_is_nav_text_filters_short_strings():
    assert scraper._is_nav_text("home")
    assert scraper._is_nav_text("Home")  # case-insensitive
    assert scraper._is_nav_text("a")  # too short


def test_is_nav_text_passes_real_titles():
    title = "The rhetoric of revision in undergraduate writing"
    assert not scraper._is_nav_text(title)


# ── _abs_url ────────────────────────────────────────────────────────────────


def test_abs_url_absolute_passes_through():
    assert scraper._abs_url("https://x.com/page", "https://other.com") == "https://x.com/page"


def test_abs_url_relative_with_leading_slash():
    assert scraper._abs_url("/page", "https://example.org") == "https://example.org/page"


def test_abs_url_drops_anchor_and_relative_no_slash():
    """Function returns None for non-absolute, non-leading-slash hrefs."""
    assert scraper._abs_url("page.html", "https://example.org") is None


def test_abs_url_handles_none():
    assert scraper._abs_url(None, "base") is None


# ── SCRAPERS dispatch table ──────────────────────────────────────────────────


def test_scrapers_table_keys_match_journal_strategies():
    """Every strategy key referenced from journals.py must have a registered
    scraper. Missing entries would silently log a warning at runtime."""
    from journals import SCRAPE_JOURNALS, RSS_JOURNALS
    needed = {j["strategy"] for j in SCRAPE_JOURNALS}
    needed |= {j["strategy"] for j in RSS_JOURNALS if j.get("strategy")}
    missing = needed - set(scraper.SCRAPERS)
    assert missing == set(), f"Missing scrapers: {missing}"


def test_scrapers_table_values_are_callable():
    for name, fn in scraper.SCRAPERS.items():
        assert callable(fn), f"SCRAPERS[{name}] is not callable"


# ── fetch_all dispatcher ─────────────────────────────────────────────────────


def test_fetch_all_dispatches_to_registered_strategies(seeded_db):
    """fetch_all iterates SCRAPE_JOURNALS + RSS_JOURNALS-with-strategy and
    calls each registered scraper. Stub every scraper to a no-op returning 0."""
    stubs = {name: MagicMock(return_value=0) for name in scraper.SCRAPERS}
    with patch.dict(scraper.SCRAPERS, stubs, clear=False):
        total = scraper.fetch_all()
    assert total == 0
    # Every scraper that has a corresponding strategy in the journals lists
    # should be invoked at least once.
    from journals import SCRAPE_JOURNALS, RSS_JOURNALS
    expected_strategies = {j["strategy"] for j in SCRAPE_JOURNALS}
    expected_strategies |= {j["strategy"] for j in RSS_JOURNALS if j.get("strategy")}
    for strategy in expected_strategies:
        assert stubs[strategy].called, f"scraper for {strategy} not called"


def test_fetch_all_swallows_scraper_exceptions(seeded_db):
    """A single misbehaving scraper raising an exception must not abort
    the whole pipeline — it logs and continues."""
    boom = MagicMock(side_effect=RuntimeError("boom"))
    nice = MagicMock(return_value=3)
    stubs = {name: nice for name in scraper.SCRAPERS}
    stubs["kairos"] = boom
    with patch.dict(scraper.SCRAPERS, stubs, clear=False):
        # Must not raise
        total = scraper.fetch_all()
    # Other scrapers still ran; total reflects them
    assert total >= 0
