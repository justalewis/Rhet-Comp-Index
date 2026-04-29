"""Characterization tests for db.py citation-network functions — covers
get_most_cited, the network builders (citation/cocitation/bibcoupling/centrality),
ego networks, sleeping beauties, journal half-life, main-path, and community
detection. Most are smoke tests; the simpler ones get hand-verified expected
counts."""

import pytest

import db
from tests._seed import J_CROSSREF, J_RSS


# ── update_citation_counts + most_cited ──────────────────────────────────────


def test_seed_top_cited_is_article_1(seeded_db):
    top = db.get_most_cited(limit=5)
    assert len(top) == 5
    assert top[0]["id"] == 1
    assert top[0]["internal_cited_by_count"] == 8


def test_get_most_cited_orders_descending(seeded_db):
    rows = db.get_most_cited(limit=10)
    counts = [r["internal_cited_by_count"] for r in rows]
    assert counts == sorted(counts, reverse=True)


def test_get_most_cited_filters_by_journal(seeded_db):
    rows = db.get_most_cited(journal=J_CROSSREF, limit=20)
    assert all(r.get("journal") is None or "Coll" in (r.get("journal") or "")
               or r["id"] in {a["id"] for a in db.get_articles(journal=J_CROSSREF, limit=200)}
               for r in rows)
    # All should be CrossRef articles — id range 1..13
    assert all(1 <= r["id"] <= 13 for r in rows)


def test_get_most_cited_year_filter(seeded_db):
    rows = db.get_most_cited(year_from=2024, year_to=2025, limit=50)
    assert all("2024" <= r["pub_date"][:4] <= "2025" for r in rows)


def test_get_most_cited_tag_filter(seeded_db):
    rows = db.get_most_cited(tag="composition theory", limit=50)
    # Every returned article must have the tag
    full = {a["id"]: a for a in db.get_articles(limit=200)}
    for r in rows:
        assert "|composition theory|" in (full[r["id"]]["tags"] or "")


# ── upsert_citation ──────────────────────────────────────────────────────────


def test_upsert_citation_dedup(fixture_db):
    # Seed two articles
    db.upsert_article("https://ex.org/a", "10.x/a", "A", None, None,
                       "2024-01-01", "College English", "crossref")
    db.upsert_article("https://ex.org/b", "10.x/b", "B", None, None,
                       "2024-02-01", "College English", "crossref")
    rows_a = db.get_articles(limit=100)
    src_id = next(r["id"] for r in rows_a if r["doi"] == "10.x/a")
    tgt_id = next(r["id"] for r in rows_a if r["doi"] == "10.x/b")
    first  = db.upsert_citation(src_id, "10.x/b", tgt_id, None)
    second = db.upsert_citation(src_id, "10.x/b", tgt_id, None)
    assert first == 1
    assert second == 0


# ── Network builders (smoke + structural) ────────────────────────────────────


def test_get_citation_network_well_formed(seeded_db):
    g = db.get_citation_network(min_citations=1, max_nodes=100)
    assert {"nodes", "links", "node_count", "link_count"} <= set(g)
    assert g["node_count"] == len(g["nodes"])
    assert g["link_count"] == len(g["links"])


def test_get_citation_network_min_citations_threshold(seeded_db):
    """min_citations filters nodes by internal_cited_by_count."""
    high  = db.get_citation_network(min_citations=10, max_nodes=100)
    assert high["nodes"] == []
    low = db.get_citation_network(min_citations=1, max_nodes=100)
    assert low["node_count"] > 0


def test_get_cocitation_network_well_formed(seeded_db):
    g = db.get_cocitation_network(min_cocitations=1, max_nodes=100)
    assert {"nodes", "links", "node_count", "link_count"} <= set(g)


def test_get_bibcoupling_network_well_formed(seeded_db):
    g = db.get_bibcoupling_network(min_coupling=1, max_nodes=100)
    assert {"nodes", "links", "node_count", "link_count"} <= set(g)


def test_get_citation_centrality_returns_metrics(seeded_db):
    g = db.get_citation_centrality(min_citations=1, max_nodes=100)
    assert {"nodes", "links", "top_eigenvector", "top_betweenness",
            "node_count", "link_count"} <= set(g)


def test_get_ego_network_focal_node_present(seeded_db):
    """Article 1 has 8 inbound citations — ego network must include it."""
    g = db.get_ego_network(article_id=1)
    assert g["focal_id"] == 1
    node_ids = {n["id"] for n in g["nodes"]}
    assert 1 in node_ids
    assert g["link_count"] >= 0


def test_get_ego_network_unknown_article(seeded_db):
    g = db.get_ego_network(article_id=99999)
    assert g["focal_id"] == 99999
    # Unknown id may produce empty or focal-only ego network
    assert isinstance(g["nodes"], list)


# ── sleeping beauties / half-life / main-path / communities ──────────────────


def test_get_sleeping_beauties_smoke(seeded_db):
    # min_total_citations=2 skips article 6, which has a single citation
    # from a "future-citing-past" edge in the seed that triggers the bug
    # documented in test_get_sleeping_beauties_crashes_on_invalid_timeline.
    res = db.get_sleeping_beauties(min_total_citations=2)
    assert "articles" in res
    assert isinstance(res["articles"], list)


def test_get_sleeping_beauties_crashes_on_invalid_timeline(seeded_db):
    """XXX: db.py:2558 — get_sleeping_beauties crashes with
    ValueError('max() iterable argument is empty') when an article's
    publication year is later than the latest year of any citing article
    (e.g. seed edge (45, 6): article 6 published 2022, only citer 45
    published 2005). The full_timeline range(t0, max_year+1) is empty
    and max() has no default. Suspected fix: skip article when t0 >
    max_year, or pass default=t0 to max()."""
    with pytest.raises(ValueError):
        db.get_sleeping_beauties(min_total_citations=1)


def test_get_journal_half_life_smoke(seeded_db):
    res = db.get_journal_half_life()
    assert "journals" in res
    assert isinstance(res["journals"], list)


def test_get_main_path_smoke(seeded_db):
    res = db.get_main_path(min_citations=1, max_nodes=50)
    assert {"path", "edges", "stats"} <= set(res)


def test_get_community_detection_smoke(seeded_db):
    res = db.get_community_detection(min_citations=1, max_nodes=50)
    assert {"nodes", "links", "communities", "modularity",
            "community_count", "node_count", "link_count", "resolution"} <= set(res)


# ── Citation centrality on empty DB ──────────────────────────────────────────


def test_citation_network_empty_db(fixture_db):
    g = db.get_citation_network(min_citations=1)
    assert g["nodes"] == []
    assert g["links"] == []
