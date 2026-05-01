# 15 — Tool improvement plan (post-audit)

Prioritised punch-list for the 44 tools in `/explore` + `/datastories`, derived from [14-tool-audit.md](14-tool-audit.md). Buckets are ordered by user-visible impact: a P0 ships a broken thing, a P3 ships a complete-but-narrow thing.

Effort buckets:
- **S (small)** — single-file, single-line edits or under ~30 minutes including testing.
- **M (medium)** — single tool's refactor, or a shared-helper change that propagates to 2–5 tools.
- **L (large)** — cross-cutting (touches many tools or requires new shared infrastructure), or non-trivial design discussion.

Where a fix touches a shared file (e.g., `_ds_export.js`) it's marked **shared** so tool-by-tool counts stay honest.

## P0 — broken

Two of these landed during the audit; the rest are still pending.

| # | Tool / Area | Specific change | Effort |
|---|-------------|-----------------|--------|
| **DONE** | Author Network (`viz/author_network.js`) | Import `PALETTE` from `../utils/colors.js`. Without it, the circle fill callback throws ReferenceError at line 152 of the pre-fix file, which exits the function before the tick handler is registered, leaving 150 author nodes overlapped at the SVG origin. **Commit fd51243.** | S — done |
| **DONE** | Institutions (`viz/institutions.js`) | Same defect. PALETTE used by the bar fill (`PALETTE[0]`) and per-line trends colour map. **Commit 59cf9b0.** | S — done |
| 1 | most_cited.js | Add `window.__expMostCited = data;` after the `/api/stats/most-cited` response is parsed. CSV button currently shows "No data to export." | S |
| 2 | citation_trends.js | Add `window.__expCitTrends = data;` after the `/api/stats/citation-trends` response is parsed. | S |
| 3 | citation_network.js | Add `window.__expCitnet = data;` after the `/api/citations/network` response is parsed. | S |
| 4 | cocitation.js | Add `window.__expCocitation = data;` after the `/api/citations/cocitation` response is parsed. | S |
| 5 | bibcoupling.js | Add `window.__expBibcoupling = data;` after the `/api/citations/bibcoupling` response is parsed. | S |
| 6 | journal_flow.js | Add `window.__expJournalFlow = data;` after the `/api/citations/journal-flow` response is parsed. | S |
| 7 | half_life.js | Add `window.__expHalfLife = data;` after the `/api/citations/half-life` response is parsed. | S |
| 8 | author_cocitation.js | Add `window.__expAuthorCocitation = data;` after the `/api/author-cocitation` response is parsed. | S |
| 9 | communities.js | Add `window.__expCommunities = data;` after the `/api/citations/communities` response is parsed. | S |
| 10 | main_path.js | Add `window.__expMainPath = data;` after the `/api/citations/main-path` response is parsed. | S |
| 11 | temporal_evolution.js | Add `window.__expTemporal = data;` after the `/api/citations/temporal-evolution` response is parsed. | S |
| 12 | institutions.js (export wiring) | Two changes in one commit: (a) change `svgSelector: '#institutions-container svg'` to `svgSelector: '#inst-bar-chart'` (the actual id) and accept that PNG/SVG export of a Chart.js `<canvas>` needs a canvas-aware path in `_ds_export.js`; (b) add `window.__expInstitutions = data;` after the response is parsed. | S — tool, M if also fixing canvas-export in `_ds_export.js` (shared) |
| 13 | reading_path.js | Wire the export toolbar from scratch — `renderExportToolbar('tab-readingpath', { svgSelector: '#rp-graph-container svg', dataProvider: () => (window.__expReadingPath?.path \|\| []) })` at the start of `initReadingPath`, plus stash `window.__expReadingPath = data` after the `/api/citations/reading-path` response is parsed. | S |
| 14 | Werkzeug-reloader 404 on `_ds_*.js` (dev-only) | Rename `static/js/viz/_ds_export.js`, `_ds_common.js`, `_ds_filters.js` → `static/js/shared/export.js`, `common.js`, `filters.js` and update every importing module (~40 files). Underscore-prefixed sibling modules in JS aren't a convention anyone reads as "shared"; the rename is mild churn that also dodges the reloader bug. | M — touches every viz module's import line |
| 15 | API empty-cache lockup (topics, institutions) | In `topics.js` and `institutions.js`, add `{ cache: 'no-cache' }` to the `fetch()` calls. If the very first fetch returns empty data (e.g., during DB warmup), subsequent loads currently hit the browser cache for up to 1 hour. | S — two lines |

The P0-export bucket (#1–#13) is mechanically a one-line stash per file plus one toolbar wiring for reading_path. Could be done as a single "P0: restore CSV export across Explore" commit, but per the audit's per-tool commit policy I'd split into 12 per-tool stash commits + 1 reading-path wiring commit.

## P1 — readability

Render correctness is fine but the visual is harder to read than it needs to be.

| # | Tool / Area | Specific change | Effort |
|---|-------------|-----------------|--------|
| 1 | All force-directed networks (Author Network, Author Co-Citation, Citation Network, Communities, Co-Citation, Bib. Coupling, Border Crossers' neighbourhood, Shared Foundations, Academic Lineages, Prince Network) | Adopt the `_ds_common.js` `enableZoomPan(svg)` helper everywhere. Several of the Explore networks use ad-hoc d3.zoom wiring that doesn't add the floating "Reset view" chip and uses different scale extents. Standardising costs little and matches the Datastories convention. | M — touches ~10 modules; one PR |
| 2 | Author Network labels | Names with count ≥5 get tail-only text (last word). Joint surnames (`Lopez Garcia`) lose information. Use `name.split(' ').slice(-2).join(' ')` instead, capped at length 22. | S |
| 3 | Inside / Outside scatter (3238 svg children) | Default render shows every article — at >1500 points the dots overlap into a smear. Add quadrant-only labels (NW: "global-not-internal", NE: "concordant", SW: "neither", SE: "internal-not-global") and a hover-only highlight to make the quadrant interpretation legible at a glance. | S–M |
| 4 | Network legend in Citation Network | The "toggle all journals" control is a separate button bar; the legend itself is below the SVG. Combine: clickable legend swatches act as the journal-toggle. Same pattern works for Author Network. | M (shared `legend.js` helper would benefit several tools) |
| 5 | Reach of Citation grid (300 svg children, sparkline cards) | Cards are uniformly sized but accumulation totals vary by 100×. Sort-by-pattern + colour the sparkline area-under-curve by total citation count, not pattern alone. | S |
| 6 | Lorenz curve (Shape of Influence) | Add a faint Gini-equality diagonal that's labelled (`diagonal = perfect equality`) rather than the current unlabeled grey line. The Gini number lives in the summary card; making the visual reading-order match would reduce confusion. | S |
| 7 | Sankey diagrams (Braided Path, Communities Over Time) | Node label collision when a decade has many small flows. Add a "Show small flows" toggle that fades < 1% nodes on default and reveals on toggle. | M |
| 8 | All scatter plots — point overlap | Use `d3.symbolDensityScatter` or jitter for points-per-bin > N. Currently every scatter (Long Tail, Fair Ranking, Inside/Outside, Uneven Debts, Walls/Bridges, Unread Canon) uses raw `cx`/`cy` with default opacity. Default opacity to 0.6 and add a `radius * sqrt(n)` density indicator. | M (shared `scatter.js` would benefit ~7 tools) |

## P2 — interaction polish

| # | Tool / Area | Specific change | Effort |
|---|-------------|-----------------|--------|
| 1 | Explore filter persistence | The Datastories side persists filter state in localStorage (`_ds_filters.js`) and reflects it to the URL hash. The Explore side stores ad-hoc per-tool params in DOM inputs that wipe on refresh. Lift the filter bar from `_ds_filters.js` (or a generalised version) into Explore — consistency across the two surfaces matters more than per-section tailoring. | L |
| 2 | Click-vs-drag on force graphs (Author Network, Author Co-Citation, Communities, etc.) | Currently a click event after a drag-end can fire the click handler unintentionally (navigates off the page during a node-position-tweak). Standard fix: track `event.defaultPrevented` from the drag behaviour and short-circuit the click handler if so. d3-drag exposes this if you `event.sourceEvent.preventDefault()` on drag-start. | S — shared in `_ds_common.js` enableDrag helper |
| 3 | Network-tooltip flicker | Tooltips on Author Network and the citation networks reposition with `mousemove` but stay visible during pan. Hide on `zoom.start`, restore on `zoom.end`. | S |
| 4 | Reset-view button placement | The Datastories Reset chip is in the top-right. Author Network has a separate "Reset view" button outside the SVG. Move that into the SVG via `enableZoomPan(svg)` and remove the external button. | S (per-tool) — paired with P1.1 |
| 5 | Filter Apply round-trip | Datastories Apply re-fetches with new params (correct). Explore tools that have parameter widgets (centrality, communities, etc.) don't always re-fetch — some only redraw client-side. Audit each one. | M |
| 6 | Centrality + Sleeping Beauties: row-click selection | Tables show top-N rows but don't highlight the corresponding chart point on hover. Standard linked-views pattern (the chart highlights the hovered row, the row highlights the hovered chart point) would help interpretation. | M |
| 7 | Long Tail / Fair Ranking quadrant interpretation | Both scatters are interpretable per-quadrant but neither shows the quadrant boundaries explicitly. Add light grid lines at the median of each axis and a one-sentence quadrant caption per quadrant. | S |
| 8 | Reading Path build flow | Multi-step wizard is ergonomic for first use but the "edit seed and rebuild" path requires re-typing. Add a recently-built list (last 5 seeds) with one-click rebuild. | M |

## P3 — feature gaps

Analysis is correct but the panel surfaces less than the user might want.

| # | Tool / Area | Specific change | Effort |
|---|-------------|-----------------|--------|
| 1 | Author Network — top_n cap | Default top_n=150 hides the long tail. Allow up to 500 with a clear "showing N of M" caption. The API already accepts up to 350. | S |
| 2 | Most-cited Citations | List-only; no breakdown by journal, decade, or cluster. Add a small per-journal breakdown sub-bar atop the list. | M |
| 3 | Citation Network — drill-in | Click on a citation-network node currently centres + highlights but doesn't enable a "show neighbourhood" sub-graph. VOSviewer and CitNetExplorer both support this. Add a "Focus on this node" button in the infobar that re-fetches a 2-hop subgraph. | M–L |
| 4 | Communities — community-detail content | The community sidebar shows the top journals/tags but not a top-articles table. The Datastories Walls/Bridges tool does have a per-community detail; lift that pattern into Explore Communities. | M |
| 5 | Datastories — comparative views | Many tools tell a great single-cluster story but the comparative case ("how does this look in TPC vs RC?") requires visiting twice. Add a "compare" toggle that splits the panel and runs the analysis under two cluster filters in parallel. Could prototype on Speed of Influence first (already has direction-bucketed structure). | L |
| 6 | Two-Way Street | Most-reciprocated table is article-level. Add a per-author and per-journal aggregation tab — both views are interpretable answers to "who reciprocates?" | M |
| 7 | Books Everyone Reads | Master-list table only. Add the union/intersection visualisation (which other tools each article appears in) as a per-row detail row. The data is already there — `n_tools` and `tools` are stashed. | S–M |
| 8 | Disciplinary Calendar | The events list itself is hand-curated. Expose an "Add event" admin path that writes to the database (or to a JSON file under `data/`). Currently every event change requires a code edit. | L |
| 9 | Reading Path | The path itself is great; export currently writes BibTeX and plain text. Add an annotated-graph export (graph + per-node short-text) so the path can be imported into Zotero or shared as a PDF. | M |
| 10 | Sleeping Beauties — Prince linkage | The Datastories Prince Network already has the SB→prince mapping. Add a "show princes" button on the Explore Sleeping Beauties detail card that links into the Datastories tool with the SB pre-selected. | S |

## Cross-tool dependencies

Several P0/P1 fixes share a target file. If grouping commits, the deps look like:

- **`_ds_common.js`** — change once, P1.1 (zoom/pan helper standardisation), P2.2 (drag/click separation), P2.3 (tooltip-hide-on-zoom) all benefit ~10 tools each.
- **`_ds_export.js`** — change once, P0.12 (canvas export path) unblocks Institutions PNG/SVG; same change handles Timeline and Citation Trends if anyone wants high-DPI canvas export later.
- **`_ds_filters.js`** — generalisation for P2.1 (Explore-side filter persistence) is a moderate refactor but would homogenise the two surfaces.

## Cross-reference against external bibliometric tools

The previous session established that Pinakes' tool inventory is broadly aligned with what VOSviewer, CitNetExplorer, Bibliometrix, Connected Papers, and Scopus offer. Re-checking that against the current state:

- **VOSviewer** — clusterised similarity maps and density-based visualisation. We have the analysis (Communities, Co-Citation, Bib. Coupling, Two Maps); the visualisation defaults are sparser (raw force-directed, no density overlay). P1.8 (density-aware scatter) is the closest gap.
- **CitNetExplorer** — designed for navigation through citation chains. Reading Path is the closest analogue and works well. The drill-in / 2-hop subgraph affordance (P3.3) is the obvious gap; CitNetExplorer treats it as primary.
- **Bibliometrix** — heavy on standardised author / venue / source indices (h-index, m-index, lhi). We surface citation counts and Gini but not the canonical author-level scores. Adding an author-page summary card with h-index could be a separate P3 item; not in this list because it's an author-page improvement, not a tool improvement.
- **Connected Papers** — similarity-by-shared-references plot for a seed. Reading Path's first view (graph) is similar; the difference is Connected Papers' "prior work / derivative work" axis layout. Not a critical gap.
- **Scopus** — comprehensive list views and faceted filters. Our list views are simpler; the cluster-aware facet in `_ds_filters.js` is more powerful than Scopus's flat-list filtering, but the per-tool faceting is shallower (no per-author or per-keyword facet within a tool). P3 territory.

The standout absence in Pinakes — and not on the audit table because nothing currently does it — is a **journal-level overview** comparable to Scopus's source-page or VOSviewer's source-cluster view. Could be a Datastories Chapter 10 ("The Journal as Unit"). Out of scope for this plan.

## Sequencing recommendation

If this list is pulled into one or two follow-up sessions:

1. **Session A (P0)** — restore CSV export across Explore (12 stashes + reading_path wiring + Institutions export). All P0 work in this session lands on `datastories-build`.
2. **Session B (env)** — rename the underscore-prefixed shared modules to dodge the Werkzeug 404 + add `cache: 'no-cache'` on the empty-cache-prone fetches.
3. **Session C (P1, P2.1–4)** — readability + interaction polish. Most are < 30 min each; bundle by shared file.
4. **Session D (P3)** — feature gaps; pick the 2–3 highest-impact, defer the rest.
