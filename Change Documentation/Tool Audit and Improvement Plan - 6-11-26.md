# Explore + Datastories tool audit — June 11, 2026

Read-only audit of all 45 tool endpoints (19 Explore-side, 26 Datastories),
per the brief: load times, data fidelity, UX/UI, build artifacts. Findings
below; the triaged improvement plan follows at the end. No code was changed
during the audit.

Method: cold/warm timing matrix via in-process test client against the local
DB (54,169 articles — near-identical to production's 53,906), independent SQL
re-derivation for fidelity spot checks, light production sampling for
calibration, code review of the compute paths, frontend console/render pass.

---

## Findings

### F1 — The citation pipeline is never scheduled (data fidelity, systemic)

`weekly_maintenance.py` documents itself as "runs every Sunday at 03:00 UTC
via cron (set up in start.sh)" — but no start.sh exists, the Dockerfile runs
only gunicorn, and no GitHub workflow invokes it. The daily cron triggers
articles fetch + backup only. Consequences:

- New articles (including yesterday's 791 deep-refresh recoveries with DOIs)
  never get reference lists harvested (`cite_fetcher`, step 4).
- OpenAlex enrichment (abstracts, affiliations, OA status — steps 5, 9) and
  retag/FTS rebuild (step 7) likewise never run automatically.
- The citation graph — the substrate of every tool in this audit — silently
  ages relative to the article index. Each day of fetching widens the gap.

Quantified: **791 DOI-bearing articles have never been reference-harvested**
(precisely yesterday's deep-refresh recoveries) and **793 have never been
OpenAlex-enriched**. The backlog equals everything added since the last
manual maintenance run, and grows with every fetch.

### F2 — Denormalized citation counters drift (data fidelity)

`internal_cited_by_count` / `internal_cites_count` on articles are
recomputed only by `update_citation_counts()` inside cite_fetcher runs
(which are unscheduled, see F1). Ranking surfaces (Most Cited page,
citation-network node selection, centrality eligibility) read the
denormalized columns. Today's Reflections dedup repointed 9 edges without
recomputing; the deep refresh added articles with zero counters.

Quantified: 10 articles' `internal_cited_by_count` and 98 articles'
`internal_cites_count` currently disagree with the citations table. The
top of the Most Cited ranking is unaffected today (verified: API top entry
matches true SQL counts exactly), but the drift compounds with every
unsynced write.

### F3 — Frozen numbers in panel methodology prose (data fidelity)

Every Datastories panel's "Run with default parameters…" paragraph (and
several Explore methodology blocks) embeds point-in-time results: corpus
Gini 0.909, 51,843 articles, 41,444 sources (79.9%), 17 cross-decade
ribbons, insularity 65.1, "13,442 articles, 44,322 edges," etc. The corpus
grew by ~1,800 articles this week alone; the live tool outputs already
diverge from the prose beside them. This is the inverse of a chart bug: the
charts are right and the words are wrong, which reads as an accuracy failure
to any careful user.

### F4 — ch4-border-crossers cannot complete a production request (perf, severity: broken)

The tool computes exact Brandes betweenness over the full classified
citation graph. Panel copy and docstring claim "60–90s first run"; measured
cold on this audit's machine it exceeded 25 minutes and was aborted
(production's shared single CPU is slower). gunicorn's worker timeout is
300s. The disk cache cannot save it because of F5. Net: any visitor click
on Border Crossers in production starts a compute that can never answer
within the timeout. The book's figures were produced by the CLI scripts;
the live tool is structurally unable to respond at current corpus size.

### F5 — Datastories disk cache invalidates every night (perf)

`datastories_cache._db_fingerprint()` is articles.db mtime+size. The 03:00
UTC cron always writes the DB (fetch_log timestamps at minimum), so every
cached entry — including any expensive warm entries — is invalidated daily.
There is no pre-warm step, so the first visitor after 03:00 pays every cold
compute. Combined with F4 this means the heavy tools are cold essentially
always.

### F6 — `cache_response` is a browser header, not a server cache (perf, architecture)

`web_helpers.cache_response(seconds=N)` only sets `Cache-Control: public,
max-age=N`. There is no server-side reuse across visitors for any Explore
endpoint. Mitigating discovery: the Explore endpoints are all fast (worst
cold: author-cocitation at 1.9s; everything else under 1s) because their
default graphs are capped at 400–800 nodes, so this is an architectural
footnote rather than a live problem — but the "cached after first run"
language in panel copy describes a cache that does not exist server-side.

### F7 — ~4,700 lines of dead JS from the pre-modular build (artifacts)

`templates/explore.html` loads only `js/explore-loader.js`, which imports
the modular `js/viz/*.js` files. Seven root-level files are referenced by
nothing: static/explore.js (3,937 lines, git-tracked), filters.js,
common.js, export.js, colors.js, highlight.js, tooltips.js (untracked local
strays). The only mentions anywhere are provenance comments in the viz
modules ("Extracted from the monolithic static/explore.js"). The copy
rewrite earlier this week even dutifully edited strings in the dead file.

### F8 — Assorted smaller artifacts

- `scrape_reflections` (~200 lines) registered in scraper.py's SCRAPERS
  dispatch but its journal is no longer in SCRAPE_JOURNALS; documented as
  dead code in comments. Same for the dead `reflections` strategy comment
  blocks in journals.py.
- Hidden tab-button shims "kept for JS compatibility" in both explore.html
  and datastories.html.
- blueprints/admin.py docstring says gunicorn's timeout is 120s; the
  Dockerfile sets 300s.
- Stray zero-byte index.db and pinakes.db at repo root, plus local log files
  (untracked).
- Stale heaviness claims in panel copy: "Heavy first-time compute (60–90s)"
  / "~30–60s" appear on tools measured at 1.5–4s, while the genuinely heavy
  tool's claim is short by an order of magnitude.

### F9 — Publication Timeline hides 31% of the corpus (data fidelity)

`/api/stats/timeline` is hardcoded to 1990–present. **17,004 of 54,169
articles (31%) predate 1990** and never appear in the tool the About page
recommends as "the gentlest entry into the corpus," with no UI indication
that a floor exists. (30 undated articles are additionally invisible to all
date-driven tools — acceptable, but worth a note on Coverage.)

### F10 — Click-to-compute friction guards costs that no longer exist (UX)

Six Explore tools (centrality, communities, cocitation, bibcoupling,
sleepers, journal-flow, plus main-path/temporal) deliberately do not load on
tab activation; the loader comments say "Don't auto-load — user clicks
'Compute' to start." Measured costs: every one of these endpoints returns in
under 1s locally, ~1–3s on production. The friction is a fossil of an
earlier, slower corpus/implementation. A first-time visitor opening
"Community Detection" sees prose and an unexplained Compute button instead
of the visualization.

### F11 — UX checks that passed, and two left unverified

Passed: zero console errors/warnings across the home page, ten activated
Explore tabs, a computed centrality run, login flow, and a rendered
Datastories panel. Filter scoping verified server-side (journal-filtered
most-cited returns only that journal). Explore filter persistence to
localStorage works by design. Not verified (manual checks recommended):
true-mobile (375px) layout for the chart-heavy panels, and chart legibility
under the terminal/scandi themes (Chart.js/D3 palettes are fixed
light-theme colors; charts likely render light-on-dark incongruously under
the terminal theme).

---

## Timing matrix (local, cold = no disk cache, fresh process)

| Endpoint | Cold | Warm | Notes |
|---|---|---|---|
| ds ch4-border-crossers | **>25 min, aborted** | n/a | exact Brandes betweenness; prod ≈ 3× slower |
| ds ch6-first-spark | **98.1 s** | 65 ms | SPC over full DAG; prod ≈ 5 min → exceeds 300s timeout |
| ds ch4-speed-of-influence | 4.0 s | ~60 ms | |
| ds ch7-two-maps | 3.4 s | ~70 ms | double Louvain |
| /api/author-cocitation | 1.9 s | **1.9 s** | no server cache; 6.1 s measured on production, every view |
| ds ch6-walls-bridges | 1.7 s | ~60 ms | |
| ds ch6-communities-time | 1.5 s | ~60 ms | copy claims "30–60s" |
| ds ch4-shifting-currents | 1.0 s | ~60 ms | |
| /api/citations/temporal-evolution | 0.74 s | 0.77 s | no server cache |
| everything else (36 endpoints) | < 0.6 s | < 0.5 s | healthy |

Production calibration (4-endpoint sample): ~3× local. All 88 measurements
returned HTTP 200; no errors.

## Fidelity checks

| Check | Result |
|---|---|
| Most Cited top entry vs true SQL citation counts | PASS (exact) |
| Timeline 2020 sum vs SQL | PASS (1,444 = 1,444) |
| Branching Traditions RC count vs journal_groups SQL | PASS (35,527; prose says 33,131 → F3) |
| Shape of Influence Gini vs independent recomputation | PASS (0.9127 vs 0.9126; prose says 0.909 → F3) |
| Solo-to-Squad 2020s solo rate vs SQL | PASS (61.5% vs 61.6%) |
| Journal filter scoping (most-cited) | PASS (0 rows outside filter) |
| Denormalized counters vs citations table | **FAIL** (10 + 98 drifted → F2) |
| Reference-harvest / enrichment backlog | **FAIL** (791 / 793 → F1) |

The computations themselves are trustworthy; the failures are pipeline
staleness, not algorithm bugs.

---

# Improvement plan (triaged)

Ordering follows the brief: data fidelity first, then availability and
performance, then UX, then cleanup. Effort: S < half a session, M ≈ a
session, L > a session.

## P0 — Data fidelity

**P0.1 — Schedule the maintenance pipeline.** (M) Add a weekly GitHub
Actions cron that triggers a new admin endpoint (e.g., POST
/api/admin/run-maintenance, background-threaded like /fetch and run-backup)
wrapping weekly_maintenance steps 4–9: cite_fetcher, OpenAlex enrichment ×2,
LiCS refs, retag/FTS, OA backfill. Include `update_citation_counts()` at the
end of every run (fixes F2 going forward). One-off first action: run the
pipeline once to clear the 791/793 backlog. This single item re-anchors
every tool in both tabs to current data.

**P0.2 — Resync denormalized counters now.** (S) One-off
`update_citation_counts()` run on local + prod; fold into P0.1's pipeline
and into any future migration that touches the citations table (the dedup
script should have called it — lesson recorded).

**P0.3 — Surface or remove the timeline's 1990 floor.** (S) Either extend
`get_timeline_data()` to full history (the chart handles 90+ year spans
fine with the existing journal filter) or add a visible scope note plus a
"show full history" toggle. Recommend: extend, with a "sparse pre-1990
coverage" caveat in the panel intro tied to the Coverage page.

**P0.4 — Fix the frozen numbers in panel prose.** (M) Two-part fix: (a) add
a one-line provenance note to every "Run with default parameters…"
paragraph — "Figures in this paragraph describe the <date> corpus snapshot;
the live tool reflects the current index." (b) refresh the headline numbers
once, post-P0.1, so the snapshot date is recent. Optional later: template
the 3–4 most-visible numbers (corpus Gini, article totals) from the API.
Include the stale heaviness claims ("60–90s", "30–60s") in the same sweep —
after P1.1 the honest numbers are seconds, except where they aren't.

## P1 — Availability and performance

**P1.1 — Make Border Crossers and First Spark answerable.** (M–L) Border
Crossers: replace exact Brandes with pivot-sampled approximation
(`nx.betweenness_centrality(G, k=256, seed=fixed)`) — preserves top-bridge
ranking at roughly two orders of magnitude less compute; document the
approximation in the methodology block (it is itself a defensible
bibliometric choice). First Spark: persist the SPC edge table keyed by the
cache fingerprint, or precompute via P1.2's pre-warm. Acceptance test: both
tools answer cold within the 300s prod timeout, ideally < 60s.

**P1.2 — Stop the nightly cache massacre; pre-warm instead.** (M) Two
complementary changes: (a) make `_db_fingerprint()` hash data shape
(MAX(articles.id), COUNT(articles), COUNT(citations)) rather than file
mtime+size, so fetch_log touches and no-op fetches stop invalidating; (b)
add a post-fetch pre-warm step to the nightly cron (after the existing
10-minute sleep) that hits each heavy Datastories endpoint with default
parameters, so the first human visitor of the day gets disk-cache speed.

**P1.3 — Server-side cache for the two slow Explore endpoints.** (S) Apply
`datastories_cache.cached` to `get_author_cocitation_network` (6.1s per
production view today) and `get_temporal_network_evolution`. Everything
else is fast enough to leave alone.

## P2 — UX/UI

**P2.1 — Auto-load the sub-second tools.** (S–M) Remove the Compute gate
for centrality, communities, cocitation, bibcoupling, sleepers,
journal-flow, main-path, temporal: load with default parameters on first
tab activation (the pattern timeline/topics/citnet already use), keep the
button as "Recompute" for parameter changes. Depends on P1.3 for
author-cocitation.

**P2.2 — Uniform loading states.** (S) Some panels show a loading message
during fetch, others render nothing until data lands. Standardize on the
existing `.loading-msg` pattern across all panels.

**P2.3 — Manual verification pass: mobile + themes.** (S) Check the
chart-heavy panels at a true 375px viewport, and chart legibility under
terminal/scandi (fixed light Chart.js/D3 palettes on dark backgrounds).
File follow-ups from whatever it finds; candidate fix is theme-aware chart
palettes read from CSS variables.

## P3 — Artifacts and cleanup

**P3.1 — Delete the dead pre-modular JS.** (S) Remove git-tracked
static/explore.js (3,937 lines) and the six untracked root strays
(filters/common/export/colors/highlight/tooltips.js). Zero references
outside provenance comments.

**P3.2 — Sweep the small artifacts.** (S) Remove `scrape_reflections` and
its SCRAPERS registration (+ dead strategy comments); delete zero-byte
index.db/pinakes.db; correct the 120s-timeout comment in admin.py; decide
whether the hidden tab-button shims in explore.html/datastories.html stay
(they are load-bearing for showTab dispatch today — removing them is a
refactor, not a deletion; fine to keep with a clearer comment).

## Suggested sequencing

1. **Session A (fidelity):** P0.2 → P0.1 (endpoint + cron + backlog run) →
   P0.3. Re-run the fidelity battery as acceptance.
2. **Session B (heavy tools):** P1.1 → P1.2 → P1.3, then P0.4's prose
   refresh against the now-current corpus.
3. **Session C (UX + cleanup):** P2.1 → P2.2 → P3.1 → P3.2, plus P2.3's
   manual checks.

Total estimate: three working sessions. Nothing here blocks anything else
except where noted (P2.1 ← P1.3; P0.4's number refresh ← P0.1).

---

# Implementation status (same day, June 11, 2026)

All plan items were implemented in one pass (commits 93912b0 + 1e61111):

- **P0.1** ✅ POST /api/admin/run-maintenance (background thread, steps 4–9)
  + .github/workflows/maintenance.yml (Sundays 04:00 UTC). Backlog run
  executed locally (step 4: 13 min, step 5: 6 min, steps 6–9 follow) and
  triggered on production post-deploy.
- **P0.2** ✅ Counters resynced both DBs; cite_fetcher's built-in resync now
  runs weekly via P0.1.
- **P0.3** ✅ Timeline serves full history (first real year: 1939); panel
  intro notes sparse pre-1990 deposit coverage.
- **P0.4** ✅ 43 provenance notes ("June 2026 corpus snapshot; the live tool
  reflects the current index") inserted across both panel files; stale
  heaviness claims corrected to measured values.
- **P1.1** ✅ Border Crossers: pivot-sampled betweenness (k=256, fixed seed,
  exact below 512 nodes); methodology prose describes the approximation.
- **P1.2** ✅ Fingerprint now hashes row populations (max id + count over
  articles and citations); POST /api/admin/prewarm added; daily cron
  triggers prewarm 15 minutes after the fetch. Cache keys canonicalized
  via signature binding so prewarm and blueprint calls share entries.
- **P1.3** ✅ get_author_cocitation_network and
  get_temporal_network_evolution disk-cached.
- **P2.1** ✅ Nine Explore tools auto-load on first activation; buttons
  relabeled Recompute. Verified rendering in-browser for centrality,
  communities, journal-flow, sleeping-beauties, half-life, main-path.
- **P2.2** ✅ Spot-checked: the viz modules' existing loading-msg pattern
  fires under auto-load.
- **P2.3** ◻ Mobile + theme checks remain manual follow-ups.
- **P3.1** ✅ static/explore.js deleted (git) + six untracked strays removed.
- **P3.2** ✅ Reflections scraper (~340 lines) and registration removed;
  zero-byte DBs deleted; stale timeout comments corrected; tab-button
  shims kept (load-bearing for showTab dispatch).

**Bonus find during implementation:** Half-Life and Main Path had been
silently broken since the explore.js → modules extraction — `jflowAbbrev`
stayed module-local in journal_flow.js while half_life.js and main_path.js
called it as a bare global (ReferenceError after data load; the
click-to-compute gate hid it). Fixed by export/import; a cross-module
bare-reference scan found no other instances. This is the strongest
argument the audit produced for P2.1: auto-loading tools makes silent
render failures visible immediately.
