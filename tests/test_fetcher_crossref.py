"""Characterization tests for fetcher.py — CrossRef parser helpers and the
fetch_journal entry point. HTTP layer is stubbed via responses; no network."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import responses

import fetcher


FIXTURE = Path(__file__).parent / "fixtures" / "crossref_sample.json"


def _load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ── Pure parsers ─────────────────────────────────────────────────────────────


def test_parse_date_prefers_published_print():
    item = _load_fixture()["message"]["items"][0]
    assert fetcher._parse_date(item) == "2024-03-15"


def test_parse_date_falls_back_to_published_online():
    item = _load_fixture()["message"]["items"][1]
    assert fetcher._parse_date(item) == "2024-02-01"


def test_parse_date_falls_back_to_issued():
    item = _load_fixture()["message"]["items"][2]
    # issued: [[2023, 11]] → fills missing day with 1
    assert fetcher._parse_date(item) == "2023-11-01"


def test_parse_date_no_dates_returns_none():
    assert fetcher._parse_date({}) is None


def test_parse_authors_semicolon_separated():
    item = _load_fixture()["message"]["items"][0]
    assert fetcher._parse_authors(item) == "Jane Smith; John Adams"


def test_parse_authors_handles_family_only():
    item = _load_fixture()["message"]["items"][2]
    # Includes one author with no 'given' — family-only
    res = fetcher._parse_authors(item)
    assert "Anonymous" in res
    assert "Carlos Diaz" in res


def test_parse_authors_empty_list_returns_none():
    assert fetcher._parse_authors({"author": []}) is None


def test_parse_abstract_strips_jats_tags():
    item = _load_fixture()["message"]["items"][0]
    res = fetcher._parse_abstract(item)
    assert "<jats:p>" not in res
    assert "<jats:italic>" not in res
    assert "uncertainty" in res


def test_parse_abstract_missing_returns_none():
    item = _load_fixture()["message"]["items"][3]
    # Item 4 has no abstract field
    assert fetcher._parse_abstract(item) is None


def test_parse_abstract_only_tags_returns_none():
    """Abstract that's only HTML/JATS tags strips to empty → None."""
    assert fetcher._parse_abstract({"abstract": "<jats:p></jats:p>"}) is None


# ── fetch_journal end-to-end with stubbed HTTP ───────────────────────────────


@responses.activate
def test_fetch_journal_inserts_articles(seeded_db):
    fixture = _load_fixture()
    # Fetch returns 5 items, then a second call returns no items (stop).
    responses.add(
        responses.GET, fetcher.CROSSREF_BASE,
        json=fixture, status=200,
    )
    # Pre-existing articles in seed do not collide with these fixture DOIs
    # (10.1234/test.000NN), so all 5 should insert.
    inserted = 0
    with patch.object(fetcher, "upsert_article", wraps=fetcher.upsert_article) as upsert_mock:
        n = fetcher.fetch_journal(issn="0010-0994", since_date=None)
        inserted = upsert_mock.call_count
    assert n == 5  # 5 fixture items insert
    assert inserted == 5


@responses.activate
def test_fetch_journal_skips_items_without_doi():
    """An item missing a DOI is silently skipped — does not crash."""
    payload = {
        "message": {
            "items": [
                {"DOI": "", "title": ["No DOI"]},
                {"DOI": "10.test/ok", "title": ["OK"]},
            ],
            "next-cursor": None,
        }
    }
    responses.add(responses.GET, fetcher.CROSSREF_BASE, json=payload, status=200)
    with patch.object(fetcher, "upsert_article") as upsert_mock:
        upsert_mock.return_value = 1
        fetcher.fetch_journal(issn="0010-0994", since_date=None)
    # Only the one with a DOI got upserted
    assert upsert_mock.call_count == 1


@responses.activate
def test_fetch_journal_handles_request_failure():
    """A 500 from CrossRef logs an error and returns gracefully (no crash)."""
    responses.add(responses.GET, fetcher.CROSSREF_BASE, status=500)
    n = fetcher.fetch_journal(issn="0010-0994", since_date=None)
    assert n == 0


@responses.activate
def test_fetch_journal_empty_items_terminates():
    responses.add(
        responses.GET, fetcher.CROSSREF_BASE,
        json={"message": {"items": [], "next-cursor": None}},
        status=200,
    )
    n = fetcher.fetch_journal(issn="0010-0994", since_date=None)
    assert n == 0


# ── Malformed input ──────────────────────────────────────────────────────────


def test_parse_date_invalid_date_parts_returns_none():
    """Malformed date_parts like [[1900, 13, 99]] should not crash."""
    item = {"published-print": {"date-parts": [[1900, 13, 99]]}}
    assert fetcher._parse_date(item) is None


def test_parse_authors_with_no_name_fields():
    """An author entry with neither given nor family is dropped."""
    item = {"author": [{"given": "", "family": ""}]}
    assert fetcher._parse_authors(item) is None
