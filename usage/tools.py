"""usage/tools.py — which part of Pinakes a request belongs to.

Every recorded request gets an `area` (browse, search, record, explore,
datastories, wac, feeds, ...) and, when it is one of the analytical tools, a
`tool` key. Tool keys follow the Explore page's own URL hashes where there is
one (main-path, half-life, ...) so a server-side API call and a browser-side
tab switch name the same tool the same way.

`auto` marks tools that run without the reader choosing them: Author Network
is the tab Explore opens on, and the author-page charts load with the page.
Reports footnote those rather than presenting them as deliberate use.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    area: str
    tool: str | None = None
    label: str | None = None
    auto: bool = False


# Explore tools, by the endpoint that computes them.
_EXPLORE = {
    "stats.api_timeline":                  ("timeline", "Timeline"),
    "stats.api_tag_cooccurrence":          ("topics", "Topics"),
    "stats.api_author_network":            ("author-network", "Author Network"),
    "stats.api_most_cited":                ("citations", "Most Cited"),
    "stats.api_institutions":              ("institutions", "Institutions"),
    "citations.api_citation_trends":       ("citation-trends", "Citation Trends"),
    "citations.api_citations_network":     ("citation-network", "Citation Network"),
    "citations.api_citation_centrality":   ("centrality", "Centrality"),
    "citations.api_cocitation_network":    ("cocitation", "Co-Citation"),
    "citations.api_bibcoupling_network":   ("bibcoupling", "Bibliographic Coupling"),
    "citations.api_journal_citation_flow": ("journal-flow", "Journal Flow"),
    "citations.api_citation_half_life":    ("half-life", "Half-Life"),
    "citations.api_citation_communities":  ("communities", "Communities"),
    "citations.api_main_path":             ("main-path", "Main Path"),
    "citations.api_temporal_evolution":    ("temporal-evolution", "Temporal Evolution"),
    "citations.api_sleeping_beauties":     ("sleeping-beauties", "Sleeping Beauties"),
    "citations.api_reading_path":          ("reading-path", "Reading Path"),
    "citations.api_ego_network":           ("ego-network", "Citation Neighborhood"),
    "authors.api_author_cocitation":       ("author-cocitation", "Author Co-Citation"),
}

# Tools that load on their own.
_AUTO = {"author-network", "author-profile"}

# Explore tab hashes whose name differs from the tool key.
HASH_ALIASES = {"citnet": "citation-network", "authors": "author-network"}

_PAGES = {
    "main.index":                     Tool("browse"),
    "main.new_articles":              Tool("browse"),
    "authors.authors_list":           Tool("browse"),
    "books.books":                    Tool("browse"),
    "articles.api_articles":          Tool("browse"),
    "articles.article_detail":        Tool("record"),
    "authors.author_detail":          Tool("record"),
    "books.book_detail":              Tool("record"),
    "institutions.institution_detail": Tool("record"),
    "articles.api_article_search":    Tool("search", "typeahead", "Search suggestions"),
    "main.explore":                   Tool("explore"),
    "main.tools":                     Tool("explore"),
    "main.citation_network_page":     Tool("explore", "citations-page", "Citations page"),
    "main.atlas":                     Tool("explore", "atlas", "Atlas"),
    "stats.most_cited_page":          Tool("explore", "most-cited-page", "Most Cited page"),
    "main.about":                     Tool("docs"),
    "main.glossary":                  Tool("docs"),
    "main.coverage":                  Tool("docs"),
    "main.design_icons":              Tool("docs"),
    "main.export":                    Tool("export", "export", "Export (BibTeX/RIS)"),
    "datastories.datastories_landing": Tool("datastories"),
    "datastories.datastories_page":   Tool("datastories"),
    "wac.wac_page":                   Tool("wac"),
    "alerts.new":                     Tool("alerts"),
    "alerts.subscribe":               Tool("alerts", "alert-signup", "Alert sign-up"),
    "redaction.request_form":         Tool("redaction"),
    "redaction.submit_request":       Tool("redaction", "redaction-request", "Name-removal request"),
    "tags.tag_feedback":              Tool("community", "tag-vote", "Tag vote"),
    "tags.suggest_tag":               Tool("community", "tag-suggest", "Tag suggestion"),
    "jwa.jwa_root":                   Tool("jwa"),
    "jwa.jwa_file":                   Tool("jwa"),
}

# Never recorded: operator traffic, health probes, the usage system itself,
# crawler housekeeping, and routes whose URLs carry personal tokens.
SKIP_ENDPOINTS = frozenset({
    "static", "main.robots_txt",
    "admin.health", "admin.health_ready", "admin.health_deep",
    "datastories.datastories_login", "datastories.datastories_logout",
    "redaction.orcid_callback", "redaction.verify",
    "alerts.verify", "alerts.manage", "alerts.delete",
    "alerts.unsubscribe", "alerts.unsubscribe_post",
})
SKIP_BLUEPRINTS = frozenset({"admin", "usage"})


def _pretty(slug: str) -> str:
    small = {"and", "of", "a", "the", "vs", "to", "in"}
    words = slug.replace("_", "-").split("-")
    out = []
    for i, w in enumerate(words):
        out.append(w if (i and w in small) else w.capitalize())
    return " ".join(out)


def datastories_label(key: str) -> str:
    """'ch5-reach-of-citation' → 'Reach of Citation (ch. 5)'."""
    chapter, _, rest = key.partition("-")
    return f"{_pretty(rest)} (ch. {chapter[2:]})" if rest else _pretty(key)


def classify(endpoint: str | None, path: str = "") -> Tool:
    """The area and tool for one endpoint name."""
    if not endpoint:
        return Tool("other")
    if endpoint in _EXPLORE:
        key, label = _EXPLORE[endpoint]
        return Tool("explore", key, label, key in _AUTO)
    if endpoint in _PAGES:
        return _PAGES[endpoint]
    bp, _, fn = endpoint.partition(".")
    if bp == "authors" and fn.startswith("author_") and fn.endswith("_api"):
        return Tool("record", "author-profile", "Author page charts", True)
    if bp == "authors" and fn == "api_author_cocitation_partners":
        return Tool("record", "author-profile", "Author page charts", True)
    if bp == "datastories" and fn.startswith("api_"):
        key = fn[4:].replace("_", "-")
        return Tool("datastories", key, datastories_label(key))
    if bp == "wac" and fn.startswith("api_"):
        key = "wac-" + fn[4:].replace("_", "-")
        return Tool("wac", key, "WAC: " + _pretty(fn[4:]))
    if bp == "feeds":
        if path.endswith((".xml", ".opml")):
            return Tool("feeds", "feed", "Feed (RSS/Atom/OPML)")
        return Tool("feeds")
    return Tool("other")


def should_skip(endpoint: str | None) -> bool:
    if not endpoint:
        return False
    if endpoint in SKIP_ENDPOINTS:
        return True
    return endpoint.partition(".")[0] in SKIP_BLUEPRINTS


def tool_for_hash(page_endpoint: str | None, fragment: str) -> Tool | None:
    """Map a tab switch inside Explore or Datastories to its tool."""
    frag = (fragment or "").strip().lstrip("#").lower()
    if not frag:
        return None
    if page_endpoint == "main.explore":
        key = HASH_ALIASES.get(frag, frag)
        for ep, (k, label) in _EXPLORE.items():
            if k == key:
                return Tool("explore", k, label, k in _AUTO)
        return None
    if page_endpoint == "datastories.datastories_page" and frag.startswith("ch"):
        return Tool("datastories", frag, datastories_label(frag))
    return None


def label_for(tool_key: str | None) -> str:
    """Human label for a stored tool key."""
    if not tool_key:
        return ""
    for k, label in _EXPLORE.values():
        if k == tool_key:
            return label
    for t in _PAGES.values():
        if t.tool == tool_key and t.label:
            return t.label
    if tool_key == "author-profile":
        return "Author page charts"
    if tool_key == "feed":
        return "Feed (RSS/Atom/OPML)"
    if tool_key.startswith("wac-"):
        return "WAC: " + _pretty(tool_key[4:])
    if tool_key.startswith("ch") and "-" in tool_key:
        return datastories_label(tool_key)
    return _pretty(tool_key)


def is_auto(tool_key: str | None) -> bool:
    return tool_key in _AUTO
