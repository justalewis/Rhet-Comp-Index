"""Characterization tests for rss_fetcher.py — feedparser-based parser
helpers. The full fetch path (which dispatches to feedparser, OAI-PMH, or
WordPress backends) is exercised lightly via stubbed feedparser."""

from time import struct_time
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

import pytest

import rss_fetcher


FIXTURE = Path(__file__).parent / "fixtures" / "rss_sample.xml"


# ── _parse_date ──────────────────────────────────────────────────────────────


def test_parse_date_from_published_parsed():
    entry = SimpleNamespace(
        published_parsed=struct_time((2024, 4, 1, 12, 0, 0, 0, 92, 0)),
    )
    assert rss_fetcher._parse_date(entry) == "2024-04-01"


def test_parse_date_falls_back_to_string():
    entry = SimpleNamespace(published="Mon, 01 Apr 2024 12:00:00 +0000")
    res = rss_fetcher._parse_date(entry)
    # Function regex-matches YYYY-MM-DD or YYYY; "01 Apr 2024" has no ISO date
    assert res == "2024"


def test_parse_date_iso_in_string():
    entry = SimpleNamespace(published="2024-04-01T12:00:00Z")
    assert rss_fetcher._parse_date(entry) == "2024-04-01"


def test_parse_date_missing_fields_returns_none():
    entry = SimpleNamespace()
    assert rss_fetcher._parse_date(entry) is None


# ── _parse_authors ───────────────────────────────────────────────────────────


def test_parse_authors_authors_list():
    entry = SimpleNamespace(authors=[{"name": "Aisha Bell"}, {"name": "Carlos Diaz"}])
    assert rss_fetcher._parse_authors(entry) == "Aisha Bell; Carlos Diaz"


def test_parse_authors_single_author_attr():
    """Some feeds expose a single string in entry.author when there's only one."""
    entry = SimpleNamespace(author="Hannah Iyer")
    # No `authors` attr means we fall through to entry.author. The function's
    # `hasattr(entry, "authors")` check is True for SimpleNamespace though —
    # so skip authors=. Use a class without authors:
    class E:
        author = "Hannah Iyer"
    assert rss_fetcher._parse_authors(E()) == "Hannah Iyer"


def test_parse_authors_empty_returns_none():
    class E:
        pass
    assert rss_fetcher._parse_authors(E()) is None


# ── _strip_html ──────────────────────────────────────────────────────────────


def test_strip_html_removes_tags():
    assert rss_fetcher._strip_html("<p>Hello <b>world</b>!</p>") == "Hello world !"


def test_strip_html_collapses_whitespace():
    assert rss_fetcher._strip_html("foo\n\n   bar\t\tbaz") == "foo bar baz"


def test_strip_html_empty_returns_none():
    assert rss_fetcher._strip_html("") is None
    assert rss_fetcher._strip_html(None) is None


# ── _parse_abstract ──────────────────────────────────────────────────────────


def test_parse_abstract_content_field_preferred():
    long_html = "<p>" + "abstract content " * 20 + "</p>"
    class E:
        content = [{"value": long_html}]
        summary = "summary fallback"
    res = rss_fetcher._parse_abstract(E())
    assert "abstract content" in res
    assert "summary fallback" not in res


def test_parse_abstract_short_summary_returns_none():
    """Abstracts < 80 chars are dropped (probably a byline)."""
    class E:
        summary = "A byline."
    assert rss_fetcher._parse_abstract(E()) is None


def test_parse_abstract_truncated_to_2000():
    long_html = "<p>" + "x " * 2000 + "</p>"
    class E:
        summary = long_html
    res = rss_fetcher._parse_abstract(E())
    assert res is not None
    assert len(res) <= rss_fetcher.ABSTRACT_MAX + 1  # +1 for the ellipsis


# ── feedparser-stubbed end-to-end ───────────────────────────────────────────


def test_fetch_rss_journal_with_stubbed_feedparser(seeded_db):
    """Stub feedparser.parse so no network is touched. Verify upsert_article
    is called once per fixture entry."""
    fake_feed = SimpleNamespace(
        bozo=False,
        entries=[
            SimpleNamespace(
                title="Public memory and the digital archive",
                link="https://www.presenttensejournal.org/post/2024-04-01-memory",
                published="2024-04-01",
                published_parsed=struct_time((2024, 4, 1, 12, 0, 0, 0, 92, 0)),
                summary="<p>This essay explores public memory in digital archives, " * 5,
                authors=[{"name": "Aisha Bell"}],
            ),
            SimpleNamespace(
                title="Disability, access, and writing infrastructure",
                link="https://www.presenttensejournal.org/post/2024-03-15-access",
                published="2024-03-15",
                published_parsed=struct_time((2024, 3, 15, 12, 0, 0, 0, 75, 0)),
                summary="An argument about access and infrastructure in writing studies. " * 5,
                authors=[{"name": "Hannah Iyer"}],
            ),
        ],
    )
    journal = {
        "name": "Present Tense: A Journal of Rhetoric in Society",
        "feed_url": "https://www.presenttensejournal.org/feed/",
    }
    with patch.object(rss_fetcher, "feedparser") as fp_mock, \
         patch.object(rss_fetcher, "upsert_article") as upsert_mock:
        fp_mock.parse.return_value = fake_feed
        upsert_mock.return_value = 1
        n = rss_fetcher.fetch_rss_journal(journal)
    assert upsert_mock.call_count == 2
    assert n == 2
