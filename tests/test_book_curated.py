"""book_fetcher curated-list ingest, the incremental date filter, and the
weekly-maintenance wiring for the book harvest."""

import book_fetcher
import db

HEADER = "tier\tyear\tpublisher\tseries\ttype\ttitle\tpeople\tisbn\tdoi\n"


def _write(tmp_path, *rows):
    p = tmp_path / "curated.tsv"
    p.write_text(HEADER + "".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")
    return str(p)


def test_curated_list_ingests_and_is_idempotent(seeded_db, tmp_path, monkeypatch):
    monkeypatch.setattr(book_fetcher, "_fetch_doi", lambda doi: None)
    path = _write(
        tmp_path,
        ("A", "2026", "University of Pittsburgh Press", "CLC", "monograph",
         "Nonviolent Response: Strategies for Responding to Writing",
         "Sheri Rysdam; JT Torres", "9780822967897", ""),
        ("A", "2025", "NCTE", "SWR", "edited-collection",
         "Decolonial Possibilities&amp;#x3a; Indigenously Rooted Practices",
         "Lisa King; Andrea Riley Mukavetz (eds.)", "9780814102398", ""),
    )
    before = db.get_book_count()
    assert book_fetcher.fetch_curated_list(path) == (2, 0)
    assert db.get_book_count() == before + 2
    assert book_fetcher.fetch_curated_list(path) == (0, 0)

    eds = db.get_books(q="Decolonial Possibilities", limit=5)[0]
    assert eds["title"] == "Decolonial Possibilities: Indigenously Rooted Practices"
    assert eds["editors"] == "Lisa King; Andrea Riley Mukavetz"
    assert eds["authors"] is None


def test_curated_list_skips_titles_already_present(seeded_db, tmp_path, monkeypatch):
    monkeypatch.setattr(book_fetcher, "_fetch_doi", lambda doi: None)
    existing = db.get_books(limit=1)[0]
    path = _write(tmp_path, ("A", "2020", "Elsewhere", "", "monograph",
                             existing["title"], "Someone", "", ""))
    assert book_fetcher.fetch_curated_list(path) == (0, 0)


def test_incremental_filter_sends_a_bare_date(monkeypatch):
    seen = {}

    def fake_get(url, params=None, retries=3):
        seen.update(params)
        return {"message": {"items": []}}

    monkeypatch.setattr(book_fetcher, "_get", fake_get)
    monkeypatch.setattr(book_fetcher.time, "sleep", lambda s: None)
    book_fetcher._fetch_member_works(1, "monograph", since_date="2026-03-18 23:03:30")
    assert seen["filter"] == "type:monograph,from-update-date:2026-03-18"


def test_book_harvest_is_a_scheduled_maintenance_step():
    import inspect
    import weekly_maintenance
    from blueprints import admin
    assert 12 in {n for n, _, _ in weekly_maintenance.STEPS}
    assert "12)" in inspect.getsource(admin._run_maintenance_in_background)


def test_usu_filter_keeps_writing_studies_only():
    ok = book_fetcher._is_usu_writing_studies
    assert ok({"DOI": "10.7330/9780874213393", "title": ["Situating Portfolios"]})
    assert ok({"DOI": "10.7330/9781646428052", "title": ["Adequate"],
               "subtitle": ["Rewriting the Logics of Success in Rhetoric and Composition"]})
    assert not ok({"DOI": "10.7330/9780874213249", "title": ["Folk Groups And Folklore Genres"]})
    assert not ok({"DOI": "10.5876/9781602232747", "title": ["Coloring the Universe"]})
    assert not ok({"DOI": "10.5876/9781646424597",
                   "title": ["Research, Education and American Indian Partnerships at the Crow Canyon Center"]})
