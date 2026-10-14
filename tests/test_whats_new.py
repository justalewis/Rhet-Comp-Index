"""What's New (/new): paging, and back-catalog records counted per journal
instead of listed. A back-catalog load once put 12,684 rows on the page.

get_new_articles() and friends compare against SQLite's datetime('now'), not
Python's clock, so these tests insert rows with fetched_at relative to 'now'
rather than using the frozen seed timestamps."""

from datetime import date, timedelta

import pytest

import db
import blueprints.main as main_bp


def _add(title, journal, pub_date, fetched="-1 day"):
    """Insert one article fetched `fetched` ago (an SQLite modifier)."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO articles (url, title, journal, pub_date, fetched_at) "
            "VALUES (?, ?, ?, ?, datetime('now', ?))",
            (f"https://example.org/{title}", title, journal, pub_date, fetched),
        )


@pytest.fixture
def new_db(fixture_db):
    # Newly published: pub_date within a year of fetched_at (or unknown).
    today = date.today()
    _add("fresh-a", "Journal A", (today - timedelta(days=40)).isoformat())
    _add("fresh-b", "Journal B", (today - timedelta(days=10)).isoformat())
    _add("fresh-c", "Journal A", (today - timedelta(days=200)).isoformat()[:7])  # YYYY-MM
    _add("undated", "Journal B", None)
    # Back catalog: old issues filled in this week.
    _add("old-1", "Journal A", "1990-03-01")
    _add("old-2", "Journal A", "1994-06-01")
    _add("old-3", "Journal A", "2000-01-01")
    _add("old-4", "Journal B", "1985-01-01")
    # Fetched a month ago: outside the 7-day window entirely.
    _add("stale", "Journal A", (today - timedelta(days=60)).isoformat(), fetched="-30 days")
    return fixture_db


def _titles(rows):
    return sorted(r["title"] for r in rows)


def test_counts_include_back_catalog_by_default(new_db):
    assert db.get_new_article_count(days=7) == 8
    assert len(db.get_new_articles(days=7)) == 8


def test_back_catalog_excluded_on_request(new_db):
    rows = db.get_new_articles(days=7, back_catalog=False)
    assert _titles(rows) == ["fresh-a", "fresh-b", "fresh-c", "undated"]
    assert db.get_new_article_count(days=7, back_catalog=False) == 4


def test_back_catalog_summary_per_journal(new_db):
    assert db.get_new_back_catalog(days=7) == [
        {"journal": "Journal A", "count": 3, "first_year": "1990", "last_year": "2000"},
        {"journal": "Journal B", "count": 1, "first_year": "1985", "last_year": "1985"},
    ]


def test_paging_is_stable_and_complete(new_db):
    pages = [db.get_new_articles(days=7, limit=3, offset=o, back_catalog=False)
             for o in (0, 3, 6)]
    assert [len(p) for p in pages] == [3, 1, 0]
    ids = [r["id"] for p in pages for r in p]
    assert len(ids) == len(set(ids)) == 4


def test_new_page_collapses_back_catalog(empty_client, new_db):
    body = empty_client.get("/new").get_data(as_text=True)
    assert "4 articles added in the last 7 days" in body
    assert "fresh-a" in body
    # Back-catalog titles are counted, not listed.
    assert "old-1" not in body
    assert "3 back-catalog records added" in body
    assert "1 back-catalog record added" in body
    assert 'href="/?journal=Journal%20A"' in body
    assert "Refresh all sources" not in body


def test_new_page_pages(empty_client, new_db, monkeypatch):
    monkeypatch.setattr(main_bp, "NEW_PER_PAGE", 3)
    first = empty_client.get("/new").get_data(as_text=True)
    assert "page 1 of 2" in first
    assert 'href="/new?page=2"' in first

    second = empty_client.get("/new?page=2").get_data(as_text=True)
    assert "page 2 of 2" in second
    # The back-catalog summary sits on the first page only.
    assert "back-catalog record" not in second

    # Past the end clamps to the last page rather than rendering empty.
    past = empty_client.get("/new?page=99").get_data(as_text=True)
    assert "page 2 of 2" in past


@pytest.mark.parametrize("path", [
    "/", "/new", "/explore", "/tools", "/article/1", "/authors", "/books",
    "/feeds", "/most-cited", "/alerts/new",
])
def test_nav_badge_leaves_out_back_catalog(empty_client, new_db, monkeypatch, path):
    """The sidebar badge counts what /new lists: 4, not the 8 fetched."""
    monkeypatch.setenv("PINAKES_ALERTS_ENABLED", "1")
    resp = empty_client.get(path)
    assert resp.status_code == 200
    assert '<span class="nav-count">4</span>' in resp.get_data(as_text=True)


def test_new_page_empty_state(empty_client):
    body = empty_client.get("/new").get_data(as_text=True)
    assert "Nothing new in the last 7 days." in body
    assert "Refresh all sources" not in body
