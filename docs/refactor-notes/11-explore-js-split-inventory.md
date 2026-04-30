# 11 — explore.js split into ES modules (Prompt F2)

Migration record for the `static/explore.js` (4,468 lines, 100 top-level functions) → `static/js/{loader, utils, viz}` decomposition. Pure structural refactor: every viz looks and behaves identically post-split.

## Final structure

```
static/js/
├── explore-loader.js        (entry; eager-imports all viz modules)
├── utils/
│   ├── colors.js           (PALETTE, journalColor, _journalColorMap, citnetJournalColor)
│   ├── tooltips.js         (escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar)
│   └── highlight.js        (applyHighlight, clearHighlight)
└── viz/
    ├── author_cocitation.js  (9 fns)
    ├── author_network.js     (1 fn — loadNetwork + button-wiring moved here)
    ├── bibcoupling.js        (5 fns)
    ├── centrality.js         (7 fns)
    ├── citation_network.js   (6 fns)
    ├── citation_trends.js    (2 fns)
    ├── cocitation.js         (5 fns)
    ├── communities.js        (5 fns)
    ├── half_life.js          (6 fns)
    ├── institutions.js       (1 fn)
    ├── journal_flow.js       (5 fns)
    ├── main_path.js          (4 fns)
    ├── most_cited.js         (1 fn)
    ├── reading_path.js       (15 fns)
    ├── sleeping_beauties.js  (3 fns)
    ├── temporal_evolution.js (9 fns)
    ├── timeline.js           (3 fns)
    └── topics.js             (1 fn)
```

`templates/explore.html` line 2005 was changed from
`<script src="/static/explore.js?v=...">` to
`<script type="module" src="/static/js/explore-loader.js?v=...">`.

The original `static/explore.js` is **kept in place** as a one-line revert path. If anything breaks in production, swap that one `<script>` tag back and the old monolithic file takes over while the regression is investigated.

## Key design decisions

### Eager imports, not lazy

The prompt suggested lazy `import()` per accordion section. I went **eager** instead — `explore-loader.js` does static `import "./viz/timeline.js";` etc. for all 18 viz modules at module-load time. Reasons:

1. Inline `onclick=`/`onchange=` handlers in the templates and inside HTML-string fragments inside the JS code reference functions (e.g., `loadCitationNetwork`, `toggleAllCitnetJournals`) defined in viz modules. ES modules don't expose top-level names globally, so each viz module ends with `window.X = X;` for every name an inline handler might call. Those `window.X = X;` lines run at module body execution.
2. With **lazy** imports, a button inside a freshly-opened accordion section could fire its `onclick` *before* the dynamic-import resolved, throwing `ReferenceError`. Race condition.
3. With **eager** imports, all 18 modules are loaded before the loader's body runs, which is before `DOMContentLoaded`. By the time any user can click anything, every needed `window.X` is bound.

Cost: total JS payload arrives at page load instead of being chunked by accordion section. For Pinakes's traffic profile (academic users on patient connections, content-heavy not interactive-heavy) this is fine — total payload is ~ 175 KB raw / ~ 50 KB gzipped, comparable to D3 itself.

### Inline-handler globals

51 function names are exposed to `window` from the various viz modules so existing `onclick="..."` attributes keep working. These are listed in `INLINE_HANDLER_NAMES` in the (now-deleted) split tool; the canonical list is just whatever functions appear at the bottom of each viz module under the `// ── Inline-handler globals ─────────────` comment block.

### Network-tab button wiring moved into `author_network.js`

The original `showTab` body had inline event-listener wiring for the network-tab's reload/reset/infobar-close buttons. That wiring touched module-private state (`netSvgEl`, `netZoomBehavior`) that's not accessible from the loader after the split. Rather than export the state as live bindings, I moved the wiring **into `loadNetwork()`** itself, gated by a `netButtonsWired` flag so listeners attach exactly once. Same observable behavior, cleaner module boundaries.

### Cross-module function calls go through `window`

The loader's `showTab(name, btn)` calls into many viz modules' load functions: `loadTimeline()`, `loadHeatmap()`, `loadNetwork()`, `initAuthorCocitation()`, `loadCitations()`, `loadCitTrends()`, `loadCitationNetwork()`, `initReadingPath()`, `loadInstitutions()`. These bare references don't resolve in module scope (modules are strict-mode by default). The loader calls them as `window.loadX()` instead. Consistent with the inline-handler pattern.

### Browser baseline

ES modules + dynamic `import()` need Chrome 91+, Firefox 89+, Safari 15+ (all > 4 years old at this writing). Documented in the loader's header comment. No bundler, no transpiler, no `package.json`.

## Verification I was able to do

- **JS syntax check** (`node --check`) on all 22 emitted files: all OK.
- **`pytest`** still passes 351/351 — the test harness only checks `/explore` returns 200, which it does (305 KB HTML).
- **Module-graph load test** via the Claude preview server: dynamic `import('/static/js/explore-loader.js')` from a sibling page loaded the loader and all 18 viz modules with **zero errors** and **zero missing globals** (all 51 `window.X` names resolved as functions).
- **Static asset serving**: every module file returns 200 with reasonable byte size.

## Verification I was NOT able to do

- **Actual chart rendering with real data.** The pytest suite doesn't execute the JS; the preview's module-load test confirms imports resolve but doesn't trigger the visualizations against the live DB.
- **Browser interactivity.** Clicking a node, dragging a slider, hovering for a tooltip — all need a human in front of the page.
- **Cross-browser parity.** Modules are well-supported in Chrome / Firefox / Safari, but a specific bug there would need a human eye.

## Click-through verification checklist

After this lands on production (or against the local dev server at `localhost:5000`), open each URL below in turn. For each:

- Page loads with no red errors in the browser console (DevTools → Console).
- The visualization renders something visible (chart, network, table — not a blank panel).
- Try one interaction: drag a slider, click a node, hover a chart point, change a journal-filter checkbox.
- Click the Back button or another tab and confirm clean transitions.

| # | Visualization | URL | Notes |
|---:|---|---|---|
| 1 | Timeline | https://pinakes.xyz/explore#timeline | Loads automatically; toggle "Top 8 / All" button |
| 2 | Topics (Tag co-occurrence) | https://pinakes.xyz/explore#topics | Heatmap; hover for cell tooltip |
| 3 | Author Network | https://pinakes.xyz/explore#authors | Loads automatically; reload/reset/search buttons; node hover |
| 4 | Most Cited | https://pinakes.xyz/explore#citations | Table with article links |
| 5 | Citation Trends | https://pinakes.xyz/explore#citation-trends | Chart.js line chart |
| 6 | Citation Network | https://pinakes.xyz/explore#citnet | Click "Compute" — slider + checkbox group control |
| 7 | Centrality | https://pinakes.xyz/explore#centrality | Click "Compute" — eigenvector / betweenness toggle |
| 8 | Co-Citation | https://pinakes.xyz/explore#cocitation | Click "Compute" — force layout |
| 9 | Bibliographic Coupling | https://pinakes.xyz/explore#bibcoupling | Click "Compute" |
| 10 | Communities | https://pinakes.xyz/explore#communities | Click "Compute"; community-color legend |
| 11 | Journal Flow | https://pinakes.xyz/explore#journal-flow | Click "Compute"; chord diagram |
| 12 | Half-Life | https://pinakes.xyz/explore#half-life | Click "Compute"; switch chart view (Comparison / Timeseries / Distribution) |
| 13 | Main Path | https://pinakes.xyz/explore#main-path | Click "Compute" |
| 14 | Temporal Evolution | https://pinakes.xyz/explore#temporal-evolution | Click "Compute"; metric dropdown changes time series; year slider switches snapshot |
| 15 | Sleeping Beauties | https://pinakes.xyz/explore#sleeping-beauties | Click "Compute"; click a row to open the per-article detail chart |
| 16 | Institutions | https://pinakes.xyz/explore#institutions | Bar chart + line chart |
| 17 | Author Co-Citation | https://pinakes.xyz/explore#author-cocitation | Click "Compute"; force layout; click a node to see top co-cited partners |
| 18 | Reading Path | https://pinakes.xyz/explore#reading-path | Search a seed article; build path; toggle Graph / List / All views |

If anything fails:

1. **Quick revert**: edit `templates/explore.html` line 2005, change `<script type="module" src="/static/js/explore-loader.js?v=...">` back to `<script src="/static/explore.js?v=...">`. The old monolithic file is still in the repo. Push that one-line change. Production is restored.
2. **Diagnose**: open DevTools, find the failing line. The most likely failure modes:
   - A function reference that should be `window.X()` but is bare. Add `window.X = X;` to that viz module's globals block, or change the reference to `window.X()`.
   - A module-private variable referenced from another file. Move the variable to `utils/` or change the reference to access it via the function that owns it.
   - A typo from the move. Diff against `static/explore.js` to spot the line.

## Decisions explicitly NOT made

- **No bundler.** No `package.json`, no Vite, no esbuild.
- **No D3 vendoring.** Still loaded from `cdn.jsdelivr.net`.
- **No TypeScript.** Vanilla JS by design.
- **No template edits.** All inline `onclick=`/`onchange=` attributes in `templates/explore.html` are unchanged; they continue to call functions on `window`.
- **No CSP relaxation.** The existing `'unsafe-inline'` for scripts already covered the inline handlers; no new CSP allowances needed.
- **No deletion of `static/explore.js`.** Kept as a one-line revert. A future prompt can delete it after a few weeks of clean operation.
