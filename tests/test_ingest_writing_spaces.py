"""ingest_writing_spaces: deposit JSON -> articles + citations, scoped to Writing Spaces and safe to re-run."""
import json
import os

import pytest

import ingest_writing_spaces as ws

CH1 = "10.37514/wsp-b.2010.1851.2.01"
CH2 = "10.37514/wsp-b.2010.1851.2.02"
ARC = "10.37514/wsp-b.2010.1851.4.001"


def _data():
    return {
        "journal": ws.JOURNAL,
        "source": "test",
        "volumes": [],
        "items": [
            {"doi": CH1, "kind": "chapter", "volume": 1, "title": "What Is Academic Writing?",
             "authors": ["L. Lennie Irvin"], "pub_date": "2010-06-14", "first_page": "3", "last_page": "17",
             "landing_url": None, "license": None,
             "references": [{"key": "ref1", "unstructured": "Bartholomae, David. Inventing the University."},
                            {"key": "ref2", "unstructured": "Something cited", "DOI": "10.9999/elsewhere"}]},
            {"doi": CH2, "kind": "chapter", "volume": 1, "title": "Navigating Genres",
             "authors": ["Kerry Dirk"], "pub_date": "2010-06-14", "first_page": "249", "last_page": "262",
             "landing_url": None, "license": None, "references": []},
            {"doi": ARC, "kind": "archive", "volume": 1, "title": "Thinking Out Loud: The Prewriting Interview",
             "authors": ["Helen H. Choi"], "pub_date": "2022-09-02", "first_page": None, "last_page": None,
             "landing_url": None, "license": None,
             "references": [{"key": "ref1", "unstructured": "Irvin, What Is Academic Writing?", "DOI": CH1}]},
        ],
    }


def _rows(conn, sql, *args):
    return [tuple(r) for r in conn.execute(sql, args).fetchall()]


def test_load_inserts_items_refs_and_self_links(fixture_db):
    from db import get_conn, init_db, upsert_article, upsert_citation
    init_db()
    # An article already in the index that cites a Writing Spaces chapter by DOI before Writing Spaces was indexed.
    upsert_article("https://doi.org/10.1/x", "10.1/x", "Citing article", "A. Author", None, "2024-01-01",
                   "College English", "crossref")
    with get_conn() as conn:
        citer = conn.execute("SELECT id FROM articles WHERE doi = '10.1/x'").fetchone()[0]
    upsert_citation(citer, CH2, None, {"DOI": CH2}, 0)

    out = ws.load(_data(), log=lambda s: None)
    assert out["inserted"] == 3 and out["articles"] == 3
    assert out["references"] == 3 and out["resolved"] == 1 and out["relinked"] == 1

    with get_conn() as conn:
        rows = {r[0]: r[1:] for r in _rows(conn, """
            SELECT doi, url, journal, volume, pages, first_page, keywords, oa_status, references_fetched_at IS NOT NULL
              FROM articles WHERE journal = ?""", ws.JOURNAL)}
        assert rows[CH1] == (f"https://doi.org/{CH1}", ws.JOURNAL, "1", "3-17", 3, None, "gold", 1)
        assert rows[ARC][3:6] == (None, None, ws.ARCHIVE_LABEL)
        ids = dict(_rows(conn, "SELECT doi, id FROM articles"))
        # the archive item's reference to chapter 1 resolves inside the index
        assert _rows(conn, "SELECT target_article_id FROM citations WHERE source_article_id = ?", ids[ARC]) == [(ids[CH1],)]
        # the pre-existing DOI-only reference to chapter 2 is now linked
        assert _rows(conn, "SELECT target_article_id FROM citations WHERE source_article_id = ?", citer) == [(ids[CH2],)]
        assert conn.execute("SELECT internal_cited_by_count FROM articles WHERE id = ?", (ids[CH1],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM fetch_log WHERE journal = ?", (ws.JOURNAL,)).fetchone()[0] == 1


def test_load_is_idempotent(fixture_db):
    from db import get_conn
    ws.load(_data(), log=lambda s: None)
    again = ws.load(_data(), log=lambda s: None)
    assert again["inserted"] == 0 and again["articles"] == 3
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM citations").fetchone()[0] == 3


def test_load_labels_an_archive_item_the_daily_fetch_inserted_first(fixture_db):
    from db import get_conn, init_db, upsert_article
    init_db()
    upsert_article(f"https://doi.org/{ARC}", ARC, "Thinking Out Loud: The Prewriting Interview", "Helen H. Choi",
                   None, "2022-09-02", ws.JOURNAL, "crossref")
    out = ws.load(_data(), log=lambda s: None)
    assert out["inserted"] == 2
    with get_conn() as conn:
        assert _rows(conn, "SELECT keywords, volume FROM articles WHERE doi = ?", ARC) == [(ws.ARCHIVE_LABEL, "1")]


def test_load_refuses_data_for_another_title(fixture_db):
    d = _data()
    d["journal"] = "Something Else"
    with pytest.raises(SystemExit):
        ws.load(d, log=lambda s: None)


def test_committed_data_file_matches_the_deposit_report():
    with open(ws.DATA_PATH, encoding="utf-8") as f:
        d = json.load(f)
    items = d["items"]
    assert d["journal"] == ws.JOURNAL
    assert sum(i["kind"] == "chapter" for i in items) == 109
    assert sum(i["kind"] == "archive" for i in items) == 112
    assert sum(len(i["references"]) for i in items) == 1619
    assert len({i["doi"] for i in items}) == len(items)
    assert all(i["doi"] == i["doi"].lower() and i["doi"].startswith("10.37514/wsp-b.") for i in items)
    from journals import crossref_doi_ok
    assert all(crossref_doi_ok("no-issn:wsp-b", i["doi"]) for i in items)
    # the public repo must not carry the depositor's contact details
    with open(ws.DATA_PATH, encoding="utf-8") as f:
        raw = f.read()
    assert "depositor" not in raw and "email_address" not in raw
    assert os.path.basename(ws.DATA_PATH) == "writing_spaces.json"
