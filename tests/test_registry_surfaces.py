"""Places that must know about every journal in journals.py.

Adding a journal to the registry used to be a one-file change that quietly
left other surfaces behind: a sidebar group the Atlas had no blurb for simply
did not render (its journals vanished from the Atlas), and a journal with no
chord-diagram abbreviation was labelled with its full title. These tests fail
loudly instead.
"""

import re
from pathlib import Path

import journals
from coverage_report import tier_journals

ROOT = Path(__file__).resolve().parent.parent


def test_every_sidebar_group_has_an_atlas_orientation():
    """The Atlas only renders groups that have an entry in its
    cluster_orientations dict; a group without one is silently skipped."""
    src = (ROOT / "templates" / "atlas.html").read_text(encoding="utf-8")
    block = src[src.index("cluster_orientations = {"):]
    block = block[: block.index("} %}")]
    keys = set(re.findall(r'^\s*"([^"]+)":\s*$', block, re.M))
    missing = [label for label, _ in journals.JOURNAL_GROUPS if label not in keys]
    assert not missing, f"Atlas has no orientation for: {missing}"


def test_every_registry_journal_has_a_chord_abbreviation():
    src = (ROOT / "static" / "js" / "viz" / "journal_flow.js").read_text(encoding="utf-8")
    block = src[src.index("const JFLOW_ABBREV"):]
    block = block[: block.index("};")]
    have = set(re.findall(r"""['"]((?:[^'"\\]|\\.|"(?=[^:]*'))+?)['"]\s*:""", block))
    # Names containing an apostrophe are written with double quotes in the JS.
    have |= set(re.findall(r'"([^"]+)":', block))
    missing = [n for n in journals.ALL_JOURNAL_NAMES if n not in have]
    assert not missing, f"no JFLOW_ABBREV entry for: {missing}"


def test_chord_abbreviations_are_unique():
    src = (ROOT / "static" / "js" / "viz" / "journal_flow.js").read_text(encoding="utf-8")
    block = src[src.index("const JFLOW_ABBREV"):]
    block = block[: block.index("};")]
    abbrevs = re.findall(r""":\s*['"]([^'"]+)['"]\s*,""", block)
    dupes = {a for a in abbrevs if abbrevs.count(a) > 1}
    assert not dupes, f"two journals share a chord label: {sorted(dupes)}"


def test_every_journal_is_classified_for_datastories():
    """get_journal_group() falls back to OTHER, so a journal that was never
    classified is indistinguishable from one that was classified OTHER on
    purpose. Require an explicit entry for every registry journal."""
    from journal_groups import JOURNAL_GROUPS as FIELD

    # Journals present before the classification was made explicit. New
    # journals must be added to journal_groups.py, not to this list.
    grandfathered = {
        "Revista Latinoamericana de Estudios de la Escritura",
        "Rhetoric, Politics & Culture",
        "Open Words: Access and English Studies",
        "Reflections: A Journal of Community-Engaged Writing and Rhetoric",
    }
    missing = [n for n in journals.ALL_JOURNAL_NAMES
               if n not in FIELD and n not in grandfathered]
    assert not missing, f"not classified in journal_groups.py: {missing}"


# ── coverage tiers ────────────────────────────────────────────────────────────


def _row(name, n, outbound):
    return {"journal": name, "article_count": n, "articles_with_outbound": outbound}


def test_tier_journals_splits_by_reference_share():
    t = tier_journals([
        _row("Technical Communication Quarterly", 100, 80),       # crossref, >= half
        _row("Rhetoric Review", 100, 20),                         # crossref, some
        _row("Poroi", 100, 0),                                    # crossref, none
        _row("Enculturation", 100, 0),                            # rss, none
        _row("Pre/Text", 100, 0),                                 # manual
        _row("Computers and Composition Digital Press", 50, 5),   # press, not a journal
    ])
    names = lambda k: [e["name"] for e in t[k]]
    assert names("with_refs") == ["Technical Communication Quarterly"]
    assert names("partial_refs") == ["Rhetoric Review"]
    assert names("metadata_only") == ["Poroi"]
    assert names("web") == ["Enculturation"]
    assert names("manual") == ["Pre/Text"]
    assert t["counts"]["indexed"] == 5
    assert "Computers and Composition Digital Press" not in sum(
        (names(k) for k in ("with_refs", "partial_refs", "metadata_only", "web", "manual")), [])


def test_tier_journals_half_is_inclusive():
    t = tier_journals([_row("Pedagogy", 10, 5)])
    assert [e["name"] for e in t["with_refs"]] == ["Pedagogy"]


def test_tier_journals_lists_registry_journals_with_no_articles():
    t = tier_journals([_row("Pedagogy", 10, 5), _row("Poroi", 0, 0)])
    assert "Poroi" in t["empty"] and "Pedagogy" not in t["empty"]
    assert "Writing on the Edge" in t["empty"]


def test_coverage_page_renders_generated_tiers(client):
    r = client.get("/coverage")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Journals by what the index holds" in html
    # The hand-kept list this replaced named a journal that is not indexed.
    assert "Journals: full CrossRef coverage" not in html


def test_atlas_renders_one_section_per_group(client):
    r = client.get("/atlas")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # Group labels carry a literal '&', which Jinja escapes in rendered output.
    for label, _ in journals.JOURNAL_GROUPS:
        assert label.replace("&", "&amp;") in html, f"{label} missing from the Atlas"
