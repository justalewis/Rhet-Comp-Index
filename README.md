# Pinakes

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)
[![Tests](https://github.com/justalewis/Rhet-Comp-Index/actions/workflows/test.yml/badge.svg)](https://github.com/justalewis/Rhet-Comp-Index/actions/workflows/test.yml)

A discipline-specific bibliometric index for Rhetoric & Composition. Live at [pinakes.wacclearinghouse.org](https://pinakes.wacclearinghouse.org).

The index covers nearly 70 journals and more than 65,000 articles, drawn from CrossRef, OpenAlex, RSS feeds, and a handful of custom scrapers (each scraper is rate-limited and respects each site's `robots.txt`; [`scraper.py`](scraper.py) carries inline ethics annotations per source). Records are stored in SQLite, served by Flask, and visualised with D3.js and Chart.js.

## Architecture

One Flask process serves HTML and a JSON API. [`app.py`](app.py) is an application factory (`create_app()`); the routes live in [`blueprints/`](blueprints/). Production runs on a Windows Server at the WAC Clearinghouse: Cloudflare in front, IIS with Application Request Routing as the reverse proxy, and Waitress serving the app on `127.0.0.1:8080` as the Windows service `Pinakes-Web`. The data is one SQLite file, `C:\Pinakes\data\articles.db` by default.

There is no separate scheduler process. Windows scheduled tasks POST to admin endpoints, and each endpoint runs its job on a background thread inside the web process. Citation networks, co-citation graphs, and other analytics are computed on demand by the [`db/`](db/) package using NetworkX.

A single shared bearer token (`PINAKES_ADMIN_TOKEN`) gates `/fetch`, `/health/deep`, and every `/api/admin/*` endpoint. The `/admin/*` review pages are empty shells that load their data from those endpoints once the token is pasted in. The Datastories tools sit behind a shared password (`PINAKES_DATASTORIES_PASSWORD`), and `/jwa` behind HTTP Basic Auth. Rate limiting uses Flask-Limiter with in-memory storage.

```
Visitor ── HTTPS ──▶ Cloudflare ──▶ IIS + ARR ──▶ Waitress (127.0.0.1:8080) ──▶ Flask (app.py → blueprints/)
                                                                                  │
                                                                  SQLite (WAL) ◀──┤
                                                    C:\Pinakes\data\articles.db   │
                                                                                  │
Windows Task Scheduler ──▶ Invoke-PinakesAdmin.ps1 ──▶ POST /fetch ──▶ fetcher / rss / scraper
                                                   ──▶ POST /api/admin/prewarm, /api/admin/run-maintenance, …
                       ──▶ Backup-Pinakes.ps1 ──▶ C:\Pinakes\backups (local copy)
```

## Local development

```bash
git clone https://github.com/justalewis/Rhet-Comp-Index.git
cd Rhet-Comp-Index

python -m venv .venv && source .venv/bin/activate    # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
pip install -r requirements-dev.txt                  # adds pytest, responses, freezegun, coverage

python app.py            # http://localhost:5000
```

The first run creates an empty `articles.db`. To populate it:

```bash
python fetcher.py        # CrossRef-indexed journals (the bulk of the corpus)
python rss_fetcher.py    # RSS / OAI / WordPress feed journals
python scraper.py        # custom HTML scrapers
```

These can take a long time on a cold corpus. In production the `Pinakes-DailyFetch` scheduled task runs all three each night through `POST /fetch` (see [Scheduled jobs](#scheduled-jobs)).

## Running tests

```bash
pytest                                       # full suite (fast)
pytest -m "not slow"                         # explicit fast suite
pytest --cov=. --cov-report=term-missing     # with coverage
```

The harness uses an isolated SQLite file per test, stubs all HTTP via `responses` and feedparser mocks, and never touches the developer's real `articles.db`. The app itself imports against one session-wide temp database (`pinakes-pytest-session.db` in the system temp folder), so two test runs on the same machine at once will collide. CI ([`test.yml`](.github/workflows/test.yml)) runs the suite on pushes and pull requests to `main`. Production does not wait on it: the server is updated by hand (see [Deployment](#deployment)).

## Deployment

Production is a Windows Server at the WAC Clearinghouse, serving [pinakes.wacclearinghouse.org](https://pinakes.wacclearinghouse.org); a test host runs at [testpinakes.wacclearinghouse.org](https://testpinakes.wacclearinghouse.org). Two documents cover it:

- [`docs/WINDOWS-SERVER-INSTALL.md`](docs/WINDOWS-SERVER-INSTALL.md) — first install: Python, the virtual environment, the NSSM service, IIS, environment variables, scheduled tasks.
- [`docs/WINDOWS-SERVER-RUNBOOK.md`](docs/WINDOWS-SERVER-RUNBOOK.md) — operations: bringing the server back to healthy, deploying an update, visitor IPs, troubleshooting.

The scripts both documents use are in [`deploy/windows/`](deploy/windows/). The server is updated by hand. The operator runs `Update-Pinakes.ps1` on it, which pulls `main`, backs up the database, reinstalls dependencies, smoke-tests the new code, restarts `Pinakes-Web`, and waits for `/health` to report the new commit; if a step from the checkout onward fails, it puts the previous commit back. A green CI run deploys nothing to the server.

The former Fly.io deployment ([`fly.toml`](fly.toml), [`Dockerfile`](Dockerfile), gunicorn) is no longer production. [`.github/workflows/fly-deploy.yml`](.github/workflows/fly-deploy.yml) still deploys it after every green Tests run on `main`, and `pinakes.xyz` now redirects to the Clearinghouse host through the Cloudflare Worker in [`deploy/xyz-redirect/`](deploy/xyz-redirect/). The scheduled workflows that drove the Fly app (`cron.yml`, `maintenance.yml`, `alerts.yml`) have been manual-only since 2026-09-19: they target `pinakes.xyz` and do nothing for the server. (On Fly, an earlier design ran the scheduler as a second process group with its own machine. That didn't work because Fly volumes are single-attach: the scheduler had no way to share `/data` with the app machine. See [`docs/refactor-notes/13-scheduler-architecture-fix.md`](docs/refactor-notes/13-scheduler-architecture-fix.md).)

### Scheduled jobs

[`deploy/windows/Install-PinakesTasks.ps1`](deploy/windows/Install-PinakesTasks.ps1) registers these Windows scheduled tasks. They run as SYSTEM, at the server's local time.

| Task | When | What it runs |
|---|---|---|
| `Pinakes-NightlyBackup` | 02:00 daily | `Backup-Pinakes.ps1` — local SQLite backup (see [Backups](#backups)) |
| `Pinakes-DailyFetch` | 03:23 daily | `POST /fetch` — incremental CrossRef fetch, then the RSS/OAI and scraper passes |
| `Pinakes-Prewarm` | 04:15 daily | `POST /api/admin/prewarm` — rebuild cached aggregates |
| `Pinakes-WeeklyMaintenance` | 04:45 Sundays | `POST /api/admin/run-maintenance` — steps 4–9 and 12 of `weekly_maintenance.py` (citations, OpenAlex, LiCS references, retag + FTS, OA backfill, OpenAlex citation counts, book harvest) |
| `Pinakes-UsageResolve` | 05:10 daily | `POST /api/admin/usage/resolve` — usage statistics; also deletes raw IPs |
| `Pinakes-UsageData` | 05:40 Sundays | `POST /api/admin/usage/refresh-data` — refresh the IP databases when stale |
| `Pinakes-WeeklyDigests` | 14:17 Fridays | Only with `-IncludeDigests`: `POST /api/admin/send-digests` |
| `Pinakes-UpdateCheck` | 08:00 daily | Only with `-IncludeUpdateCheck`: `Update-Pinakes.ps1 -Check`. Never deploys; its output goes only to the task's console, so run `-Check` by hand to see whether the server is behind |

The endpoint tasks go through `Invoke-PinakesAdmin.ps1`, which reads `PINAKES_ADMIN_TOKEN` from the machine environment and calls the app on the loopback address. Each endpoint starts its job on a background thread and answers at once, so a task that finishes green means the job was accepted, not that it succeeded. Every run gets a line in `C:\Pinakes\logs\admin-tasks.log`; the job's own progress and errors go to the app's log, which is the service's stderr file (`C:\Pinakes\logs\web-stderr.log` in the install guide's NSSM setup). The fetch, deep-refresh, maintenance, and digest jobs share one lock, so only one of them writes to SQLite at a time: `/fetch` answers 409 while another holds it (the task logs that as a skipped run, not a failure), and maintenance waits its turn.

### Configuration

Settings are environment variables. Set them at Machine scope on the server, where the scheduled tasks read them too, or on the `Pinakes-Web` service through NSSM, and restart the service after a change. [Section 6 of the install guide](docs/WINDOWS-SERVER-INSTALL.md#6-configure-environment-variables) shows how. The ones that matter most:

| Variable | What it does |
|---|---|
| `DB_PATH` | Path to `articles.db`. |
| `PINAKES_ADMIN_TOKEN` | Bearer token for `/fetch`, `/health/deep`, and `/api/admin/*`. Unset, those endpoints answer 503. |
| `PINAKES_SECRET_KEY` | Signs the Datastories session cookie and the ORCID `state`. Set it. The app refuses to start without it only when `FLY_APP_NAME` is set; on the server an unset key quietly becomes a random per-process key, so every service restart logs readers out of Datastories and breaks any ORCID verification in progress. |
| `PINAKES_SITE_URL` | The public address. Emailed links and the ORCID `redirect_uri` are built from it, because Waitress strips the forwarding headers. |
| `PINAKES_TRUSTED_IP_HEADER` | `CF-Connecting-IP` on the server. Without it every visitor looks like `127.0.0.1` and they all share one rate-limit budget. |
| `CROSSREF_MAILTO` | Contact address for the CrossRef User-Agent, which puts fetches in CrossRef's polite pool. Kept out of the repo on purpose. |
| `PINAKES_DATASTORIES_PASSWORD` | The shared Datastories password. Unset, the tools answer 503; the landing page still renders. |
| `SENTRY_DSN` | Optional error reporting (below). |

The Sentry DSN is optional. Without it, [`monitoring.py`](monitoring.py) is a no-op and errors only surface in the app's log on the server (the service's stderr file). With it, the web process reports errors with `component=web`; ingestion errors are additionally tagged with `source=crossref|rss|scrape|openalex|citations` and (when known) `journal=<name>`.

### Backups

The nightly backup is local. `Pinakes-NightlyBackup` runs [`Backup-Pinakes.ps1`](deploy/windows/Backup-Pinakes.ps1), which copies `articles.db` through SQLite's online backup API into `C:\Pinakes\backups`, checks the copy with `PRAGMA quick_check`, and keeps the newest `PINAKES_BACKUP_KEEP` (default 10). It does the same for `usage.db` once usage statistics have been switched on. `Update-Pinakes.ps1` takes another copy of `articles.db` before every deploy. These copies sit on the same disk as the database, so they cover a bad migration or a bad deploy, not the loss of the server. To put one back, follow [Restore A](docs/runbooks/disaster-recovery.md#restore-a--from-a-local-backup) in the disaster recovery runbook, which also re-applies author redactions made after the backup was taken.

#### Offsite backups (optional)

`POST /api/admin/run-backup` snapshots the DB, compresses with zstd, encrypts with age, uploads to an S3-compatible bucket, and prunes per the 30-daily / 26-weekly / 12-monthly retention policy. No scheduled task runs it. On the server it runs only when someone calls `Invoke-PinakesAdmin.ps1 -Endpoint Backup`, and it needs the six `PINAKES_BACKUP_*` variables below.

A successful offsite run is also the only thing that writes `scheduler.heartbeat` (beside the database), the file `/health/deep` reads for `scheduler_healthy`. So on the server `/health/deep` reports `scheduler_healthy: false` unless an offsite backup has succeeded in the last 25 hours. That flag says nothing about whether the daily fetch ran.

```bash
# 1. Generate an age key pair on your own machine; KEEP the private key off the server.
age-keygen -o ~/.pinakes/age.key
PUB=$(grep '^# public key:' ~/.pinakes/age.key | cut -d' ' -f4)

# 2. Create a Backblaze B2 bucket "pinakes-backup" and an application key
#    scoped to that bucket. Note its keyID and applicationKey.
```

```powershell
# 3. On the server, set the six variables at Machine scope, then restart the service.
[Environment]::SetEnvironmentVariable('PINAKES_BACKUP_BUCKET',         'pinakes-backup', 'Machine')
[Environment]::SetEnvironmentVariable('PINAKES_BACKUP_ENDPOINT',       'https://s3.us-west-002.backblazeb2.com', 'Machine')
[Environment]::SetEnvironmentVariable('PINAKES_BACKUP_REGION',         'us-west-002', 'Machine')
[Environment]::SetEnvironmentVariable('PINAKES_BACKUP_ACCESS_KEY_ID',  '<keyID>', 'Machine')
[Environment]::SetEnvironmentVariable('PINAKES_BACKUP_SECRET_KEY',     '<applicationKey>', 'Machine')
[Environment]::SetEnvironmentVariable('PINAKES_BACKUP_AGE_PUBLIC_KEY', '<the age1... public key from step 1>', 'Machine')
Restart-Service Pinakes-Web
```

**The age private key is the most important secret in this project.** Store it in a password manager AND a paper backup. If you lose it, every backup becomes unrecoverable.

To run one and then inspect the resulting B2 object:

```powershell
& C:\Pinakes\app\deploy\windows\Invoke-PinakesAdmin.ps1 -Endpoint Backup
```

To restore an offsite backup, `restore.py` downloads from the bucket (it needs the same six variables) and `restore_local.py` works on a `.db.zst.age` file you have already downloaded. Both decrypt, decompress, run an integrity check, and re-apply the author-redaction ledger, and both write a new file rather than touching the live database.

The key file you pass to `--age-key` must hold only the `AGE-SECRET-KEY-1…` line. Both tools read the whole file, and the two `#` comment lines `age-keygen` writes at the top make it fail to parse (`restore_local.py` also refuses any file that does not start with `AGE-SECRET-KEY-1`). Make a restore copy first:

```powershell
Select-String '^AGE-SECRET-KEY-1' age.key | ForEach-Object Line | Set-Content age-restore.key
```

```bash
grep '^AGE-SECRET-KEY-1' ~/.pinakes/age.key > ~/.pinakes/age-restore.key   # POSIX equivalent

python restore.py --list
python restore.py --latest --out ./restored.db --age-key ~/.pinakes/age-restore.key
python restore_local.py --file <backup>.db.zst.age --age-key <age-restore.key> --out <restored.db>
```

[Restore B](docs/runbooks/disaster-recovery.md#restore-b--from-the-encrypted-offsite-backup) in the disaster recovery runbook has the full procedure on the server, including how to put the restored file in place. Two things to know: `restore.py` ends by printing the old Fly commands for that step, which do not apply; and if the redaction re-apply fails, `restore.py` only prints a warning and exits 0, where `restore_local.py` stops with exit code 1.

### Health endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | none | Liveness — process is up. No DB. Reports the running commit, which `Update-Pinakes.ps1` polls for after a deploy. |
| `GET /health/ready` | none | Readiness — DB is reachable. Checked by `Update-Pinakes.ps1` and `Test-PinakesHealth.ps1` on the server (and by Fly's check loop on the old deployment). |
| `GET /health/deep` | admin token | Full diagnostic — counts, last-fetch, disk, scheduler heartbeat (see [Offsite backups](#offsite-backups-optional)), integrity check. |

```bash
curl -H "Authorization: Bearer $PINAKES_ADMIN_TOKEN" https://pinakes.wacclearinghouse.org/health/deep | jq
```

### Triggering a fetch manually

`Pinakes-DailyFetch` runs every night. To force a fetch early, on the server:

```powershell
& C:\Pinakes\app\deploy\windows\Invoke-PinakesAdmin.ps1 -Endpoint Fetch
```

or from anywhere, with the token:

```bash
curl -X POST -H "Authorization: Bearer $PINAKES_ADMIN_TOKEN" https://pinakes.wacclearinghouse.org/fetch
```

## Project structure

Representative, not exhaustive: one-off scripts are grouped.

```
Rhet-Comp-Index/
├── app.py                       Application factory (create_app); registers the blueprints
├── blueprints/                  Routes, one module per area (16: main, articles, authors, citations, admin, …)
├── web_helpers.py               Shared helpers, Jinja filters, security headers, error handlers
├── auth.py                      Admin-token decorator
├── auth_datastories.py          Datastories password gate
├── rate_limit.py                Flask-Limiter configuration and client-IP resolution
├── health.py                    /health, /health/ready, /health/deep
├── monitoring.py                Sentry setup (no-op without SENTRY_DSN)
├── db/                          SQLite layer, by concern; core.py holds PRAGMAs, schema and migrations
├── journals.py                  Journal definitions (CrossRef, RSS, scrape, manual) and sidebar groups
├── journal_groups.py            Field-group classification for the Datastories tools
├── tagger.py                    Controlled-vocabulary auto-tagging
├── feeds.py                     Atom feeds for /feed.xml and /feed/<slug>.xml
├── alerts.py, digest.py         Saved-search alerts and their email digests
├── notifications.py             Transactional email
├── redaction.py, orcid_oauth.py Author name-removal requests and ORCID verification
├── datastories_cache.py         Server-side memoization for slow Datastories tools
├── mem_watchdog.py              Memory guard for the old Fly gunicorn worker (no-op on Windows)
├── usage/                       Usage statistics, off by default (docs/usage-statistics.md)
│
├── fetcher.py                   CrossRef API ingester
├── rss_fetcher.py               RSS / OAI-PMH / WordPress ingester
├── scraper.py                   Per-journal HTML scrapers
├── deep_refresh.py              Full corpus revalidation (POST /fetch with {"deep": true})
├── weekly_maintenance.py        Numbered maintenance pipeline; /api/admin/run-maintenance runs steps 4–9 and 12
│
├── enrich_openalex.py           OpenAlex affiliation + abstract enrichment
├── openalex_citations.py        OpenAlex citation backfill
├── cite_fetcher.py              CrossRef references → citation edges
├── fetch_institutions.py        Institution affiliation enrichment
├── scrape_lics_refs.py          LiCS reference lists (weekly, step 6)
├── retag.py                     Re-run the auto-tagger on existing rows
├── enrich.py                    Unpaywall + Semantic Scholar enrichment
├── book_fetcher.py              CrossRef book + chapter ingester; curated list in data/curated_books.tsv
├── book_title_repair.py         Book and chapter title repair (weekly, step 12)
├── fetch_parlor.py, fetch_pitt.py, fetch_routledge.py, fetch_siup.py
│                                Per-press book scrapers, run on demand
├── ingest_*.py                  One-off loaders: JAC, Peer Review 1.1, RPC 4.1, the /wac dashboard tables
├── backfill_*.py, dedupe_*.py, audit_artifacts.py, cull_upc.py, seed_usu_rhet_comp.py, scrape_ccdp.py
│                                One-off repair and seeding scripts
├── coverage_report.py           Coverage snapshot generator (used by /coverage)
├── citation_duplicates_report.py, crossref_book_probe.py, probe_new_publishers.py
│                                Read-only reports and probes
│
├── backup.py                    Offsite backup: snapshot → zstd → age → S3/B2
├── restore.py                   Restore an offsite backup from the bucket
├── restore_local.py             Restore from a backup file already on disk
│
├── deploy/windows/              PowerShell scripts for the Clearinghouse server (README there)
├── deploy/xyz-redirect/         Cloudflare Worker that redirects pinakes.xyz (README there)
├── templates/                   Jinja2 templates (base-alexandrian.html → base-core.html)
├── static/                      Alexandrian Suite CSS and fonts, JS modules (README there)
├── data/                        Committed inputs: seeds/ (README there), curated books, title overrides, WAC dump
├── data_exports/                Coverage snapshot written by coverage_report.py
├── jwa-webtext/                 The JWA webtext bundle, served at /jwa behind Basic Auth
├── docs/                        Architecture, methodology, server install + runbook, refactor notes
├── tests/                       Pytest harness (about 700 tests)
│
├── conftest.py                  Test fixtures
├── pytest.ini                   Test config
├── requirements.txt             Runtime deps
├── requirements-dev.txt         Dev / test deps
├── Dockerfile, fly.toml         Former Fly.io deployment (gunicorn); no longer production
└── articles.db                  SQLite database (gitignored; C:\Pinakes\data\ on the server by default)
```

## Documentation

| Document | For |
|---|---|
| [Architecture](docs/architecture.md) | How Pinakes is built — system overview, data flow, ingestion, deployment |
| [Methodology](docs/methodology.md) | What each Explore tool measures, how it's computed, and the scholarly references |
| [Author redaction](docs/author-redaction.md) | The author opt-out ("right to be forgotten") feature — token model, data model, suppression spine, request flow |
| [Usage statistics](docs/usage-statistics.md) | What the off-by-default usage statistics record, how institutions are identified, how to run them |
| [Journal coverage](docs/journal-coverage.md) | Every venue indexed by Pinakes with its ingestion path |
| [Windows Server install](docs/WINDOWS-SERVER-INSTALL.md) | First install on the Clearinghouse server |
| [Windows Server runbook](docs/WINDOWS-SERVER-RUNBOOK.md) | Operating the server — recovery, updates, visitor IPs, troubleshooting |
| [Windows deployment scripts](deploy/windows/README.md) | Inventory of the PowerShell scripts in `deploy/windows/` |
| [pinakes.xyz redirect](deploy/xyz-redirect/README.md) | The Cloudflare Worker that answers for the old domain |
| [Disaster recovery runbook](docs/runbooks/disaster-recovery.md) | Restoring the database on the server — from its local backups first, or from the encrypted offsite copy |
| [Author redaction runbook](docs/runbooks/author-redaction.md) | Operating the opt-out feature — config, reviewing requests, undo, restore |
| [`static/`](static/README.md) | The stylesheets, fonts, icons, and chart colour rules |
| [Refactor notes](docs/refactor-notes/) | Audit trail for the structural improvements (notes 01 through 17) |

## Contributing

External contributions are rare; the project is maintained by one person, but bug reports and small PRs are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the testing requirements and the scraping-ethics review that applies to any new scraper.

## License

Released under the [GNU GPL 3.0](LICENSE).

## Citation

A research note describing the index is in preparation for the *Journal of Writing Analytics*. Until that lands, please cite the repository:

```bibtex
@misc{lewis_pinakes_2026,
  author = {Lewis, Justin},
  title  = {Pinakes: A Bibliometric Index for Rhetoric and Composition},
  year   = {2026},
  url    = {https://github.com/justalewis/Rhet-Comp-Index},
}
```
