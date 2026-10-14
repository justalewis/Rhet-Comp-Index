"""book_title_repair — cleaning, override application, and the landing-page parse."""

import book_title_repair as btr


def test_clean_title_unescapes_double_escaped_entities():
    assert btr.clean_title("Liminality&amp;#x3a; The Work of Resilience") == \
        "Liminality: The Work of Resilience"
    assert btr.clean_title("Atrav&amp;eacute;s &amp;quot;nas Am&amp;eacute;ricas&amp;quot;") == \
        'Através "nas Américas"'


def test_clean_title_collapses_whitespace_and_strips_tags():
    assert btr.clean_title("Weathering the  Storm ") == "Weathering the Storm"
    assert btr.clean_title("A <i>Techne</i> for Citizens") == "A Techne for Citizens"


def test_clean_title_leaves_clean_titles_alone():
    t = "Writing Studies in Latin America: Seminal Works"
    assert btr.clean_title(t) == t
    assert not btr.is_suspect(t)


def test_replacement_char_and_urls_need_ground_truth():
    assert btr.needs_ground_truth("Am�rica Latina")
    assert btr.needs_ground_truth(btr.clean_title("https&amp;#x3a;&amp;#x2f;&amp;#x2f;x.com"))
    assert not btr.needs_ground_truth("América Latina")


def test_plan_fixes_prefers_override_and_skips_unrecoverable():
    rows = [
        {"id": 1, "doi": "10.37514/A", "title": "Am�rica", "record_type": "book"},
        {"id": 2, "doi": "10.37514/B", "title": "Am�rica", "record_type": "book"},
        {"id": 3, "doi": "10.1/c", "title": "X&amp;#x3a; Y", "record_type": "chapter"},
        {"id": 4, "doi": "10.1/d", "title": "Fine", "record_type": "chapter"},
    ]
    fixes = btr.plan_fixes(rows, {"10.37514/a": "América"})
    assert [(f[0], f[3]) for f in fixes] == [(1, "América"), (3, "X: Y")]


def test_doi_key_tolerates_hand_typed_drift():
    # Pages drop the "10." and sometimes carry the wrong year.
    assert btr._doi_key("37514/PER-B.2015.0735.2.05") == btr._doi_key("10.37514/per-b.2015.0735.2.05")
    assert btr._doi_key("10.37514/TPC-B.2021.1121.2.08") == btr._doi_key("10.37514/tpc-b.2020.1121.2.08")


def test_entry_regex_does_not_swallow_page_chrome():
    page = ('<a href="/">Home</a> Books Journals <a href="/x">Menu</a> lots of nav '
            '<p><a href="/docs/ch1.pdf">&quot;Pandemic Pedagogy&quot; as a Framework</a>, A. Author<br />'
            '<span>DOI: 10.37514/PCW-B.2023.1862.2.01</span></p>')
    titles = [btr.clean_title(m.group("title")) for m in btr._ENTRY_RE.finditer(page)]
    assert titles == ['"Pandemic Pedagogy" as a Framework']


def test_plausible_rejects_mismatched_scrape():
    assert btr._plausible("Teachers’ Beliefs about Peer Review", "Teachers� Beliefs about Peer Review")
    assert not btr._plausible("Section 2. Networks Chapter 5. Basic Writing’s Network",
                              "Chapter 5. Basic Writing�s Network")


def test_match_numbering_follows_the_deposit():
    assert btr._match_numbering("3. Opciones Teóricas", "pciones Te�ricas") == "Opciones Teóricas"
    assert btr._match_numbering("8. Creating", "8. Creating") == "8. Creating"
