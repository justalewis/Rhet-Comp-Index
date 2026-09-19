"""Atom feed tests — serializer correctness, slug stability, conditional GET.

The stakes are asymmetric here: a malformed feed does not 500, it just makes
every subscriber's reader silently stop showing new items. So these tests lean
on actually parsing the output rather than on substring assertions.
"""

from xml.etree import ElementTree as ET

import pytest

import feeds as feeds_mod
from blueprints.feeds import SITE_URL
from journals import ALL_JOURNAL_NAMES, SLUG_TO_JOURNAL, JOURNAL_TO_SLUG, slugify

ATOM = "{http://www.w3.org/2005/Atom}"


# ── Slugs ────────────────────────────────────────────────────────────────────

def test_every_journal_has_a_unique_slug():
    """Feed URLs are permanent; a collision would repoint a live subscription."""
    assert len(SLUG_TO_JOURNAL) == len(set(ALL_JOURNAL_NAMES))
    assert len(JOURNAL_TO_SLUG) == len(ALL_JOURNAL_NAMES)


def test_slugs_are_url_safe():
    for name, slug in JOURNAL_TO_SLUG.items():
        assert slug, f"{name!r} produced an empty slug"
        assert all(c.isalnum() or c == "-" for c in slug), f"{name!r} → {slug!r}"
        assert not slug.startswith("-") and not slug.endswith("-")


def test_slug_round_trips():
    for name in ALL_JOURNAL_NAMES:
        assert SLUG_TO_JOURNAL[JOURNAL_TO_SLUG[name]] == name


@pytest.mark.parametrize("name,expected", [
    ("Philosophy & Rhetoric", "philosophy-rhetoric"),
    ("Pre/Text", "pre-text"),
    ("WPA: Writing Program Administration", "wpa-writing-program-administration"),
])
def test_slugify_is_stable(name, expected):
    """Pins the published URL shape. If this test fails because slugify was
    'improved', the improvement breaks every existing subscription."""
    assert slugify(name) == expected


# ── Serializer ───────────────────────────────────────────────────────────────

def _article(**over):
    base = {
        "id": 1,
        "title": "A Title",
        "authors": "Jane Smith and John Adams",
        "abstract": "An abstract.",
        "pub_date": "2026-04-01",
        "journal": "College English",
        "doi": "10.1234/ce.1",
        "fetched_at": "2026-04-29 12:00:00",
    }
    base.update(over)
    return base


def _render(articles):
    return feeds_mod.render_atom(
        articles, title="T", self_url="https://pinakes.xyz/feed.xml",
        site_url="https://pinakes.xyz",
    )


def test_render_produces_parseable_atom():
    root = ET.fromstring(_render([_article(), _article(id=2)]))
    assert root.tag == f"{ATOM}feed"
    assert root.findtext(f"{ATOM}updated")
    assert len(root.findall(f"{ATOM}entry")) == 2


def test_feed_declares_the_browser_stylesheet():
    """Without the PI a browser shows raw XML, which tells a person who
    clicked a feed link nothing about what to do with it."""
    xml = _render([_article()])
    assert 'xml-stylesheet' in xml
    assert feeds_mod.STYLESHEET_HREF in xml
    # The PI must precede the root element or the browser ignores it.
    assert xml.index("xml-stylesheet") < xml.index("<feed")


def test_stylesheet_pi_does_not_break_parsing():
    """Feed readers must still see plain Atom underneath the PI."""
    root = ET.fromstring(_render([_article(), _article(id=2)]))
    assert root.tag == f"{ATOM}feed"
    assert len(root.findall(f"{ATOM}entry")) == 2


def test_stylesheet_is_served(client):
    resp = client.get(feeds_mod.STYLESHEET_HREF)
    try:
        assert resp.status_code == 200
        ET.fromstring(resp.data)  # must be well-formed XSLT
    finally:
        # Flask's static handler streams from an open file; the test client
        # does not close it for us and pytest escalates the ResourceWarning.
        resp.close()


def test_entry_ids_are_stable_and_unique():
    root = ET.fromstring(_render([_article(id=7), _article(id=8)]))
    ids = [e.findtext(f"{ATOM}id") for e in root.findall(f"{ATOM}entry")]
    assert ids == ["tag:pinakes.xyz,2026:article/7",
                   "tag:pinakes.xyz,2026:article/8"]


def test_tag_authority_does_not_follow_the_site_domain():
    """TAG_AUTHORITY is an identifier, not an address, and must never be
    updated to match a new canonical hostname.

    Entry ids are how a reader knows it has seen an entry before. Repointing
    the authority re-mints every id in every feed, so every subscriber's
    reader re-shows the entire backlog as unread — silently, with no way to
    reach them and no way back. RFC 4151 tag URIs are deliberately opaque and
    never resolve, so an authority naming a host the project no longer serves
    from is correct, not stale.

    If a domain migration brought you here: leave this alone. SITE_URL in
    blueprints.feeds is the one that follows the canonical host.
    """
    assert feeds_mod.TAG_AUTHORITY == "pinakes.xyz,2026"
    assert SITE_URL.rstrip("/") not in f"tag:{feeds_mod.TAG_AUTHORITY}"


def test_ampersands_and_markup_do_not_break_the_document():
    """The failure this guards: one bad abstract makes the whole feed
    not-well-formed, and every reader drops it silently."""
    nasty = _article(
        title="Rhetoric & Composition <em>Now</em>",
        abstract="Tom & Jerry <jats:italic>ibid</jats:italic> 5 > 3 & 2 < 4",
    )
    xml = _render([nasty])
    root = ET.fromstring(xml)  # raises if malformed
    entry = root.find(f"{ATOM}entry")
    assert entry.findtext(f"{ATOM}title") == "Rhetoric & Composition Now"
    assert "<jats:italic>" not in entry.findtext(f"{ATOM}summary")


def test_illegal_control_characters_are_stripped():
    """XML 1.0 has no escape for these; ElementTree emits them raw and the
    document becomes unparseable. Bad PDF extraction upstream produces them."""
    xml = _render([_article(abstract="before\x0bafter\x00end")])
    root = ET.fromstring(xml)
    summary = root.find(f"{ATOM}entry").findtext(f"{ATOM}summary")
    assert "\x0b" not in summary and "\x00" not in summary


def test_partial_pub_dates_still_yield_published():
    for raw, expect in (("2019", "2019-01-01"), ("2019-04", "2019-04-01")):
        root = ET.fromstring(_render([_article(pub_date=raw)]))
        published = root.find(f"{ATOM}entry").findtext(f"{ATOM}published")
        assert published.startswith(expect)


def test_unparseable_pub_date_omits_published_but_keeps_the_entry():
    root = ET.fromstring(_render([_article(pub_date="n.d.")]))
    entry = root.find(f"{ATOM}entry")
    assert entry.find(f"{ATOM}published") is None
    assert entry.findtext(f"{ATOM}title") == "A Title"


def test_summary_is_truncated():
    root = ET.fromstring(_render([_article(abstract="word " * 400)]))
    summary = root.find(f"{ATOM}entry").findtext(f"{ATOM}summary")
    assert len(summary) <= feeds_mod.SUMMARY_CHARS + 2  # +ellipsis


def test_empty_feed_still_has_required_updated():
    """Atom requires <updated> even with no entries; a reader rejects the feed
    without it, so an empty journal must not produce an invalid document."""
    root = ET.fromstring(_render([]))
    assert root.findtext(f"{ATOM}updated")
    assert root.findall(f"{ATOM}entry") == []


def test_feed_updated_is_the_newest_arrival():
    updated = feeds_mod.feed_updated([
        _article(fetched_at="2026-04-01 00:00:00"),
        _article(fetched_at="2026-04-29 12:00:00"),
        _article(fetched_at="2026-02-01 00:00:00"),
    ])
    assert updated.strftime("%Y-%m-%d") == "2026-04-29"


# ── Routes ───────────────────────────────────────────────────────────────────

def test_feed_all_serves_atom(client):
    resp = client.get("/feed.xml")
    assert resp.status_code == 200
    assert resp.mimetype == "application/atom+xml"
    root = ET.fromstring(resp.data)
    assert root.findall(f"{ATOM}entry"), "seeded DB should yield entries"


def test_feed_sets_caching_headers(client):
    resp = client.get("/feed.xml")
    assert resp.headers["ETag"]
    assert resp.headers["Last-Modified"]
    assert "max-age=1800" in resp.headers["Cache-Control"]
    assert "s-maxage=1800" in resp.headers["Cache-Control"]


def test_matching_etag_returns_304_with_no_body(client):
    """The whole point of the bounded-URL design: polling must be cheap."""
    first = client.get("/feed.xml")
    second = client.get("/feed.xml",
                        headers={"If-None-Match": first.headers["ETag"]})
    assert second.status_code == 304
    assert second.data == b""
    assert second.headers["ETag"] == first.headers["ETag"]


def test_stale_etag_returns_the_body(client):
    resp = client.get("/feed.xml", headers={"If-None-Match": '"nope"'})
    assert resp.status_code == 200
    assert resp.data


def test_if_modified_since_returns_304(client):
    first = client.get("/feed.xml")
    second = client.get("/feed.xml",
                        headers={"If-Modified-Since": first.headers["Last-Modified"]})
    assert second.status_code == 304


def test_malformed_if_modified_since_serves_the_body(client):
    resp = client.get("/feed.xml", headers={"If-Modified-Since": "not a date"})
    assert resp.status_code == 200


def test_journal_feed_is_scoped_to_that_journal(client):
    slug = JOURNAL_TO_SLUG["College English"]
    root = ET.fromstring(client.get(f"/feed/{slug}.xml").data)
    entries = root.findall(f"{ATOM}entry")
    assert entries
    for e in entries:
        cat = e.find(f"{ATOM}category")
        assert cat.get("term") == "College English"


def test_unknown_slug_404s(client):
    assert client.get("/feed/not-a-journal.xml").status_code == 404


def test_feed_entry_count_is_capped(client):
    from blueprints.feeds import FEED_LIMIT
    root = ET.fromstring(client.get("/feed.xml").data)
    assert len(root.findall(f"{ATOM}entry")) <= FEED_LIMIT


def test_empty_database_serves_a_valid_feed(empty_client):
    resp = empty_client.get("/feed.xml")
    assert resp.status_code == 200
    ET.fromstring(resp.data)


def test_feeds_directory_page_lists_every_journal(client):
    resp = client.get("/feeds")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    for name in ALL_JOURNAL_NAMES:
        assert f'href="/feed/{JOURNAL_TO_SLUG[name]}"' in body


def test_feeds_page_offers_a_copyable_absolute_address(client):
    """A relative path pasted into a feed reader is useless; the copy button
    has to carry the full URL."""
    body = client.get("/feeds").get_data(as_text=True)
    assert f'data-feed="{SITE_URL}/feed.xml"' in body


def test_landing_page_serves_html_not_xml(client):
    """A person clicking a journal name gets a page, not raw markup."""
    slug = JOURNAL_TO_SLUG["College English"]
    resp = client.get(f"/feed/{slug}")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)
    assert f"{SITE_URL}/feed/{slug}.xml" in body
    assert "feed reader" in body.lower()


def test_landing_and_raw_urls_do_not_collide(client):
    """/feed/<slug> and /feed/<slug>.xml differ by a suffix; Werkzeug must not
    route the .xml request to the landing page."""
    slug = JOURNAL_TO_SLUG["College English"]
    assert client.get(f"/feed/{slug}").mimetype == "text/html"
    assert client.get(f"/feed/{slug}.xml").mimetype == "application/atom+xml"


def test_landing_page_unknown_slug_404s(client):
    assert client.get("/feed/not-a-journal").status_code == 404


def test_landing_page_handles_a_journal_with_no_articles(client, empty_client):
    """Writing on the Edge has nothing indexed; the page must still explain the
    feed rather than look broken."""
    slug = JOURNAL_TO_SLUG["Writing on the Edge"]
    resp = empty_client.get(f"/feed/{slug}")
    assert resp.status_code == 200
    assert "has not indexed anything" in resp.get_data(as_text=True)


def test_feeds_page_links_journals_to_landing_pages(client):
    """Journal names must not point at the .xml — that is the whole fix."""
    slug = JOURNAL_TO_SLUG["College English"]
    body = client.get("/feeds").get_data(as_text=True)
    assert f'href="/feed/{slug}"' in body


def test_feeds_page_explains_what_to_do_with_an_address(client):
    body = client.get("/feeds").get_data(as_text=True)
    assert "feed reader" in body.lower()
    assert "Paste the address" in body


def test_robots_disallows_feeds(client):
    assert "Disallow: /feed" in client.get("/robots.txt").get_data(as_text=True)


def test_index_advertises_the_feed(client):
    body = client.get("/").get_data(as_text=True)
    assert 'type="application/atom+xml"' in body
    assert 'href="/feed.xml"' in body


def test_single_journal_view_advertises_that_journals_feed(client):
    slug = JOURNAL_TO_SLUG["College English"]
    body = client.get("/?journal=College+English").get_data(as_text=True)
    assert f'href="/feed/{slug}.xml"' in body
    assert 'href="/feed.xml"' not in body


def test_multi_journal_view_falls_back_to_the_all_feed(client):
    """There is no feed for an arbitrary filter combination, so a two-journal
    view must not advertise one it cannot serve."""
    body = client.get(
        "/?journal=College+English&journal=Pre%2FText"
    ).get_data(as_text=True)
    assert 'href="/feed.xml"' in body
    assert "/feed/college-english.xml" not in body


def test_tag_filtered_single_journal_view_still_offers_only_the_journal_feed(client):
    """The journal feed ignores the tag filter. Advertising it here is honest
    only because it is the journal's feed, not 'this search's' feed."""
    slug = JOURNAL_TO_SLUG["College English"]
    body = client.get("/?journal=College+English&tag=pedagogy").get_data(as_text=True)
    assert f'href="/feed/{slug}.xml"' in body


# ── Section feeds ────────────────────────────────────────────────────────────

def test_every_section_has_a_unique_slug():
    from journals import GROUP_SLUG_TO_LABEL, JOURNAL_GROUPS
    assert len(GROUP_SLUG_TO_LABEL) == len(JOURNAL_GROUPS)


def test_section_feed_merges_its_journals(client):
    """A section feed must contain every journal in the section and nothing
    from outside it."""
    from journals import GROUP_SLUG_TO_JOURNALS
    root = ET.fromstring(client.get("/feed/group/rhetoric.xml").data)
    allowed = set(GROUP_SLUG_TO_JOURNALS["rhetoric"])
    terms = {e.find(f"{ATOM}category").get("term")
             for e in root.findall(f"{ATOM}entry")}
    assert terms, "seeded DB should place articles in this section"
    assert terms <= allowed


def test_section_feed_excludes_other_sections(client):
    """Technical Communication must not carry Rhetoric articles."""
    root = ET.fromstring(client.get("/feed/group/technical-communication.xml").data)
    terms = {e.find(f"{ATOM}category").get("term")
             for e in root.findall(f"{ATOM}entry")}
    assert "Pre/Text" not in terms


def test_section_feed_landing_lists_its_journals(client):
    from journals import GROUP_SLUG_TO_JOURNALS
    body = client.get("/feed/group/technical-communication").get_data(as_text=True)
    assert "Technical Communication" in body
    for name in GROUP_SLUG_TO_JOURNALS["technical-communication"]:
        assert name in body


def test_unknown_section_404s(client):
    assert client.get("/feed/group/not-a-section.xml").status_code == 404
    assert client.get("/feed/group/not-a-section").status_code == 404
    assert client.get("/feed/group/not-a-section.opml").status_code == 404


# ── Selection encoding ───────────────────────────────────────────────────────

def test_selection_code_is_order_independent():
    """The address must depend on which journals were picked, not the order the
    boxes happened to be ticked — otherwise two identical selections produce
    two cache entries and two 'different' feeds."""
    from blueprints.feeds import encode_selection
    a = encode_selection(["College English", "Pre/Text"])
    b = encode_selection(["Pre/Text", "College English"])
    assert a == b


def test_selection_round_trips():
    from blueprints.feeds import encode_selection, decode_selection
    names = ["College English", "Pre/Text", "Kairos: A Journal of Rhetoric, Technology, and Pedagogy"]
    assert decode_selection(encode_selection(names)) == sorted(names)


def test_every_journal_has_a_unique_code():
    from journals import CODE_TO_JOURNAL
    assert len(CODE_TO_JOURNAL) == len(ALL_JOURNAL_NAMES)


@pytest.mark.parametrize("bad", [
    "", "zz", "abcde", "abcdefg", "gggggg", "16c97e16c97e",
])
def test_malformed_selection_codes_are_rejected(bad):
    """A code that silently dropped an unknown chunk would hand somebody a feed
    missing the journal they subscribed for, with nothing to show it."""
    from blueprints.feeds import decode_selection
    assert decode_selection(bad) is None


def test_duplicate_codes_in_a_selection_are_rejected():
    from blueprints.feeds import decode_selection
    from journals import JOURNAL_TO_CODE
    code = JOURNAL_TO_CODE["College English"]
    assert decode_selection(code + code) is None


def test_selection_feed_contains_only_the_chosen_journals(client):
    from blueprints.feeds import encode_selection
    code = encode_selection(["College English", "Pre/Text"])
    root = ET.fromstring(client.get(f"/feed/select/{code}.xml").data)
    terms = {e.find(f"{ATOM}category").get("term")
             for e in root.findall(f"{ATOM}entry")}
    assert terms == {"College English", "Pre/Text"}


def test_selection_landing_page_and_404(client):
    from blueprints.feeds import encode_selection
    code = encode_selection(["College English"])
    resp = client.get(f"/feed/select/{code}")
    assert resp.status_code == 200
    assert "College English" in resp.get_data(as_text=True)
    assert client.get("/feed/select/zzzzzz").status_code == 404


def test_selection_form_works_without_javascript(client):
    """The tickbox form is a real GET form; the script only saves a round trip."""
    from journals import JOURNAL_TO_CODE
    resp = client.get("/feeds/select", query_string=[
        ("j", JOURNAL_TO_CODE["College English"]),
        ("j", JOURNAL_TO_CODE["Pre/Text"]),
    ])
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("/feed/select/")


def test_selection_form_with_nothing_ticked_returns_to_the_directory(client):
    resp = client.get("/feeds/select")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/feeds")


def test_selection_form_ignores_unknown_codes(client):
    from journals import JOURNAL_TO_CODE
    resp = client.get("/feeds/select", query_string=[
        ("j", JOURNAL_TO_CODE["College English"]), ("j", "zzzzzz"),
    ])
    assert resp.status_code == 302
    assert resp.headers["Location"] == \
        f"/feed/select/{JOURNAL_TO_CODE['College English']}"


# ── OPML ─────────────────────────────────────────────────────────────────────

def test_opml_lists_each_journal_separately(client):
    resp = client.get("/feed/group/technical-communication.opml")
    assert resp.status_code == 200
    root = ET.fromstring(resp.data)
    outlines = root.findall(".//outline")
    from journals import GROUP_SLUG_TO_JOURNALS
    assert len(outlines) == len(GROUP_SLUG_TO_JOURNALS["technical-communication"])
    for o in outlines:
        assert o.get("xmlUrl", "").startswith(f"{SITE_URL}/feed/")
        assert o.get("xmlUrl", "").endswith(".xml")


def test_opml_is_offered_as_a_download(client):
    resp = client.get("/feed.opml")
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("Content-Disposition", "")
    root = ET.fromstring(resp.data)
    assert len(root.findall(".//outline")) == len(ALL_JOURNAL_NAMES)


def test_opml_escapes_journal_names(client):
    """'Philosophy & Rhetoric' must not break the document."""
    resp = client.get("/feed.opml")
    root = ET.fromstring(resp.data)  # raises if malformed
    titles = {o.get("title") for o in root.findall(".//outline")}
    assert "Philosophy & Rhetoric" in titles


# ── Builder UI ───────────────────────────────────────────────────────────────

def test_builder_form_has_a_checkbox_for_every_journal(client):
    from journals import JOURNAL_TO_CODE
    body = client.get("/feeds").get_data(as_text=True)
    for name in ALL_JOURNAL_NAMES:
        assert f'value="{JOURNAL_TO_CODE[name]}"' in body


def test_builder_checkboxes_have_labels(client):
    """Each input needs a label bound by id, or the form is unusable with a
    screen reader."""
    import re
    from journals import JOURNAL_TO_CODE
    body = client.get("/feeds").get_data(as_text=True)
    for name in ALL_JOURNAL_NAMES:
        code = JOURNAL_TO_CODE[name]
        assert f'id="fb-{code}"' in body
        assert re.search(rf'<label for="fb-{code}">', body)


def test_builder_posts_to_a_real_endpoint(client):
    body = client.get("/feeds").get_data(as_text=True)
    assert 'action="/feeds/select"' in body
    assert 'method="get"' in body


def test_feeds_page_offers_section_feeds(client):
    from journals import GROUP_SLUG_TO_LABEL
    body = client.get("/feeds").get_data(as_text=True)
    for gslug in GROUP_SLUG_TO_LABEL:
        assert f'href="/feed/group/{gslug}"' in body
