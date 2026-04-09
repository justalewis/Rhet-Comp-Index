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
    {"name": "Communication Design Quarterly",                 "issn": "2166-1642"},  # ACM SIGDOC — 407 DOIs, 2012–present
    {"name": "Communication Design Quarterly Review",          "issn": "2166-1200"},  # ACM SIGDOC predecessor — 65 DOIs, 2001–2019
    # WAC Clearinghouse journals
    {"name": "Across the Disciplines",                         "issn": "1554-8244"},  # WAC Clearinghouse — 370 DOIs, 2004–present
    {"name": "The WAC Journal",                                "issn": "1544-4929"},  # WAC Clearinghouse — 345 DOIs, 1989–present
    {"name": "Journal of Writing Analytics",                   "issn": "2474-7491"},  # WAC Clearinghouse — 95 DOIs, 2017–present
    {"name": "Prompt: A Journal of Academic Writing Assignments", "issn": "2476-0943"},  # WAC Clearinghouse — 127 DOIs, 2016–present
    {"name": "Peitho",                                         "issn": "2169-0774"},  # Coalition of Feminist Scholars / WAC Clearinghouse — 114 DOIs
    {"name": "Double Helix",                                   "issn": "2372-7497"},  # WAC Clearinghouse — 112 DOIs, 2012–2024
    # Other CrossRef-indexed journals
    {"name": "Advances in the History of Rhetoric",            "issn": "1936-0835"},  # Penn State UP — 312 DOIs, 1998–2019
    {"name": "Assessing Writing",                              "issn": "1075-2935"},  # Elsevier — 1000 DOIs, 1994–present
    {"name": "Rhetoric of Health and Medicine",                "issn": "2573-5063"},  # U of Florida Press — 197 DOIs, 2018–present
    {"name": "Business and Professional Communication Quarterly", "issn": "2329-4922"},  # SAGE — 508 DOIs, 2013–present
    # Composition Studies: not in CrossRef (0 DOIs); scraped from compstudiesjournal.com
    # {"name": "Composition Studies",                          "issn": "1534-9322"},
    # English Journal (NCTE): not indexed in CrossRef
    # WLN: A Journal of Writing Center Scholarship: in CrossRef but no DOIs deposited; print archive (1975–2015) scraped separately
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
    # Composition Forum — moved to SCRAPE_JOURNALS (two-era full-archive scraper)
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
    # Reflections — moved to SCRAPE_JOURNALS (archive page scraper supersedes WP API).
    # WP API captured title+date only; scraper adds authors+abstracts from /archive/.
    # {
    #     "name": "Reflections: A Journal of Community-Engaged Writing and Rhetoric",
    #     "url":  "https://reflectionsjournal.net/",
    #     "feed_url": "https://reflectionsjournal.net/feed/",
    #     "wp_api_url": "https://reflectionsjournal.net/wp-json/wp/v2/posts",
    # },
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
    {
        "name": "Reflections: A Journal of Community-Engaged Writing and Rhetoric",
        "url": "https://reflectionsjournal.net/",
        "strategy": "reflections",
        "notes": "WordPress. Archive page at /archive/ lists all articles with pipe-separated "
                 "title|author format. Article pages have abstracts for ~2022+. "
                 "Older issues (pre-Vol 21) link to PDFs — metadata from archive page only.",
    },
    {
        "name": "Composition Forum",
        "url": "https://compositionforum.com/",
        "strategy": "comp_forum",
        "notes": "Two-era site: old PHP (vols 14.2–54) and new WordPress (55+). "
                 "Server blocks bot UAs; scraper uses browser-like headers. "
                 "Full archive: ~500 articles with titles, authors, abstracts.",
    },
]

# ── Scraped digital presses ─────────────────────────────────────────────────
# Open-access digital book publishers without CrossRef DOIs or RSS.
# Metadata scraped from public catalog pages via custom scraper scripts.
# source='scrape' in the articles table. No live refresh — re-run scraper.

DIGITAL_PRESS_JOURNALS = [
    {
        "name": "Computers and Composition Digital Press",
        "url": "https://ccdigitalpress.org/books",
        "strategy": "ccdp",
        "notes": "Open-access digital books (monographs + edited collections). "
                 "27 books, ~280 chapters. Scraped via scrape_ccdp.py. "
                 "No Dublin Core metadata; parsed from page content. "
                 "All CC-licensed (gold OA).",
    },
]

# ── Manually indexed print journals ──────────────────────────────────────────
# No web presence, no CrossRef DOIs, no RSS. Records inserted via one-off
# ingestion scripts (e.g. ingest_pretext.py) from hand-compiled indexes.
# source='manual' in the articles table. No live refresh.

MANUAL_JOURNALS = [
    {
        "name": "Pre/Text",
        "full_name": "Pre/Text: A Journal of Rhetorical Theory",
        "editor": "Victor J. Vitanza",
        "years": "1980–2016",
        "volumes": "1–22 (vols 19–20 not indexed)",
        "notes": "Analog-only journal; never digitized or assigned DOIs. "
                 "234 articles indexed from a hand-compiled record across 22 volumes. "
                 "Ingested via ingest_pretext.py.",
    },
]

# ── Open-access classification ──────────────────────────────────────────────
# Journals that are entirely open access (no paywall for any article).
# Used by backfill_oa_status() in db.py to tag articles without API calls.
# Journals NOT listed here are assumed to be subscription / mixed and need
# per-article OA checking via OpenAlex.

GOLD_OA_JOURNALS = {
    # WAC Clearinghouse — all fully OA
    "Across the Disciplines",
    "The WAC Journal",
    "Journal of Writing Analytics",
    "Prompt: A Journal of Academic Writing Assignments",
    "Peitho",
    "Double Helix",
    # Independent OA journals
    "Kairos: A Journal of Rhetoric, Technology, and Pedagogy",
    "Enculturation",
    "Composition Forum",
    "Present Tense: A Journal of Rhetoric in Society",
    "KB Journal: The Journal of the Kenneth Burke Society",
    "Reflections: A Journal of Community-Engaged Writing and Rhetoric",
    "Literacy in Composition Studies",
    "Basic Writing e-Journal",
    "Journal of Multimodal Rhetorics",
    "Praxis: A Writing Center Journal",
    "The Peer Review",
    "Writing Center Journal",
    "Writing Lab Newsletter",
    "Poroi",
    "Community Literacy Journal",
    "Composition Studies",
    # CDQ is ACM OA
    "Communication Design Quarterly",
    "Communication Design Quarterly Review",
    # University press OA
    "Rhetoric of Health and Medicine",
}


# ── Journals currently unavailable ───────────────────────────────────────────
# Displayed in the sidebar for reference. Not fetched.

UNAVAILABLE_JOURNALS = [
    {
        "name": "Rhetor: Journal of the Canadian Society for the Study of Rhetoric",
        "note": "Domain (cssr-scer.ca) has been hijacked as of early 2025 and "
                "redirects to an unrelated site. Journal status unknown.",
    },
]

# ── Journal groups (for sidebar navigation) ──────────────────────────────────
# Ordered list of (group_label, [journal_names]).
# Any journal not listed here will appear ungrouped at the end.

JOURNAL_GROUPS = [
    ("Composition & Writing Studies", [
        "College Composition and Communication",
        "College English",
        "Composition Studies",
        "Composition Forum",
        "Research in the Teaching of English",
        "Pedagogy",
        "Teaching English in the Two-Year College",
        "Assessing Writing",
        "Journal of Writing Analytics",
        "Prompt: A Journal of Academic Writing Assignments",
        "Writing on the Edge",
        "Literacy in Composition Studies",
        "Basic Writing e-Journal",
    ]),
    ("Rhetoric", [
        "Rhetoric Society Quarterly",
        "Rhetoric Review",
        "Philosophy & Rhetoric",
        "Rhetoric & Public Affairs",
        "Poroi",
        "Advances in the History of Rhetoric",
        "Pre/Text",
        "Peitho",
        "Present Tense: A Journal of Rhetoric in Society",
        "KB Journal: The Journal of the Kenneth Burke Society",
        "Enculturation",
        "Written Communication",
    ]),
    ("Technical Communication", [
        "Technical Communication Quarterly",
        "Journal of Business and Technical Communication",
        "Journal of Technical Writing and Communication",
        "Communication Design Quarterly",
        "Communication Design Quarterly Review",
        "Business and Professional Communication Quarterly",
        "Double Helix",
        "Rhetoric of Health and Medicine",
    ]),
    ("Writing Centers", [
        "Writing Center Journal",
        "Praxis: A Writing Center Journal",
        "Writing Lab Newsletter",
        "The Peer Review",
    ]),
    ("WAC / Writing Across the Curriculum", [
        "Across the Disciplines",
        "The WAC Journal",
    ]),
    ("Digital & Multimodal", [
        "Computers and Composition",
        "Kairos: A Journal of Rhetoric, Technology, and Pedagogy",
        "Journal of Multimodal Rhetorics",
    ]),
    ("Community Literacy", [
        "Community Literacy Journal",
        "Reflections: A Journal of Community-Engaged Writing and Rhetoric",
    ]),
]

# ── Convenience lookups ───────────────────────────────────────────────────────

ISSN_TO_NAME = {j["issn"]: j["name"] for j in CROSSREF_JOURNALS}

ALL_JOURNAL_NAMES = (
    [j["name"] for j in CROSSREF_JOURNALS]
    + [j["name"] for j in RSS_JOURNALS]
    + [j["name"] for j in SCRAPE_JOURNALS]
    + [j["name"] for j in DIGITAL_PRESS_JOURNALS]
    + [j["name"] for j in MANUAL_JOURNALS]
)
