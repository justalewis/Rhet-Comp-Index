"""Regression tests for the ingest-boundary safety guards.

Three separate guards, all covering the same underlying situation: the strings
in this index are written by other people. Titles and abstracts arrive in
CrossRef deposits, article and OA links are scraped out of journal HTML, and
DOIs in the ``doi`` column do not all come from CrossRef. None of that is
hostile today, and none of it is ours to trust.

  * ``safe_link``            — no non-http(s) scheme reaches an href
  * ``_parse_abstract`` /
    ``_strip_html``          — decoding never *re-creates* markup a strip removed
  * ``_encode_doi``          — a DOI cannot rewrite the CrossRef request URL
"""

import pytest

import cite_fetcher
import fetcher
import rss_fetcher
from db import is_synthetic_id, safe_link, upsert_article
from db.articles import get_articles


# ── safe_link: scheme allowlist ──────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://doi.org/10.1234/abc",
    "http://kairos.technorhetoric.net/25.1/",
    "  https://example.org/x  ",           # surrounding whitespace is fine
])
def test_safe_link_accepts_http_and_https(url):
    assert safe_link(url) == url.strip()


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "JavaScript:alert(1)",                  # scheme match is case-insensitive
    "java\tscript:alert(1)",                # browsers ignore control chars here
    "java\nscript:alert(1)",
    " javascript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "/relative/path",                       # not an absolute link
    "",
    None,
])
def test_safe_link_rejects_everything_else(url):
    assert safe_link(url) is None


def test_upsert_article_refuses_dangerous_url(fixture_db):
    """`url` is NOT NULL and renders as an href on every list page, so a bad
    scheme drops the row rather than storing something no view should emit."""
    inserted = upsert_article(
        url="javascript:alert(1)", doi=None, title="Hostile",
        authors="A. Author", abstract=None, pub_date="2026-01-01",
        journal="Test Journal", source="scrape",
    )
    assert inserted == 0
    assert get_articles(q="Hostile") == []


def test_upsert_article_drops_dangerous_oa_url_but_keeps_row(fixture_db):
    """oa_url is optional, so a bad one is discarded without losing the article."""
    inserted = upsert_article(
        url="https://example.org/real-article", doi=None, title="Legitimate",
        authors="A. Author", abstract=None, pub_date="2026-01-01",
        journal="Test Journal", source="scrape",
        oa_url="javascript:alert(1)",
    )
    assert inserted == 1
    row = get_articles(q="Legitimate")[0]
    assert row["oa_url"] is None


@pytest.mark.parametrize("url", ["jac:v20n3p447", "pretext:v1i1-2p11"])
def test_safe_link_still_rejects_synthetic_ids(url):
    """safe_link guards *hrefs*, so it stays strict. The synthetic ids are not
    links and must not be handed to a template as if they were."""
    assert safe_link(url) is None
    assert is_synthetic_id(url) is True


@pytest.mark.parametrize("url", ["jac:v20n3p447", "pretext:v1i1-2p11"])
def test_upsert_article_accepts_synthetic_manual_ids(fixture_db, url):
    """JAC (transcribed from CompPile) and Pre/Text (print-only) have no page
    per article, so `url` carries a synthetic id. 1,411 rows in the live corpus
    depend on these surviving re-ingest."""
    assert upsert_article(
        url=url, doi=None, title="Analog Article",
        authors="A. Author", abstract=None, pub_date="1998-01-01",
        journal="JAC: A Journal of Rhetoric, Culture, and Politics",
        source="manual",
    ) == 1


def test_is_synthetic_id_does_not_shadow_a_dangerous_scheme():
    assert is_synthetic_id("javascript:alert(1)") is False
    assert is_synthetic_id("data:text/html,x") is False
    assert is_synthetic_id("") is False
    assert is_synthetic_id(None) is False


def test_upsert_article_still_accepts_normal_links(fixture_db):
    assert upsert_article(
        url="https://example.org/ok", doi="10.1234/ok", title="Ordinary",
        authors="A. Author", abstract=None, pub_date="2026-01-01",
        journal="Test Journal", source="crossref",
        oa_url="https://example.org/ok.pdf",
    ) == 1


# ── Entity decoding must not re-create stripped markup ───────────────────────
#
# The order is the whole point: strip-then-unescape lets an *encoded* tag slip
# past the strip untouched, and the unescape then hands back live markup.

ENCODED_MARKUP = "Rhetoric &lt;img src=x onerror=alert(1)&gt; and delivery"


def test_parse_abstract_does_not_resurrect_encoded_markup():
    out = fetcher._parse_abstract({"abstract": ENCODED_MARKUP})
    assert "<img" not in out
    assert "onerror" not in out


def test_parse_abstract_handles_double_encoding():
    out = fetcher._parse_abstract({"abstract": "A &amp;lt;script&amp;gt; B"})
    assert "<script" not in out


def test_parse_abstract_still_strips_real_jats_and_decodes_entities():
    out = fetcher._parse_abstract(
        {"abstract": "<jats:p>Genre &amp; uptake in <jats:italic>situ</jats:italic></jats:p>"}
    )
    assert out == "Genre & uptake in situ"


def test_parse_abstract_empty_stays_none():
    assert fetcher._parse_abstract({"abstract": ""}) is None
    assert fetcher._parse_abstract({}) is None


def test_rss_strip_html_does_not_resurrect_encoded_markup():
    out = rss_fetcher._strip_html(f"<p>{ENCODED_MARKUP}</p>")
    assert "<img" not in out
    assert "onerror" not in out


def test_rss_strip_html_still_strips_tags_and_collapses_space():
    assert rss_fetcher._strip_html("<p>Writing   &amp;  <b>rhetoric</b></p>") == \
        "Writing & rhetoric"


# ── DOI cannot rewrite the CrossRef request URL ──────────────────────────────

@pytest.mark.parametrize("doi", [
    "10.1234/abc.def",
    "10.1080/07350198.2020.1774830",
    "10.7330/9781646420674.c003",
])
def test_encode_doi_leaves_real_dois_untouched(doi):
    """Real DOIs must round-trip unchanged — the '/' separator stays literal,
    or every existing citation lookup would break."""
    assert cite_fetcher._encode_doi(doi) == doi


def test_encode_doi_escapes_query_and_fragment_characters():
    assert cite_fetcher._encode_doi("10.1234/x?rows=1000") == "10.1234/x%3Frows%3D1000"
    assert cite_fetcher._encode_doi("10.1234/x#frag") == "10.1234/x%23frag"


@pytest.mark.parametrize("doi", [
    "../../heartbeat",
    "not-a-doi",
    "10.1234/with space",
    "",
    None,
])
def test_encode_doi_rejects_malformed(doi):
    assert cite_fetcher._encode_doi(doi) is None


def test_fetch_crossref_work_skips_malformed_doi_without_calling_out(monkeypatch):
    """A malformed DOI must never reach the wire."""
    def _boom(*a, **kw):
        raise AssertionError("requests.get called with a malformed DOI")
    monkeypatch.setattr(cite_fetcher.requests, "get", _boom)
    assert cite_fetcher._fetch_crossref_work("../../heartbeat") is None
