"""Tests for app.py input validators (_safe_int, _safe_float, BibTeX/RIS
helpers) and db._sanitize_fts. These are the highest-value defenses against
malformed user input arriving via query strings."""

import pytest

import app
import db


# ── _safe_int ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("val,default,expected", [
    ("5",       1,  5),
    (5,         1,  5),
    ("abc",     1,  1),
    (None,      1,  1),
    ("",        1,  1),
    ("3.14",    1,  1),  # float string is invalid for int()
])
def test_safe_int_basic(val, default, expected):
    assert app._safe_int(val, default) == expected


@pytest.mark.parametrize("val,lo,hi,expected", [
    ("5",   1,  10,  5),
    ("-5",  1,  10,  1),    # clamped to lo
    ("99",  1,  10,  10),   # clamped to hi
    ("5",   None, 3, 3),    # only hi
    ("-5",  0,    None, 0), # only lo
])
def test_safe_int_clamping(val, lo, hi, expected):
    assert app._safe_int(val, default=0, lo=lo, hi=hi) == expected


# ── _safe_float ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("val,default,expected", [
    ("3.14",    0.0,    3.14),
    (3.14,      0.0,    3.14),
    ("abc",     1.0,    1.0),
    (None,      1.0,    1.0),
    ("",        1.0,    1.0),
])
def test_safe_float_basic(val, default, expected):
    assert app._safe_float(val, default) == expected


def test_safe_float_clamping():
    assert app._safe_float("100.0", 0.0, lo=0.0, hi=10.0) == 10.0
    assert app._safe_float("-5.0",  0.0, lo=0.0, hi=10.0) == 0.0


# ── _bibtex_key ──────────────────────────────────────────────────────────────


def test_bibtex_key_simple_article():
    art = {"authors": "Jane Smith; John Adams",
           "pub_date": "2024-03-15",
           "title": "On rhetoric"}
    assert app._bibtex_key(art) == "smith2024on"


def test_bibtex_key_no_authors():
    art = {"authors": "", "pub_date": "2024-01-01", "title": "Hello"}
    assert app._bibtex_key(art) == "unknown2024hello"


def test_bibtex_key_no_title():
    art = {"authors": "Jane Smith", "pub_date": "2024", "title": ""}
    assert app._bibtex_key(art) == "smith2024untitled"


def test_bibtex_key_strips_punctuation():
    """Author 'Smith, J.' → 'smithj' or similar — non-alnum chars removed."""
    art = {"authors": "Smith, J.", "pub_date": "2024-01-01", "title": "Hi"}
    key = app._bibtex_key(art)
    assert "," not in key
    assert "." not in key


# ── _to_bibtex ───────────────────────────────────────────────────────────────


def test_to_bibtex_includes_doi_and_url():
    art = {"authors": "Jane Smith", "pub_date": "2024-01-01",
           "title": "T", "journal": "College English",
           "doi": "10.x/abc", "url": "https://doi.org/10.x/abc"}
    out = app._to_bibtex([art])
    assert "@article{smith2024t," in out
    assert "doi     = {10.x/abc}" in out
    assert "url     = {https://doi.org/10.x/abc}" in out


def test_to_bibtex_escapes_braces_in_title():
    art = {"authors": "X", "pub_date": "2024", "title": "A {tricky} title",
           "journal": "J", "doi": "", "url": ""}
    out = app._to_bibtex([art])
    # Braces are doubled: { → {{, } → }}
    assert "{{tricky}}" in out


def test_to_bibtex_empty_list_returns_empty_string():
    assert app._to_bibtex([]) == ""


def test_to_bibtex_authors_joined_with_and():
    art = {"authors": "A; B; C", "pub_date": "2024", "title": "T",
           "journal": "J", "doi": "", "url": ""}
    out = app._to_bibtex([art])
    assert "A and B and C" in out


# ── _to_ris ──────────────────────────────────────────────────────────────────


def test_to_ris_required_markers():
    art = {"authors": "Jane Smith", "pub_date": "2024-01-01",
           "title": "T", "journal": "J", "doi": "10.x/y",
           "url": "https://doi.org/10.x/y"}
    out = app._to_ris([art])
    assert out.startswith("TY  - JOUR")
    assert "AU  - Jane Smith" in out
    assert "TI  - T" in out
    assert "JO  - J" in out
    assert "PY  - 2024" in out
    assert "DO  - 10.x/y" in out
    assert "ER  -" in out


def test_to_ris_multiple_authors_each_on_own_line():
    art = {"authors": "A; B; C", "pub_date": "2024", "title": "T",
           "journal": "J", "doi": "", "url": ""}
    out = app._to_ris([art])
    assert out.count("AU  -") == 3


def test_to_ris_empty_list_returns_empty_string():
    assert app._to_ris([]) == ""


# ── format_period filter ─────────────────────────────────────────────────────


@pytest.mark.parametrize("inp,expected", [
    ("2024-03",  "March 2024"),
    ("2024",     "2024"),
    ("Undated",  "Undated"),
    ("",         "Undated"),
])
def test_format_period(inp, expected):
    assert app.format_period(inp) == expected


def test_format_period_invalid_month_passes_through():
    """Invalid month number leaves the period unchanged."""
    assert app.format_period("2024-99") == "2024-99"


# ── _sanitize_fts (db.py) extra cases ───────────────────────────────────────


def test_sanitize_fts_strips_outer_whitespace():
    assert db._sanitize_fts("  foo  ") == '"foo"*'


def test_sanitize_fts_unicode_passes_through():
    """Unicode chars are valid in FTS5 tokens."""
    assert db._sanitize_fts("naïveté") == '"naïveté"*'
