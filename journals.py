# journals.py
# Single source of truth for all journal configurations.
# Three fetch strategies: CrossRef API, RSS/Atom feed, web scraping.

# ── CrossRef-indexed journals ─────────────────────────────────────────────────
# Fetched via CrossRef API by ISSN. Use the ISSN that CrossRef actually indexes
# (usually the online/electronic ISSN when both exist).
# Verified ISSNs:
#   Community Literacy Journal — print 1555-9734 (465 DOIs); online 2162-6324 (243 DOIs)
#   Poroi                      — online 2151-2957 (259 DOIs); print 1558-9048 has 0

CROSSREF_JOURNALS = [
    {"name": "College Composition and Communication",          "issn": "0010-096X"},
    {"name": "College English",                                "issn": "0010-0994"},
    {"name": "Written Communication",                          "issn": "0741-0883"},
    {"name": "Rhetoric Society Quarterly",                     "issn": "0277-3945"},
    {"name": "Rhetoric Review",                                "issn": "0735-0198"},
    {"name": "Technical Communication Quarterly",              "issn": "1057-2252"},
    {"name": "Research in the Teaching of English",            "issn": "0034-527X"},
    {"name": "Journal of Business and Technical Communication","issn": "1050-6519"},
    {"name": "Journal of Technical Writing and Communication", "issn": "0047-2816"},
    {"name": "Philosophy & Rhetoric",                          "issn": "0031-8213"},
    {"name": "Rhetoric & Public Affairs",                      "issn": "1094-8392"},
    {"name": "Teaching English in the Two-Year College",       "issn": "0098-6291"},
    {"name": "Pedagogy",                                       "issn": "1531-4200"},
    {"name": "Community Literacy Journal",                     "issn": "1555-9734"},  # print ISSN — 465 DOIs
    {"name": "Poroi",                                          "issn": "2151-2957"},  # online ISSN — 259 DOIs
    {"name": "Computers and Composition",                      "issn": "8755-4615"},
    # Composition Studies: not in CrossRef (0 DOIs); scraped from compstudiesjournal.com
    # {"name": "Composition Studies",                          "issn": "1534-9322"},
]

# ── Web-native journals with confirmed RSS/Atom feeds ─────────────────────────
# All feed URLs verified March 2026.

RSS_JOURNALS = [
    {
        "name": "Enculturation",
        "url":  "https://enculturation.net/",
        "feed_url": "https://enculturation.net/rss.xml",
        # Drupal 7 — no OAI, no WP API. Full archive scraped via scraper.py.
        # RSS kept as fallback for new issues; scraper handles historical archive.
        "strategy": "enculturation",
    },
    {
        "name": "Composition Forum",
        "url":  "https://compositionforum.com/",
        "feed_url": "https://compositionforum.com/feed/",
        # WordPress REST API gives full archive (48 articles) vs RSS cap of 10.
        # Server blocks non-browser User-Agents, so wp_api_url triggers a
        # separate harvester that uses a browser UA. Author/abstract not exposed
        # via the API — title, date, and URL only.
        "wp_api_url": "https://compositionforum.com/wp-json/wp/v2/posts",
    },
    {
        "name": "Present Tense: A Journal of Rhetoric in Society",
        "url":  "https://www.presenttensejournal.org/",
        "feed_url": "https://www.presenttensejournal.org/feed/",
        # WordPress REST API: 241 articles available vs RSS cap of 10.
        "wp_api_url": "https://www.presenttensejournal.org/wp-json/wp/v2/posts",
    },
    {
        "name": "KB Journal: The Journal of the Kenneth Burke Society",
        "url":  "https://kbjournal.org/",
        "feed_url": "https://kbjournal.org/node/feed",
        # RSS only returns ~9 recent items. Full archive scraped via scraper.py.
        "strategy": "kb_journal",
    },
    {
        "name": "Reflections: A Journal of Community-Engaged Writing and Rhetoric",
        "url":  "https://reflectionsjournal.net/",
        "feed_url": "https://reflectionsjournal.net/feed/",
        # WordPress REST API: 554 posts available vs RSS cap of 10.
        # Posts are organized by volume.issue categories; all appear to be articles.
        "wp_api_url": "https://reflectionsjournal.net/wp-json/wp/v2/posts",
    },
    {
        "name": "Literacy in Composition Studies",
        "url":  "https://licsjournal.org/",
        "feed_url": "https://licsjournal.org/index.php/LiCS/gateway/plugin/WebFeedGatewayPlugin/rss2",
        # OJS exposes OAI-PMH, which returns all records — not just the RSS cap of 10.
        # rss_fetcher.py will prefer this endpoint over the RSS feed when present.
        "oai_url": "https://licsjournal.org/index.php/LiCS/oai",
    },
]

# ── Web-native journals requiring scraping ────────────────────────────────────
# No RSS available. Each entry maps to a named strategy in scraper.py.
# Metadata quality varies — scraped articles often lack author lists and abstracts.

SCRAPE_JOURNALS = [
    {
        "name": "Kairos: A Journal of Rhetoric, Technology, and Pedagogy",
        "url": "https://www.technorhetoric.net/",
        "strategy": "kairos",
        "notes": "Custom static HTML. Issues at /{vol}.{issue}/index.html. "
                 "Vol 1 = 1996; Vol 30.2 = Spring 2026.",
    },
    {
        "name": "Praxis: A Writing Center Journal",
        "url": "https://www.praxisuwc.com/",
        "strategy": "praxis",
        "notes": "Squarespace. No RSS. Issues listed at /issues-archive.",
    },
    {
        "name": "Journal of Multimodal Rhetorics",
        "url": "https://journalofmultimodalrhetorics.com/",
        "strategy": "jmr",
        "notes": "Custom Ruby/Rack app. Issue TOC at /{vol}-{issue}-issue. Vol 1 = 2017.",
    },
    {
        "name": "Basic Writing e-Journal",
        "url": "https://bwe.ccny.cuny.edu/",
        "strategy": "bwe",
        "notes": "Static HTML (CUNY). Archive index at /Archives.html. "
                 "Largely dormant since Vol 16.1 (2020).",
    },
    {
        "name": "Composition Studies",
        "url": "https://compstudiesjournal.com/",
        "strategy": "comp_studies",
        "notes": "WordPress.com. Not in CrossRef. Archive at /archive/; "
                 "issues 2017–present have individual article PDF links. "
                 "Pre-2016 issues are full-PDF only (not scraped).",
    },
    {
        "name": "Writing on the Edge",
        "url": "https://woejournal.ucdavis.edu/",
        "strategy": "woe",
        "notes": "Drupal 10 (UC Davis). RSS blocked (403). "
                 "Subscription journal — metadata scrapeable, full text paywalled.",
    },
    {
        "name": "Writing Lab Newsletter",
        "url": "https://writinglabnewsletter.org/",
        "strategy": "wln",
        "notes": "Print newsletter (1975–2015). Archive at /resources.html as full-issue PDFs. "
                 "No individual article pages; entries are at the issue level.",
    },
    {
        "name": "Writing Center Journal",
        "url": "https://docs.lib.purdue.edu/wcj/",
        "strategy": "wcj",
        "notes": "Purdue Digital Commons (Open Access). Vol/iss TOC pages list articles "
                 "with titles and authors. Vol 1 ≈ 1980.",
    },
    {
        "name": "The Peer Review",
        "url": "https://thepeerreview-iwca.org/",
        "strategy": "peer_review",
        "notes": "IWCA WordPress site. Issues listed at /issues/. "
                 "Article slugs at root level.",
    },
]

# ── Journals currently unavailable ───────────────────────────────────────────
# Displayed in the sidebar for reference. Not fetched.

UNAVAILABLE_JOURNALS = [
    {
        "name": "Rhetor: Journal of the Canadian Society for the Study of Rhetoric",
        "note": "Domain (cssr-scer.ca) has been hijacked as of early 2025 and "
                "redirects to an unrelated site. Journal status unknown.",
    },
]

# ── Convenience lookups ───────────────────────────────────────────────────────

ISSN_TO_NAME = {j["issn"]: j["name"] for j in CROSSREF_JOURNALS}

ALL_JOURNAL_NAMES = (
    [j["name"] for j in CROSSREF_JOURNALS]
    + [j["name"] for j in RSS_JOURNALS]
    + [j["name"] for j in SCRAPE_JOURNALS]
)
