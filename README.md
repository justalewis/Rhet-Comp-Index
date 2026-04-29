# Rhetoric & Composition — Current Scholarship

A local web app that aggregates newly published articles from 14 major Rhet/Comp journals via the CrossRef API.

## Setup

```bash
pip install -r requirements.txt
```

## First run — fetch articles

```bash
# Fetch all 14 journals (will take a few minutes on the first run)
python fetcher.py

# Or fetch a single journal by ISSN
python fetcher.py 0010-096X
```

## Run the web app

```bash
python app.py
# Open http://localhost:5000
```

## Keep it updated automatically

Run the scheduler in a separate terminal (or at login):

```bash
python scheduler.py
```

This runs an incremental fetch at startup and again every 24 hours.

You can also click **Refresh from CrossRef** in the sidebar to trigger a manual fetch anytime.

## Project structure

```
├── app.py          Flask web server
├── fetcher.py      CrossRef API integration
├── scheduler.py    APScheduler daily refresh
├── db.py           SQLite database layer
├── journals.py     Journal names and ISSNs
├── templates/
│   └── index.html  Main page template
├── static/
│   └── style.css   Stylesheet
├── articles.db     SQLite database (created on first run)
└── requirements.txt
```

## Deployment secrets

The app reads sensitive configuration from environment variables. On Fly.io,
set them as secrets (NOT in `fly.toml`'s `[env]` block):

| Secret | Purpose | Required? |
|---|---|---|
| `PINAKES_ADMIN_TOKEN` | Bearer token for `POST /fetch` and other mutating endpoints (see `auth.py`). When unset, mutating endpoints return HTTP 503. | Yes for production; optional for read-only local dev. |

Generate and set the admin token:

```bash
flyctl secrets set PINAKES_ADMIN_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

To trigger a manual fetch from the production server:

```bash
curl -X POST https://pinakes.xyz/fetch \
  -H "Authorization: Bearer $PINAKES_ADMIN_TOKEN"
```

`GET /health` reports the configuration status as `{"admin_auth": "configured" | "missing"}` so you can verify the secret landed without leaking its value.

## Production deployment (Fly.io)

Pinakes runs as **two process groups** on Fly:

| Process | Entry point | Purpose |
|---|---|---|
| `app` | `gunicorn ... app:app` | Web server. Receives all HTTP traffic. |
| `scheduler` | `python scheduler.py` | Daily fetch + weekly OpenAlex enrichment. No HTTP. Writes `/data/scheduler.heartbeat` so `GET /health/deep` can verify it's alive. |

After the first deploy, scale each process group to one machine (one-time):

```bash
flyctl scale count app=1 scheduler=1
```

Subsequent deploys preserve the scaling. Verify both groups are running:

```bash
flyctl status
flyctl machine list
```

### Health endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | none | Liveness — process is up. <50ms, no DB. |
| `GET /health/ready` | none | Readiness — DB is reachable. Used by Fly's check loop. |
| `GET /health/deep` | admin token | Full diagnostic — counts, last-fetch, disk, scheduler heartbeat, integrity check. |

Example deep-health probe from your laptop:

```bash
curl -H "Authorization: Bearer $PINAKES_ADMIN_TOKEN" https://pinakes.xyz/health/deep | jq
```

## Running tests

```bash
pip install -r requirements-dev.txt
pytest                    # full suite (fast tests only)
pytest -m "not slow"      # explicit fast suite
pytest --cov=. --cov-report=term-missing  # with coverage
```

The harness uses an isolated SQLite file per test (no production data is touched) and stubs all HTTP via `responses` and `feedparser` mocks. CI runs the suite on every push and pull request, and the Fly deploy is gated on test passage.

## Notes

- The CrossRef API is free and requires no API key
- Edit the `User-Agent` string in `fetcher.py` to include your email (polite API practice)
- On a first full fetch, CrossRef rate limits may slow things down slightly; subsequent incremental fetches are fast
