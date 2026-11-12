"""classify_citation_ra.py — DataCite Phase 0. HTTP stubbed via responses."""

import json

import responses

import classify_citation_ra as cra
import db


def test_doi_prefix():
    assert cra.doi_prefix("10.5281/zenodo.123") == "10.5281"
    assert cra.doi_prefix(" 10.1080/ABC ") == "10.1080"
    assert cra.doi_prefix("") is None
    assert cra.doi_prefix(None) is None
    assert cra.doi_prefix("not-a-doi/xyz") is None


def test_migration_adds_target_ra(fixture_db):
    with db.get_conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(citations)")}
    assert "target_ra" in cols
    db.init_db()  # idempotent


@responses.activate
def test_lookup_prefixes_maps_agencies_and_unknown():
    responses.add(
        responses.GET,
        cra.DOIRA_URL + "10.1080,10.5281,10.9999",
        json=[
            {"DOI": "10.1080", "RA": "Crossref"},
            {"DOI": "10.5281", "RA": "DataCite"},
            {"DOI": "10.9999", "status": "DOI does not exist"},
        ],
    )
    out = cra.lookup_prefixes(["10.5281", "10.1080", "10.9999"], delay=0)
    assert out == {"10.1080": "crossref", "10.5281": "datacite", "10.9999": "unknown"}


@responses.activate
def test_classify_updates_only_unclassified_and_is_idempotent(fixture_db):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO articles (url, title, journal, source) "
            "VALUES ('http://x/1', 'T', 'J', 'crossref')"
        )
        aid = conn.execute("SELECT id FROM articles").fetchone()[0]
        for doi in ("10.5281/zenodo.1", "10.5281/zenodo.2", "10.1080/abc", None):
            conn.execute(
                "INSERT INTO citations (source_article_id, target_doi) VALUES (?, ?)",
                (aid, doi),
            )
        conn.commit()
        responses.add(
            responses.GET,
            cra.DOIRA_URL + "10.1080,10.5281",
            json=[{"DOI": "10.1080", "RA": "Crossref"}, {"DOI": "10.5281", "RA": "DataCite"}],
        )
        assert cra.classify(conn) == 3
        assert cra.classify(conn) == 0  # nothing left; no second request
        rows = dict(
            conn.execute(
                "SELECT target_doi, target_ra FROM citations WHERE target_doi IS NOT NULL"
            ).fetchall()
        )
    assert rows["10.5281/zenodo.1"] == "datacite"
    assert rows["10.1080/abc"] == "crossref"
    assert len(responses.calls) == 1
