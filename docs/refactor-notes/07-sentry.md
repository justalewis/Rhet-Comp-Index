# 07 — Sentry error monitoring (Prompt D1)

Audit trail for adding Sentry to the web process, the scheduler, and the five long-running ingestion scripts.

## Files added / modified

- `monitoring.py` (new) — `init_sentry(component)`, `capture_fetcher_error(source, journal, exc)`, `_scrub_pii` before-send hook.
- `requirements.txt` — adds `sentry-sdk[flask]>=2.0` (resolved to 2.58.0 locally).
- `app.py` — `init_sentry("web")` called between top-level imports and the Flask app construction, so ingestion at import time (init_db, OA backfill, prewarm queries) is also covered.
- `scheduler.py` — `init_sentry("scheduler")` at top of file; `capture_fetcher_error` calls added to the per-source try/except blocks in `job()` and `openalex_job()`.
- `fetcher.py`, `rss_fetcher.py`, `scraper.py`, `enrich_openalex.py`, `cite_fetcher.py` — each gets a `SOURCE_NAME` constant and a single `capture_fetcher_error` call in its outer per-journal or per-record exception handler. Inner low-level errors (malformed dates, single-record parse failures) are NOT captured — they're noise at the dashboard level.
- `conftest.py` — sets `FLASK_ENV=testing` at module load before importing `app`, so `init_sentry()` short-circuits even if a developer happens to have `SENTRY_DSN` exported in their shell.
- `tests/test_monitoring.py` (new) — 15 tests covering each skip path, the happy-path init (with kwargs assertions), idempotence, every PII-scrubbing branch, and the capture-error tagging logic.
- `fly.toml`, `README.md` — `SENTRY_DSN` documented as an optional secret with the full structured-tag scheme.

## Where capture_fetcher_error was added

| File | Line | Context | Tags applied |
|---|---|---|---|
| `fetcher.py` | ~109 | per-journal `RequestException` in `fetch_journal` | `source=crossref`, `journal=<name>` |
| `rss_fetcher.py` | ~424 | per-journal `Exception` in `fetch_all` loop | `source=rss`, `journal=<name>` |
| `scraper.py` | ~4191 | per-strategy `Exception` in `fetch_all` dispatcher | `source=scrape`, `journal=<name>` |
| `enrich_openalex.py` | ~175 | per-article `Exception` in main enrichment loop | `source=openalex` (no journal — loop is over articles, not journals) |
| `cite_fetcher.py` | ~191 | per-article `Exception` in `run_fetch` outer body | `source=citations` (same — per-article) |
| `scheduler.py` | three sites in `job()`, one in `openalex_job()` | per-source try/except wrapper around the `fetch_all` calls | `source=<crossref/rss/scrape/openalex>`, no journal |

## Decisions

### `traces_sample_rate=0.01`, `profiles_sample_rate=0.0`

Sentry's free tier allows 10k transactions / month. The site sees roughly 200 organic requests / day plus ~5,750 internal health-check hits (Fly's 15s cadence × 86,400/15 = 5,760, but `/health` and `/health/ready` are exempt from rate limiting and Sentry's Flask integration captures all 2xx as transactions). At a 1% sample rate, that's ~ 1,800 / month — comfortably under quota with headroom for traffic spikes.

Profiles are off entirely. Profiling is useful when chasing a specific performance problem; it consumes orders of magnitude more quota than tracing. We can flip it on temporarily when investigating a slow endpoint, then back off.

### What gets captured vs what gets logged

Every `capture_fetcher_error` site is paired with an existing `log.error(...)` line. Sentry doesn't replace logging — it adds structured tagged events on top. The intent: a maintainer monitoring Sentry sees "RSS fetch for KB Journal failed 3× in 24 hours" without having to grep through `flyctl logs`. Routine warnings (rate-limit retries, 404s from optional fields, etc.) stay log-only.

### Why not just use Sentry's logging integration

The `LoggingIntegration` would auto-capture every `log.error` call, which inflates noise and doesn't carry the structured `source` / `journal` tags. The explicit-call pattern in this PR keeps Sentry events focused on operationally interesting failures.

## PII scrubbing

`_scrub_pii` runs as Sentry's `before_send` hook on every event. It strips:

- `request.headers["Authorization"]` — replaced with `[FILTERED]`. The `PINAKES_ADMIN_TOKEN` is the only auth header in the wild today, but defense in depth.
- `request.headers["Cookie"]` — `[FILTERED]`. Pinakes sets no cookies, but third-party tooling could.
- Any query parameter whose name contains `token` (case-insensitive) — value replaced with `[FILTERED]`. Handles both string and list-of-pairs `query_string` shapes.
- `request.data` — set to `None`. Search queries typed into the homepage box could be sensitive (e.g., a researcher exploring a fraught topic) and aren't worth shipping to Sentry.

Non-HTTP events (scheduler errors, captured exceptions from ingestion scripts) have no `request` key and pass through untouched.

## Configuring the Sentry project

The Sentry project URL goes here once it's created:

> **Sentry project**: _(to be filled in when the operator creates the project)_

Recommended Sentry-side configuration:

1. **Issue alert**: `level:error AND component:fetcher` → email the maintainer when 5+ events fire in 1 hour. This catches a rotten ingester (e.g., an OpenAlex schema change) without paging on every transient timeout.
2. **Inbound filter**: drop events with `release:dev` to keep local debugging out of the production project.
3. **Data scrubbing**: the project's default scrubbing rules should remain enabled in addition to our before-send hook. Defense in depth.

## Manual verification step

The prompt's "definition of done" includes a manual check: forcing a 500 in development with the DSN set should produce an event in the Sentry dashboard. To do this:

```bash
export SENTRY_DSN='https://...@.../...'
unset FLASK_ENV
python -c "from app import app; app.test_client().get('/__force-500')"
# Check the Sentry dashboard within ~30 seconds
```

(`/__force-500` doesn't exist; the 404 still flows through Sentry's request hooks for the trace sample, but won't show as an issue. To force a 500, transiently raise inside any view function.)

This step is left to the operator and is not covered by the test suite — Sentry network calls are stubbed throughout the tests by design.

## Decisions explicitly NOT made in this prompt

- **No user-feedback widget.** Per prompt constraint — would inject UI on the public site.
- **No session replay.** Per prompt constraint — quota cost, plus we don't need it.
- **No `LoggingIntegration`.** Auto-capturing every `log.error` would dilute the dashboard and lose structured tags. Explicit `capture_fetcher_error` sites are deliberate.
- **No raised trace sample rate.** Stays at 0.01 per prompt constraint; can be bumped temporarily during incident response.
- **No `capture_message` for non-exception conditions** (rate-limit hits, missed heartbeats, etc.). Those belong in metrics, not error monitoring. If/when we add a metrics pipeline, it'll be a separate prompt.
