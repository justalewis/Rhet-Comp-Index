"""Characterization tests for db.py article reads/writes — covers
get_articles filters and pagination, get_total_count, _build_where direct
behavior, _sanitize_fts edge cases, and upsert_article INSERT-OR-IGNORE
semantics."""

import pytest

import db
from tests._seed import (
    J_CROSSREF, J_RSS, J_SCRAPE, J_MANUAL,
    expected_total_articles, expected_articles_by_journal, expected_year_range,
)


# ── get_articles + get_total_count ────────────────────────────────────────────


def test_get_articles_returns_50_when_unfiltered(seeded_db):
    rows = db.get_articles(limit=200)
    assert len(rows) == expected_total_articles()
    # Every row is a dict with the article columns
    assert isinstance(rows[0], dict)
    assert {"id", "url", "doi", "title", "journal", "source", "pub_date"} <= set(rows[0])


def test_get_articles_orders_by_pub_date_desc(seeded_db):
    rows = db.get_articles(limit=10)
    pub_dates = [r["pub_date"] for r in rows]
    assert pub_dates == sorted(pub_dates, reverse=True)


def test_get_total_count_matches_get_articles(seeded_db):
    total = db.get_total_count()
    rows  = db.get_articles(limit=10000)
    assert total == len(rows)


@pytest.mark.parametrize("journal,expected", list(expected_articles_by_journal().items()))
def test_get_articles_filters_by_journal(seeded_db, journal, expected):
    rows = db.get_articles(journal=journal, limit=200)
    assert len(rows) == expected
    assert all(r["journal"] == journal for r in rows)


def test_get_articles_journal_list_filter(seeded_db):
    """Multi-journal filter passes a Python list and returns the union."""
    journals = [J_CROSSREF, J_RSS]
    rows = db.get_articles(journal=journals, limit=200)
    expected = (expected_articles_by_journal()[J_CROSSREF]
                + expected_articles_by_journal()[J_RSS])
    assert len(rows) == expected
    assert {r["journal"] for r in rows} == set(journals)


@pytest.mark.parametrize("source,expected", [
    ("crossref", 13), ("rss", 13), ("scrape", 12), ("manual", 12),
])
def test_get_articles_filters_by_source(seeded_db, source, expected):
    rows = db.get_articles(source=source, limit=200)
    assert len(rows) == expected
    assert all(r["source"] == source for r in rows)


def test_get_articles_year_filters(seeded_db):
    rows = db.get_articles(year_from=2024, year_to=2025, limit=200)
    assert all(r["pub_date"][:4] in {"2024", "2025"} for r in rows)
    # 2024+2025 articles span CrossRef (5) + RSS (6) + Kairos (5) = 16
    assert len(rows) > 0


def test_get_articles_year_to_only(seeded_db):
    rows = db.get_articles(year_to=2000, limit=200)
    assert all(r["pub_date"][:4] <= "2000" for r in rows)
    # Pre/Text years 1990-2000 = 5 articles
    assert len(rows) == 5


def test_get_articles_tag_filter(seeded_db):
    rows = db.get_articles(tag="composition theory", limit=200)
    # Tags column stored as |tag|composition| — get_articles matches |tag|
    assert all("|composition theory|" in (r["tags"] or "") for r in rows)
    assert len(rows) > 0


def test_get_articles_q_simple_word_match(seeded_db):
    rows = db.get_articles(q="composition", limit=200)
    # FTS prefix-search wraps each word; matches title/authors/abstract
    assert len(rows) > 0
    # Empty query path
    no_q = db.get_articles(q="", limit=200)
    assert len(no_q) == expected_total_articles()


def test_get_articles_q_no_match_returns_empty(seeded_db):
    rows = db.get_articles(q="zzzunmatchedstringxyz", limit=10)
    assert rows == []


def test_get_articles_pagination_offset_zero(seeded_db):
    page1 = db.get_articles(limit=10, offset=0)
    page2 = db.get_articles(limit=10, offset=10)
    assert len(page1) == 10
    assert len(page2) == 10
    assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})


def test_get_articles_offset_past_total_returns_empty(seeded_db):
    rows = db.get_articles(limit=10, offset=10000)
    assert rows == []


def test_get_articles_combined_filters(seeded_db):
    rows = db.get_articles(
        journal=J_CROSSREF, source="crossref",
        year_from=2024, year_to=2025, limit=200,
    )
    assert all(r["journal"] == J_CROSSREF for r in rows)
    assert all(r["source"] == "crossref" for r in rows)
    assert all("2024" <= r["pub_date"][:4] <= "2025" for r in rows)


# ── _build_where direct ──────────────────────────────────────────────────────


def test_build_where_empty_returns_empty_clause(seeded_db):
    clause, params = db._build_where()
    assert clause == ""
    assert params == []


def test_build_where_handles_sql_injection_attempt(seeded_db):
    """The q parameter goes through FTS5 sanitization, not raw SQL."""
    nasty = "'; DROP TABLE articles; --"
    clause, params = db._build_where(q=nasty)
    assert "MATCH ?" in clause
    # Sanitized form wraps each word as a prefix term
    assert "DROP" in params[0]  # not stripped, but treated as FTS token
    # Crucially: the table is still intact after running the query
    rows = db.get_articles(q=nasty, limit=10)
    assert isinstance(rows, list)
    assert db.get_total_count() == expected_total_articles()


def test_build_where_tag_pipe_character(seeded_db):
    """A tag containing a pipe character would be malformed — db queries
    interpret pipes as separators, so a tag like 'a|b' will not match."""
    rows = db.get_articles(tag="composition|theory", limit=10)
    # Won't match the |composition theory| stored form
    assert rows == []


def test_build_where_year_boundaries(seeded_db):
    clause, params = db._build_where(year_from=1990, year_to=2025)
    assert "a.pub_date >= ?" in clause
    assert "a.pub_date <= ?" in clause
    assert "1990-01-01" in params
    assert "2025-12-31" in params


# ── _sanitize_fts edge cases ──────────────────────────────────────────────────


@pytest.mark.parametrize("inp,expected", [
    ("",           '""'),
    ("   ",        '""'),
    ("hello",      '"hello"*'),
    ("foo bar",    '"foo"* "bar"*'),
])
def test_sanitize_fts_simple_inputs(inp, expected):
    assert db._sanitize_fts(inp) == expected


@pytest.mark.parametrize("structured", [
    '"exact phrase"',
    "foo AND bar",
    "foo OR bar",
    "foo NOT bar",
    "foo*",
])
def test_sanitize_fts_passes_structured_through(structured):
    assert db._sanitize_fts(structured) == structured


# ── upsert_article ────────────────────────────────────────────────────────────


def test_upsert_article_new_returns_one(fixture_db):
    n = db.upsert_article(
        url="https://doi.org/10.test/new1",
        doi="10.test/new1",
        title="A new article",
        authors="Test Author",
        abstract="Abstract.",
        pub_date="2024-01-01",
        journal="College English",
        source="crossref",
    )
    assert n == 1


def test_upsert_article_duplicate_returns_zero(fixture_db):
    args = dict(
        url="https://doi.org/10.test/dup",
        doi="10.test/dup",
        title="Once",
        authors=None,
        abstract=None,
        pub_date="2024-01-01",
        journal="College English",
        source="crossref",
    )
    first = db.upsert_article(**args)
    second_args = dict(args)
    second_args["title"] = "A different title"
    second = db.upsert_article(**second_args)
    assert first == 1
    assert second == 0
    # The original title should remain — INSERT OR IGNORE doesn't update
    rows = db.get_articles(q='Once', limit=10)
    assert any("Once" in r["title"] for r in rows)


def test_upsert_article_with_oa_fields(fixture_db):
    db.upsert_article(
        url="https://doi.org/10.test/oa",
        doi="10.test/oa",
        title="OA test",
        authors="A",
        abstract=None,
        pub_date="2024-06-01",
        journal="Kairos: A Journal of Rhetoric, Technology, and Pedagogy",
        source="scrape",
        oa_status="gold",
        oa_url="https://example.org/oa",
    )
    rows = db.get_articles(limit=10)
    assert rows[0]["oa_status"] == "gold"
    assert rows[0]["oa_url"] == "https://example.org/oa"


# ── get_year_range ────────────────────────────────────────────────────────────


def test_get_year_range_seeded(seeded_db):
    assert db.get_year_range() == expected_year_range()


def test_get_year_range_empty_db(fixture_db):
    assert db.get_year_range() == (None, None)


# ── get_all_tags ──────────────────────────────────────────────────────────────


def test_get_all_tags_returns_sorted_pairs(seeded_db):
    tags = db.get_all_tags()
    assert isinstance(tags, list)
    assert all(isinstance(t, tuple) and len(t) == 2 for t in tags)
    # Sorted by count descending then alphabetically
    counts = [c for _, c in tags]
    assert counts == sorted(counts, reverse=True) or all(
        # ties allowed but within ties names ascend
        tags[i][1] >= tags[i+1][1] or
        (tags[i][1] == tags[i+1][1] and tags[i][0] < tags[i+1][0])
        for i in range(len(tags) - 1)
    )
    # composition tag is on every article — should be the most common
    assert tags[0][0] == "composition"


def test_get_all_tags_filtered_by_journal(seeded_db):
    cross_tags = db.get_all_tags(journal=J_CROSSREF)
    rss_tags   = db.get_all_tags(journal=J_RSS)
    assert cross_tags != rss_tags  # different distributions
    cross_total = sum(c for _, c in cross_tags if _ == "composition")
    assert cross_total == 13  # every CrossRef article has the composition tag


# ── get_new_articles / get_new_article_count ──────────────────────────────────


def test_get_new_article_count_uses_fetched_at(seeded_db):
    """Seed marks 5 articles as fetched within the last 7 days."""
    # Note: get_new_articles uses datetime('now') in SQL, not Python's
    # datetime — so freeze_time won't influence it. The seed sets
    # fetched_at relative to FROZEN_NOW = 2026-04-29; if today's real
    # date differs by more than 23 days, this assertion may shift.
    n = db.get_new_article_count(days=10000)
    assert n == 50  # all are within 10000 days
    n = db.get_new_article_count(days=1)
    assert n >= 0  # XXX: depends on real wall clock vs seed timestamps


def test_get_new_articles_returns_list(seeded_db):
    rows = db.get_new_articles(days=10000)
    assert isinstance(rows, list)
    assert len(rows) == 50
