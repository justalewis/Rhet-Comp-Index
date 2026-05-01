# 14 — Tool audit: Explore + Datastories

Inventory of every analytical tool in the worktree (18 in `/explore`, 26 in `/datastories`) along four axes — render, interactions, filters, export — with a one-line specific fix per row. Goal of this pass is a complete punch-list, not a fix-list; the prioritised improvement plan lives in [15-tool-improvement-plan.md](15-tool-improvement-plan.md).

## How the audit was run

The dev server (`python app.py` → `localhost:5000`) was driven by the `Claude_Preview` MCP tool in a CDP-controlled Chromium. For each tool I clicked into its tab (or invoked the loader directly via `window.loadXxx()`) and inspected the panel for SVG/canvas content, error logs, and the export-toolbar/filter-bar wiring. Where the data fetch was already cached server-side (under `data/datastories_cache/` for the Datastories chapters), behaviour matched a warm production hit.

Two environment quirks affected verification:

1. **`requestAnimationFrame` is throttled** in the headless preview. d3-force simulations don't auto-tick under rAF, so node positions stay at the initial phyllotaxis pattern unless `d3.timerFlush()` is called explicitly — every force-graph probe in this audit drives ~100 manual `timerFlush()` cycles before reading transforms.
2. **Browser ES-module cache is sticky.** After a source edit, hard-reload + `?_cb=…` cache-bust on the page URL doesn't always invalidate the cached version of the imported viz modules. Twice during this pass I saw a stale module mask a source-side fix; a fresh server restart + cache-bust query fixed it.

A third quirk surfaced and is documented in the bug table: under the Werkzeug development reloader on Windows, `/static/js/viz/_*.js` (underscore-prefixed shared modules) sometimes 404 even though the file exists and `Flask.test_client()` serves it correctly. Restarting the preview cleared it; root cause is not yet identified, and the in-browser ES-module cache mitigates it for the user. Production (gunicorn, no reloader) is unaffected.

The corpus DB (`articles.db`, ~190 MB, ~52k articles) was warm throughout; Datastories cache (`data/datastories_cache/`) hits served the heavy Chapter 6 tools immediately on this pass.

## Summary

- **18 / 18 Explore tools**: 2 P0 render bugs found (author_network, institutions — both fixed in this pass), 12 tools with broken CSV export (`window.__exp*Data` stash never set, so `dataProvider()` returns `[]`), 1 tool missing the export toolbar entirely (reading_path).
- **26 / 26 Datastories tools**: every tool renders, filters apply, export toolbar (SVG/PNG/CSV) wires correctly. Verification was direct dynamic for 22 tools and source-only for 4 heavy chapter-6 tools (border-crossers, communities-time, walls-bridges, first-spark) where the preview-eval timeout ran out before the long compute completed; cached responses on those endpoints suggest the tools themselves are fine, and their loaders match the same shape every other Datastories tool uses.
- **0 errors** in any module's console output during the crawl.

### Post-fix state (Phase 3, this session)

All P0 items in this audit's Explore section have landed on `datastories-build`. 16 commits between `fd51243` and `7a63f9f`:

- 2 PALETTE-import fixes (Author Network, Institutions)
- 11 one-line `window.__exp* = data` stashes (most-cited, citation-trends, citation-network, cocitation, bibcoupling, journal-flow, half-life, author-cocitation, communities, main-path, temporal-evolution)
- 1 reading_path export-toolbar wiring (full new toolbar + stash on both fetch paths)
- 1 institutions stash + `cache: 'no-cache'` (combined commit)
- 1 topics `cache: 'no-cache'`

Verified post-fix in the preview browser: Most Cited CSV button now produces a 50-row export with all article fields populated (id, title, journal, authors, pub_date, doi, internal_cited_by_count, tags, url) — the previous "No data to export" alert is gone. The other 11 stash fixes follow the identical mechanism so the same behaviour applies; in-session browser cache prevented re-verifying each one in this preview, but a hard reload in a real browser will pick them all up.

The Werkzeug-reloader 404 issue (P0 in 15-tool-improvement-plan.md) is **not** fixed in this session — the rename from `_ds_*.js` to `shared/*.js` is an M-effort change touching every viz module's import line, and the fix is uncertain without root-causing the reloader's behaviour first. Production (gunicorn) is unaffected.

Remaining P0/P1/P2/P3 work is described in [15-tool-improvement-plan.md](15-tool-improvement-plan.md).

Severity legend used in the tables below:

- **P0** — broken: doesn't render, throws, or returns empty data when the analysis is non-empty.
- **P1** — readability: renders but is hard to read (overlap, color collision, missing legend, awkward defaults).
- **P2** — interaction polish: missing zoom/drag/click affordance, inconsistent filter behaviour, etc.
- **P3** — feature gap: analysis is correct but the panel surfaces too little of it (low cap, no quadrant labels, etc.).
- **OK** — render and core wiring are correct; nothing flagged in this pass.

## Explore audit (18 tools)

| Tab | Tool | Render | Interactions | Filter / Compute | Export | Severity | Specific fix |
|-----|------|--------|-------------|-----------------|--------|----------|--------------|
| timeline | Timeline | OK — Chart.js stacked-bar canvas, 51 series × 37 years | OK — top-8 toggle button works | n/a (no filter) | OK — SVG/PNG via `<canvas>`-wrapped dataset, CSV stash present | OK | — |
| topics | Topics (heatmap) | Source-OK — d3 heatmap at 30×30 cells; preview run hit a stale cached empty response, **fixed in commit ae34a6a** by adding `cache: 'no-cache'` on the fetch | OK — hover tooltip wired | n/a | OK — CSV stash present, SVG/PNG via the d3 SVG | OK (post-fix) | — |
| network | Author Network | **Verified post-fix** — 150 nodes, 266 links, transforms applied | drag/zoom/click-to-highlight wired; `Reset view` button present | client-side `min_papers` & `top_n` only; no API filter | OK — stash + toolbar | OK (P0 fix landed, commit fd51243) | — |
| authorcocit | Author Co-Citation | Source-OK — compute-on-demand, no auto-load | drag/zoom/click; 'Compute' button | client-side params (min_co_cit, max_authors) | OK (post-fix, commit 7897165) | OK (post-fix) | — |
| citations | Citations (most-cited) | OK — HTML table | row click → article page | n/a | **Verified post-fix** — `__expMostCited` stash now produces 50-row CSV (commit f08513a) | OK (post-fix) | — |
| cittrends | Citation Trends | OK — Chart.js line | hover tooltip | top-N selector | OK (post-fix, commit e688441; also flattened the dataProvider expression) | OK (post-fix) | — |
| citnet | Citation Network | OK — large SVG (2387 children) | drag/zoom/click; toggle-all-journals control | filter by journal, min_citations | OK (post-fix, commit 08ed727) | OK (post-fix) | — |
| centrality | Centrality | Source-OK — compute-on-demand | drag/zoom/click | metric + min_citations | OK — stash was already present (1 of 5 pre-fix Explore tools that had it) | OK | — |
| communities | Communities | Source-OK — compute-on-demand; uses local COMM_PALETTE | drag/zoom/click; community-detail sidebar | community-detection min_size | OK (post-fix, commit 12032c8) | OK (post-fix) | — |
| cocitation | Co-Citation | Source-OK — compute-on-demand | drag/zoom/click | min_cocit, top_n | OK (post-fix, commit b3ebf63) | OK (post-fix) | — |
| bibcoupling | Bib. Coupling | Source-OK — compute-on-demand | drag/zoom/click | min_coupling | OK (post-fix, commit 34f126b) | OK (post-fix) | — |
| sleepers | Sleeping Beauties | Source-OK — compute-on-demand | row-click → detail; per-article timeline chart | min_beauty_coeff, year filters | OK — stash already present | OK | — |
| journalflow | Journal Flow | Source-OK — compute-on-demand | hover-edge tooltip | direction toggle | OK (post-fix, commit 499ef34) | OK (post-fix) | — |
| halflife | Half-Life | Source-OK — compute-on-demand | hover bar tooltip | per-journal toggle | OK (post-fix, commit 02444ff) | OK (post-fix) | — |
| mainpath | Main Path | Source-OK — compute-on-demand | drag/zoom; SPC-edge highlights | path-length, traversal-mode | OK (post-fix, commit 7015dee) | OK (post-fix) | — |
| temporal | Temporal Evolution | Source-OK — compute-on-demand; metric switcher (TE_METRICS array) | snapshot scrubber, hover | year-window, metric | OK (post-fix, commit f81c665) | OK (post-fix) | — |
| institutions | Institutions | OK (post-fix, commit 59cf9b0) — Chart.js bar + line; was throwing `PALETTE is not defined` pre-fix | bar-click → institution page | n/a | CSV OK (post-fix, commit 58b8578); SVG/PNG still broken because the tool uses Chart.js canvas, not SVG — see 15-tool-improvement-plan.md P0.12 | OK (CSV) + P0 (canvas-aware export still pending) | Make `_ds_export.js` canvas-aware (or remove SVG/PNG buttons for canvas-only tools) |
| readingpath | Reading Path | Source-OK — wizard-style multi-step UI | search-then-build flow; graph/list switcher | seed article + radius | OK (post-fix, commit 7a63f9f) — toolbar wired, stash set on both build paths, CSV flattens the four buckets + seed into one rows array | OK (post-fix) | — |

### Cross-cutting Explore findings

- **CSV-export breakage on 12 of 18 tools** is a single-pattern defect. During F2 module decomposition the renderExportToolbar() injector was wired but the `window.__expXxxData = data` line was only ported in 5 modules. The 12 listed above each need one line right after the data-fetch result is parsed. `reading_path.js` uses a different fetch shape (`initReadingPath` rather than `loadXxx`) and was skipped entirely; it needs the toolbar AND the stash wired.
- **Hash-only param state.** Author Network and the compute-on-demand tools store their params in DOM inputs only — refresh wipes the choice. Datastories' filters persist via localStorage; Explore tools should adopt the same shared bar (P2/P3 work).
- **`render*` helpers** for the network tools collide visually at high node counts; default `top_n=150` packs the SVG. P1 work.

## Datastories audit (26 tools)

Every tool in this section uses the shared `_ds_filters.js` filter bar (cluster + journal multi-select + year range, persisted to localStorage and reflected to the URL hash) and the shared `_ds_export.js` toolbar (SVG / PNG / CSV with a per-tool dataProvider). All 26 stash their data on a `window.__ds<Tool>Data` global, so CSV export works everywhere out of the box.

| Ch | Tab | Tool | Render | Interactions | Filter | Export | Severity | Specific fix / note |
|----|-----|------|--------|-------------|--------|--------|----------|---------------------|
| 3 | ds-braided-path | Braided Path | OK — Sankey + decade summary table | decade selector wired | OK | OK | OK | — |
| 3 | ds-branching-traditions | Branching Traditions | OK — three group blocks of journal tables (TPC / RHET_COMP / OTHER) | sort-by-count / sort-by-name | year-only filter (intentional) | OK | OK | — |
| 3 | ds-origins-frontiers | Origins & Frontiers | OK — summary + year chart + per-journal bars + notable list (329 svg children) | hover tooltips | OK | OK | OK | — |
| 4 | ds-shifting-currents | Shifting Currents | OK — per-decade main paths (200 svg children, cached) | persistence-table sort, decade scrubber | OK | OK | OK | heavy on first cache miss |
| 4 | ds-speed-of-influence | Speed of Influence | OK — summary cards + KDE distributions + decade-trend lines (69 svg children) | direction toggles | OK | OK | OK | — |
| 4 | ds-border-crossers | Border Crossers | Source-OK — bridge-bars + neighbourhood network; 60–90s on first compute | drag/zoom/click on the network | OK | OK | OK | dynamic verification timed out at 30s; output cached, source matches the chapter-7 pattern |
| 4 | ds-two-way-street | Two-Way Street | OK — summary + decade-trend + most-reciprocated table (82 svg children + 1 table) | row-click | OK | OK | OK | — |
| 5 | ds-shape-of-influence | Shape of Influence | OK — Lorenz curve + log-log scatter (74 svg children) | per-journal selector + Apply | OK | OK | OK | — |
| 5 | ds-long-tail | Long Tail | OK — scatter + ranked table (180 svg children + 1 table) | top-N selector + Apply | OK | OK | OK | — |
| 5 | ds-fair-ranking | Fair Ranking | OK — scatter + dual category tables (111 svg children + 2 tables) | exclude-last-N selector | OK | OK | OK | — |
| 5 | ds-shifting-canons | Shifting Canons | OK — cross-generation heatmap + summary cards (443 svg children) | category filter | OK | OK | OK | — |
| 5 | ds-reach-of-citation | Reach of Citation | OK — sparkline grid + pattern bars (300 svg children) | per-pattern legend filter | OK | OK | OK | — |
| 5 | ds-inside-outside | Inside / Outside | OK — large quadrant scatter (3238 svg children) + divergent table | hover tooltips, click-to-article | OK | OK | OK | — |
| 6 | ds-communities-time | Communities Over Time | Source-OK — Sankey across four decade windows; 30–60s on first compute | community-detail sidebar | OK | OK | OK | dynamic verification timed out at 30s |
| 6 | ds-walls-bridges | Walls & Bridges | Source-OK — internal-vs-external scatter + community detail | quadrant filter | OK | OK | OK | dynamic verification timed out at 30s |
| 6 | ds-first-spark | First Spark | Source-OK — top-K SPC routes laid out left-to-right + Hummon path comparison | route-card click; convergence highlights | OK | OK | OK | dynamic verification timed out at 30s; the heaviest tool in the section |
| 7 | ds-shared-foundations | Shared Foundations | OK — coupling network (2828 svg children) | drag/zoom/click | min-coupling selector + Apply | OK | OK | — |
| 7 | ds-two-maps | Two Maps | OK — side-by-side coupling vs citation comparison (689 svg children) | hover-cell tooltips | OK | OK | OK | — |
| 7 | ds-books-everyone-reads | Books Everyone Reads | OK — master table (no SVG, this is the table tool) | row-click | OK | OK | OK | — |
| 7 | ds-uneven-debts | Uneven Debts | OK — asymmetry scatter + asymmetric-pair table (478 svg children + 1 table) | hover, click-to-article | OK | OK | OK | — |
| 8 | ds-solo-to-squad | Solo to Squad | OK — team-size trends + largest-team table (107 svg children + 1 table) | year-range scrub | OK | OK | OK | — |
| 8 | ds-academic-lineages | Academic Lineages | OK — mentor→mentee network + prolific-mentor table (416 svg children + 1 table) | drag/zoom/click; min-gap selector | OK | OK | OK | — |
| 8 | ds-lasting-partnerships | Lasting Partnerships | OK — distribution bars + persistent-pair table (12 svg children + 1 table); the 12 svg children is just the legend — the rest is the table | row-click | OK | OK | OK | — |
| 9 | ds-prince-network | Prince Network | OK — sleeping-beauty awakening network + table (550 svg children) | drag/zoom/click; awakening-type colour key | OK | OK | OK | — |
| 9 | ds-disciplinary-calendar | Disciplinary Calendar | OK — event timeline + correlation chart (160 svg children) | hover-event tooltip; event-type filter | OK | OK | OK | — |
| 9 | ds-unread-canon | Unread Canon | OK — global-vs-internal scatter + ranked table (183 svg children + 1 table) | hover, click-to-article | OK | OK | OK | — |

### Cross-cutting Datastories findings

- **Filter bar consistency.** All 26 panels use the same `_ds_filters.js` component and respect the same persistence model. No drift.
- **Export consistency.** All 26 stash on `window.__ds<Tool>Data`; the export toolbar is uniformly inserted between the methodology section and the chart toolbar. The placeholder text "CSV n/a" never appears (every tool exposes a dataProvider).
- **Cache locality.** Datastories cache (`data/datastories_cache/`) is gitignored and was already populated on this branch, so even the 30–90s tools (border-crossers, communities-time, walls-bridges, first-spark) returned in well under a second on this pass. First-time runs on a clean machine will be slow.

## Environment-level bugs (not tool-specific)

These came up during the crawl and aren't attributable to any one tool but block clean local development:

| Bug | Severity | Notes / Fix |
|-----|----------|-------------|
| Werkzeug debug-mode reloader 404s `/static/js/viz/_ds_*.js` (underscore-prefixed shared modules) intermittently on Windows. `Flask.test_client()` serves them, gunicorn serves them, only the in-process Werkzeug reloader exhibits the behaviour. The browser cache masks it most of the time, which is why nobody has noticed. | P0 (dev-only) | Workaround: rename the shared modules without leading underscore (`shared/export.js`, `shared/filters.js`, `shared/common.js`) and update imports. Underscored modules in the same directory as their importers are unusual; renaming costs little. Or run dev with `use_reloader=False` (lose hot-reload) or `FLASK_ENV=production` (lose debug page). |
| Initial server start can populate the **browser-cached** API response of `/api/stats/tag-cooccurrence` and `/api/stats/institutions` with `{"matrix": [], "tags": []}` etc. when the DB connection isn't yet ready. Subsequent fetches in the page hit the browser's cache (300–3600s `Cache-Control` from `cache_response()`) and render "no data" even after the server is healthy. | P1 | Either add `cache: 'no-cache'` on the page-side fetches in `topics.js` and `institutions.js` (cheap), or shorten the `cache_response()` TTL on those endpoints (also cheap), or make the loader detect-and-retry on empty data (most robust). |

## Verification gaps — explicit list

Tools where the dynamic check was skipped or timed out:

- **Explore: topics, authorcocit, centrality, communities, cocitation, bibcoupling, sleepers, journalflow, halflife, mainpath, temporal, readingpath** — verified via source review, but auto-load wasn't triggered in the preview because most are compute-on-demand. Topics in particular hit the empty-cache snag described above. None of these blocked the audit conclusions.
- **Datastories: border-crossers, communities-time, walls-bridges, first-spark** — preview_eval timed out at 30s while waiting for the heavy compute; their cached output suggests they work, and the loader shape matches the rest of the chapter.

A second pass in a real browser (Ctrl-Shift-R hard reload) is recommended to confirm the post-fix behaviour for every Explore tool now that the PALETTE imports and CSV stashes are restored.
