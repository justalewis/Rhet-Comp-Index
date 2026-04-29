# Changelog

All notable changes to Pinakes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project loosely follows [SemVer](https://semver.org/) — though as a single-deployment site rather than a published library, version bumps mostly track operational milestones (the JWA submission, schema migrations, etc.).

History before 2026-04 is not reconstructed here. The Git log is authoritative for older changes.

## [Unreleased]

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
- `templates/error.html` now extends `base-core.html` (theme switcher and CSS inherited; sidebar intentionally omitted because a DB outage would otherwise cascade into a second failure).
- README rewritten end-to-end to reflect the actual scope of the project (44+ journals, 50,000+ articles, four data sources).
- Fly health-check path moved from `/health` to `/health/ready` — readiness is the right semantics for "should this machine receive traffic."

### Removed

- Duplicate `.github/workflows/deploy.yml` (`fly-deploy.yml` is the surviving deploy workflow). Both fired on every push to main and were deduplicated only by `concurrency: deploy-group`.
