"""Characterization tests for db.py book / chapter reads — get_books filters,
get_book_count, get_book_by_id, get_book_chapters."""

import pytest

import db


def test_get_books_returns_book_records_only(seeded_db):
    rows = db.get_books(limit=100)
    assert all(r["record_type"] == "book" for r in rows)
    assert len(rows) == 5


def test_get_book_count_matches_get_books(seeded_db):
    assert db.get_book_count() == len(db.get_books(limit=100))


def test_get_books_filter_by_publisher(seeded_db):
    rows = db.get_books(publisher="WAC Clearinghouse", limit=100)
    assert all(r["publisher"] == "WAC Clearinghouse" for r in rows)
    # Three books in WAC Clearinghouse per seed
    assert len(rows) == 3


def test_get_books_filter_by_book_type(seeded_db):
    monos = db.get_books(book_type="monograph", limit=100)
    eds   = db.get_books(book_type="edited-collection", limit=100)
    assert {r["book_type"] for r in monos} == {"monograph"}
    assert {r["book_type"] for r in eds} == {"edited-collection"}
    assert len(monos) == 3
    assert len(eds) == 2


def test_get_books_year_filter(seeded_db):
    rows = db.get_books(year_from=2020, year_to=2022, limit=100)
    years = {r["year"] for r in rows}
    assert years <= {2020, 2021, 2022}


def test_get_books_q_search_in_title(seeded_db):
    rows = db.get_books(q="Digital", limit=100)
    assert any("Digital" in r["title"] for r in rows)


def test_get_book_count_with_filters(seeded_db):
    # WAC Clearinghouse + monograph
    n = db.get_book_count(publisher="WAC Clearinghouse", book_type="monograph")
    assert n == 2


def test_get_book_by_id_returns_dict(seeded_db):
    book = db.get_book_by_id(1)
    assert book is not None
    assert book["title"].startswith("Composition Pedagogy")
    assert book["record_type"] == "book"


def test_get_book_by_id_unknown(seeded_db):
    assert db.get_book_by_id(99999) is None


def test_get_book_chapters_returns_six(seeded_db):
    """Book 4 (Handbook of Writing Center Studies) has 6 chapter rows."""
    chapters = db.get_book_chapters(4)
    assert len(chapters) == 6
    # Front-matter sorts before chapters
    assert chapters[0]["record_type"] == "front-matter"


def test_get_book_chapters_unknown_book(seeded_db):
    assert db.get_book_chapters(99999) == []


def test_get_book_publishers(seeded_db):
    rows = db.get_book_publishers()
    assert isinstance(rows, list)
    publishers = {r["publisher"] for r in rows}
    assert {"WAC Clearinghouse", "Utah State UP"} <= publishers
