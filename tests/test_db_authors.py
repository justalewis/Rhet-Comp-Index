"""Characterization tests for db.py author-related reads — get_all_authors,
get_authors_by_letter, get_author_articles, and partial-name round-tripping."""

import pytest

import db


def test_get_all_authors_returns_count_pairs(seeded_db):
    authors = db.get_all_authors(limit=500)
    assert isinstance(authors, list)
    assert all(isinstance(a, tuple) and len(a) == 2 for a in authors)
    # Sort: count desc then name asc
    counts = [c for _, c in authors]
    assert counts == sorted(counts, reverse=True)


def test_get_all_authors_includes_seeded_names(seeded_db):
    names = {n for n, _ in db.get_all_authors(limit=500)}
    # The first canonical author "Jane Smith" appears in multiple rotations
    assert "Jane Smith" in names
    assert "John Adams" in names


def test_get_all_authors_respects_limit(seeded_db):
    top = db.get_all_authors(limit=3)
    assert len(top) == 3


def test_get_authors_by_letter_S(seeded_db):
    rows = db.get_authors_by_letter("S")
    # Smith, Stone — both seeded
    names = {r["name"] for r in rows}
    assert "Jane Smith" in names
    assert "Quentin Stone" in names
    # All last names start with S
    for r in rows:
        last = r["name"].strip().split()[-1]
        assert last.upper().startswith("S")


def test_get_authors_by_letter_lowercase_input(seeded_db):
    """Letter parameter is .upper()'d internally."""
    upper = db.get_authors_by_letter("A")
    lower = db.get_authors_by_letter("a")
    assert {r["name"] for r in upper} == {r["name"] for r in lower}


def test_get_authors_by_letter_no_match(seeded_db):
    rows = db.get_authors_by_letter("Z")
    # "Wendy Young" and "Victor Xu" don't start with Z; no Z names seeded
    assert all(r["name"].split()[-1].startswith("Z") for r in rows)


def test_get_authors_by_letter_includes_orcid(seeded_db):
    rows = db.get_authors_by_letter("S")
    smith = next(r for r in rows if r["name"] == "Jane Smith")
    assert smith["orcid"] == "0000-0001-1111-1111"
    assert smith["institution_name"] == "Smith Hall, U of Iowa"


def test_get_author_articles_round_trip(seeded_db):
    """Articles where Jane Smith is in the authors list are returned
    sorted pub_date DESC."""
    rows = db.get_author_articles("Jane Smith")
    assert len(rows) > 0
    pubs = [r["pub_date"] for r in rows]
    assert pubs == sorted(pubs, reverse=True)
    assert all("Jane Smith" in r["authors"] for r in rows)


def test_get_author_articles_partial_match(seeded_db):
    """Function uses LIKE %name%, so partial matches return supersets."""
    smith_rows = db.get_author_articles("Smith")
    jane_rows  = db.get_author_articles("Jane Smith")
    assert len(smith_rows) >= len(jane_rows)


def test_get_author_articles_unknown_returns_empty(seeded_db):
    rows = db.get_author_articles("Nonexistent Person")
    assert rows == []


def test_get_author_by_name_returns_record(seeded_db):
    rec = db.get_author_by_name("Jane Smith")
    assert rec is not None
    assert rec["orcid"] == "0000-0001-1111-1111"


def test_get_author_by_name_unknown_returns_none(seeded_db):
    assert db.get_author_by_name("Not A Real Author") is None
