"""DataCite Phases 2-3: scoring, tiers, Methods 3-5, curation, pages, precision."""

import json

import pytest
import responses

import datacite_fetcher as dcf
import datacite_precision as prec
import datacite_scope as scope
import db
import ingest_datacite as ing
from db import datacite as dc

PHRASE = scope.discovery_terms()[0]          # a real multi-word tagger trigger
# Seven distinct vocabulary tags as subjects: scores 21, above every tier threshold.
STRONG = [next(p for p in pats if " " in p) for pats in scope.VOCAB.values() if any(" " in p for p in pats)][:7]
ORCID = "0000-0002-1825-0097"
TOKEN = "test-admin-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _rec(doi, title="A deposit", subjects=(), desc="", creators=None, rtype="Dataset", related=()):
    return {
        "id": doi,
        "attributes": {
            "doi": doi, "titles": [{"title": title}], "publisher": "Zenodo",
            "publicationYear": 2021,
            "types": {"resourceTypeGeneral": rtype, "resourceType": rtype},
            "creators": creators or [{"name": "Doe, Jane", "givenName": "Jane", "familyName": "Doe",
                                      "nameIdentifiers": [{"nameIdentifier": f"https://orcid.org/{ORCID}",
                                                           "nameIdentifierScheme": "ORCID"}],
                                      "affiliation": []}],
            "relatedIdentifiers": [{"relatedIdentifier": d, "relatedIdentifierType": "DOI", "relationType": r} for d, r in related], "subjects": [{"subject": s} for s in subjects],
            "rightsList": [], "descriptions": [{"descriptionType": "Abstract", "description": desc}],
            "fundingReferences": [{"funderName": "NEH"}],
            "viewCount": 0, "downloadCount": 0, "citationCount": 0, "url": "https://x",
        },
        "relationships": {"client": {"data": {"id": "c.x"}}},
    }


def _entry(rec, *evidence):
    return {"record": rec, "evidence": list(evidence)}


def _dump(tmp_path, entries):
    p = tmp_path / "dump.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    return str(p)


# ── scope module ─────────────────────────────────────────────────────────────

def test_discovery_terms_are_multiword_only():
    terms = scope.discovery_terms()
    assert terms and all(" " in t for t in terms)


def test_score_weights_subject_over_title_over_description():
    s_subj, _ = scope.score_fields("", "", [PHRASE])
    s_title, _ = scope.score_fields(PHRASE, "", [])
    s_desc, _ = scope.score_fields("", PHRASE, [])
    assert s_subj > s_title > s_desc > 0


def test_score_ignores_html_and_unrelated_text():
    assert scope.score_fields("<b>Perovskite solar cells</b>", "battery chemistry", ["materials"])[0] == 0


@pytest.mark.parametrize("methods,score,tier", [
    (["cited_by_corpus"], 0, "A"),
    (["relation_to_corpus"], 0, "A"),
    (["author_in_corpus"], scope.TIER_B_MIN, "B"),
    (["seed"], scope.TIER_B_MIN, "B"),
    (["author_in_corpus"], scope.TIER_B_MIN - 1, "C"),
    (["author_in_corpus"], scope.AUTHOR_MIN - 1, None),
    (["topical"], scope.DISCOVERY_MIN, "C"),
    (["topical"], scope.DISCOVERY_MIN - 1, None),
    (["topical", "author_in_corpus"], scope.TIER_B_MIN, "B"),
    (["weak_relation"], scope.TIER_B_MIN, "B"),
    (["weak_relation"], scope.TIER_B_MIN - 1, None),
])
def test_tier_for(methods, score, tier):
    assert scope.tier_for(methods, score) == tier


def test_load_seeds_parses_kinds_and_skips_comments(tmp_path):
    p = tmp_path / "s.tsv"
    p.write_text("# c\nkind\tvalue\tquery\tnote\nterm\tfoo bar\t\tn\n\nclient\tcos.osf\tq1\t\n",
                 encoding="utf-8")
    seeds = scope.load_seeds(str(p))
    assert seeds["term"] == [("foo bar", "", "n")]
    assert seeds["client"] == [("cos.osf", "q1", "")]
    assert scope.load_seeds(str(tmp_path / "missing.tsv")) == {}


def test_shipped_seed_file_parses():
    seeds = scope.load_seeds()
    assert seeds.get("client"), "shipped seeds should include at least the OSF narrowing example"
    assert all(q for _, q, _ in seeds["client"]), "client seeds must carry a narrowing query"


# ── fetcher methods 3-5 ──────────────────────────────────────────────────────

@pytest.fixture
def with_author(fixture_db):
    with db.get_conn() as conn:
        conn.execute("INSERT INTO authors (name, orcid) VALUES ('Jane Doe', ?)",
                     (f"https://orcid.org/{ORCID}",))
        conn.commit()
    return fixture_db


@responses.activate
def test_method3_queries_both_orcid_forms_and_records_evidence(with_author):
    responses.add(responses.GET, dcf.API, json={"data": [_rec("10.5281/z.1")], "links": {}})
    with db.get_conn() as conn:
        found = dcf.harvest(conn, methods=(3,), seeds={}, delay=0)
    q = responses.calls[0].request.url
    assert ORCID in q and "orcid.org" in q          # bare and URL forms both queried
    assert found["10.5281/z.1"]["evidence"] == [
        {"method": "author_in_corpus", "matched_on": ORCID}]


@responses.activate
def test_method5_keeps_only_records_above_threshold(fixture_db):
    good = _rec("10.5281/good", subjects=STRONG)
    bad = _rec("10.5281/bad", title="Perovskite cells")
    responses.add(responses.GET, dcf.API, json={"data": [good, bad], "links": {}})
    with db.get_conn() as conn:
        found = dcf.harvest(conn, methods=(5,), seeds={}, limit=1, delay=0)
    assert list(found) == ["10.5281/good"]
    ev = found["10.5281/good"]["evidence"][0]
    assert ev["method"] == "topical" and ev["score"] >= scope.DISCOVERY_MIN


@responses.activate
def test_method4_seeded_client_passes_narrowing_query(fixture_db):
    responses.add(responses.GET, dcf.API, json={"data": [_rec("10.17605/osf.1")], "links": {}})
    seeds = {"client": [("cos.osf", 'titles.title:"writing center"', "")]}
    with db.get_conn() as conn:
        found = dcf.harvest(conn, methods=(4,), seeds=seeds, delay=0)
    url = responses.calls[0].request.url
    assert "client-id=cos.osf" in url and "writing+center" in url.replace("%20", "+").replace("%22", "")
    assert found["10.17605/osf.1"]["evidence"][0] == {"method": "seed", "matched_on": "client:cos.osf"}


# ── ingest tiering, curation, redaction ──────────────────────────────────────

def _tiers(conn):
    return {r["doi"]: r["tier"] for r in conn.execute("SELECT doi, tier FROM datacite_works")}


def test_ingest_assigns_b_c_and_drops_weak_topical(with_author, tmp_path):
    entries = [
        _entry(_rec("10.5/b", subjects=STRONG), {"method": "author_in_corpus", "matched_on": ORCID}),
        _entry(_rec("10.5/c", title="Unrelated photos", subjects=STRONG[:1]), {"method": "author_in_corpus", "matched_on": ORCID}),
        _entry(_rec("10.5/t", subjects=STRONG), {"method": "topical", "matched_on": PHRASE, "score": 21}),
        _entry(_rec("10.5/drop", title="Chemistry"), {"method": "topical", "matched_on": PHRASE, "score": 1}),
    ]
    ing.ingest(_dump(tmp_path, entries))
    with db.get_conn() as conn:
        t = _tiers(conn)
        w = conn.execute("SELECT * FROM datacite_works WHERE doi='10.5/b'").fetchone()
        creator = conn.execute("SELECT author_id FROM datacite_creators WHERE work_doi='10.5/b'").fetchone()
    assert t == {"10.5/b": "B", "10.5/c": "C", "10.5/t": "C"}
    assert w["topic_score"] >= scope.TIER_B_MIN and json.loads(w["funders"]) == ["NEH"]
    assert creator["author_id"] is not None          # ORCID matched to authors row


def test_curation_survives_reingest_and_gates_visibility(with_author, tmp_path):
    p = _dump(tmp_path, [
        _entry(_rec("10.5/c1", title="Photos", subjects=STRONG[:1]), {"method": "author_in_corpus", "matched_on": ORCID}),
        _entry(_rec("10.5/c2", title="Scans", subjects=STRONG[:1]), {"method": "author_in_corpus", "matched_on": ORCID}),
    ])
    ing.ingest(p)
    assert {r["doi"] for r in dc.get_curation_queue()} == {"10.5/c1", "10.5/c2"}
    assert dc.queue_size() == 2
    assert dc.decide_curation("10.5/c1", "approved", actor="t")["decision"] == "approved"
    assert dc.decide_curation("10.5/c2", "rejected", reason="not field", actor="t")["reason"] == "not field"
    assert dc.decide_curation("10.5/missing", "approved") is None
    with pytest.raises(ValueError):
        dc.decide_curation("10.5/c1", "maybe")
    ing.ingest(p)                                    # rebuild wipes works, keeps decisions
    with db.get_conn() as conn:
        assert _tiers(conn) == {"10.5/c1": "curated", "10.5/c2": "rejected"}
    assert dc.queue_size() == 0
    assert [o["doi"] for o in dc.get_datacite_for_author(1)] == ["10.5/c1"]   # rejected stays hidden


def test_rejected_never_appears_on_public_queries(with_author, tmp_path):
    ing.ingest(_dump(tmp_path, [_entry(_rec("10.5/x", title="Photos", subjects=STRONG[:1]),
                                       {"method": "author_in_corpus", "matched_on": ORCID})]))
    dc.decide_curation("10.5/x", "rejected")
    assert dc.get_datacite_for_author(1) == []
    assert dc.overview()["published"] == 0


# ── routes ───────────────────────────────────────────────────────────────────

def test_admin_queue_requires_token(empty_client, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", TOKEN)
    assert empty_client.get("/api/admin/datacite/queue").status_code == 401
    assert empty_client.post("/api/admin/datacite/decide", json={}).status_code == 401


def test_admin_decide_flow(with_author, empty_client, tmp_path, monkeypatch):
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", TOKEN)
    ing.ingest(_dump(tmp_path, [_entry(_rec("10.5/q", title="Photos", subjects=STRONG[:1]),
                                       {"method": "author_in_corpus", "matched_on": ORCID})]))
    r = empty_client.get("/api/admin/datacite/queue", headers=AUTH).get_json()
    assert r["pending"] == 1 and r["items"][0]["doi"] == "10.5/q" and r["items"][0]["evidence"]
    bad = empty_client.post("/api/admin/datacite/decide", headers=AUTH, json={"doi": "10.5/q", "decision": "x"})
    assert bad.status_code == 400
    nf = empty_client.post("/api/admin/datacite/decide", headers=AUTH, json={"doi": "nope", "decision": "approved"})
    assert nf.status_code == 404
    ok = empty_client.post("/api/admin/datacite/decide", headers=AUTH,
                           json={"doi": "10.5/q", "decision": "approved"})
    assert ok.status_code == 200
    assert empty_client.get("/api/admin/datacite/queue", headers=AUTH).get_json()["pending"] == 0


def test_admin_page_shell_embeds_no_data(empty_client):
    resp = empty_client.get("/admin/datacite")
    assert resp.status_code == 200 and b"noindex" in resp.data


def test_dashboard_and_api_render_on_empty_and_populated(with_author, empty_client, tmp_path):
    assert empty_client.get("/datacite").status_code == 200
    ing.ingest(_dump(tmp_path, [_entry(_rec("10.5/s", title="A tool", rtype="Software"),
                                       {"method": "relation_to_corpus", "matched_on": "10.1/a"})]))
    page = empty_client.get("/datacite")
    assert page.status_code == 200 and b"Software" in page.data and b"lower bound" in page.data
    assert empty_client.get("/api/datacite/types").get_json()["totals"][0]["type"] == "Software"
    assert empty_client.get("/api/datacite/no-such-analysis").status_code == 404
    for name in ("overview", "repositories", "supplement-by-journal", "supplement-by-year",
                 "cited-beyond-articles", "builders", "tool-lifecycles", "licenses", "funders",
                 "usage-vs-citation", "shared-objects"):
        assert empty_client.get(f"/api/datacite/{name}").status_code == 200, name


def test_author_page_shows_research_objects(with_author, empty_client, tmp_path):
    ing.ingest(_dump(tmp_path, [_entry(_rec("10.5/s", title="Corpus tool", rtype="Software", subjects=STRONG[:1]),
                                       {"method": "author_in_corpus", "matched_on": ORCID},
                                       {"method": "topical", "matched_on": PHRASE, "score": 21})]))
    # no topical text -> candidate, so hidden...
    assert b"Research objects" not in empty_client.get("/author/Jane%20Doe").data
    dc.decide_curation("10.5/s", "approved")
    resp = empty_client.get("/author/Jane%20Doe")
    assert resp.status_code == 200 and b"Research objects" in resp.data and b"Corpus tool" in resp.data


def test_coverage_page_includes_datacite_section(with_author, empty_client, tmp_path):
    ing.ingest(_dump(tmp_path, [_entry(_rec("10.5/s", title="A tool", rtype="Software"),
                                       {"method": "relation_to_corpus", "matched_on": "10.1/a"})]))
    resp = empty_client.get("/coverage")
    assert resp.status_code == 200 and b"research objects beyond Crossref" in resp.data


# ── analyses ─────────────────────────────────────────────────────────────────

def test_supplement_rate_and_builders(with_author, tmp_path):
    with db.get_conn() as conn:
        for i in range(30):
            conn.execute("INSERT INTO articles (url, title, journal, source, doi, pub_date) "
                         "VALUES (?,?,?,?,?,?)", (f"http://x/{i}", "T", "JWA", "crossref",
                                                  f"10.1/a{i}", "2020-01-01"))
        conn.commit()
    ing.ingest(_dump(tmp_path, [_entry(_rec("10.5/s", title="Tool", rtype="Software"),
                                       {"method": "relation_to_corpus", "matched_on": "10.1/a0"})]))
    with db.get_conn() as conn:
        aid = conn.execute("SELECT id FROM articles WHERE doi='10.1/a0'").fetchone()[0]
        conn.execute("INSERT INTO datacite_relations (work_doi, relation_type, related_id, "
                     "related_id_type, article_id) VALUES ('10.5/s','IsSupplementTo','10.1/a0','DOI',?)",
                     (aid,))
        conn.commit()
    by_j = dc.supplement_rate("journal")
    assert by_j == [{"label": "JWA", "articles": 30, "linked": 1, "rate": 3.33}]
    assert dc.supplement_rate("year", min_articles=30)[0]["label"] == "2020"
    assert dc.infrastructure_builders()[0]["name"] == "Jane Doe"
    assert dc.tool_lifecycles()[0]["title"] == "Tool"
    assert dc.funders() == [{"funder": "NEH", "n": 1}]


# ── precision study ──────────────────────────────────────────────────────────

def test_precision_sample_and_report_roundtrip(with_author, tmp_path):
    ing.ingest(_dump(tmp_path, [
        _entry(_rec(f"10.5/a{i}"), {"method": "relation_to_corpus", "matched_on": "10.1/a"})
        for i in range(6)]))
    with db.get_conn() as conn:
        rows = prec.draw_sample(conn, per_tier=4, seed=7)
        assert rows == prec.draw_sample(conn, per_tier=4, seed=7)       # reproducible
    assert len(rows) == 4 and {r["tier"] for r in rows} == {"A"}
    for i, r in enumerate(rows):
        r["in_field"] = "1" if i < 3 else "0"
    rep = prec.compute_report(rows)
    assert rep["tiers"] == [{"tier": "A", "n": 4, "in_field": 3, "precision": 0.75}]


def test_repositories_fold_case_variants(with_author, tmp_path):
    a, b = _rec("10.5/a"), _rec("10.5/b")
    a["attributes"]["publisher"], b["attributes"]["publisher"] = "figshare", "Figshare"
    ev = {"method": "relation_to_corpus", "matched_on": "10.1/a"}
    ing.ingest(_dump(tmp_path, [_entry(a, ev), _entry(b, ev)]))
    assert dc.repositories() == [{"repository": "Figshare", "n": 2}]


def test_effective_methods_separates_weak_from_strong_relations():
    weak = [{"method": "relation_to_corpus", "matched_on": "x", "relation": "References"}]
    strong = [{"method": "relation_to_corpus", "matched_on": "x", "relation": "IsSupplementTo"}]
    unknown = [{"method": "relation_to_corpus", "matched_on": "x"}]
    assert scope.effective_methods(weak) == {"weak_relation"}
    assert scope.effective_methods(strong) == {"relation_to_corpus"}
    assert scope.effective_methods(unknown) == {"relation_to_corpus"}
    assert scope.effective_methods(weak + strong) == {"weak_relation", "relation_to_corpus"}


def test_weak_relation_only_record_is_dropped_unless_topical(fixture_db, tmp_path):
    weak = {"method": "relation_to_corpus", "matched_on": "10.1/a", "relation": "References"}
    # A bibliography-style supplement: "References" the article, nothing topical.
    junk = _entry(_rec("10.5/junk", title="Additional file 1 of a trial"), weak)
    on_topic = _entry(_rec("10.5/topic", subjects=STRONG), weak)
    ing.ingest(_dump(tmp_path, [junk, on_topic]))
    with db.get_conn() as conn:
        assert _tiers(conn) == {"10.5/topic": "B"}


def test_article_panel_excludes_weak_relations_but_keeps_strong(fixture_db, tmp_path, monkeypatch):
    with db.get_conn() as conn:
        conn.execute("INSERT INTO articles (url, title, journal, source, doi) "
                     "VALUES ('http://x/1','T','J','crossref','10.1/a')")
        aid = conn.execute("SELECT id FROM articles").fetchone()[0]
        conn.commit()
    strong = _entry(_rec("10.5/s", title="Real supplement", related=[("10.1/a", "IsSupplementTo")]),
                    {"method": "relation_to_corpus", "matched_on": "10.1/a", "relation": "IsSupplementTo"})
    weak_topical = _entry(_rec("10.5/w", subjects=STRONG, related=[("10.1/a", "Cites")]),
                          {"method": "relation_to_corpus", "matched_on": "10.1/a", "relation": "Cites"})
    ing.ingest(_dump(tmp_path, [strong, weak_topical]))
    # B is held for review by default, so only the strong (A) record is public ...
    assert dc.overview()["published"] == 1
    # ... and it is the one presented as something behind the article
    assert [o["doi"] for o in dc.get_datacite_for_article(aid)] == ["10.5/s"]
    assert dc.overview()["linked_articles"] == 1
    # with B publishing on, the weak-relation topical record is public too but
    # still not presented as something behind the article
    monkeypatch.setattr(dc, "_PUB", "('A','B','curated')")
    assert dc.overview()["published"] == 2
    assert [o["doi"] for o in dc.get_datacite_for_article(aid)] == ["10.5/s"]


def test_overview_separates_cited_only_records(fixture_db, tmp_path):
    cited = _entry(_rec("10.5/c"), {"method": "cited_by_corpus", "matched_on": "10.5/c"})
    linked = _entry(_rec("10.5/l", related=[("10.1/a", "IsSupplementTo")]),
                    {"method": "relation_to_corpus", "matched_on": "10.1/a", "relation": "IsSupplementTo"})
    both = _entry(_rec("10.5/b"), {"method": "cited_by_corpus", "matched_on": "10.5/b"},
                  {"method": "relation_to_corpus", "matched_on": "10.1/a", "relation": "IsSupplementTo"})
    ing.ingest(_dump(tmp_path, [cited, linked, both]))
    ov = dc.overview()
    assert ov["published"] == 3 and ov["cited_only"] == 1


def test_orcid_batch_query_fits_in_a_url():
    """Regression: 25 ORCIDs per query overflowed DataCite's URL limit (HTTP 414)."""
    import requests
    orcids = [f"0000-0001-{i:04d}-{i:03d}X" for i in range(dcf.ORCID_BATCH)]
    req = requests.Request("GET", dcf.API, params={"query": dcf.orcid_query(orcids),
                                                    "page[size]": 1000, "page[cursor]": 1}).prepare()
    assert len(req.url) < dcf.MAX_QUERY_URL


@responses.activate
def test_failed_method_keeps_other_methods_results_and_reports_error(with_author):
    ok = _rec("10.5281/ok", subjects=STRONG)
    # method 3 (ORCID query) fails with a non-retryable 400; method 5 succeeds
    responses.add(responses.GET, dcf.API, status=400, match=[
        responses.matchers.query_param_matcher({"page[size]": "1000", "page[cursor]": "1",
                                                "query": dcf.orcid_query([ORCID])})])
    responses.add(responses.GET, dcf.API, json={"data": [ok], "links": {}})
    errors = []
    with db.get_conn() as conn:
        found = dcf.harvest(conn, methods=(3, 5), seeds={}, limit=1, delay=0, errors=errors)
    assert list(found) == ["10.5281/ok"]
    assert len(errors) == 1 and errors[0].startswith("method 3")


def test_tier_b_is_held_for_review_until_published_or_approved(with_author, tmp_path, monkeypatch):
    assert "B" not in scope.PUBLISHED_TIERS and "B" in scope.REVIEW_TIERS
    ing.ingest(_dump(tmp_path, [_entry(_rec("10.5/b", subjects=STRONG),
                                       {"method": "author_in_corpus", "matched_on": ORCID})]))
    assert dc.overview()["published"] == 0 and dc.queue_size() == 1
    assert [q["doi"] for q in dc.get_curation_queue()] == ["10.5/b"]
    assert dc.get_datacite_for_author(1) == []
    dc.decide_curation("10.5/b", "approved")           # approval publishes it as curated
    ing.ingest(_dump(tmp_path, [_entry(_rec("10.5/b", subjects=STRONG),
                                       {"method": "author_in_corpus", "matched_on": ORCID})]))
    assert dc.queue_size() == 0 and [o["doi"] for o in dc.get_datacite_for_author(1)] == ["10.5/b"]
