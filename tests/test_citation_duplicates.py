"""Duplicate citation rows: the v17 uniqueness guards, the analytics' immunity
to repeated rows, dedupe_citations.py, citation_duplicates_report.py, and the
LiCS scraper's per-run replacement.

Production picked up duplicates because the weekly LiCS galley scrape
re-inserted every DOI-bearing reference on each run (one pair reached 38
copies). These tests rebuild that state on the deterministic seed by dropping
the guards and inserting extra copies of seed edges, then check that no count,
weight or network changes."""

import json
import logging
import sqlite3
from contextlib import closing

import pytest

import citation_duplicates_report
import db
import dedupe_citations
import scrape_lics_refs
from db import datastories as ds
from db.core import CITATION_UNIQUE_INDEXES

GUARDS = [name for name, _ in CITATION_UNIQUE_INDEXES]

# (citing id, cited id, extra copies) — all edges exist once in the seed.
# 5 and 8 both cite 1, so co-citation (1,14)/(1,27) and coupling (5,8) pairs
# are present; 45 -> 6 is the cited-before-publication edge.
EXTRA_COPIES = [(5, 1, 4), (5, 14, 3), (8, 1, 2), (8, 27, 5), (12, 39, 2),
                (45, 6, 3), (3, 14, 1)]
EXTRA_ROWS = sum(n for _, _, n in EXTRA_COPIES)


def _guards_present(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='citations'")}
    return {g: g in names for g in GUARDS}


def _inject_duplicates(db_file):
    """Recreate the production state: guards gone, seed edges repeated."""
    conn = sqlite3.connect(db_file)
    for name in GUARDS:
        conn.execute(f"DROP INDEX IF EXISTS {name}")
    for src, tgt, extra in EXTRA_COPIES:
        doi = conn.execute("SELECT doi FROM articles WHERE id=?", (tgt,)).fetchone()[0]
        for _ in range(extra):
            conn.execute(
                "INSERT INTO citations (source_article_id, target_doi, target_article_id) "
                "VALUES (?, ?, ?)", (src, doi, tgt))
    conn.commit()
    conn.close()


@pytest.fixture
def dup_db(seeded_db):
    _inject_duplicates(seeded_db)
    db.update_citation_counts()
    return seeded_db


def _snapshot(db_file):
    """Everything a read-only tool must leave alone."""
    conn = sqlite3.connect(db_file)
    try:
        return (
            conn.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall(),
            conn.execute("SELECT * FROM citations ORDER BY id").fetchall(),
            conn.execute("SELECT id, internal_cited_by_count, internal_cites_count "
                         "FROM articles ORDER BY id").fetchall(),
        )
    finally:
        conn.close()


# ── Guards ───────────────────────────────────────────────────────────────────


def test_fresh_db_has_guards(fixture_db):
    with db.get_conn() as conn:
        assert all(_guards_present(conn).values())


def test_guard_refuses_repeat_pair_but_not_doiless_references(fixture_db):
    for i in (1, 2):
        db.upsert_article(f"https://ex.org/{i}", f"10.x/{i}", f"Article {i}", None,
                          None, "2024-01-01", "College English", "crossref")
    src, tgt = 1, 2
    assert db.upsert_citation(src, "10.x/2", tgt, None) == 1
    assert db.upsert_citation(src, "10.x/2", tgt, None) == 0
    # Same indexed article under another key is still the same citation.
    assert db.upsert_citation(src, "ref:abc", tgt, {"unstructured": "x"}) == 0
    # The same outside DOI twice: refused.
    assert db.upsert_citation(src, "10.9/out", None, None) == 1
    assert db.upsert_citation(src, "10.9/out", None, None) == 0
    # DOI-less references are never refused, even with identical text.
    raw = {"unstructured": "Smith, J. Some Book. Press, 1990."}
    assert db.upsert_citation(src, None, None, raw, ord=3) == 1
    assert db.upsert_citation(src, None, None, raw, ord=4) == 1
    assert db.upsert_citation(src, None, None, None) == 1
    assert db.upsert_citation(src, None, None, None) == 1


def test_init_db_tolerates_existing_duplicates(dup_db, caplog):
    with caplog.at_level(logging.WARNING, logger="db.core"):
        db.init_db()
    assert "duplicate rows" in caplog.text
    with db.get_conn() as conn:
        # Each guard is built independently: the injected repeats are all
        # resolved rows, so only the target guard is blocked.
        assert _guards_present(conn) == {"idx_citations_unique_target": False,
                                         "idx_citations_unique_doi": True}
        assert conn.execute("SELECT COUNT(*) FROM citations").fetchone()[0] == 30 + EXTRA_ROWS


# ── Counts and analytics ignore repeated rows ────────────────────────────────


def test_counts_not_inflated(dup_db):
    by_id = {a["id"]: a for a in db.get_articles(limit=200)}
    assert by_id[1]["internal_cited_by_count"] == 8
    assert by_id[14]["internal_cited_by_count"] == 7
    assert by_id[5]["internal_cites_count"] == 2
    assert db.get_most_cited(limit=1)[0]["internal_cited_by_count"] == 8
    assert len(db.get_article_citations(1)) == 8
    assert len(db.get_article_references(5)) == 2
    assert len(db.get_article_all_references(5)) == 2


def test_cocitation_weight_counts_citing_articles(dup_db):
    g = db.get_cocitation_network(min_cocitations=1, max_nodes=100)
    weights = {(l["source"], l["target"]): l["weight"] for l in g["links"]}
    # Only article 5 cites both 1 and 14; 4 + 3 extra rows used to make this 20.
    assert weights[(1, 14)] == 1
    assert db.get_cocitation_network(min_cocitations=2)["links"] == []


def test_bibcoupling_weight_counts_shared_references(dup_db):
    g = db.get_bibcoupling_network(min_coupling=1, max_nodes=100)
    weights = {(l["source"], l["target"]): l["weight"] for l in g["links"]}
    # 5 and 8 share one reference (article 1), however many copies exist.
    assert weights[(5, 8)] == 1
    assert db.get_bibcoupling_network(min_coupling=2)["links"] == []


def test_ego_network_links_are_distinct(dup_db):
    g = db.get_ego_network(1)
    pairs = [(l["source"], l["target"]) for l in g["links"]]
    assert len(pairs) == len(set(pairs)) == g["link_count"]


ANALYTICS = {
    "most_cited": lambda: db.get_most_cited(limit=50),
    "article_citations": lambda: db.get_article_citations(1),
    "article_references": lambda: db.get_article_references(5),
    "all_references": lambda: db.get_article_all_references(8),
    "outside_count": lambda: db.get_outside_citation_count(8),
    "ego": lambda: db.get_ego_network(1),
    "citation_network": lambda: db.get_citation_network(min_citations=1, max_nodes=100),
    "cocitation_1": lambda: db.get_cocitation_network(min_cocitations=1, max_nodes=100),
    "cocitation_filtered": lambda: db.get_cocitation_network(
        min_cocitations=1, journals=["College English", "Pre/Text"], max_nodes=100),
    "bibcoupling_1": lambda: db.get_bibcoupling_network(min_coupling=1, max_nodes=100),
    "bibcoupling_filtered": lambda: db.get_bibcoupling_network(
        min_coupling=1, year_from=2000, year_to=2025, max_nodes=100),
    "sleeping_beauties": lambda: db.get_sleeping_beauties(min_total_citations=1),
    "centrality": lambda: db.get_citation_centrality(min_citations=1, max_nodes=100),
    "journal_flow": lambda: db.get_journal_citation_flow(),
    "half_life": lambda: db.get_journal_half_life(include_distribution=True,
                                                  include_timeseries=True),
    "communities": lambda: db.get_community_detection(min_citations=1, max_nodes=50),
    "main_path": lambda: db.get_main_path(min_citations=1, max_nodes=50),
    # @cached: call the undecorated function so no cache file is written.
    "temporal": lambda: db.get_temporal_network_evolution.__wrapped__(min_citations=1),
    "reading_path": lambda: db.get_reading_path(1),
    "author_cocitation": lambda: db.get_author_cocitation_network.__wrapped__(
        min_cocitations=1),
    "citation_trends": lambda: db.get_citation_trends(),
}


@pytest.mark.parametrize("name", sorted(ANALYTICS))
def test_analytics_unchanged_by_duplicate_rows(seeded_db, name):
    clean = ANALYTICS[name]()
    _inject_duplicates(seeded_db)
    db.update_citation_counts()
    assert ANALYTICS[name]() == clean


# Datastories (the @cached ones again via __wrapped__) and the author page.
# Jane Smith is first author of every hub (1, 14, 27, 39).
ROW_ORDER_ANALYTICS = {
    "ds_braided_path": lambda: ds.ds_braided_path.__wrapped__(),
    "ds_braided_path_filtered": lambda: ds.ds_braided_path.__wrapped__(
        journals=["College English", "Pre/Text"]),
    "ds_speed_of_influence": lambda: ds.ds_speed_of_influence(),
    "ds_communities_time": lambda: ds.ds_communities_time.__wrapped__(),
    "ds_long_tail": lambda: ds.ds_long_tail(),
    "ds_shifting_canons": lambda: ds.ds_shifting_canons(),
    "ds_shifting_canons_filtered": lambda: ds.ds_shifting_canons(year_from=2000),
    "ds_reach_of_citation": lambda: ds.ds_reach_of_citation(),
    "author_citing_venues": lambda: db.get_author_citing_venues("Jane Smith"),
}


def _drop_guards(db_file):
    with closing(sqlite3.connect(db_file)) as conn:
        for name in GUARDS:
            conn.execute(f"DROP INDEX IF EXISTS {name}")
        conn.commit()


@pytest.mark.parametrize("name", sorted(ROW_ORDER_ANALYTICS))
def test_row_order_analytics_unchanged_by_duplicate_rows(seeded_db, name):
    # These tools break ties (top-N cuts, community labels, link order) in the
    # order SQLite returns rows, and dropping the guards changes that order:
    # the covering index reads by (source, target), a table scan by id. Drop
    # them before the clean run too, so the repeated rows are the only change.
    _drop_guards(seeded_db)
    clean = ROW_ORDER_ANALYTICS[name]()
    _inject_duplicates(seeded_db)
    db.update_citation_counts()
    assert ROW_ORDER_ANALYTICS[name]() == clean


def test_prince_network_lists_each_citer_once(seeded_db, monkeypatch):
    # The seed has no article with the 10 citations ds_prince_network asks
    # for, so stand in for get_sleeping_beauties with the hub, awake in 2021.
    import db.citations
    beauty = {"id": 1, "title": "Hub", "journal": "College English",
              "authors": "Jane Smith", "pub_date": "2020-01-15",
              "awakening_year": 2021, "beauty_coefficient": 1.0, "sleep_years": 1}
    monkeypatch.setattr(db.citations, "get_sleeping_beauties",
                        lambda **kw: {"articles": [beauty]})
    _drop_guards(seeded_db)
    clean = ds.ds_prince_network.__wrapped__()
    _inject_duplicates(seeded_db)
    after = ds.ds_prince_network.__wrapped__()
    assert after == clean
    princes = [p["id"] for p in after["beauties"][0]["princes"]]
    # Citers of article 1 from 2020 on (40 is from 1992). 5 and 8 carry 4 and
    # 2 extra rows and used to be listed 5 and 3 times.
    assert sorted(princes) == [5, 8, 12, 16, 22, 27, 33]


def test_author_citing_venues_count_each_link_once(seeded_db):
    clean = db.get_author_citing_venues("Jane Smith")
    _inject_duplicates(seeded_db)
    after = db.get_author_citing_venues("Jane Smith")
    assert after == clean
    # Seed edges into her four articles: 8 + 7 + 6 + 5. The injected copies
    # used to add 17 more.
    assert after["total"] == 26


# ── dedupe_citations.py ──────────────────────────────────────────────────────


def _add_outside_duplicates(db_file):
    """Repeated out-of-index rows plus rows the dedupe must keep."""
    conn = sqlite3.connect(db_file)
    raw = json.dumps({"unstructured": "Smith, J. Some Book. Press, 1990."})
    conn.executemany(
        "INSERT INTO citations (source_article_id, target_doi, target_article_id, raw_reference) "
        "VALUES (?, ?, ?, ?)",
        [(5, "10.9/out", None, None)] * 3          # 2 surplus
        + [(5, None, None, raw)] * 2                # 1 surplus (same text)
        + [(5, None, None, None)] * 2)              # nothing to compare: kept
    conn.commit()
    conn.close()


def test_apply_fix_deletes_only_duplicates_and_is_idempotent(dup_db):
    _add_outside_duplicates(dup_db)
    with db.get_conn() as c:
        first_ids = {(r[0], r[1]): r[2] for r in c.execute(
            "SELECT source_article_id, target_article_id, MIN(id) FROM citations "
            "WHERE target_article_id IS NOT NULL GROUP BY 1, 2")}

    conn = sqlite3.connect(dup_db, isolation_level=None)
    try:
        assert dedupe_citations.apply_fix(conn) == EXTRA_ROWS + 3
        assert all(_guards_present(conn).values())
        kept = {(r[0], r[1]): r[2] for r in conn.execute(
            "SELECT source_article_id, target_article_id, id FROM citations "
            "WHERE target_article_id IS NOT NULL")}
        assert kept == first_ids          # lowest id survives in each group
        assert conn.execute("SELECT COUNT(*) FROM citations WHERE source_article_id=5 "
                            "AND target_article_id IS NULL AND target_doi IS NULL "
                            "AND raw_reference IS NULL").fetchone()[0] == 2
        assert dedupe_citations.apply_fix(conn) == 0
    finally:
        conn.close()
    assert db.get_article_by_id(1)["internal_cited_by_count"] == 8


def test_apply_fix_rolls_back_on_failure(dup_db, monkeypatch):
    monkeypatch.setattr(dedupe_citations, "ensure_citation_unique_indexes",
                        lambda conn: ["idx_citations_unique_target"])
    before = _snapshot(dup_db)
    conn = sqlite3.connect(dup_db, isolation_level=None)
    try:
        with pytest.raises(RuntimeError):
            dedupe_citations.apply_fix(conn)
    finally:
        conn.close()
    assert _snapshot(dup_db) == before


def test_dedupe_dry_run_writes_nothing(dup_db, capsys):
    before = _snapshot(dup_db)
    assert dedupe_citations.main(["--db", str(dup_db)]) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out
    line = next(l for l in out.splitlines() if l.strip().startswith("duplicate rows"))
    assert line.split()[-1] == str(EXTRA_ROWS)
    assert _snapshot(dup_db) == before


def test_dedupe_yes_backs_up_then_fixes(dup_db, tmp_path, capsys, monkeypatch):
    cleared = []
    monkeypatch.setattr(dedupe_citations, "clear_datastories_cache",
                        lambda: cleared.append(True) or 0)
    backups = tmp_path / "backups"
    assert dedupe_citations.main(
        ["--db", str(dup_db), "--yes", "--backup-dir", str(backups)]) == 0
    out = capsys.readouterr().out
    assert "Backup verified" in out and "After:" in out
    assert cleared == [True]

    (backup_file,) = backups.glob("*-pre-citation-dedupe.db")
    with closing(sqlite3.connect(backup_file)) as b:
        assert b.execute("SELECT COUNT(*) FROM citations").fetchone()[0] == 30 + EXTRA_ROWS
    with closing(sqlite3.connect(dup_db)) as c:
        assert c.execute("SELECT COUNT(*) FROM citations").fetchone()[0] == 30
        assert all(_guards_present(c).values())

    # Second run: nothing to do, no second backup.
    assert dedupe_citations.main(
        ["--db", str(dup_db), "--yes", "--backup-dir", str(backups)]) == 0
    assert "Nothing to do" in capsys.readouterr().out
    assert len(list(backups.glob("*.db"))) == 1


# ── citation_duplicates_report.py ────────────────────────────────────────────


def test_report_counts_and_writes_nothing(dup_db, tmp_path, capsys):
    before = _snapshot(dup_db)
    out_file = tmp_path / "report.txt"
    assert citation_duplicates_report.main(
        ["--db", str(dup_db), "--out", str(out_file)]) == 0
    assert _snapshot(dup_db) == before

    text = out_file.read_text(encoding="utf-8")
    assert text in capsys.readouterr().out
    assert "Guard idx_citations_unique_target: MISSING" in text
    line = next(l for l in text.splitlines() if "rows dedupe_citations.py would delete" in l)
    assert line.split()[-1] == str(EXTRA_ROWS)
    # (8, 27) carries the most copies: 1 original + 5.
    assert any(l.split()[:3] == ["8", "27", "6"] for l in text.splitlines())


def test_report_connection_cannot_write(dup_db):
    conn = citation_duplicates_report.open_read_only(str(dup_db))
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM citations")
    finally:
        conn.close()


def test_report_on_clean_db(seeded_db, capsys):
    assert citation_duplicates_report.main(["--db", str(seeded_db)]) == 0
    assert "No duplicate rows" in capsys.readouterr().out


def test_report_refuses_missing_db(tmp_path):
    missing = tmp_path / "nope.db"
    assert citation_duplicates_report.main(["--db", str(missing)]) == 1
    assert not missing.exists()


# ── LiCS scraper ─────────────────────────────────────────────────────────────


@pytest.fixture
def lics_db(fixture_db):
    """A LiCS citing article (id 1) plus two indexed targets."""
    db.upsert_article("https://lics/1", "10.21623/1.9.1.1", "Citing Essay", None, None,
                      "2024-01-01", "Literacy in Composition Studies", "crossref")
    db.upsert_article("https://ex/2", "10.5555/t1", "Target by DOI", None, None,
                      "2010-01-01", "College English", "crossref")
    db.upsert_article("https://ex/3", None, "Literacy Sponsors and the Long Middle",
                      None, None, "2012-01-01", "College English", "crossref")
    return fixture_db


REFS = [
    'Adams, A. "Target by DOI." College English, 2010. doi:10.5555/t1.',
    'Brandt, D. "Literacy Sponsors and the Long Middle." 2012.',
    "Clark, C. A Book. Press, 2019. https://doi.org/10.9999/outside",
    "Diaz, D. Another reference without a DOI or quoted title, 2001.",
]


def _rows_for(article_id):
    with db.get_conn() as conn:
        return conn.execute("SELECT target_doi, target_article_id FROM citations "
                            "WHERE source_article_id = ? ORDER BY id",
                            (article_id,)).fetchall()


def test_store_references_is_idempotent(lics_db):
    doi_map = db.get_doi_to_article_id_map()
    titles = scrape_lics_refs._build_title_index()
    first = scrape_lics_refs.store_references(1, REFS, doi_map, titles)
    rows_first = [tuple(r) for r in _rows_for(1)]
    # The old code deleted only "ref:" rows, so each re-run added another copy
    # of the two DOI-bearing references.
    second = scrape_lics_refs.store_references(1, REFS, doi_map, titles)
    assert first == second == (4, 2)
    assert [tuple(r) for r in _rows_for(1)] == rows_first
    assert {r[1] for r in rows_first} == {2, 3, None}


def test_store_references_clears_copies_left_by_old_runs(lics_db):
    with db.get_conn() as conn:
        for name in GUARDS:
            conn.execute(f"DROP INDEX {name}")
        for _ in range(3):
            conn.execute("INSERT INTO citations (source_article_id, target_doi, "
                         "target_article_id, raw_reference) VALUES (1, '10.5555/t1', 2, ?)",
                         (json.dumps({"unstructured": REFS[0]}),))
        conn.commit()
    scrape_lics_refs.store_references(
        1, REFS, db.get_doi_to_article_id_map(), scrape_lics_refs._build_title_index())
    assert [r[0] for r in _rows_for(1)].count("10.5555/t1") == 1


def test_repeat_match_kept_as_text_not_second_edge(lics_db):
    refs = [REFS[1], 'Brandt, D. "Literacy Sponsors and the Long Middle." Reprint, 2015.']
    stored, matches = scrape_lics_refs.store_references(
        1, refs, db.get_doi_to_article_id_map(), scrape_lics_refs._build_title_index())
    assert (stored, matches) == (2, 1)
    assert [r[1] for r in _rows_for(1)] == [3, None]


def test_process_article_skips_crossref_supplied_article(lics_db, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("should not fetch")
    monkeypatch.setattr(scrape_lics_refs, "_resolve_doi_to_view_url", no_network)
    # A CrossRef reference row plus a scraped copy stacked on it.
    db.upsert_citation(1, "10.5555/t1", 2, {"key": "ref1", "DOI": "10.5555/t1"}, ord=0)
    db.upsert_citation(1, "ref:abc", None, {"unstructured": REFS[3]})
    article = {"id": 1, "doi": "10.21623/1.9.1.1", "title": "Citing Essay"}

    assert scrape_lics_refs.process_article(article, {}, {}, dry_run=True) == 0
    assert len(_rows_for(1)) == 2                     # dry run leaves both
    assert scrape_lics_refs.process_article(article, {}, {}) == 0
    assert [tuple(r) for r in _rows_for(1)] == [("10.5555/t1", 2)]


# ── dedupe_reflections.py repoint under the guards ───────────────────────────


def test_reflections_merge_repoints_without_duplicating(fixture_db):
    import dedupe_reflections
    for url, title in [("https://r/c", "Canon"), ("https://r/d", "Dupe"),
                       ("https://r/x", "Citer")]:
        db.upsert_article(url, None, title, None, None, "2020-01-01",
                          dedupe_reflections.JOURNAL, "crossref")
    c_id, d_id, x_id = 1, 2, 3
    db.upsert_citation(x_id, None, c_id, None)
    db.upsert_citation(x_id, None, d_id, None)       # X cited both copies
    db.upsert_citation(d_id, None, x_id, None)       # the dupe's own reference
    with db.get_conn() as conn:
        cols, rows = dedupe_reflections.fetch_rows(conn)
        by_id = {r["id"]: r for r in rows}
        dedupe_reflections.apply_merge(conn, by_id[c_id], by_id[d_id], cols)
        conn.commit()
        pairs = conn.execute("SELECT source_article_id, target_article_id "
                             "FROM citations ORDER BY 1, 2").fetchall()
    assert [tuple(p) for p in pairs] == [(c_id, x_id), (x_id, c_id)]
