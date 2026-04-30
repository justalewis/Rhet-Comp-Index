# 13 — Scheduler architecture fix (post-G1 emergency)

Audit trail for replacing the standalone `scheduler.py` Fly process group with a GitHub Actions cron triggering admin-protected HTTP endpoints on the app machine.

## What went wrong

The architecture introduced in [refactor-notes/04](04-health-and-scheduler.md) (Prompt B3) ran the scheduler as a separate Fly process group. The intent was clean separation of concerns: the web process handles HTTP, the scheduler runs APScheduler's `BlockingScheduler` and writes to `/data/articles.db` directly. Both were assumed to share the same volume.

That assumption was wrong. **Fly volumes are single-attach**: a given volume can only be mounted to one machine at a time. The fly.toml `[[mounts]]` block applied to all process groups in principle, but in practice each scheduler machine got created without a volume attached. The `RUN mkdir -p /data` in the Dockerfile created `/data` as a regular directory in the scheduler's ephemeral container filesystem, which masked the problem: the scheduler started fine, sqlite happily created a fresh empty `articles.db` in container-local storage, APScheduler started, and the heartbeat file was written. The process appeared to run.

What was actually happening:

- The scheduler's `articles.db` was empty and separate from the app's real DB.
- Whatever the scheduler fetched at 03:00 UTC was being written to its private container filesystem.
- All that state was lost on container restart.
- The backup pipeline (when configured) would have snapshotted the empty container DB and uploaded that, instead of the real corpus.

The problem surfaced when the operator was setting up backup secrets. The eight rapid `flyctl secrets set` calls each restarted the scheduler machine. Fly retried a few times, then left the scheduler stopped. When we restarted it manually to diagnose, it began a full re-fetch of every CrossRef journal into its empty container DB — hammering the CrossRef API to populate a database nobody could see.

We stopped the runaway, scaled the scheduler process group to zero, and made the architecture fix recorded here.

## Why "Path B" became necessary

The original B3 prompt called out two paths:

- **Path A** — separate Fly process group running `scheduler.py`. Chosen at the time.
- **Path B** — delete `scheduler.py`, drive fetches/backups from a GitHub Actions cron hitting `POST /fetch` and similar endpoints on the app machine.

Path A is the path that turned out not to work on Fly. Path B works because the cron job is external; it only needs to make authenticated HTTP requests to the app, which has the volume.

The B3 audit doc explicitly listed the migration steps for switching to Path B in case the architecture stopped fitting. We're doing exactly that.

## What changed in this fix

Files removed:

- `scheduler.py` — the standalone APScheduler process; can never work on Fly's volume model.

Files modified:

- `fly.toml` — removed the `[processes]` block (which had defined `app` and `scheduler` entries). The Dockerfile's `CMD` now runs gunicorn directly. The `[http_service]` block lost its `processes = ["app"]` line because there are no longer multiple process groups to choose from.
- `Dockerfile` — comment block updated to describe the new single-process deployment.
- `blueprints/admin.py` — added `POST /api/admin/run-backup`. Synchronous: invokes `backup.run_backup()` in-process (the app process has the volume), writes `/data/scheduler.heartbeat` on success, returns the full summary dict as JSON. Returns HTTP 500 on failure so the GitHub Action sees the failure clearly.
- `tests/test_auth.py` — added four tests for the new endpoint covering auth required, returns summary on success with heartbeat write, returns 500 with no heartbeat write on failure.
- `tests/test_routes_html.py` — bumped the route-count assertion from 44 to 45.
- `README.md` — replaced the "two process groups" deployment description with single-machine plus cron. Updated the data-flow ASCII diagram. Removed the `flyctl scale count app=1 scheduler=1` instruction (no longer applicable). Removed the local `python scheduler.py` recommendation.
- `Dockerfile` comments updated.
- `CHANGELOG.md` — entry added.

Files added:

- `.github/workflows/cron.yml` — daily cron at 03:00 UTC plus `workflow_dispatch` for manual triggering. Two jobs:
  1. `fetch`: `POST /fetch` (returns 200 immediately, fetch runs as a daemon thread inside gunicorn, against the real volume).
  2. `backup`: waits 10 minutes for the async fetch to finish most of its work, then `POST /api/admin/run-backup` (synchronous, returns full summary; failure surfaces as a workflow error). Both authenticate with the `PINAKES_ADMIN_TOKEN` GitHub Actions secret.

## What the operator must do after this lands

1. **Verify the existing scheduler machines are scaled to zero.** `flyctl status -a rhet-comp-index` should show only the `app` machine. If schedulers persist, run `flyctl scale count -a rhet-comp-index app=1 scheduler=0`.
2. **Add `PINAKES_ADMIN_TOKEN` as a GitHub Actions repository secret.** Settings → Secrets and variables → Actions → New repository secret. Name: `PINAKES_ADMIN_TOKEN`. Value: same as the existing Fly secret of the same name.
3. **Trigger one cron run manually to verify end-to-end.** Repository → Actions tab → "Daily fetch + backup" workflow → Run workflow. Watch both jobs go green; the backup job's log will show the JSON summary including the resulting B2 object key.
4. **Verify in the B2 console that a new file appeared** under the bucket's date prefix, sized roughly 80 MB.

After step 4, the architecture is fully operational and the daily cron will run automatically going forward.

## Heartbeat semantics

The `/data/scheduler.heartbeat` file is now written by `/api/admin/run-backup` on successful upload. The file's name is preserved for backward compatibility with `health.py` and `/health/deep`. The semantics shift slightly:

- **Before**: heartbeat = "the scheduler process is alive and at most 5 minutes from its last write."
- **After**: heartbeat = "the most recent successful backup upload was less than 25 hours ago."

Both signal "the scheduled work is happening." The new semantic is operationally tighter — a stale heartbeat now means a real backup has failed, which is more actionable than "a process appears unresponsive."

## What this fix does NOT do

- **Does not change the backup pipeline itself.** `backup.run_backup` is unchanged.
- **Does not move secrets.** All eight Fly secrets stay where they are; the only new secret is the GitHub Actions equivalent of `PINAKES_ADMIN_TOKEN`.
- **Does not delete `restore.py`.** Disaster recovery still works via the same restore script.
- **Does not change the disaster-recovery runbook** (`docs/runbooks/disaster-recovery.md`), beyond an implied note that the backup write path now goes through `/api/admin/run-backup` rather than a standalone scheduler.
- **Does not delete prior audit notes.** `04-health-and-scheduler.md` remains the historical record of the choice we made and why; this note (13) supersedes it operationally but doesn't erase the reasoning.

## Decisions not made

- **Whether to add a `/api/admin/run-fetch` synchronous endpoint** parallel to the backup one. The cron currently uses the existing async `/fetch`; if the GitHub Action ever needs to verify a specific fetch ran to completion (e.g., for blocking the backup until the fetch finishes), we'd add that. Today the 10-minute sleep between cron jobs is good-enough.
- **Whether to delete `restore.py` after a few months**. It still works; deleting it would be a separate decision once the backup pipeline has been operational for a quarterly drill or two.
