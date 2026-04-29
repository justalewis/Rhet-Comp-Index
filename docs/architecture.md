# Architecture

> **Status: placeholder.** A full architecture write-up will land with prompt G1 alongside the *Journal of Writing Analytics* research note. Until then, this page sketches the major components and points to the audit-trail notes that already document specific subsystems in detail.

## At a glance

Pinakes is a Flask web app backed by SQLite, deployed to Fly.io as two process groups:

- **`app`** — `gunicorn` running [`app.py`](../app.py). Single worker; serves all HTTP traffic.
- **`scheduler`** — APScheduler `BlockingScheduler` running [`scheduler.py`](../scheduler.py). Daily fetch + weekly OpenAlex enrichment. No HTTP.

Both processes share the SQLite file at `/data/articles.db` on a Fly volume. SQLite is in WAL mode; the single web worker plus the single scheduler process means at most one writer at any time, and concurrent reads are unproblematic.

## Subsystems

| Concern | Module(s) | Documented in |
|---|---|---|
| Routes & request handling | [`app.py`](../app.py) | this file (placeholder) |
| Database layer | [`db.py`](../db.py) | [refactor-notes/01](refactor-notes/01-test-harness.md) (testing) |
| Auth on mutating endpoints | [`auth.py`](../auth.py) | [refactor-notes/02](refactor-notes/02-admin-auth.md) |
| Rate limiting | [`rate_limit.py`](../rate_limit.py) | [refactor-notes/03](refactor-notes/03-rate-limiting.md) |
| Health checks | [`health.py`](../health.py) | [refactor-notes/04](refactor-notes/04-health-and-scheduler.md) |
| CrossRef ingestion | [`fetcher.py`](../fetcher.py) | this file (placeholder) |
| RSS / OAI / WordPress | [`rss_fetcher.py`](../rss_fetcher.py) | this file (placeholder) |
| Custom HTML scrapers | [`scraper.py`](../scraper.py) | inline ethics annotations per source |
| OpenAlex enrichment | [`enrich_openalex.py`](../enrich_openalex.py) | this file (placeholder) |
| Auto-tagging | [`tagger.py`](../tagger.py) | this file (placeholder) |

## Data flow

```
External sources → Ingesters → upsert_article (db.py) → SQLite
                                       │
       OpenAlex enrichment ────────────┤
                                       ▼
                                 articles, authors, citations,
                                 books, institutions tables
                                       │
                                       ▼
                                  Flask routes (app.py)
                                       │
                  HTML (Jinja) / JSON / BibTeX / RIS exports
```

Citation networks, co-citation graphs, sleeping-beauty detection, and other analytics are computed on demand in `db.py` using NetworkX. None of these are precomputed; they're cheap enough on the current corpus (~50 k articles, ~ 30 k citation edges) that on-demand computation is fine and avoids a stale-cache problem.

## Why SQLite

Pinakes is read-heavy (web traffic) with one writer (the scheduler). A single SQLite file in WAL mode handles this cleanly. The corpus is < 200 MB; a relational DB would add operational surface for no measurable benefit. If the corpus ever grows past ~ 5 GB or we need horizontal scaling, the migration target is Postgres on Fly's managed offering — but we're nowhere near that.

## Why one process group, two roles

The web worker and the scheduler can share one Fly machine *or* run on two. We chose two process groups (rather than a `BackgroundScheduler` inside gunicorn) because:

- APScheduler's `BackgroundScheduler` inside gunicorn has previously caused deadlocks at preload time (see the comment block in [`Dockerfile`](../Dockerfile)).
- A separate process is observable independently — the heartbeat file tells `/health/deep` whether the scheduler is actually running.
- Restarting the web process for a deploy doesn't interrupt a long-running fetch.

See [refactor-notes/04](refactor-notes/04-health-and-scheduler.md) for the alternative we considered (GitHub Actions cron) and why we rejected it.

## What this page will become

Prompt G1 — scheduled to run after the structural cleanup is done — will replace this placeholder with a full architecture document covering routing, database schema, the analytics pipeline, deployment topology, and the threat model. For now, the refactor notes linked above are authoritative for the subsystems they cover.
