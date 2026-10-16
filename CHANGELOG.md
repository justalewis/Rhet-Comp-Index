# Changelog

All notable changes to Pinakes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project loosely follows [SemVer](https://semver.org/) — though as a single-deployment site rather than a published library, version bumps mostly track operational milestones (the JWA submission, schema migrations, etc.).

History before 2026-04 is not reconstructed here. The Git log is authoritative for older changes.

## [Unreleased]

### Added (October 2026)

- Usage statistics (`usage/`, off unless `PINAKES_USAGE_TRACKING=1`). Each request is recorded to a separate `usage.db` with its page or tool, referrer, language, browser type and a daily-rotating visitor id; `static/usage.js` adds browser-confirmed views, tool switches inside Explore and Datastories, DOI click-throughs, exports and time on page. A nightly resolver identifies networks and institutions (verified reverse DNS, RDAP, IPEDS, ROR, the Hipo universities list, DB-IP Lite), classes each visitor-day as person, likely person, bot, feed reader or datacenter, and deletes raw IP addresses. Token-gated dashboard at `/admin/usage`, a printable grant report with a public small-count mode, CSV export, `python -m usage` jobs, two Windows scheduled tasks, `usage.db` in the nightly backup, and a "Usage statistics" notice on `/about`. See `docs/usage-statistics.md`.
- Usage-statistics privacy fixes from a doc-versus-code audit, before recording is switched on: a removed author's name is no longer stored (404s keep no object, referrers and browser events keep only route patterns, the removal ledger is applied when rows are written and again nightly); GPC/DNT visits no longer keep Cloudflare's city and region, and a visitor who sends either signal is treated that way for the whole day; `usage.db` uses `secure_delete`, the resolver truncates its write-ahead log, and the nightly backup scrubs addresses and salts from its copy (`scrub_usage_backup.py`).
- Optional "how you use Pinakes" form at `/tell-us` (off unless `PINAKES_USAGE_SURVEY=1`): institution, role, uses, course, a note that can be quoted with permission, and an optional contact email. Responses are matched to institutions nightly and appear on `/admin/usage` (with CSV export and delete) and, as counts and consented quotes, in the grant report.
- `GET /api/admin/client-ip` and `deploy/windows/Test-PinakesClientIp.ps1`: check that the app sees real visitor addresses through Cloudflare → IIS → Waitress (`PINAKES_TRUSTED_IP_HEADER`).

- 12 journals from a field-coverage audit against CrossRef: Journal of Basic Writing, Language and Learning Across the Disciplines, Academic.Writing, RhetNet, Academic Labor: Research and Artistry, Journal of Second Language Writing, Quarterly Journal of Speech, Argumentation and Advocacy, Discourse and Writing / Rédactologie, Journal of Writing Assessment, Women's Studies in Communication, and Written Language & Literacy (about 12,600 articles). Held back for scope or coverage: Reading and Writing, English Education, Technical Communication (STC).
- Sidebar and Atlas group "Writing Research & Assessment" (Assessing Writing, Journal of Writing Research, Journal of Academic Writing, Journal of Writing Assessment, Journal of Second Language Writing).
- `journals.CROSSREF_SELECTORS`: select a journal by DOI prefix and container title when its CrossRef deposits carry no ISSN (RLEE, Academic Labor), with `exclude_dois` for mis-registered duplicates.
- Publication-year range (`year_from` / `year_to`) on the timeline, tag co-occurrence, author network, institutions, and citation-trends endpoints, with From / To controls on the Explore panels.
- `coverage_report.tier_journals`: the coverage page's journal lists are now generated from the live per-journal snapshot instead of kept by hand.
- `tests/test_registry_surfaces.py`: fails when a registry journal lacks a chord-diagram abbreviation or a sidebar group lacks an Atlas orientation.
- Schema v17: two partial unique indexes on `citations` (one row per citing article and indexed article; one per citing article and DOI or synthetic key). References with neither stay unconstrained. `init_db` builds them on a clean table and only warns while duplicates remain.
- `citation_duplicates_report.py`: read-only report on duplicate citation rows (totals, by citing journal, most inflated articles, when the surplus rows were written).
- `dedupe_citations.py`: dry run by default; `--yes` backs up the database, deletes duplicate rows (keeping the lowest id), builds the v17 indexes, recomputes the internal counts and clears the Datastories cache.

### Fixed (October 2026)

- Duplicate citation rows inflated Most Cited, co-citation, bibliographic coupling, communities, Sleeping Beauties and the article pages. The weekly LiCS reference scrape deleted only its DOI-less rows before re-inserting, so every Sunday added another copy of each reference that carried a DOI (one pair reached 38 copies; "Composing a Further Life" showed 75 citations from 4 citing articles). The scraper now replaces all of its own rows, skips articles whose references came from CrossRef, and removes any copy it had stacked on a CrossRef list. Internal counts and every citation-network query now count distinct articles, not rows.
- The Datastories tools that read citation rows directly (Braided Path, every tool built on the shared edge list, Long Tail, Shifting Canons, Reach of Citation, and the Prince Network, which listed a citer once per copy) and the "Who reads" panel on author pages now count each citing→cited link once. On a clean table their output is unchanged.
- The coverage report behind /coverage and the methodology exports (`coverage_report.py`) counted stored citation rows, so repeated rows inflated its raw and resolved outbound edges, internal inbound edges, intra-journal edges and corpus citation totals. It now counts each reference once. On a clean table every figure is unchanged.
- Sleeping Beauties raised `ValueError` when every citing article predated the cited one; such articles are now skipped.
- `dedupe_reflections.py` repoints citations with `UPDATE OR IGNORE`, so merging two copies of an article no longer leaves a citing article citing it twice.

- Atlas silently skipped any sidebar group without a hand-written orientation, so its journals did not appear there. Added the missing group and a test.
- Coverage page claimed reference lists for six journals that deposit none (Peitho, Poroi, Prompt, The WAC Journal, Community Literacy Journal, Rhetoric of Health and Medicine), listed English Journal as indexed, and named two journals as in the index that are not.
- `Computers and Composition` (the journal) was unclassified in the datastories field breakdown and counted as "Other"; it is now Rhetoric & Composition.
- Home page and README stated journal and article counts that were months out of date; the home page now reads them from the index.
- Author names entity-encoded in some CrossRef deposits (`Ar&aacute;nguiz`) are decoded at ingestion.

### Changed (post-G1 emergency fix)

- Removed `scheduler.py` and the `scheduler` Fly process group; replaced with a GitHub Actions cron at 03:00 UTC that hits `POST /fetch` and the new `POST /api/admin/run-backup` endpoint. The previous architecture (separate Fly machine running APScheduler) didn't work because Fly volumes are single-attach: the scheduler machine had no way to share `/data` with the app machine, and was silently writing to its own ephemeral container filesystem. See [`docs/refactor-notes/13-scheduler-architecture-fix.md`](docs/refactor-notes/13-scheduler-architecture-fix.md).
- New endpoint `POST /api/admin/run-backup` (admin-token-protected): runs the backup pipeline synchronously inside the app process, returns the full summary as JSON, and writes `/data/scheduler.heartbeat` on success.
- `fly.toml` simplified: `[processes]` block removed (single-process deployment), `[http_service].processes` removed.
- `Dockerfile` comment updated to describe the single-process model.
- README's "Deployment" section rewritten to describe the cron-driven model.

### Added

- Pytest harness with 320+ characterization tests across `db.py`, `app.py`, the three ingestion paths, and the input-validation helpers. Coverage gates: ≥ 75 % on `app.py`, ≥ 60 % on `db.py`. CI runs on every push; the Fly deploy is gated on test passage.
- Bearer-token authentication on `POST /fetch` (`PINAKES_ADMIN_TOKEN` env var). Read-only endpoints remain anonymous.
- Tiered Flask-Limiter configuration: 60/min default, 20/min on `/api/citations/*` and `/api/stats/*`, 120/min on `/api/articles/search`, 6/hour on `/fetch`. Custom 429 handler returns JSON for API clients and HTML otherwise; both shapes carry a `Retry-After` header.
- Three-level health check: `/health` (liveness, no DB), `/health/ready` (DB reachable; what Fly's check loop hits), `/health/deep` (admin-protected; counts, disk, scheduler heartbeat, integrity check).
- Heartbeat file at `/data/scheduler.heartbeat`, written by the cron-triggered `POST /api/admin/run-backup` endpoint on each successful backup, so `/health/deep` can verify scheduled work is happening. (The original Fly-process-group design that originally produced the heartbeat was reverted; see the post-G1 emergency-fix entry above and [`docs/refactor-notes/13-scheduler-architecture-fix.md`](docs/refactor-notes/13-scheduler-architecture-fix.md).)
- `data/seeds/` directory holding the two large checked-in JSON files used by one-off ingestion scripts, with a README explaining each file's producer / consumer.
- `CONTRIBUTING.md` documenting the scraping-ethics rules and the test requirements for new contributions.
- `docs/architecture.md`, `docs/methodology.md` (placeholders to be expanded with the JWA research note), `docs/refactor-notes/` (audit trail for the structural changes in this release).

### Changed

- `db.py` (4,405 lines, 96 functions) decomposed into a `db/` package — submodules `core`, `articles`, `authors`, `citations`, `books`, `institutions`, `coverage`, `fetch_log`. Public API preserved exactly via re-exports in `db/__init__.py`; every import site outside the package keeps working with no edits. Internals moved verbatim — no SQL, docstring, or type-hint changes. See [`docs/refactor-notes/09-db-split-inventory.md`](docs/refactor-notes/09-db-split-inventory.md).
- `app.py` (1,460 lines, 44 routes) decomposed into an application factory plus 8 Blueprints (`admin`, `main`, `articles`, `authors`, `citations`, `stats`, `books`, `institutions`) and `web_helpers.py` for shared filters, decorators, and error handlers. URLs preserved byte-identical; endpoint names change from bare to prefixed (`index` → `main.index`), which is harmless because no `url_for()` call exists in templates or Python code. See [`docs/refactor-notes/10-app-split-inventory.md`](docs/refactor-notes/10-app-split-inventory.md).
- `static/explore.js` (4,468 lines, 100 functions) decomposed into ES modules: `static/js/explore-loader.js` (entry) + `static/js/utils/` (3 shared helpers) + `static/js/viz/` (18 per-visualization modules). 51 inline-handler function names continue to be available on `window` so existing `onclick=`/`onchange=` attributes in templates work unchanged. The old `explore.js` is kept in place as a one-line-revert fallback. See [`docs/refactor-notes/11-explore-js-split-inventory.md`](docs/refactor-notes/11-explore-js-split-inventory.md).
- Long-form documentation: [`docs/architecture.md`](docs/architecture.md) (system overview with a Mermaid data-flow diagram, terse technical register), [`docs/methodology.md`](docs/methodology.md) (one section per Explore tool with primary references — Bonacich, Freeman, Kessler, Small, Newman & Girvan, Blondel et al., Hummon & Doreian, Ke et al., Burton & Kebler, Glänzel, Wasserman & Faust, et al. — written in scholarly first-person voice), and [`docs/journal-coverage.md`](docs/journal-coverage.md) (every venue indexed by Pinakes with its ingestion path). README "Documentation" section links them. See [`docs/refactor-notes/12-documentation.md`](docs/refactor-notes/12-documentation.md) for the full bibliography and maintenance notes.
- `templates/error.html` now extends `base-core.html` (theme switcher and CSS inherited; sidebar intentionally omitted because a DB outage would otherwise cascade into a second failure).
- README rewritten end-to-end to reflect the actual scope of the project (44+ journals, 50,000+ articles, four data sources).
- Fly health-check path moved from `/health` to `/health/ready` — readiness is the right semantics for "should this machine receive traffic."

### Removed

- Duplicate `.github/workflows/deploy.yml` (`fly-deploy.yml` is the surviving deploy workflow). Both fired on every push to main and were deduplicated only by `concurrency: deploy-group`.
