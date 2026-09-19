# Windows Server deployment scripts

Operational tooling for the Windows/IIS deployment of Pinakes — the one behind
`pinakes.wacclearinghouse.org`. The Fly.io deployment (`pinakes.xyz`) does
not use any of this; it deploys through `.github/workflows/fly-deploy.yml`.

**Start here:** [`docs/WINDOWS-SERVER-RUNBOOK.md`](../../docs/WINDOWS-SERVER-RUNBOOK.md)
is the step-by-step procedure. This file is just the inventory.

## Scripts

| Script | Purpose |
|---|---|
| `Update-Pinakes.ps1` | Deploy from GitHub. Backs up, deploys, verifies the running service reports the new commit, rolls back if not. |
| `Test-PinakesHealth.ps1` | Health check. `-Remote` runs the HTTPS-only subset from any machine. |
| `Invoke-PinakesAdmin.ps1` | POST to an admin endpoint (fetch, prewarm, maintenance, digests, backup) with the bearer token. |
| `Backup-Pinakes.ps1` | On-demand database backup. |
| `Install-PinakesTasks.ps1` | Register / list / remove the scheduled jobs. |
| `Pinakes.Common.ps1` | Shared config and helpers. Dot-sourced; not run directly. |
| `sqlite_backup.py` | Hot-copy helper via SQLite's online backup API. |

Every `.ps1` supports `Get-Help .\Script.ps1 -Full`.

## Quick reference

```powershell
.\Update-Pinakes.ps1 -Check                 # what would change? changes nothing
.\Update-Pinakes.ps1                        # deploy origin/main
.\Update-Pinakes.ps1 -Ref e0f82ec           # deploy a specific commit (or roll back)
.\Test-PinakesHealth.ps1                    # full check, on the server
.\Test-PinakesHealth.ps1 -Remote            # HTTPS-only check, from anywhere
.\Invoke-PinakesAdmin.ps1 -Endpoint Fetch   # fetch new articles now
.\Backup-Pinakes.ps1 -Label before-thing    # ad-hoc backup
.\Install-PinakesTasks.ps1 -List            # scheduled job status
```

## Design notes

**PowerShell 5.1.** Windows Server 2019/2022 ship 5.1, so no `&&`, no ternary,
no null-coalescing, no `-AsHashtable`. Verify a change still parses under 5.1
before shipping it.

**Configuration comes from Machine-scope environment variables**, read by
`Get-PinakesSetting` in `Pinakes.Common.ps1`. A site that installed outside
`C:\Pinakes` sets `PINAKES_ROOT` and everything follows. Nothing is hardcoded to
one machine, and `PINAKES_ADMIN_TOKEN` never appears in a script or a scheduled
task definition.

**Health checks go to `127.0.0.1`, not the public hostname.** Going direct means
the check cannot be answered out of the IIS/ARR response cache, and it separates
"is the app up?" from "is the reverse proxy configured?". `Test-PinakesHealth.ps1`
also appends a cache-buster to every request for the same reason — a crawl that
does not is measuring the proxy, not the application.

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

**The scheduled tasks replace a service that no longer works.** Section 8.3 of
the install guide built a `Pinakes-Scheduler` service around `scheduler.py`,
which was deleted in commit `fa25cae`. The recurring jobs became admin endpoints
that something external POSTs to on a schedule — GitHub Actions against
`pinakes.xyz` for Fly, Windows Task Scheduler here.
