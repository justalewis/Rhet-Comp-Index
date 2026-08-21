"""feeds.py — Atom 1.0 serialization for the journal feeds at /feed.xml and
/feed/<slug>.xml.

Why Atom and not RSS 2.0
------------------------
Atom requires a stable, opaque `<id>` per entry and uses unambiguous RFC 3339
timestamps. RSS 2.0's `<guid>` is optional and its dates are RFC 822, which
readers disagree about. Since the whole point of a feed is "have I seen this
item before," the format with mandatory identity wins.

Why the stdlib and not feedgen
------------------------------
This is ~60 lines of tree-building. `xml.etree.ElementTree` also escapes text
nodes for us, which matters more than it sounds: abstracts in this index still
carry stray ampersands and residual JATS markup from upstream, and a feed that
emits a bare `&` is not merely ugly — readers reject the whole document as
not-well-formed and the subscriber silently stops receiving anything.

Entry identity
--------------
`tag:pinakes.xyz,2026:article/<id>` (RFC 4151). Deliberately opaque and
derived from the primary key rather than the DOI or URL: an article's DOI can
arrive late and its URL can be rewritten by a publisher migration, and either
change would make every reader re-show the whole feed as unread.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

ATOM_NS = "http://www.w3.org/2005/Atom"

# Tag URI authority + date, per RFC 4151. The date is the year the scheme was
# minted and never changes — it is part of the identifier, not a timestamp.
TAG_AUTHORITY = "pinakes.xyz,2026"

FEED_MIMETYPE = "application/atom+xml"

# Browser-facing stylesheet for the feed (see the PI in render_atom).
STYLESHEET_HREF = "/static/feed.xsl"

# XML 1.0 forbids most C0 control characters outright; there is no escape for
# them. Upstream abstracts occasionally carry them (stray \x0b, \x1a from bad
# PDF extraction), and ElementTree will emit them verbatim, producing a feed
# that every reader rejects. Strip rather than escape.
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

SUMMARY_CHARS = 500


def plain_text(value, limit=None):
    """Collapse a stored abstract/title to plain text safe for an XML node."""
    text = _WS.sub(" ", _TAGS.sub(" ", _ILLEGAL_XML.sub("", value or ""))).strip()
    if limit and len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def _parse_sqlite_ts(value):
    """SQLite datetime('now') text → aware UTC datetime, or None.

    Accepts the 'YYYY-MM-DD HH:MM:SS' that our writers produce and the
    ISO 'T'-separated form, since a few backfill scripts used isoformat().
    """
    if not value:
        return None
    raw = str(value).strip().replace("T", " ")
    for fmt, width in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(raw[:width], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _parse_pub_date(value):
    """pub_date → aware UTC datetime, or None. Partial dates ('2019',
    '2019-04') are common upstream; widen them to the first of the period
    rather than dropping the entry's <published> altogether."""
    raw = (value or "").strip()
    for fmt, width in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        if len(raw) >= width:
            try:
                return datetime.strptime(raw[:width], fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _rfc3339(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def feed_updated(articles):
    """Newest arrival time in the payload, as an aware UTC datetime.

    Drives both the feed's <updated> element and the route's Last-Modified
    header, so the two can never disagree. Falls back to now() for an empty
    feed, because Atom requires <updated> even with no entries.
    """
    stamps = [_parse_sqlite_ts(a.get("fetched_at")) for a in articles]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else datetime.now(timezone.utc)


def render_opml(entries, *, title):
    """OPML subscription list — the format every feed reader imports.

    A merged feed collapses several journals into one stream. OPML does the
    opposite: it hands the reader each journal as its own subscription, so they
    stay separately foldered and separately markable-as-read. Both are wanted,
    for different habits, and OPML is the one that answers "give me everything
    in Technical Communication" without flattening the distinction between the
    journals in it.

    `entries` is a sequence of (title, feed_url, site_url) tuples.
    """
    root = ET.Element("opml", {"version": "2.0"})
    head = ET.SubElement(root, "head")
    _sub(head, "title", plain_text(title))
    body = ET.SubElement(root, "body")
    for entry_title, feed_url, site_url in entries:
        ET.SubElement(body, "outline", {
            "type": "rss",
            "text": plain_text(entry_title),
            "title": plain_text(entry_title),
            "xmlUrl": feed_url,
            "htmlUrl": site_url,
        })
    return '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode")


def _sub(parent, tag, text=None, **attrs):
    el = ET.SubElement(parent, tag, {k: v for k, v in attrs.items() if v})
    if text is not None:
        el.text = text
    return el


def render_atom(articles, *, title, self_url, site_url, subtitle=None, feed_id=None):
    """Render articles as an Atom 1.0 document.

    `self_url` is the feed's own canonical address (rel="self", which readers
    use to de-duplicate and to re-subscribe after a redirect); `site_url` is
    the HTML page a human should land on.
    """
    root = ET.Element("feed", {"xmlns": ATOM_NS})
    _sub(root, "title", plain_text(title))
    if subtitle:
        _sub(root, "subtitle", plain_text(subtitle))
    _sub(root, "id", feed_id or self_url)
    _sub(root, "updated", _rfc3339(feed_updated(articles)))
    _sub(root, "link", href=self_url, rel="self", type=FEED_MIMETYPE)
    _sub(root, "link", href=site_url, rel="alternate", type="text/html")
    generator = _sub(root, "generator", "Pinakes")
    generator.set("uri", "https://pinakes.xyz")

    for a in articles:
        entry = ET.SubElement(root, "entry")
        _sub(entry, "title", plain_text(a.get("title")) or "Untitled")
        _sub(entry, "id", f"tag:{TAG_AUTHORITY}:article/{a['id']}")

        article_url = f"{site_url.rstrip('/')}/article/{a['id']}"
        _sub(entry, "link", href=article_url, rel="alternate", type="text/html")

        fetched = _parse_sqlite_ts(a.get("fetched_at"))
        _sub(entry, "updated", _rfc3339(fetched or datetime.now(timezone.utc)))
        published = _parse_pub_date(a.get("pub_date"))
        if published:
            _sub(entry, "published", _rfc3339(published))

        # authors is a single denormalized string ("A, B and C"); Atom wants one
        # <author> each, but splitting a free-text name list is exactly the
        # heuristic that mangles "Smith, Jr." and Spanish surnames. One author
        # element carrying the string as-is is honest and renders correctly.
        if a.get("authors"):
            author = ET.SubElement(entry, "author")
            _sub(author, "name", plain_text(a["authors"]))

        if a.get("journal"):
            _sub(entry, "category", term=plain_text(a["journal"]))

        summary = plain_text(a.get("abstract"), limit=SUMMARY_CHARS)
        if summary:
            el = _sub(entry, "summary", summary)
            el.set("type", "text")

        if a.get("doi"):
            _sub(entry, "link", href=f"https://doi.org/{a['doi']}", rel="related",
                 title="DOI")

    # The xml-stylesheet PI is what a browser uses to render this as a readable
    # page instead of a wall of tags; feed readers ignore it and parse the Atom
    # underneath. People do click feed links, and raw XML tells them nothing
    # about what to do next. See static/feed.xsl.
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<?xml-stylesheet type="text/xsl" href="{STYLESHEET_HREF}"?>\n'
        + ET.tostring(root, encoding="unicode")
    )
