# Windows Server deployment scripts

Operational tooling for the Windows/IIS deployment of Pinakes — production,
`pinakes.wacclearinghouse.org`, and the test host
`testpinakes.wacclearinghouse.org`. The former Fly.io deployment (`fly.toml`,
`Dockerfile`, gunicorn) is no longer production and uses none of this.
`.github/workflows/fly-deploy.yml` still deploys it after every green Tests run
on `main`, and `pinakes.xyz` now redirects to the Clearinghouse host.

**Start here:** [`docs/WINDOWS-SERVER-RUNBOOK.md`](../../docs/WINDOWS-SERVER-RUNBOOK.md)
is the step-by-step procedure. This file is just the inventory.

## Scripts

| Script | Purpose |
|---|---|
| `Update-Pinakes.ps1` | Deploy from GitHub. Backs up `articles.db`, deploys, verifies the running service reports the new commit, rolls back if not. |
| `Test-PinakesHealth.ps1` | Health check. HTTP checks go to `127.0.0.1` unless `-BaseUrl` names the public URL. `-Remote` runs the HTTPS-only subset from any machine. |
| `Test-PinakesClientIp.ps1` | Checks the app sees real visitor IPs through Cloudflare → IIS → Waitress. Run from any machine. |
| `Invoke-PinakesAdmin.ps1` | POST to an admin endpoint with the bearer token: `Fetch`, `DeepFetch`, `Prewarm`, `Maintenance`, `Digests`, `Backup` (offsite), `UsageResolve`, `UsageData`. |
| `Backup-Pinakes.ps1` | Local backup of `articles.db`, and of `usage.db` when it exists. The nightly task runs it; run it by hand before anything risky. |
| `Install-PinakesTasks.ps1` | Register / list / remove the scheduled jobs. |
| `Watch-PinakesHealth.ps1` | Restarts the service when it is running but `/health` has stopped answering. Run every 5 min by `Pinakes-HealthWatchdog`. |
| `Set-PinakesService.ps1` | NSSM service settings: stdout/stderr logs with rotation, waitress threads, `PYTHONUNBUFFERED`. |
| `Pinakes.Common.ps1` | Shared config and helpers. Dot-sourced; not run directly. |
| `sqlite_backup.py` | Hot-copy helper via SQLite's online backup API. |

Every script except `Pinakes.Common.ps1` supports `Get-Help .\Script.ps1 -Full`.

## Quick reference

```powershell
.\Update-Pinakes.ps1 -Check                 # what would change? changes nothing
.\Update-Pinakes.ps1                        # deploy origin/main
.\Update-Pinakes.ps1 -Ref e0f82ec           # deploy a specific commit (or roll back)
.\Test-PinakesHealth.ps1                    # full check, on the server
.\Test-PinakesHealth.ps1 -BaseUrl https://pinakes.wacclearinghouse.org   # same, HTTP checks via the proxy
.\Test-PinakesHealth.ps1 -Remote            # HTTPS-only check, from anywhere
.\Invoke-PinakesAdmin.ps1 -Endpoint Fetch   # fetch new articles now
.\Backup-Pinakes.ps1 -Label before-thing    # ad-hoc backup
.\Install-PinakesTasks.ps1 -List            # scheduled job status
.\Set-PinakesService.ps1 -Show              # service config (logs, threads)
.\Watch-PinakesHealth.ps1 -DryRun           # would the watchdog restart right now?
```

## Design notes

**PowerShell 5.1.** Windows Server 2019/2022 ship 5.1, so no `&&`, no ternary,
no null-coalescing, no `-AsHashtable`. Verify a change still parses under 5.1
before shipping it.

**Configuration comes from Machine-scope environment variables**, read by
`Get-PinakesSetting` in `Pinakes.Common.ps1` (Machine scope first, then the
current session; §6.3 of the
[install guide](../../docs/WINDOWS-SERVER-INSTALL.md) lists them). A site that
installed outside `C:\Pinakes` sets `PINAKES_ROOT` and everything follows. Nothing is hardcoded to
one machine, and `PINAKES_ADMIN_TOKEN` never appears in a script or a scheduled
task definition.

**Health checks go to `127.0.0.1`, not the public hostname.** Going direct means
the check cannot be answered out of the IIS/ARR response cache, and it separates
"is the app up?" from "is the reverse proxy configured?". To test the proxy too,
give `Test-PinakesHealth.ps1` the public URL with `-BaseUrl`; without it,
`Public and local versions agree` compares `127.0.0.1` with itself.
`Test-PinakesHealth.ps1` also appends a cache-buster to every request — a crawl
that does not is measuring the proxy's cache, not the application.

**A deploy is not verified until `/health` reports the new SHA.** `health.py`
derives the version from `git rev-parse --short HEAD` once, at process start, so
the new SHA appearing there is the only evidence that the service restarted onto
new code rather than merely surviving the update. `Update-Pinakes.ps1` treats
anything less as a failure and rolls back.

**Backups use SQLite's online backup API**, never `Copy-Item`. The app is a
long-running writer against the same file; a plain copy can capture a torn page
mid-transaction, and the result looks fine until the day you try to restore it.
`sqlite_backup.py` also runs `PRAGMA quick_check` on the copy before reporting
success.

**NSSM restarts a process that exits, not one that hangs.** A waitress process
with every worker thread stuck still reports Running, so on 2026-10-17 the site
returned 502 for most of a day while Windows saw nothing wrong.
`Watch-PinakesHealth.ps1` asks `/health` (no database work, sub-millisecond when
healthy) three times 20 s apart, and only then dumps the threads with py-spy and
restarts. It stands aside while the service is Stopped, during a deploy, inside
five minutes of a start, and whenever `<root>\watchdog.pause` exists.

**The scheduled tasks replace a service that no longer works.** Older copies of
the install guide built a `Pinakes-Scheduler` service around `scheduler.py`,
which was deleted in commit `fa25cae`; §8.3 of the guide now registers these
tasks instead. The recurring jobs became admin endpoints that something
external POSTs to on a schedule. For Fly that was GitHub Actions against
`pinakes.xyz` (`cron.yml`, `maintenance.yml`, `alerts.yml`), whose schedules
have been commented out since 2026-09-19. Here it is Windows Task Scheduler.
