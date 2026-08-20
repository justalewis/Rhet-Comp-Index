"""Atom feed tests — serializer correctness, slug stability, conditional GET.

The stakes are asymmetric here: a malformed feed does not 500, it just makes
every subscriber's reader silently stop showing new items. So these tests lean
on actually parsing the output rather than on substring assertions.
"""

from xml.etree import ElementTree as ET

import pytest

import feeds as feeds_mod
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
        assert f"/feed/{JOURNAL_TO_SLUG[name]}.xml" in body


def test_feeds_page_offers_a_copyable_absolute_address(client):
    """A relative path pasted into a feed reader is useless; the copy button
    has to carry the full URL."""
    slug = JOURNAL_TO_SLUG["College English"]
    body = client.get("/feeds").get_data(as_text=True)
    assert f'data-feed="https://pinakes.xyz/feed/{slug}.xml"' in body
    assert 'data-feed="https://pinakes.xyz/feed.xml"' in body


def test_landing_page_serves_html_not_xml(client):
    """A person clicking a journal name gets a page, not raw markup."""
    slug = JOURNAL_TO_SLUG["College English"]
    resp = client.get(f"/feed/{slug}")
    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)
    assert f"https://pinakes.xyz/feed/{slug}.xml" in body
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
