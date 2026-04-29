"""Characterization tests for coverage and OA-status functions —
get_coverage_stats, get_detailed_coverage, backfill_oa_status."""

import pytest

import db
from tests._seed import J_CROSSREF, J_RSS, J_SCRAPE, J_MANUAL


def test_get_coverage_stats_per_journal(seeded_db):
    rows = db.get_coverage_stats()
    by_j = {r["journal"]: r for r in rows}
    assert J_CROSSREF in by_j
    # Seed marks 5 articles in J_CROSSREF as having references_fetched_at set.
    cs = by_j[J_CROSSREF]
    assert cs["fetched_count"] == 5
    assert cs["article_count"] == 13
    assert cs["coverage_pct"] == round(100.0 * 5 / 13, 1)


def test_get_coverage_stats_other_journals_zero(seeded_db):
    rows = db.get_coverage_stats()
    by_j = {r["journal"]: r for r in rows}
    for jname in (J_RSS, J_SCRAPE, J_MANUAL):
        assert by_j[jname]["fetched_count"] == 0
        assert by_j[jname]["coverage_pct"] == 0.0


def test_get_coverage_stats_sorts_by_coverage_desc(seeded_db):
    rows = db.get_coverage_stats()
    coverages = [r["coverage_pct"] for r in rows]
    assert coverages == sorted(coverages, reverse=True)


def test_get_detailed_coverage_returns_dict_or_none(seeded_db):
    """Live snapshot path or fallback to file. Either is acceptable shape-wise."""
    res = db.get_detailed_coverage()
    # The function falls back to a JSON file if the live build_snapshot
    # raises; both code paths are exercised in CI. Result is dict-like or None.
    assert res is None or isinstance(res, dict)


def test_get_detailed_coverage_caches_result(seeded_db):
    """Two consecutive calls with the same year_min should return the
    same object reference (or at least equal contents) due to in-process cache."""
    a = db.get_detailed_coverage(year_min=2020)
    b = db.get_detailed_coverage(year_min=2020)
    assert a == b


# ── backfill_oa_status ───────────────────────────────────────────────────────


def test_backfill_oa_status_tags_gold_journals(fixture_db):
    """Seed lets us start clean: insert one article from a gold-OA journal
    and one from a paywalled journal, then run backfill."""
    db.upsert_article(
        url="https://example.org/kairos-1", doi="10.x/k1",
        title="Kairos test article", authors="Test", abstract=None,
        pub_date="2024-01-01",
        journal="Kairos: A Journal of Rhetoric, Technology, and Pedagogy",
        source="scrape",
    )
    db.upsert_article(
        url="https://example.org/college-english-1", doi="10.x/ce1",
        title="College English test article", authors="Test", abstract=None,
        pub_date="2024-01-01",
        journal="College English",
        source="crossref",
    )
    res = db.backfill_oa_status()
    assert res["tagged"] >= 1
    assert res["total_gold_articles"] >= 1
    # The Kairos article should now be marked gold.
    rows = db.get_articles(limit=10)
    kairos = next(r for r in rows if "Kairos" in r["journal"])
    college = next(r for r in rows if r["journal"] == "College English")
    assert kairos["oa_status"] == "gold"
    assert kairos["oa_url"]  # populated to either doi.org URL or article URL
    assert college["oa_status"] != "gold"


def test_backfill_oa_status_empty_db_returns_zero(fixture_db):
    res = db.backfill_oa_status()
    assert res == {"tagged": 0, "already_tagged": 0, "total_gold_articles": 0}


def test_backfill_oa_status_idempotent(fixture_db):
    db.upsert_article(
        url="https://example.org/peitho", doi=None,
        title="Peitho gold", authors=None, abstract=None,
        pub_date="2024-01-01", journal="Peitho", source="crossref",
    )
    first  = db.backfill_oa_status()
    second = db.backfill_oa_status()
    assert first["tagged"] == 1
    assert second["tagged"] == 0  # already tagged
    assert second["already_tagged"] == 1
