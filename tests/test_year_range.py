"""Publication-year range on the corpus-wide visualisation endpoints.

The citation and datastories visualisations already took year_from/year_to.
These cover the five that did not: timeline, tag co-occurrence, author
network, institutions and citation trends. The behaviours worth pinning:

  * no range  -> exactly the old output (nothing changes unless asked)
  * a range   -> only articles inside it, inclusive at both ends
  * garbage / reversed bounds degrade to "no cutoff" / a swapped range,
    never a 400 or an empty chart
"""

import pytest

import db
from db.core import year_range_sql
from web_helpers import parse_year_range


# ── year_range_sql ────────────────────────────────────────────────────────────


def test_year_range_sql_no_bounds_is_empty():
    assert year_range_sql("pub_date") == ([], [])
    assert year_range_sql("pub_date", "", None) == ([], [])


def test_year_range_sql_both_bounds():
    clauses, params = year_range_sql("a.pub_date", 2000, "2010")
    assert clauses == ["SUBSTR(a.pub_date, 1, 4) >= ?", "SUBSTR(a.pub_date, 1, 4) <= ?"]
    assert params == ["2000", "2010"]


def test_year_range_sql_swaps_reversed_range():
    assert year_range_sql("pub_date", 2020, 2010)[1] == ["2010", "2020"]


@pytest.mark.parametrize("bad", ["abc", "", None, "20x0", 99, 12345, "1e3"])
def test_year_range_sql_ignores_junk(bad):
    assert year_range_sql("pub_date", bad, bad) == ([], [])


def test_year_range_sql_junk_on_one_side_keeps_the_other():
    clauses, params = year_range_sql("pub_date", "nope", 2015)
    assert len(clauses) == 1 and params == ["2015"]


# ── parse_year_range ──────────────────────────────────────────────────────────


def test_parse_year_range():
    assert parse_year_range({}) == (None, None)
    assert parse_year_range({"year_from": "1990", "year_to": "2020"}) == (1990, 2020)
    assert parse_year_range({"year_from": "2020", "year_to": "1990"}) == (1990, 2020)
    assert parse_year_range({"year_from": " 2001 "}) == (2001, None)
    assert parse_year_range({"year_from": "abc", "year_to": "99999"}) == (None, None)


# ── db layer ──────────────────────────────────────────────────────────────────


def _years(rows):
    return sorted({int(r["year"]) for r in rows})


def test_timeline_unfiltered_unchanged(seeded_db):
    full = db.get_timeline_data()
    assert full == db.get_timeline_data(year_from=None, year_to=None)
    assert full == db.get_timeline_data(year_from="", year_to="")


def test_timeline_range_is_inclusive_and_exclusive(seeded_db):
    full_years = _years(db.get_timeline_data())
    lo, hi = full_years[len(full_years) // 3], full_years[2 * len(full_years) // 3]
    rows = db.get_timeline_data(year_from=lo, year_to=hi)
    ys = _years(rows)
    assert ys and ys[0] >= lo and ys[-1] <= hi
    assert lo in ys and hi in ys
    assert sum(r["count"] for r in rows) < sum(r["count"] for r in db.get_timeline_data())


def test_timeline_reversed_equals_ordered(seeded_db):
    assert db.get_timeline_data(year_from=2015, year_to=2005) == \
           db.get_timeline_data(year_from=2005, year_to=2015)


def test_timeline_open_ended_ranges(seeded_db):
    full = _years(db.get_timeline_data())
    assert _years(db.get_timeline_data(year_from=2010))[0] >= 2010
    assert _years(db.get_timeline_data(year_to=2010))[-1] <= 2010
    assert _years(db.get_timeline_data(year_from=full[0]))[-1] == full[-1]


def test_timeline_empty_range_is_empty_not_error(seeded_db):
    assert db.get_timeline_data(year_from=1901, year_to=1902) == []


def test_tag_cooccurrence_range_never_exceeds_full(seeded_db):
    full = db.get_tag_cooccurrence()
    part = db.get_tag_cooccurrence(year_from=2015)
    assert set(part["tags"]) <= set(full["tags"])
    assert sum(map(sum, part["matrix"])) <= sum(map(sum, full["matrix"]))
    empty = db.get_tag_cooccurrence(year_from=1901, year_to=1902)
    assert empty == {"tags": [], "matrix": []}


def test_author_network_range_never_exceeds_full(seeded_db):
    full = db.get_author_network(min_papers=2, top_n=500)
    part = db.get_author_network(min_papers=2, top_n=500, year_from=2015)
    assert {n["id"] for n in part["nodes"]} <= {n["id"] for n in full["nodes"]}
    counts = {n["id"]: n["count"] for n in full["nodes"]}
    assert all(n["count"] <= counts[n["id"]] for n in part["nodes"])
    assert db.get_author_network(min_papers=2, top_n=500, year_from=1901, year_to=1902) == \
           {"nodes": [], "links": []}


def test_citation_trends_range(seeded_db):
    full = db.get_citation_trends()
    if not full:
        pytest.skip("seed has no fetched-reference years")
    ys = [int(r["year"]) for r in full]
    lo = ys[len(ys) // 2]
    part = db.get_citation_trends(year_from=lo)
    assert part and all(int(r["year"]) >= lo for r in part)
    assert db.get_citation_trends(year_from=1901, year_to=1902) == []


def test_institutions_range(seeded_db):
    full = db.get_top_institutions_v2(limit=50)
    part = db.get_top_institutions_v2(limit=50, year_from=2015)
    assert len(part) <= len(full)
    full_counts = {r["display_name"]: r["article_count"] for r in full}
    for r in part:
        assert r["article_count"] <= full_counts.get(r["display_name"], 10 ** 9)
    tl = db.get_institution_timeline_v2(top_n=5, year_from=2015, year_to=2020)
    assert all(2015 <= int(y) <= 2020 for y in tl["years"])


# ── routes ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [
    "/api/stats/timeline",
    "/api/stats/tag-cooccurrence",
    "/api/stats/author-network",
    "/api/stats/institutions",
    "/api/stats/citation-trends",
])
def test_routes_accept_year_range_and_degrade_gracefully(client, path):
    ok = client.get(path + "?year_from=2005&year_to=2015")
    assert ok.status_code == 200
    # Junk and reversed bounds are not an error.
    assert client.get(path + "?year_from=abc&year_to=zzz").status_code == 200
    assert client.get(path + "?year_from=2015&year_to=2005").status_code == 200


def test_timeline_route_honours_range(client):
    full = client.get("/api/stats/timeline").get_json()
    part = client.get("/api/stats/timeline?year_from=2005&year_to=2015").get_json()
    assert part["years"] and min(map(int, part["years"])) >= 2005
    assert max(map(int, part["years"])) <= 2015
    assert len(part["years"]) < len(full["years"])
    # junk == unfiltered; reversed == ordered
    assert client.get("/api/stats/timeline?year_from=abc").get_json() == full
    assert client.get("/api/stats/timeline?year_from=2015&year_to=2005").get_json() == part


def test_explore_page_renders_year_controls(client):
    html = client.get("/explore").get_data(as_text=True)
    for prefix in ("tl", "tags", "net", "ct", "inst"):
        assert f'id="{prefix}-year-from"' in html, prefix
        assert f'id="{prefix}-year-to"' in html, prefix
