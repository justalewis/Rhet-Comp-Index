# Changelog

All notable changes to Pinakes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project loosely follows [SemVer](https://semver.org/) — though as a single-deployment site rather than a published library, version bumps mostly track operational milestones (the JWA submission, schema migrations, etc.).

History before 2026-04 is not reconstructed here. The Git log is authoritative for older changes.

## [Unreleased]

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
- Fly process groups: `app` (gunicorn) and `scheduler` (`python scheduler.py`). The scheduler writes `/data/scheduler.heartbeat` so deep-health can verify it is actually running.
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
