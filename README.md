# Pinakes

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)
[![Tests](https://github.com/justalewis/Rhet-Comp-Index/actions/workflows/test.yml/badge.svg)](https://github.com/justalewis/Rhet-Comp-Index/actions/workflows/test.yml)

A discipline-specific bibliometric index for Rhetoric & Composition. Live at [pinakes.xyz](https://pinakes.xyz).

The index covers 44+ journals and 50,000+ articles, drawn from CrossRef, OpenAlex, RSS feeds, and a handful of custom scrapers (each scraper is rate-limited and respects each site's `robots.txt`; [`scraper.py`](scraper.py) carries inline ethics annotations per source). Records are stored in SQLite, served by Flask, and visualised with D3.js.

## Architecture

A single Flask process serves HTML and a JSON API; a separate APScheduler process runs daily fetches. Both share a SQLite file on a Fly.io persistent volume. Citation networks, co-citation graphs, and other analytics are computed on demand by [`db.py`](db.py) using NetworkX. Authentication on mutating endpoints uses a single shared bearer token; rate limiting uses Flask-Limiter with in-memory storage.

```
Client ── HTTPS ──▶ Fly.io edge ──▶ gunicorn (1 worker) ──▶ Flask (app.py)
                                                              │
                                              SQLite (WAL) ◀──┤
                                              /data/articles.db
                                                              │
APScheduler ──▶ scheduler.py ──▶ fetcher.py / rss_fetcher.py / scraper.py
                                       └──▶ CrossRef · OpenAlex · journal sites
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

These can take a long time on a cold corpus. To run all three on a daily cadence locally:

```bash
python scheduler.py      # blocking; Ctrl+C to stop
```

## Running tests

```bash
pytest                                       # full suite (fast)
pytest -m "not slow"                         # explicit fast suite
pytest --cov=. --cov-report=term-missing     # with coverage
```

The harness uses an isolated SQLite file per test, stubs all HTTP via `responses` and feedparser mocks, and never touches the developer's real `articles.db`. CI runs the suite on every push and pull request; the Fly deploy is gated on test passage.

## Deployment

Pinakes deploys to Fly.io as **two process groups**:

| Process | Entry point | Purpose |
|---|---|---|
| `app` | `gunicorn ... app:app` | Web server. Receives HTTP traffic. |
| `scheduler` | `python scheduler.py` | Daily fetch + weekly OpenAlex enrichment. Writes `/data/scheduler.heartbeat` so `GET /health/deep` can verify it's alive. |

### One-time setup

```bash
# Required: generate and store the admin token (used by /fetch and /health/deep)
flyctl secrets set PINAKES_ADMIN_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Optional: hook up Sentry error monitoring (free tier is fine)
flyctl secrets set SENTRY_DSN='https://...@...ingest.sentry.io/...'

# After the first deploy, scale each process group to one machine
flyctl scale count app=1 scheduler=1
```

The Sentry DSN is optional. Without it, [`monitoring.py`](monitoring.py) is a no-op and errors only surface in `flyctl logs`. With it, both the web process and the scheduler report errors with `component=web` / `component=scheduler` tags; ingestion errors are additionally tagged with `source=crossref|rss|scrape|openalex|citations` and (when known) `journal=<name>`.

### Backups (recommended)

The scheduler runs an online SQLite backup nightly at 03:00 UTC. The pipeline is `sqlite3 .backup` → zstd → age-encrypt → S3-compatible bucket. See [`docs/runbooks/disaster-recovery.md`](docs/runbooks/disaster-recovery.md) for the restoration procedure.

```bash
# 1. Generate an age key pair locally; KEEP the private key off Fly.
age-keygen -o ~/.pinakes/age.key
PUB=$(grep '^# public key:' ~/.pinakes/age.key | cut -d' ' -f4)

# 2. Create a Backblaze B2 bucket "pinakes-backup" and an application key
#    scoped to that bucket. Note its keyID and applicationKey.

# 3. Set six Fly secrets:
flyctl secrets set \
  PINAKES_BACKUP_BUCKET=pinakes-backup \
  PINAKES_BACKUP_ENDPOINT=https://s3.us-west-002.backblazeb2.com \
  PINAKES_BACKUP_REGION=us-west-002 \
  PINAKES_BACKUP_ACCESS_KEY_ID=<keyID> \
  PINAKES_BACKUP_SECRET_KEY=<applicationKey> \
  PINAKES_BACKUP_AGE_PUBLIC_KEY=$PUB
```

**The age private key is the most important secret in this project.** Store it in a password manager AND a paper backup. If you lose it, every backup becomes unrecoverable.

To restore manually:

```bash
python restore.py --list
python restore.py --latest --out ./restored.db --age-key ~/.pinakes/age.key
```

### Health endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | none | Liveness — process is up. <50ms, no DB. |
| `GET /health/ready` | none | Readiness — DB is reachable. Used by Fly's check loop. |
| `GET /health/deep` | admin token | Full diagnostic — counts, last-fetch, disk, scheduler heartbeat, integrity check. |

```bash
curl -H "Authorization: Bearer $PINAKES_ADMIN_TOKEN" https://pinakes.xyz/health/deep | jq
```

### Triggering a fetch manually

The scheduler runs every 24 hours. To force one early:

```bash
curl -X POST -H "Authorization: Bearer $PINAKES_ADMIN_TOKEN" https://pinakes.xyz/fetch
```

## Project structure

```
Rhet-Comp-Index/
├── app.py                       Flask web server, all routes
├── auth.py                      Admin-token decorator
├── rate_limit.py                Flask-Limiter configuration
├── health.py                    /health, /health/ready, /health/deep
├── db.py                        SQLite layer + analytics queries
├── journals.py                  Journal definitions (CrossRef, RSS, scrape, manual)
├── tagger.py                    Controlled-vocabulary auto-tagging
├── scheduler.py                 Standalone APScheduler process
│
├── fetcher.py                   CrossRef API ingester
├── rss_fetcher.py               RSS / OAI-PMH / WordPress ingester
├── scraper.py                   Per-journal HTML scrapers
│
├── enrich.py                    Wrapper coordinating enrichment passes
├── enrich_openalex.py           OpenAlex affiliation + abstract enrichment
├── openalex_citations.py        OpenAlex citation backfill
├── cite_fetcher.py              CrossRef references → citation edges
├── backfill_abstracts.py        Back-fill missing abstracts via OpenAlex
├── book_fetcher.py              CrossRef book + chapter ingester
├── fetch_institutions.py        Institution affiliation enrichment
│
├── coverage_report.py           Coverage snapshot generator (used by /coverage)
├── crossref_book_probe.py       Probe for which publishers index in CrossRef
├── cull_upc.py                  One-off: prune unwanted UP-only chapters
├── ingest_peer_review_1_1.py    One-off: load Peer Review 1.1 references
├── probe_new_publishers.py      Exploratory: find new publishers to add
├── retag.py                     Re-run the auto-tagger on existing rows
├── scrape_ccdp.py               One-off: scrape CCDP catalogue
├── scrape_lics_refs.py          One-off: scrape LiCS reference lists
├── seed_usu_rhet_comp.py        One-off: seed USU Press records
├── weekly_maintenance.py        Wrapper invoked weekly on Fly
│
├── fetch_parlor.py              ┐
├── fetch_pitt.py                ├─ Per-press book scrapers, run on demand
├── fetch_routledge.py           │
├── fetch_siup.py                ┘
│
├── templates/                   Jinja2 templates (base.html → base-core.html)
├── static/                      style.css + theme variants + explore.js (D3)
├── data/seeds/                  Hand-curated ingestion inputs (see README there)
├── docs/                        Architecture notes, methodology, refactor notes
├── tests/                       Pytest harness (~ 320 tests)
│
├── conftest.py                  Test fixtures
├── pytest.ini                   Test config
├── requirements.txt             Runtime deps
├── requirements-dev.txt         Dev / test deps
├── Dockerfile                   Multi-stage build for Fly
├── fly.toml                     Fly deployment config (two process groups)
└── articles.db                  SQLite database (gitignored; on /data in prod)
```

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
