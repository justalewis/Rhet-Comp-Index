# 04 — Layered health checks and scheduler architecture (Prompt B3)

> **SUPERSEDED 2026-04-30** — see [13-scheduler-architecture-fix.md](13-scheduler-architecture-fix.md). The "Path A" architecture described below (a separate Fly process group running `scheduler.py`) never actually worked in production: Fly volumes are single-attach, so the scheduler machine could not share `/data` with the app and was silently writing fetches to its own ephemeral container filesystem. The bug surfaced during backup-secret setup and was fixed by deleting `scheduler.py`, removing the `[processes]` block from `fly.toml`, and switching to a GitHub Actions cron that POSTs to admin-token-protected endpoints on the app machine. This document is preserved as historical record of the original choice and its rationale.
>
> The three-level `/health` design described below remains in production unchanged.

Audit trail for hardening `/health` and resolving the `scheduler.py` deployment story.

## Files added / modified

- `health.py` (new) — `liveness`, `readiness`, `deep_diagnostic`; module-level `START_TIME`, `APP_VERSION`, integrity-check cache, heartbeat I/O.
- `app.py` — replaces single `/health` route with `/health`, `/health/ready`, `/health/deep`. `APP_VERSION` is now imported from `health.py` (single source of truth) rather than computed in app.py too. `/health` and `/health/ready` are `@limiter.exempt`; `/health/deep` is `@require_admin_token`.
- `fly.toml` — adds `[processes]` block with `app` (gunicorn) and `scheduler` (`python scheduler.py`); restricts `[http_service]` to `processes = ["app"]`; switches the Fly check path from `/health` to `/health/ready`.
- `Dockerfile` — comment block clarifies that fly.toml's `[processes]` overrides `CMD` on Fly. CMD itself stays as gunicorn for `docker run` ergonomics.
- `scheduler.py` — writes `/data/scheduler.heartbeat` at startup, after every job, and on a 5-minute APScheduler interval. Imports `health.write_heartbeat`.
- `conftest.py` — `fixture_db` and `seeded_db` now also call `health.clear_integrity_cache()` so the 6-hour cached integrity result doesn't leak between tests.
- `tests/test_health.py` (new) — 18 tests covering all three probes, heartbeat freshness states (none / fresh / stale), and integrity-check caching.
- `tests/test_routes_html.py` — route count assertion bumped from 42 to 44.
- `tests/test_auth.py` — removes the redundant `/health admin_auth` tests now that `tests/test_health.py::test_liveness_includes_admin_auth_status` covers the same contract.
- `README.md` — adds the deployment process-group section, `flyctl scale count app=1 scheduler=1` step, and a table of the three health endpoints with a `curl /health/deep` example.

## The three-level health rationale (Kubernetes-style)

The Kubernetes liveness/readiness probe model maps cleanly here:

| Probe | What it answers | Cost | Auth | When to fail |
|---|---|---|---|---|
| `/health` | Is the *process* alive? | <1ms, no DB | none | Process crashed (would not respond at all) |
| `/health/ready` | Should this machine receive *traffic*? | <250ms, light DB query | none | DB unreachable, schema gone |
| `/health/deep` | Is the *system* healthy end-to-end? | up to 5s, comprehensive | admin token | Anything operationally wrong |

Fly's check loop fires every 15 seconds. Putting it on `/health/ready` means Fly will pull a misbehaving machine from the pool the moment the SQLite file becomes unreachable — which is the right posture. `/health` stays available as the bare liveness probe so other tools (uptime monitors, load balancers in non-Fly environments) can still check the process without paying the DB cost.

`/health/deep` exposes operational metadata (article counts, disk free, integrity check, scheduler heartbeat) so admin observability doesn't require SSH-ing onto the Fly machine. Token-protected because counts and disk free are not things random scanners need to know.

### Sample `/health/deep` response

```json
{
  "status": "ok",
  "version": "e0a583e",
  "uptime_seconds": 12453.7,
  "counts":         {"articles": 28430, "books": 47, "authors": 9821},
  "last_fetched_at": "2026-04-29 03:00:14",
  "db_error":        null,
  "disk":           {"total_gb": 3.0, "free_gb": 1.7, "used_pct": 43.3},
  "scheduler":      {
    "heartbeat_age_seconds": 142.0,
    "scheduler_healthy":     true,
    "heartbeat_path":        "/data/scheduler.heartbeat"
  },
  "integrity_check": ["ok"],
  "auth":            {"admin_auth": "configured"},
  "security_headers": {
    "X-Frame-Options": "DENY",
    "...": "..."
  }
}
```

## Path A: Fly process groups for the scheduler

The other realistic option (Path B in the prompt) is to delete `scheduler.py` entirely and run a GitHub Actions workflow that POSTs `/fetch` on a cron. Path A wins for this codebase because:

- **`scheduler.py` already exists and works.** Path B requires writing a new GitHub workflow, storing the admin token as a workflow secret, and accepting GitHub's cron timing semantics (which are best-effort and silently delayed under load).
- **Fly's per-machine timing is reliable.** Once the scheduler process is running, APScheduler's interval triggers fire at fixed offsets from process start.
- **No external dependency on GitHub.** A cron that lives outside the deployment is one more thing that can rot when no one notices.
- **Operational consistency.** The scheduler logs go to Fly's log stream alongside the web logs. With Path B, you'd be reading Fly logs for the web side and GitHub Actions logs for the scheduler — two tools, two retention policies, two access controls.

### Switching to Path B in the future

If a future maintainer chooses Path B (perhaps to drop the second machine for cost, or because Fly's process groups stop being free), the migration is small:

1. Delete `scheduler.py` and remove `[processes].scheduler` from `fly.toml`.
2. Re-add a default `CMD` to `Dockerfile` (or keep gunicorn there as it already is).
3. Add `.github/workflows/cron-fetch.yml` that runs on `schedule: [cron: '0 4 * * *']` and `curl -X POST -H "Authorization: Bearer ..." pinakes.xyz/fetch`.
4. Drop the heartbeat file logic in `health.py` or repurpose it to track the last `/fetch` call.

The decision can be reversed at any time. This audit trail records why we picked A in 2026-04 so a future maintainer doesn't have to re-derive the reasoning.

## The heartbeat is load-bearing

`/health/deep` returns `scheduler.scheduler_healthy = false` when:

- The heartbeat file doesn't exist (scheduler has never started, or `/data` isn't mounted in the scheduler's container).
- The heartbeat file is older than 25 hours (24h job interval + 1h slack).

The 5-minute `write_heartbeat` interval inside `scheduler.py` is the actual production cadence — the 25-hour threshold is just the alarm bound. So a wedged scheduler shows up in `/health/deep` within ~5 minutes of the last successful heartbeat plus the polling interval of whatever monitor is reading `/health/deep`.

`scheduler.py` calls `write_heartbeat()` in three places: at startup, after every `job()` and `openalex_job()` call, and on its own 5-minute interval. The startup write is important — a freshly-deployed scheduler is observable to `/health/deep` immediately, before the first 24-hour fetch fires.

## What this run does NOT do

- **No Sentry / metrics.** That's prompt D1.
- **No automated alerting on stale heartbeat.** `/health/deep` reports the state; a downstream monitor (uptime check, Sentry metric, Grafana) would consume it. Not in scope here.
- **No schema-version reporting.** `db.py` has `init_db()` migrations but no version table. Adding one is its own prompt.
- **No DB-level row-count caps in `/health/deep`.** Counts are unbounded; on a 100M-row corpus they'd take seconds. Not a concern at our scale (single-digit-thousands articles).
- **No double-fetch protection.** With `min_machines_running = 1` and one scheduler instance, double-fetch is naturally prevented. If we ever scale either process group to multiple machines, we'll need a write lock or election step.

## Operator runbook (one-time and ongoing)

**One-time after this PR merges to main:**

```bash
flyctl scale count app=1 scheduler=1
```

Then verify:

```bash
curl -H "Authorization: Bearer $PINAKES_ADMIN_TOKEN" \
     https://pinakes.xyz/health/deep | jq '.scheduler'
# Expect: scheduler_healthy: true within ~5 minutes of deploy
```

**Ongoing:**

- Routine checks: uptime monitor pings `/health/ready` every 60s; pages on three consecutive failures.
- Weekly: hit `/health/deep`, eyeball `disk.free_gb` and `counts`. The 3GB volume holds ~30M rows of articles before pressure; well above present.
- After unscheduled fetches: confirm `last_fetched_at` advanced.
