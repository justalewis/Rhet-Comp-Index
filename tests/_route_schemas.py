"""Per-route JSON shape contracts. Used by test_routes_api.py to detect when
a route accidentally renames or drops a top-level key. Lists the *required*
top-level keys; routes may return additional keys."""

JSON_ROUTE_SCHEMAS: dict[str, set[str]] = {
    "/api/articles":                        {"articles", "total"},
    "/api/author/Jane Smith/timeline":      {"years", "series", "books"},
    "/api/author/Jane Smith/coauthors":     {"nodes", "links"},
    "/api/author/Jane Smith/topics":        set(),  # returns a list, not a dict
    "/api/citations/ego":                   {"focal_id", "nodes", "links"},
    "/api/stats/timeline":                  set(),
    "/api/stats/tag-cooccurrence":          set(),
    "/api/stats/author-network":            {"nodes", "links"},
    "/api/stats/citation-trends":           set(),
    "/api/citations/network":               {"nodes", "links"},
    "/api/citations/cocitation":            {"nodes", "links"},
    "/api/citations/bibcoupling":           {"nodes", "links"},
    "/api/citations/centrality":            {"nodes", "links"},
    "/api/citations/sleeping-beauties":     {"articles"},
    "/api/citations/journal-flow":          set(),
    "/api/citations/half-life":             {"journals"},
    "/api/citations/communities":           {"nodes", "links", "communities"},
    "/api/citations/main-path":             {"path", "edges", "stats"},
    "/api/citations/temporal-evolution":    set(),
    "/api/articles/search":                 set(),
    "/api/citations/reading-path":          set(),
    "/api/author-cocitation":               {"nodes"},  # returns nodes/edges/pairs/stats; "edges" not "links"
    "/api/author/Jane Smith/cocitation-partners": set(),
    "/api/stats/most-cited":                set(),
    "/api/stats/institutions":              set(),
}

# HTML routes: URL -> required substring in body (page-specific h2 or content).
HTML_ROUTE_HEADINGS: dict[str, str] = {
    "/":              "Journals",     # index header varies; covers all branches
    "/authors":       "Authors",
    "/explore":       "Explore",
    "/tools":         "All Tools",
    "/new":           "What's New",
    "/about":         "What This Is",
    "/coverage":      "Corpus Snapshot",
    "/most-cited":    "Most cited in this index",
    "/books":         "Books",
}
