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


# ── Field-coverage additions (2026-10) ────────────────────────────────────────

NEW_JOURNALS = {
    "Journal of Basic Writing": "0147-1635",
    "Language and Learning Across the Disciplines": "1091-7098",
    "Academic.Writing": "1552-325X",
    "RhetNet": "2768-251X",
    "Academic Labor: Research and Artistry": "no-issn:alr-j",
    "Journal of Second Language Writing": "1060-3743",
    "Quarterly Journal of Speech": "0033-5630",
    "Argumentation and Advocacy": "1051-1431",
    "Discourse and Writing / Rédactologie": "2563-7320",
    "Journal of Writing Assessment": "2169-9232",
    "Women's Studies in Communication": "1930-6458",
    "Written Language & Literacy": "1387-6732",
}


def test_new_journals_registered_with_expected_issn():
    for name, issn in NEW_JOURNALS.items():
        assert journals.ISSN_TO_NAME[issn] == name


def test_every_journal_is_in_exactly_one_sidebar_group():
    for name in journals.ALL_JOURNAL_NAMES:
        homes = [g for g, names in journals.JOURNAL_GROUPS if name in names]
        assert len(homes) == 1, (name, homes)


def test_writing_research_group_exists_with_expected_members():
    groups = dict(journals.JOURNAL_GROUPS)
    assert set(groups["Writing Research & Assessment"]) == {
        "Assessing Writing", "Journal of Writing Assessment",
        "Journal of Writing Research", "Journal of Second Language Writing",
        "Journal of Academic Writing",
    }


def test_academic_labor_selected_by_doi_stem():
    p = crossref_query_params("no-issn:alr-j")
    assert p["filter"] == "prefix:10.37514,type:journal-article"
    assert p["query.container-title"] == "Academic Labor: Research and Artistry"
    assert crossref_doi_ok("no-issn:alr-j", "10.37514/alr-j.2024.8.1.01")
    assert not crossref_doi_ok("no-issn:alr-j", "10.37514/rle-j.2025.2.4.02")


def test_open_access_flags():
    for n in ("Journal of Basic Writing", "RhetNet", "Academic.Writing",
              "Discourse and Writing / Rédactologie", "Journal of Writing Assessment"):
        assert n in journals.GOLD_OA_JOURNALS
    # Publisher-paywalled titles are left to per-article OA checks.
    for n in ("Quarterly Journal of Speech", "Journal of Second Language Writing"):
        assert n not in journals.GOLD_OA_JOURNALS


def test_datastories_field_classification_for_new_journals():
    from journal_groups import get_journal_group
    assert get_journal_group("Journal of Basic Writing") == "RHET_COMP"
    assert get_journal_group("Journal of Writing Assessment") == "RHET_COMP"
    # QJS is 6,580 articles from 1915 — kept out of the RHET_COMP bucket.
    assert get_journal_group("Quarterly Journal of Speech") == "OTHER"
