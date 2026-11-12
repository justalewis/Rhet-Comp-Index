"""DataCite Phase 1: fetcher harvest (HTTP stubbed), ingest, redaction, panel."""

import json

import pytest
import responses

import datacite_fetcher as dcf
import db
import ingest_datacite as ing
from db.datacite import get_datacite_for_article

ART_DOI = "10.37514/jwa-j.2018.2.1.09"


def _record(doi, title="Session notes data", rtype="Dataset", creators=None, related=None,
            publisher="Zenodo"):
    return {
        "id": doi,
        "attributes": {
            "doi": doi,
            "titles": [{"title": title}],
            "publisher": publisher,
            "publicationYear": 2020,
            "types": {"resourceTypeGeneral": rtype, "resourceType": rtype},
            "creators": creators or [{"name": "Doe, Jane", "givenName": "Jane", "familyName": "Doe",
                                      "nameIdentifiers": [], "affiliation": []}],
            "relatedIdentifiers": related or [
                {"relatedIdentifier": ART_DOI.upper(), "relatedIdentifierType": "DOI",
                 "relationType": "IsSupplementTo"}],
            "subjects": [], "rightsList": [], "descriptions": [],
            "viewCount": 1, "downloadCount": 2, "citationCount": 3, "url": "https://x",
        },
        "relationships": {"client": {"data": {"id": "cern.zenodo"}}},
    }


@pytest.fixture
def corpus(fixture_db):
    with db.get_conn() as conn:
        conn.execute("INSERT INTO articles (url, title, journal, source, doi) "
                     "VALUES ('http://x/1','JWA piece','JWA','crossref',?)", (ART_DOI,))
        conn.commit()
        aid = conn.execute("SELECT id FROM articles").fetchone()[0]
    return aid


def _dump(tmp_path, entries):
    p = tmp_path / "dump.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    return str(p)


@responses.activate
def test_harvest_method2_matches_case_insensitively_and_records_evidence(corpus):
    rec = _record("10.5281/zenodo.1")
    responses.add(responses.GET, dcf.API, json={"data": [rec], "links": {}})
    with db.get_conn() as conn:
        found = dcf.harvest(conn, methods=(2,), seeds={}, delay=0)
    ev = found["10.5281/zenodo.1"]["evidence"]
    assert ev == [{"method": "relation_to_corpus", "matched_on": ART_DOI,
                   "relation": "IsSupplementTo"}]


@responses.activate
def test_harvest_method1_cited_datacite_doi(corpus):
    with db.get_conn() as conn:
        conn.execute("INSERT INTO citations (source_article_id, target_doi, target_ra) "
                     "VALUES (?, '10.5281/ZENODO.9', 'datacite')", (corpus,))
        conn.commit()
        responses.add(responses.GET, dcf.API,
                      json={"data": [_record("10.5281/zenodo.9", related=[{
                          "relatedIdentifier": "10.1/none", "relationType": "Cites"}])],
                          "links": {}})
        found = dcf.harvest(conn, methods=(1,), seeds={}, delay=0)
    assert found["10.5281/zenodo.9"]["evidence"][0]["method"] == "cited_by_corpus"


@responses.activate
def test_query_related_follows_pagination():
    responses.add(responses.GET, dcf.API,
                  json={"data": [{"id": "a"}], "links": {"next": "https://api.datacite.org/dois?p=2"}})
    responses.add(responses.GET, "https://api.datacite.org/dois?p=2",
                  json={"data": [{"id": "b"}], "links": {}})
    import requests
    out = dcf.query_related(requests.Session(), ["10.1/x"], delay=0)
    assert [r["id"] for r in out] == ["a", "b"]


def test_ingest_builds_tables_and_resolves_relation(corpus, tmp_path):
    entry = {"record": _record("10.5281/zenodo.1"),
             "evidence": [{"method": "relation_to_corpus", "matched_on": ART_DOI,
                           "relation": "IsSupplementTo"}]}
    assert ing.ingest(_dump(tmp_path, [entry])) == 1
    with db.get_conn() as conn:
        w = conn.execute("SELECT * FROM datacite_works").fetchone()
        rel = conn.execute("SELECT * FROM datacite_relations").fetchone()
        ev = conn.execute("SELECT * FROM datacite_scope_evidence").fetchone()
    assert w["tier"] == "A" and w["client_id"] == "cern.zenodo"
    assert rel["article_id"] == corpus
    assert ART_DOI in ev["matched_on"]


def test_ingest_is_idempotent(corpus, tmp_path):
    entry = {"record": _record("10.5281/zenodo.1"), "evidence": [
        {"method": "relation_to_corpus", "matched_on": ART_DOI}]}
    p = _dump(tmp_path, [entry])
    ing.ingest(p); ing.ingest(p)
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM datacite_works").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM datacite_creators").fetchone()[0] == 1


def test_ingest_redacts_ledgered_creator(corpus, tmp_path):
    import redaction
    redaction.redact_author("Jane Doe")
    entry = {"record": _record("10.5281/zenodo.1"), "evidence": [
        {"method": "relation_to_corpus", "matched_on": ART_DOI}]}
    ing.ingest(_dump(tmp_path, [entry]))
    with db.get_conn() as conn:
        c = conn.execute("SELECT name, orcid FROM datacite_creators").fetchone()
    assert "Doe" not in c["name"] and redaction.is_redaction_token(c["name"])


def test_panel_collapses_versions_and_labels_links(corpus, tmp_path):
    concept = {"record": _record("10.5281/zenodo.1"),
               "evidence": [{"method": "relation_to_corpus", "matched_on": ART_DOI}]}
    version = {"record": _record("10.5281/zenodo.1.v2"),
               "evidence": [{"method": "relation_to_corpus", "matched_on": ART_DOI}]}
    ing.ingest(_dump(tmp_path, [concept, version]))
    objs = get_datacite_for_article(corpus)
    assert [o["doi"] for o in objs] == ["10.5281/zenodo.1"]
    assert objs[0]["links"] == ["Supplement to this article"]
    assert objs[0]["creators"] == ["Doe, Jane"]


def test_panel_includes_cited_objects(corpus, tmp_path):
    entry = {"record": _record("10.5281/zenodo.9", related=[{
        "relatedIdentifier": "10.1/none", "relationType": "Cites"}]),
        "evidence": [{"method": "cited_by_corpus", "matched_on": "10.5281/zenodo.9"}]}
    ing.ingest(_dump(tmp_path, [entry]))
    with db.get_conn() as conn:
        conn.execute("INSERT INTO citations (source_article_id, target_doi) "
                     "VALUES (?, '10.5281/Zenodo.9')", (corpus,))
        conn.commit()
    objs = get_datacite_for_article(corpus)
    assert objs[0]["links"] == ["Cited in this article's references"]


def test_article_page_renders_panel(corpus, tmp_path, empty_client):
    entry = {"record": _record("10.5281/zenodo.1"), "evidence": [
        {"method": "relation_to_corpus", "matched_on": ART_DOI}]}
    ing.ingest(_dump(tmp_path, [entry]))
    resp = empty_client.get(f"/article/{corpus}")
    assert resp.status_code == 200
    assert b"Data, software" in resp.data and b"zenodo.1" in resp.data
