# Architecture

A technical overview of how Pinakes is built. Companion to [methodology.md](methodology.md), which describes what the visualisations measure rather than how the system is wired.

## Overview

Pinakes is a Flask web application backed by SQLite. Journals are populated by three ingestion paths (CrossRef, RSS / OAI-PMH / WordPress, custom HTML scrapers) plus a handful of one-off manual imports; books come from a separate harvest. OpenAlex and CrossRef reference lists enrich the records periodically. The registry in [`journals.py`](../journals.py) lists 70 venues in Rhetoric and Composition (69 journals and one digital book press), which together account for over fifty thousand articles. The full venue list is in [journal-coverage.md](journal-coverage.md). Production runs on a Windows Server at the WAC Clearinghouse, https://pinakes.wacclearinghouse.org: Cloudflare, then IIS with Application Request Routing, then a single Waitress process. Daily ingestion, cache prewarming and weekly maintenance are Windows scheduled tasks that POST to admin-token-protected endpoints on the running app; the nightly backup is a local scheduled script.

The system is intentionally small. Every component is replaceable in a few hours of work without a migration plan. The constraints driving this — single maintainer, scholarly publication horizon, no funded operations team — are documented per choice in the audit notes under [`refactor-notes/`](refactor-notes/).

## Data flow

```mermaid
flowchart LR
    A1[CrossRef API] -->|fetcher.py| U(upsert_article)
    A2[RSS / OAI / WP] -->|rss_fetcher.py| U
    A3[Custom HTML] -->|scraper.py| U
    M[Manual ingest] -->|ingest_*.py| U
    B1[Book publishers] -->|book_fetcher.py| UB(upsert_book)
    U --> DB[(SQLite + WAL<br/>articles.db)]
    UB --> DB

    DB -->|enrich_openalex.py<br/>openalex_citations.py| OA[abstracts, OA status<br/>affiliations<br/>citation counts]
    OA --> DB

    DB -->|cite_fetcher.py<br/>scrape_lics_refs.py| CR[reference lists]
    CR --> DB

    DB -->|tagger.py| T[auto-tags]
    T --> DB

    DB -->|FTS5 triggers| FTS[(articles_fts)]

    DB --> WEB[Flask + Jinja + D3]
    WEB --> USER[/explore, /coverage, /article, ...]
```

The live ingestion paths write through `db.articles.upsert_article`, which is idempotent on URL: re-running an ingester never produces duplicates. (A few one-off import scripts, `ingest_jac.py` and `ingest_peer_review_1_1.py`, and the hand-run book-publisher scripts insert their rows directly.) Enrichment passes (OpenAlex, CrossRef references) read from and write to the same SQLite file as the live web app, in WAL mode, with one writer at a time (see the lock described under Storage layer).

## Storage layer

The main database is one SQLite file at `DB_PATH`: `C:\Pinakes\data\articles.db` on the Clearinghouse box by default ([`deploy/windows/Pinakes.Common.ps1`](../deploy/windows/Pinakes.Common.ps1)), `articles.db` at the repo root when the variable is unset. WAL mode is on; busy_timeout is 30 s (raised from 10 s after a scrape lost a write race to a concurrent writer); the cache is set to 20 MB and `mmap_size` to 128 MB. Configuration is in [`db/core.py:get_conn`](../db/core.py) — read it before changing anything; the PRAGMAs are load-bearing.

The single-writer invariant is enforced with a process-level lock, `app._fetch_lock`. The incremental fetch, the deep refresh, the maintenance pipeline and the saved-search digest all hold it while they run. `POST /fetch` answers 409 while the lock is held instead of starting a second writer thread; maintenance and digests wait for it (up to six hours) before starting. Scripts run by hand from a shell don't take the lock, so run them when no scheduled job is writing; the busy_timeout only covers brief overlaps.

Usage statistics, when switched on, are written to a separate file, `usage.db`, beside `articles.db` ([`usage/store.py`](../usage/store.py)). SQLite locks per file, so recording page views never contends with the jobs that write `articles.db`. See [usage-statistics.md](usage-statistics.md).

The schema of `articles.db` has 21 tables, counting the FTS5 virtual table. `db.core.init_db` creates them and applies migrations at app startup; migrations currently run through v17:

| Table | Purpose |
|---|---|
| `articles` | One row per article. URL is the unique key; DOI is stored separately when available |
| `articles_fts` | FTS5 virtual table over `title`, `authors`, `abstract` |
| `fetch_log` | Per-source last-fetched timestamp for incremental ingestion |
| `citations` | One row per reference in a citing article's bibliography: `target_doi` and `target_article_id` when known, the raw reference as JSON |
| `authors` | Canonical author records with ORCID and institution |
| `author_article_affiliations` | Author-to-article-to-institution joins |
| `books` | Monographs and edited collections, with chapters as child rows |
| `institutions` | OpenAlex institution records |
| `article_author_institutions` | Per-article author institution affiliations |
| `openalex_fetch_log` | Per-article OpenAlex fetch status, for resumable enrichment |
| `redaction_ledger` | One row per redacted author: token, retained name, variants — the locked table behind the opt-out feature ([author-redaction.md](author-redaction.md)) |
| `redaction_meta` | Key/value store; holds the per-install redaction salt |
| `redaction_requests` | Author opt-out requests pending verification + review |
| `redaction_audit` | Append-only audit trail of every request's lifecycle |
| `wac_works` | WAC Clearinghouse publisher dashboard (`/wac`): one row per work under DOI prefix 10.37514. Read-only to the web app; rebuilt by `ingest_wac.py` |
| `wac_authors` | One row per (work, person) for the WAC dashboard, author or editor, with affiliation |
| `user_tags` | Visitor-proposed topic tags, moderated at `/admin/user-tags`. Kept apart from `articles.tags`, which the classifier owns |
| `tag_feedback` | Visitor up/down votes on classifier tags, one per IP per tag |
| `suppressed_articles` | Blocklist of DOIs/URLs that `upsert_article` refuses, so a removed record can't be re-ingested |
| `alert_subscriptions` | Saved-search email alerts: address, frozen filter set, watermark, hashed verification token |
| `alert_sends` | Append-only record of each digest sent |

v17 adds no table. It puts uniqueness indexes on `citations` (one row per citing article per resolved target, or per DOI), and when existing duplicates block them it logs a warning and leaves the table alone; `dedupe_citations.py` clears those.

The choice to stay on SQLite rather than Postgres is deliberate. The corpus is read-heavy with one writer; SQLite in WAL mode handles this cleanly. A database server would add operational surface (another service to install, patch and back up, network round-trips, retry logic) for no measurable benefit at this scale. If the corpus ever grows past ~ 5 GB or more than one app process becomes necessary, the migration target is Postgres — but Pinakes is nowhere near that.

## Ingestion paths

Three live ingestion paths for journals, a book harvest, and a manual fallback:

**CrossRef** ([`fetcher.py`](../fetcher.py)). 51 of the 70 venues are fetched from CrossRef with cursor pagination. Most are selected by ISSN; two WAC Clearinghouse journals whose deposits carry no ISSN (Revista Latinoamericana de Estudios de la Escritura and Academic Labor: Research and Artistry) are selected by DOI prefix and container title instead (`journals.CROSSREF_SELECTORS`). Titles, author lists with given/family split and publication dates are standard. Abstracts, keywords and reference lists depend on what each publisher deposits: the Journal of Second Language Writing deposits no abstracts, the Journal of Basic Writing no reference lists. The `fetch_log` table records the last fetch per journal so subsequent runs are incremental.

**RSS / OAI-PMH / WordPress** ([`rss_fetcher.py`](../rss_fetcher.py)). Five open-access journals expose machine-readable feeds. The fetcher uses OAI-PMH (Literacy in Composition Studies, Journal of Response to Writing) or the WordPress REST API (Present Tense) when the journal's config names one (full archive, supports incremental harvests), and falls back to the RSS feed when not. RSS is typically capped at the most recent ten or twelve items, so the two RSS-only journals, Enculturation and KB Journal, have their archives backfilled by scraper strategies in `scraper.py`.

**Custom scrapers** ([`scraper.py`](../scraper.py)). Eleven journals have no machine-readable feed. Each has a per-journal scraper function with inline ethics annotations: which `robots.txt` paths are allowed, what crawl-delay is observed, what era of the site is covered. `scraper.fetch_all` runs these plus the Enculturation and KB Journal backfills. The scraper uses BeautifulSoup with `lxml` and respects per-site rate limits (5–10 s between requests is typical). Metadata quality is uneven and intentionally so: titles and authors are usually present, abstracts are not. The full set of constraints is in [CONTRIBUTING.md](../CONTRIBUTING.md#scraping-ethics). Computers and Composition Digital Press, the one book press in the registry, is scraped separately by [`scrape_ccdp.py`](../scrape_ccdp.py), run by hand.

**Books**. Monographs and edited collections go into the `books` table, with chapters as child rows. [`book_fetcher.py`](../book_fetcher.py) harvests them from CrossRef (WAC Clearinghouse, Utah State / UP Colorado, Ohio State, curated DOI lists for Pittsburgh and Routledge, and the hand-verified list in `data/curated_books.tsv`); it runs weekly as maintenance step 12, followed by a title-repair pass. `fetch_parlor.py`, `fetch_pitt.py`, `fetch_routledge.py` and `fetch_siup.py` harvest individual publishers' catalogues, are run by hand, and insert into `books` directly rather than through `upsert_book`.

**Manual ingest**. Two journals have no DOIs, feed or scrapeable archive. JAC was imported from CompPile records (`data/seeds/jac_articles.csv`) by [`ingest_jac.py`](../ingest_jac.py). Pre/Text was published only in print; its record was hand-compiled and ingested once by a one-off script that is not in this repository (`journals.py` calls it `ingest_pretext.py`). Smaller one-off scripts fill gaps in otherwise automated journals: [`ingest_peer_review_1_1.py`](../ingest_peer_review_1_1.py) (The Peer Review issue 1.1, with its references) and [`ingest_rpc_4_1.py`](../ingest_rpc_4_1.py) (Rhetoric, Politics & Culture vol. 4.1, never deposited with CrossRef). There is no automated re-ingest path.

A two-tier metadata distinction matters for downstream tools: scraped articles often lack abstracts and lack DOIs, which excludes them from analyses that depend on rich metadata (topic modelling) or citation linkage (the CrossRef-references pipeline only resolves DOIs). The coverage section of [methodology.md](methodology.md) says which tools need outbound reference lists and which need DOIs.

## Enrichment

Four enrichment passes run on top of the ingested base records, all as steps of the weekly maintenance pipeline ([`weekly_maintenance.py`](../weekly_maintenance.py)) rather than the daily fetch:

**OpenAlex** ([`enrich_openalex.py`](../enrich_openalex.py), step 5). For each article with a DOI, OpenAlex returns abstracts (decoded from the inverted-index format), open-access status (`gold`/`green`/`hybrid`/`bronze`/`closed`), the canonical OpenAlex work ID, and author affiliations with institution ROR IDs. Processes only articles whose `openalex_enriched_at` is null, polite-pool rate-limited at 10 req/s.

**CrossRef references** ([`cite_fetcher.py`](../cite_fetcher.py), step 4). For each article with a DOI whose references haven't been fetched, CrossRef returns its reference list. Every reference is stored in `citations`, in bibliography order. A reference with a DOI gets `target_doi`, and `target_article_id` when that DOI belongs to an in-corpus article. A reference without a DOI is stored with `target_doi` NULL and the raw CrossRef reference as JSON, so the article page can show the full bibliography; with no `target_article_id` it adds nothing to the in-corpus citation graph. A re-run replaces an article's rows rather than adding to them, and the run ends by recomputing the denormalized `internal_cited_by_count` / `internal_cites_count` columns.

**LiCS references** ([`scrape_lics_refs.py`](../scrape_lics_refs.py), step 6). Literacy in Composition Studies has DOIs but no reference lists on CrossRef, so this scrapes the Works Cited from each article's HTML galley and stores one row per reference (DOI-less ones keyed `ref:<hash>`), replacing its previous rows on every run.

**OpenAlex citation counts** ([`openalex_citations.py`](../openalex_citations.py), step 9). Fetches each article's global citation count into `articles.openalex_cited_by_count`, distinct from CrossRef's count, logging each fetch in `openalex_fetch_log`.

## Tagging

[`tagger.py`](../tagger.py) applies a hand-curated controlled vocabulary of 61 terms, in 13 families, to article titles and abstracts via case-insensitive regex matching. Single-word triggers are wrapped with `\b` boundaries (so "grammar" does not fire on "programmatic"); multi-word and hyphenated phrases are literal substring matches. The vocabulary is partial and intentionally so: it is not a substitute for a learned topic model. Its purpose is faceted browsing on the site. `retag.py` reapplies it to the whole corpus and rebuilds the FTS index (weekly maintenance step 7).

The current vocabulary (v3, April 2026) was refined against [CompPile](http://comppile.org)'s disciplinary glossary, the Bedford Bibliography subject taxonomy and WPA Outcomes Statement terminology, so terms map to distinctions a Rhet/Comp scholar would recognise. The sources and design principles are in the module docstring. Visitor-suggested tags are stored separately in `user_tags` and never written to the classifier's `articles.tags`.

## Web app

Flask + Jinja2 templates + D3 and Chart.js (loaded from the jsDelivr CDN) for the visualisations. The application factory [`app.py:create_app`](../app.py) registers sixteen Blueprints (`main`, `articles`, `authors`, `citations`, `stats`, `books`, `institutions`, `feeds`, `alerts`, `admin`, `redaction`, `tags`, `wac`, `jwa`, `usage`, `datastories`) and shared middleware. Before each request, in order:

1. `block_denied_ips` — 403 for networks in `PINAKES_BLOCKED_CIDRS` (default: the Alibaba Cloud range behind the 2026-06-20 incident), before any routing or DB work.
2. `require_cloudflare_origin` — off unless `PINAKES_CLOUDFLARE_ONLY` is set. It keys on Fly's `Fly-Client-IP` header, so on the Clearinghouse box, where no proxy sets that header, it has no effect.
3. `redirect_www` — 301 from `www.` to the bare host.
4. `shed_expensive_load` — caps concurrent requests to `/explore`, `/export`, `/citations`, `/article/` and `/author/` at `PINAKES_MAX_EXPENSIVE_INFLIGHT` (default 6) and answers 503 with Retry-After beyond that (incident 2026-07-05).

After each request, security headers (X-Frame-Options DENY, HSTS, CSP). Also registered: gzip compression, the [Flask-Limiter](https://flask-limiter.readthedocs.io/) tiered rate limiter (60/min default, 20/min on graph and stats computations, 120/min on search), `usage.init_app` (records requests to `usage.db` only when `PINAKES_USAGE_TRACKING` is set), and the [admin-token decorator](../auth.py) on mutating endpoints.

`ProxyFix` (x_proto, x_host) makes request-built absolute URLs come out `https://` behind Fly's proxy. On the Clearinghouse box Waitress strips `X-Forwarded-*` before the app sees them, so ProxyFix has nothing to read there: emailed links and the ORCID `redirect_uri` are built from `PINAKES_SITE_URL` by `web_helpers.public_url_for`.

Per-visualization JavaScript lives in [`static/js/viz/`](../static/js/viz/) — 44 ES modules. The 18 Explore modules are loaded eagerly at page load by [`static/js/explore-loader.js`](../static/js/explore-loader.js); the 26 `ds_*` Datastories modules are loaded the same way by [`static/js/datastories-loader.js`](../static/js/datastories-loader.js). Eager rather than lazy: inline `onclick=` handlers in the templates would otherwise race against module loads. Browser baseline: Chrome 91+, Firefox 89+, Safari 15+.

Static assets are cache-busted with `?v={{ version }}`, where `version` is `health.APP_VERSION`, computed once when the process starts: Fly's per-release `FLY_RELEASE_VERSION`/`FLY_MACHINE_VERSION` when set, otherwise the git short SHA from `git rev-parse --short HEAD`, otherwise `dev`. On the Clearinghouse box that is the deployed commit's short SHA, which is also what `Update-Pinakes.ps1` checks on `/health` to confirm the service restarted onto the new code.

## Author redaction

An author opt-out ("right to be forgotten") feature: a verified author can have their name removed from the index while the scholarship — and every metric it feeds — stays intact. Because author identity in Pinakes *is* the name string (no integer id), the name is replaced everywhere it's stored by a stable per-author token that renders as "Name Redacted by Author Request"; the token becomes the new identity key, so metrics are preserved by construction. Suppression is re-applied on ingest at two choke-points, `upsert_article` and `upsert_book`, and `redaction.resweep_all` re-applies the whole ledger after a deep refresh and as step 10 of a full `python weekly_maintenance.py` run. The daily fetch and the scheduled maintenance endpoint don't run a resweep, and scripts that insert rows directly (`fetch_parlor.py`, `fetch_pitt.py`, `fetch_routledge.py`, `fetch_siup.py`, `ingest_jac.py`, `ingest_peer_review_1_1.py`) bypass the choke-points, so after running one of those, run `python redaction.py resweep`. Requests flow through a public form → email or ORCID verification → an admin-gated review page (`/admin/redactions`) → an audited approve/deny. The full design — token model, data model, the resurrection problem, render/durability/reversibility, and the request flow — is in [author-redaction.md](author-redaction.md); operations are in [runbooks/author-redaction.md](runbooks/author-redaction.md).

## Deployment

Production is a Windows Server at the WAC Clearinghouse, serving https://pinakes.wacclearinghouse.org; the test host is https://testpinakes.wacclearinghouse.org. Requests pass through Cloudflare, then IIS with Application Request Routing, to Waitress on port 8080, which NSSM runs as the Windows service `Pinakes-Web`. That is one process with a pool of worker threads, so the in-memory rate limiter, the load-shed semaphore and `app._fetch_lock` each cover every request. Default paths, each overridable with a machine environment variable ([`deploy/windows/Pinakes.Common.ps1`](../deploy/windows/Pinakes.Common.ps1)): checkout `C:\Pinakes\app`, virtualenv `C:\Pinakes\venv`, database `C:\Pinakes\data\articles.db`, logs `C:\Pinakes\logs`, backups `C:\Pinakes\backups`. Installation is in [WINDOWS-SERVER-INSTALL.md](WINDOWS-SERVER-INSTALL.md), operations in [WINDOWS-SERVER-RUNBOOK.md](WINDOWS-SERVER-RUNBOOK.md); the scripts are in [`deploy/windows/`](../deploy/windows/).

Scheduled work runs under Windows Task Scheduler, registered by [`Install-PinakesTasks.ps1`](../deploy/windows/Install-PinakesTasks.ps1). The tasks run as SYSTEM, which reads `PINAKES_ADMIN_TOKEN` from the machine environment, and reach the admin endpoints through [`Invoke-PinakesAdmin.ps1`](../deploy/windows/Invoke-PinakesAdmin.ps1) at `http://127.0.0.1:<PORT>`, bypassing IIS and its cache. Times are the server's local time:

| Task | When | Runs | Behaviour |
|---|---|---|---|
| `Pinakes-NightlyBackup` | 02:00 daily | `Backup-Pinakes.ps1` | Hot backup through SQLite's online backup API to `C:\Pinakes\backups\articles-<timestamp>-nightly.db` (plus `usage.db` when it exists), checked with `PRAGMA quick_check`; keeps the newest 10 (`PINAKES_BACKUP_KEEP`) |
| `Pinakes-DailyFetch` | 03:23 daily | `POST /fetch` | Returns 200 immediately; the incremental fetch (gold-OA backfill, CrossRef, RSS/OAI/WP, scrapers) runs in a background thread. 409 if a job already holds the lock, which the task logs as a skip |
| `Pinakes-Prewarm` | 04:15 daily | `POST /api/admin/prewarm` | Recomputes the default views of the disk-cached heavy analyses |
| `Pinakes-WeeklyMaintenance` | Sundays 04:45 | `POST /api/admin/run-maintenance` | Waits for the lock, then runs `weekly_maintenance.py` steps 4–9 and 12 (citations, OpenAlex, LiCS references, retag + FTS, OA backfill, OpenAlex citation counts, book harvest). Steps 10 and 11, the redaction and article-suppression resweeps, are not part of this run |
| `Pinakes-UsageResolve`, `Pinakes-UsageData` | 05:10 daily; Sundays 05:40 | usage endpoints | Usage statistics; see [usage-statistics.md](usage-statistics.md) |
| `Pinakes-WeeklyDigests` | Fridays 14:17 | `POST /api/admin/send-digests` | Registered only with `-IncludeDigests`. Saved-search emails |
| `Pinakes-UpdateCheck` | 08:00 daily | `Update-Pinakes.ps1 -Check` | Registered only with `-IncludeUpdateCheck`. Reports drift from GitHub; never deploys |

The admin endpoints hand their work to a background thread and return 200 at once, so a green task means the job was accepted, not that it finished. Each run appends a line to `C:\Pinakes\logs\admin-tasks.log`; the job's own progress goes to the application log (see Observability). A deep refresh is never scheduled: run it deliberately with `Invoke-PinakesAdmin.ps1 -Endpoint DeepFetch`.

`POST /api/admin/run-backup` is the offsite backup pipeline (snapshot → zstd → age encryption → S3/B2 upload → retention prune; [`backup.py`](../backup.py)). It needs the six `PINAKES_BACKUP_*` secrets, runs in a background thread and returns 200 `{"status": "backup started"}`. No scheduled task calls it; run it with `Invoke-PinakesAdmin.ps1 -Endpoint Backup` once the secrets are configured. Restoring from either the local or the offsite copies is covered in [runbooks/disaster-recovery.md](runbooks/disaster-recovery.md).

Updates are manual. The operator runs [`Update-Pinakes.ps1`](../deploy/windows/Update-Pinakes.ps1) on the box: it fetches `origin/main`, backs up the database, stops the service, moves the checkout to the new commit, reinstalls dependencies, smoke-tests that the code imports against a scratch database, starts the service and polls `/health` until it reports the new commit's short SHA. If any step from the checkout move onward fails, it restores the previous commit and restarts. It does not wait for CI.

The former Fly.io deployment (`fly.toml`, `Dockerfile`, gunicorn) is no longer production. [`.github/workflows/fly-deploy.yml`](../.github/workflows/fly-deploy.yml) still deploys it after every green Tests run on `main`, and pinakes.xyz now redirects to the Clearinghouse host (a Cloudflare Worker, [`deploy/xyz-redirect`](../deploy/xyz-redirect/)). On Fly the app ran as one gunicorn worker with 8 gthread threads on a single machine (2048 MB, 1 CPU, region IAD), with the database on a 3 GB volume at `/data`, and its jobs were triggered by GitHub Actions workflows ([`cron.yml`](../.github/workflows/cron.yml), `maintenance.yml`, `alerts.yml`). Their schedules have been commented out since 2026-09-19, because they target pinakes.xyz; they can still be run by hand (workflow_dispatch) but no longer reach production. That externally triggered design superseded the original two-process-group design (Path A from [refactor-notes/04](refactor-notes/04-health-and-scheduler.md)), which didn't work because Fly volumes are single-attach and a separate scheduler machine had no way to share `/data` with the app. The full audit trail of the switch is in [refactor-notes/13-scheduler-architecture-fix.md](refactor-notes/13-scheduler-architecture-fix.md).

## Observability

Three layers of operational visibility:

**Logs.** On the Clearinghouse box everything is under `C:\Pinakes\logs`. Python logging writes to stderr, which NSSM sends to `web-stderr.log`; `web-stdout.log` only receives `print()` output. `admin-tasks.log` has one line per scheduled job and `update-<timestamp>.log` a transcript of each `Update-Pinakes.ps1` run. `app.py` doesn't configure logging. WARNING and above always reach stderr; INFO lines (fetch progress, `Backup OK`) appear only if the root logger is at INFO, which the ingestion modules' import-time `logging.basicConfig(level=INFO)` sets only when nothing configured logging before them.

Log lines that name a client use the IP from `rate_limit.resolve_client_ip`, which takes the first of: on Fly only (`FLY_APP_NAME` set), `CF-Connecting-IP` when `Fly-Client-IP` is a Cloudflare edge address, then `Fly-Client-IP` (only Fly's proxy overwrites that header; on the Clearinghouse chain a visitor could send their own, so it is ignored there); the header named by `PINAKES_TRUSTED_IP_HEADER`, when the socket peer is loopback or private (on the box that is `CF-Connecting-IP`, since IIS hands every request over from the loopback address), falling back to the socket address; the first `X-Forwarded-For` entry; the socket address. `GET /api/admin/client-ip` (admin token) reports which rule produced the answer. Typical events: `Auth required`, `Auth failed`, `load-shed 503`, `Background fetch skipped`. No request bodies are logged.

**Sentry** (optional, gated on the `SENTRY_DSN` environment variable). Errors from the web process are tagged `component=web`; ingestion errors carry `source=crossref|rss|scrape|openalex|citations` and `journal=<name>`. PII scrubbing strips `Authorization` headers, `Cookie` headers, query parameters whose name contains `token`, and request bodies. Configuration in [`monitoring.py`](../monitoring.py).

**Health endpoints**:

| Endpoint | Auth | Purpose |
|---|---|---|
| `/health` | none | Liveness — process is up; reports `version`. <1 ms, no DB query. Polled by `Update-Pinakes.ps1` after a deploy; checked by `Test-PinakesHealth.ps1` |
| `/health/ready` | none | Readiness — DB reachable within 250 ms. Probed by `Test-PinakesHealth.ps1`; also the health check in `fly.toml` for the former Fly app |
| `/health/deep` | admin token | Full diagnostic — counts, last-fetch, disk free, scheduler heartbeat age, integrity check (cached 6 h), security-header config |

The heartbeat in `/health/deep` is written only when `POST /api/admin/run-backup` succeeds. None of the box's scheduled tasks call that endpoint, so `scheduler_healthy` reads false there unless the offsite backup is configured and run.

## Testing

Pytest harness with three rings, all pure-offline (HTTP stubbed via `responses` and `feedparser` mocks; isolated SQLite per test):

1. **Unit tests** for every public function in `db/`, with one happy path and one edge case each.
2. **Route smoke tests** for every URL in `app.url_map`, including the JSON shape contract for `/api/*`, the BibTeX/RIS grammar for `/export`, and the security-header presence on every response.
3. **Ingestion parser tests** with captured fixture payloads for CrossRef, RSS, and the scraper, exercising both happy paths and malformed inputs.

CI ([`.github/workflows/test.yml`](../.github/workflows/test.yml)) runs the full suite (over 400 tests) on every push and pull request to `main`. Only the Fly deploy workflow is gated on it; the Clearinghouse box is updated by hand with `Update-Pinakes.ps1`, which has its own import smoke test, `/health` version check and rollback. Test infrastructure is documented in [`refactor-notes/01-test-harness.md`](refactor-notes/01-test-harness.md).

## What we explicitly do not do

These choices are deliberate. Future maintainers should not "fix" them without first reading the linked audit note.

- **No live scraping during web requests.** Every viz reads pre-computed data from SQLite. Scraping is offline, scheduled, polite. ([CONTRIBUTING.md scraping ethics](../CONTRIBUTING.md#scraping-ethics).)
- **No user accounts.** A single shared bearer token, `PINAKES_ADMIN_TOKEN`, gates `/fetch`, `/health/deep` and every `/api/admin/*` endpoint; the `/admin/*` pages are shells that load nothing until the operator pastes that token. The Datastories tools use one shared password (or the admin token), the `/jwa` preview uses HTTP Basic Auth, and author opt-out requests are verified by email or ORCID rather than by login. ([refactor-notes/02-admin-auth.md](refactor-notes/02-admin-auth.md).)
- **No write-through caching layer.** Nothing is cached at write time; expensive results are memoized on first use and invalidated by the data. The heavy Datastories and Explore analyses (14 functions in `db/datastories.py` and `db/citations.py`) are cached to disk by `@cached` in [`datastories_cache.py`](../datastories_cache.py), keyed on their arguments and a fingerprint of the `articles` and `citations` row populations, so a fetch that changes the data invalidates them and a no-op fetch doesn't. The nightly prewarm recomputes their default views after the fetch, since a cold compute can take minutes. `/coverage` holds its detailed table in-process for an hour (`db/coverage.py`) and the sidebar is rebuilt at most every five minutes. Routes decorated with `cache_response` send `Cache-Control: private`, a browser-only directive, because a shared cache such as IIS/ARR can't be told when a fetch lands; feeds are the exception and allow shared caching. Everything else runs on demand against SQLite. ([refactor-notes/03-rate-limiting.md](refactor-notes/03-rate-limiting.md) for the protective rate caps.)
- **No microservices.** One web process, one main SQLite file (plus the separate `usage.db`), one server. Scheduled work is Windows Task Scheduler calling authenticated admin endpoints on that same process over loopback, so the jobs share the app's fetch lock. The complexity of a service mesh is not justified by the workload. ([refactor-notes/13-scheduler-architecture-fix.md](refactor-notes/13-scheduler-architecture-fix.md) for why the earlier in-cluster scheduler design didn't fit on Fly.)
- **No bundler for the JavaScript.** Native ES modules, D3 and Chart.js from CDN, no `package.json` for the site (the pinakes.xyz redirect Worker in `deploy/xyz-redirect` is a separate project). ([refactor-notes/11-explore-js-split-inventory.md](refactor-notes/11-explore-js-split-inventory.md).)
- **No ORM.** Direct `sqlite3` with parameterised queries. ([refactor-notes/09-db-split-inventory.md](refactor-notes/09-db-split-inventory.md).)

If any of these stop fitting Pinakes — different scale, different team, different deployment — the right move is to revisit the linked audit note and write a new one explaining the change, not to retrofit a heavier architecture into the existing code.
