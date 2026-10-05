"""Journals whose CrossRef deposits carry no ISSN are selected by DOI prefix."""
import journals
from journals import crossref_doi_ok, crossref_query_params

RLEE = "2998-7016"


def test_issn_journals_keep_plain_issn_filter():
    assert crossref_query_params("1554-8244") == {
        "filter": "issn:1554-8244,type:journal-article"}
    assert crossref_doi_ok("1554-8244", "10.37514/anything")


def test_rlee_selected_by_prefix_and_title():
    p = crossref_query_params(RLEE)
    assert p["filter"] == "prefix:10.37514,type:journal-article"
    assert p["query.container-title"].startswith("Revista Latinoamericana")


def test_rlee_doi_filter_rejects_other_wac_journals():
    assert crossref_doi_ok(RLEE, "10.37514/rle-j.2026.3.5.01")
    assert crossref_doi_ok(RLEE, "10.37514/RLE-J.2026.3.5.01")  # case-insensitive
    assert not crossref_doi_ok(RLEE, "10.37514/pei-j.2026.1.1.01")


def test_rlee_mistyped_duplicate_excluded():
    assert not crossref_doi_ok(RLEE, "10.37514/rle-j.2025.4.2.02")
    assert crossref_doi_ok(RLEE, "10.37514/rle-j.2025.2.4.02")


def test_rlee_registered_everywhere_it_must_be():
    name = "Revista Latinoamericana de Estudios de la Escritura"
    assert journals.ISSN_TO_NAME[RLEE] == name
    assert name in journals.GOLD_OA_JOURNALS
    assert name in journals.SLUG_TO_JOURNAL.values()
