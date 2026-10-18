# Pinakes on Windows Server — Operations Runbook

**Who this is for:** whoever has administrator access to the Windows Server that
runs `pinakes.wacclearinghouse.org`. No prior knowledge of the application
is assumed.

**What this covers:** the two things you do after the install is finished.

- **[Part A](#part-a--bring-the-deployment-to-healthy)** — bring an installed
  deployment to a healthy, populated state. Do this once per server.
- **[Part B](#part-b--push-an-update-from-github)** — deploy a new version from
  GitHub. Do this whenever there is a release.

For a first install on a new machine, use
[WINDOWS-SERVER-INSTALL.md](WINDOWS-SERVER-INSTALL.md) instead.

Everything here is scripted. The scripts live in the repository under
`deploy\windows\`, so they arrive on the server with the code itself.

---

## Conventions

- Every command runs in an **elevated PowerShell** window: right-click Start,
  then *Terminal (Admin)* or *Windows PowerShell (Admin)*.
- Paths assume the default layout from the install guide:

  | Path | Contents |
  |---|---|
  | `C:\Pinakes\app` | the git clone of the repository |
  | `C:\Pinakes\venv` | the Python virtual environment |
  | `C:\Pinakes\data\articles.db` | the SQLite database — the only real state |
  | `C:\Pinakes\logs` | service and job logs |
  | `C:\Pinakes\backups` | database backups (created by the scripts) |

  If this server installed somewhere else, set the root once and every script
  follows it:

  ```powershell
  [Environment]::SetEnvironmentVariable("PINAKES_ROOT", "D:\Apps\Pinakes", "Machine")
  ```

  Then close and reopen PowerShell.

- The application is one Windows service, **`Pinakes-Web`**, running the Flask
  app behind waitress on `127.0.0.1:8080`. IIS reverse-proxies the public
  hostname to it. The address waitress listens on is set in the service's
  arguments, which `nssm get Pinakes-Web AppParameters` shows. It should be
  `--host=127.0.0.1`; [Visitor IP addresses](#visitor-ip-addresses) says why.
- Commands call the scripts by full path,
  `& C:\Pinakes\app\deploy\windows\<script>.ps1`, so they work from any
  directory. A path written `.\deploy\windows\…` is relative to
  `C:\Pinakes\app`, or to the root of a clone on another machine.

---

## Part A — Bring the deployment to healthy

This part was written on 2026-08-27 for the test deployment, which then had
the three problems below. The steps are still the checklist for bringing any
installed server to a healthy, populated state, whether it is a new install or
one set up from an older version of the install guide. Each step says what it
is for, so you can skip any that have already been done.

<details>
<summary><strong>What was wrong on 2026-08-27</strong></summary>

1. **The server was running old code that could not fetch articles.** `/health`
   reported commit `e0f82ec`; `main` was four commits ahead. The missing fix
   was commit `138f4cd`. CrossRef stopped accepting a sort order combined with
   cursor paging and returns `400
   sort-criteria-incompatible-with-cursor` for the exact request that code
   sends. Every deep page failed, so the index stayed empty. This is why
   `/api/articles` returned `{"articles":[],"total":0}`.

2. **IIS/ARR was caching API responses for an hour, including empty ones.** The
   app was sending `Cache-Control: public, max-age=3600`, which ARR honours by
   storing its own copy — so requests never reached the app at all. During the
   initial seed that meant the site kept serving empty JSON out of the proxy
   cache while the database behind it filled up, and the site looked broken
   when it was not. Fixed in the app (commit `ddbf583`) by sending `private`
   instead; deploying step A4 is what applies it.

3. **Nothing was scheduled.** Older versions of the install guide created a
   `Pinakes-Scheduler` service pointed at `scheduler.py`, but that file was
   deleted from the repository in commit `fa25cae`. That service cannot start.
   The equivalent jobs on the Fly deployment ran from GitHub Actions against
   `pinakes.xyz`, which did nothing for this server (those schedules have been
   switched off since 2026-09-19). So there was no daily fetch, no backup, and
   no maintenance. Step A6 registers them as scheduled tasks.

</details>

### A1. Get the deployment tooling onto the server

The update scripts ship inside the repository. On a server installed from the
current guide they are already in `C:\Pinakes\app\deploy\windows\`; if so,
skip to A2. Otherwise this first update is the only one you do by hand.
Everything after it uses the script.

Note the version the service is running now, then see what is about to be
deployed:

```powershell
Invoke-RestMethod http://127.0.0.1:8080/health    # .version: the commit to return to if A4 fails
Set-Location C:\Pinakes\app
git fetch origin
git status
git log --oneline HEAD..origin/main
```

That last command lists the commits about to be deployed. (On 2026-08-27 there
were four, ending with `138f4cd fix(fetch): drop sort+cursor combo rejected by
CrossRef`.)

If `git status` shows local modifications, stop and send them to Justin before
continuing — the next step discards them.

```powershell
git reset --hard origin/main
```

You now have `C:\Pinakes\app\deploy\windows\` with the scripts listed under
[The scripts](#the-scripts).

### A2. Check prerequisites

```powershell
& C:\Pinakes\app\deploy\windows\Test-PinakesHealth.ps1
```

This prints PASS / FAIL / WARN for the service, the database file, the app,
the admin token, and whether the code matches GitHub. Nothing is changed. By
default every HTTP check goes straight to the app on `127.0.0.1:8080`, not
through IIS; A8 shows how to test the public path as well.

Some results are expected to be unhappy at this point and will be fixed below:

- `Index contains articles` — WARN, 0 articles, until the fetch in A7.
- `Service running checked-out code` — FAIL if A1 moved the tree, because the
  service has not restarted yet.
- `API responses not shared-cacheable` — FAIL while the service still runs
  code from before the cache fix (`Cache-Control: public`).

If it reports that the service `Pinakes-Web` does not exist, this machine is
not installed the way the guide assumes — go back to
[WINDOWS-SERVER-INSTALL.md](WINDOWS-SERVER-INSTALL.md). The same applies if
`Update-Pinakes.ps1` or `Install-PinakesTasks.ps1` later stops because the
venv Python or git is missing. Those two check for that; this script does not.

### A3. Confirm the admin token

The fetch and maintenance endpoints require a bearer token. The service already
has one (`/health` reports `"admin_auth":"configured"`), but the scheduled tasks
need to read the same value from the machine environment:

```powershell
[Environment]::GetEnvironmentVariable("PINAKES_ADMIN_TOKEN", "Machine")
```

If that prints nothing, the token was set another way (for example directly on
the NSSM service). Set it at Machine scope so the tasks can find it, using the
**same value the service is running with**:

```powershell
[Environment]::SetEnvironmentVariable("PINAKES_ADMIN_TOKEN", "<token>", "Machine")
Restart-Service Pinakes-Web
```

Justin has the token. If nobody has it, generate a new one and set it — nothing
depends on the old value:

```powershell
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$token = [Convert]::ToBase64String($bytes)
[Environment]::SetEnvironmentVariable("PINAKES_ADMIN_TOKEN", $token, "Machine")
Write-Host "New token (send to Justin over a secure channel): $token"
Restart-Service Pinakes-Web
```

Either way, check that the service and the tasks now agree. If the service
has its own copy of the token (`nssm get Pinakes-Web AppEnvironmentExtra`
lists `PINAKES_ADMIN_TOKEN=…`), change it there as well, keeping the other
lines, the way [Visitor IP addresses](#visitor-ip-addresses) shows for
another variable. A `403` from `Invoke-PinakesAdmin.ps1` means the two still
differ.

### A4. Deploy the current code

```powershell
& C:\Pinakes\app\deploy\windows\Update-Pinakes.ps1
```

If you ran A1, the tree is already at `origin/main`, so this mostly restarts
the service onto it — but run it anyway rather than restarting by hand,
because it takes a backup, reinstalls dependencies, and verifies that the
service comes up on the new commit.

Expect roughly two minutes. The last lines should read:

```
[...] == Update complete
[...] OK    Version:    <the new commit>
[...]       Admin auth: configured
[...]       Database:   reachable
[...] OK    Public URL: https://pinakes.wacclearinghouse.org returned HTTP 200
[...]       Transcript: C:\Pinakes\logs\update-<timestamp>.log
```

If it fails, see [When an update fails](#when-an-update-fails). One caveat
after A1: the script rolls back to the commit the tree was on when it started,
and A1 already moved the tree, so a rollback here lands on the same new
commit. To get back to the old code, use the manual recovery in that section
with the version you noted in A1.

### A5. Remove the dead scheduler service

Only if one exists on this machine:

```powershell
Get-Service Pinakes-Scheduler -ErrorAction SilentlyContinue
```

If that returns something, it is the broken service described above. Remove it:

```powershell
Stop-Service Pinakes-Scheduler -ErrorAction SilentlyContinue
nssm remove Pinakes-Scheduler confirm
```

### A6. Register the scheduled jobs

```powershell
& C:\Pinakes\app\deploy\windows\Install-PinakesTasks.ps1 -IncludeUpdateCheck
```

This creates eight scheduled tasks, all running as SYSTEM. Times are the
server's local time.

| Task | When | What |
|---|---|---|
| `Pinakes-NightlyBackup` | 02:00 daily | Local database backup to `C:\Pinakes\backups` |
| `Pinakes-DailyFetch` | 03:23 daily | Incremental CrossRef/RSS fetch |
| `Pinakes-Prewarm` | 04:15 daily | Rebuild cached aggregates |
| `Pinakes-WeeklyMaintenance` | 04:45 Sundays | Weekly maintenance pass |
| `Pinakes-UsageResolve` | 05:10 daily | Usage statistics: resolve recorded addresses, then delete them. Registered whether or not recording is on; see [Usage statistics](#usage-statistics). |
| `Pinakes-UsageData` | 05:40 Sundays | Usage statistics: refresh the IP databases when stale. Skips its downloads while recording is off. |
| `Pinakes-HealthWatchdog` | every 5 min | Restarts the app if it is running but `/health` stops answering. See [When the site is down](#when-the-site-is-down-502). |
| `Pinakes-UpdateCheck` | 08:00 daily | Runs `Update-Pinakes.ps1 -Check`. Never deploys. Registered only with `-IncludeUpdateCheck`. |

Backup runs before the fetch, so there is always a copy from before the day's
writes. Prewarm runs after, so it warms new data rather than old.

`Pinakes-UpdateCheck` does not report anywhere you can read. Its output goes
to the console, which Task Scheduler discards; it writes no transcript and
nothing to `admin-tasks.log`; and it exits 0 whether or not the server is
behind, so its last result shows only whether the check ran without an
error. To find out whether this server has fallen behind GitHub, run
`Update-Pinakes.ps1 -Check` by hand, or look at `Code matches GitHub` in
`Test-PinakesHealth.ps1`.

Confirm:

```powershell
& C:\Pinakes\app\deploy\windows\Install-PinakesTasks.ps1 -List
```

The list names nine tasks. `Pinakes-WeeklyDigests` reads `not registered`
unless you added `-IncludeDigests`.

> The weekly saved-search email digest is **not** registered by default. Add
> `-IncludeDigests` only once `PINAKES_ALERTS_ENABLED=1` and the sending domain
> is verified in Resend; until then the endpoint is a no-op.

### A6b. Service logging and threads

```powershell
& C:\Pinakes\app\deploy\windows\Set-PinakesService.ps1 -Show   # look first; changes nothing
& C:\Pinakes\app\deploy\windows\Set-PinakesService.ps1         # apply, restart, wait for /health
```

Points NSSM's stdout/stderr at `C:\Pinakes\logs\web-stdout.log` and
`web-stderr.log` with daily/10 MB rotation, runs waitress with 8 threads
instead of its default 4, and adds `PYTHONUNBUFFERED=1` so the last lines
before a crash or hang actually reach the file. The listen address and every
existing environment line (DB_PATH, the admin token) are kept. It restarts the
service, so run it outside the 02:00–06:00 job window.

### A7. Run the initial fetch

This is the long one. It walks every indexed journal's full CrossRef history and
writes a few hundred MB.

```powershell
& C:\Pinakes\app\deploy\windows\Invoke-PinakesAdmin.ps1 -Endpoint Fetch
```

It returns immediately with `{"status":"fetch started"}` — the work happens in a
background thread inside the web service. Expect **30 to 90 minutes** for the
full corpus. Watch the article count climb (run it every few minutes; the
fetch is done when the count stops moving and `last_fetched_at` stops
advancing):

```powershell
$h = @{ Authorization = 'Bearer ' + [Environment]::GetEnvironmentVariable('PINAKES_ADMIN_TOKEN', 'Machine') }
(Invoke-RestMethod http://127.0.0.1:8080/health/deep -Headers $h) | Select-Object counts, last_fetched_at
```

Errors and warnings go to the service's stderr log, `web-stderr.log` if the
service was installed per the guide (`nssm get Pinakes-Web AppStderr` shows the
file):

```powershell
Get-Content C:\Pinakes\logs\web-stderr.log -Wait -Tail 30
```

The fetchers also log a line per journal (`Done: College English — 412 new
articles`) and a summary per pass (`CrossRef fetch complete. Total new: N`, then
the RSS and `Scrape fetch complete` lines), but those are INFO-level, and under
`waitress-serve` the app's INFO lines most likely never reach the log: Waitress
sets up logging at WARNING before the fetcher's own INFO setting can take
effect. Don't wait for them. Press Ctrl+C to stop watching the log; that does
not stop the fetch.

If you see `400` and `sort-criteria-incompatible-with-cursor`, the server is
still on the old code — A4 did not take. Check `/health` and go back to A1.

### A8. Verify

Once the article count has stopped moving, run the check against the public
hostname, so its HTTP checks go through Cloudflare and IIS instead of straight
to the app:

```powershell
& C:\Pinakes\app\deploy\windows\Test-PinakesHealth.ps1 -BaseUrl https://pinakes.wacclearinghouse.org
```

(On the test host, use `https://testpinakes.wacclearinghouse.org`.) The
service, database and git checks still run on the server as before.

Everything should now be PASS. In particular:

- `Index contains articles` — a real count, not 0.
- `API responses not shared-cacheable` — `Cache-Control: private, max-age=3600`.
  If this still says `public`, the deploy did not land.
- `Public and local versions agree` — the version the app reports on
  127.0.0.1 matches the one served through the public hostname, so the proxy
  is not serving a stale copy. Without `-BaseUrl`, both sides of this check are
  127.0.0.1 and it proves nothing about the proxy.

Then look at the site: <https://pinakes.wacclearinghouse.org/explore> — pick
any tool from the accordion and confirm a chart draws.

> **If pages look stale or empty right after the deploy,** IIS/ARR may still be
> holding copies cached from before the fix. They expire within an hour on their
> own. To clear them immediately, recycle the ARR cache from IIS Manager
> (server node → *Application Request Routing Cache* → *Clear all cached
> content*), or `iisreset` if that is acceptable on this box.

### A9. Tell Justin

Send him the output of:

```powershell
& C:\Pinakes\app\deploy\windows\Test-PinakesHealth.ps1 -BaseUrl https://pinakes.wacclearinghouse.org
```

He can also check from his own machine at any time, without server access —
see [What Justin can do without server access](#what-justin-can-do-without-server-access).

---

## Part B — Push an update from GitHub

This is the routine after Part A. It takes about two minutes.

### B0. Before you start (Justin's side)

Nothing deploys to this server automatically. A change reaches it only when
someone runs the update script. The change must be merged to `main` on
<https://github.com/justalewis/Rhet-Comp-Index> first; the repository's own CI
runs the test suite on every push to `main`, so a red build is visible there
before anyone deploys.

### B1. See what would change

```powershell
& C:\Pinakes\app\deploy\windows\Update-Pinakes.ps1 -Check
```

Reports the deployed commit, the target commit, and every commit in between.
**Changes nothing** — safe to run any time, and it does not need an elevated
shell.

If it says `Already up to date`, you are done.

### B2. Deploy

```powershell
& C:\Pinakes\app\deploy\windows\Update-Pinakes.ps1
```

In order, the script:

1. Fetches from GitHub and compares commits. Exits early if there is nothing to do.
2. Stops if the working tree has local modifications, untracked files
   included, unless you pass `-Force` (see B4).
3. Takes a hot backup of `articles.db` to `C:\Pinakes\backups`, unless you
   pass `-SkipBackup`.
4. Stops `Pinakes-Web`.
5. Moves the working tree to the target commit (`git reset --hard`, then
   `git clean -fd`).
6. Reinstalls Python dependencies.
7. Smoke-tests that the new code imports, against a scratch database.
8. Starts the service and polls `/health` until it reports the **new** commit.
9. Rolls back if pip fails (6), the import test fails (7), or `/health` does
   not report the new commit in time (8): resets to the previous commit,
   reinstalls its dependencies, and restarts the service. A failure anywhere
   else — git in step 5, or the service refusing to stop or start — ends the
   script with no rollback, and can leave the service stopped. See
   [When an update fails](#when-an-update-fails).

Step 8 is the point of the exercise. The app derives its version from
`git rev-parse --short HEAD` when the process starts, so `/health` reporting the
new SHA is proof the service actually restarted onto the new code — not merely
that it is still running.

### B3. Verify

```powershell
& C:\Pinakes\app\deploy\windows\Test-PinakesHealth.ps1 -BaseUrl https://pinakes.wacclearinghouse.org
```

All PASS. Then load the site and click through one tool.

### B4. Useful variations

Deploy a specific commit rather than the tip of `main`. The repository has no
release tags, so give a commit SHA, short or full (from
`git log --oneline origin/main`), or a branch written as `origin/<branch>`:

```powershell
& C:\Pinakes\app\deploy\windows\Update-Pinakes.ps1 -Ref <sha>
```

Until the server is back on `main`, `Test-PinakesHealth.ps1` shows
`Code matches GitHub` as WARN.

Deliberately step **back** to a known-good commit — this is the manual rollback,
and it takes a backup and verifies just like a forward deploy:

```powershell
& C:\Pinakes\app\deploy\windows\Update-Pinakes.ps1 -Ref <known-good-sha>
```

The scripts move with the tree. Commits before `ece22a0` (2026-08-27) have no
`deploy\windows\` folder at all, and `Test-PinakesClientIp.ps1` arrived in
`6151a2a`, so stepping back that far removes scripts this runbook uses.
Stepping back also does not undo schema migrations the newer code ran.

Someone edited a file directly on the server and the script refuses to discard
it. Review the changes first (`git diff`, and `git status` for new files), send
them to Justin so they can be committed properly, then:

```powershell
& C:\Pinakes\app\deploy\windows\Update-Pinakes.ps1 -Force
```

`-Force` discards edits to tracked files and deletes untracked files in
`C:\Pinakes\app` (`git clean -fd`). Files git ignores, such as `.env` and
`*.db`, are left alone.

You have just taken a backup by hand, or the database is still empty, and want
to skip the pre-update one:

```powershell
& C:\Pinakes\app\deploy\windows\Update-Pinakes.ps1 -SkipBackup
```

Slow server, or a very large database — startup runs schema migrations before it
answers, so give it longer (the default is 180 seconds):

```powershell
& C:\Pinakes\app\deploy\windows\Update-Pinakes.ps1 -TimeoutSec 600
```

### When an update fails

What the script does depends on where it failed:

- **pip install or the import smoke test failed.** It resets to the previous
  commit, reinstalls its dependencies, restarts the service, and exits with
  `… failed; rolled back to <sha>.` That last line is printed even when the
  rollback did not work. Look above it for an `ERROR` line starting
  `Rollback itself failed` or `Rollback restored the old code but the app is
  STILL not healthy`.
- **The app did not report the new version within `-TimeoutSec`.** It prints
  the last 40 lines of `C:\Pinakes\logs\web-stderr.log`, rolls back the same
  way, and says which way it went: `the site is back on <sha>`, or
  `AND rollback failed. The site is DOWN.`
- **Anything else** — git could not move the tree, or the service would not
  stop or start. It prints the error and stops, with no rollback. The service
  may be left stopped, and the tree may be on either commit.

After any failure, check where things stand:

```powershell
Get-Service Pinakes-Web                          # Running or Stopped?
git -C C:\Pinakes\app rev-parse --short HEAD     # which commit the tree is on
Invoke-RestMethod http://127.0.0.1:8080/health   # .version: what the service is running
& C:\Pinakes\app\deploy\windows\Test-PinakesHealth.ps1
```

If the service is stopped and the tree is still on the previous commit,
`Start-Service Pinakes-Web` is enough. If the tree and the running version
disagree, or nothing answers, recover by hand as below.

Each run, except `-Check`, writes a full transcript to
`C:\Pinakes\logs\update-<timestamp>.log`. Send that to Justin along with:

```powershell
Get-Content C:\Pinakes\logs\web-stderr.log -Tail 60
```

**If rollback also failed, or there was none** — the site is down. Recover by
hand:

```powershell
Set-Location C:\Pinakes\app
git log --oneline -10                  # find the last known-good commit
git reset --hard <that-commit>
C:\Pinakes\venv\Scripts\python.exe -m pip install -r requirements.txt
Start-Service Pinakes-Web
Invoke-RestMethod http://127.0.0.1:8080/health
```

**If a schema migration corrupted the database** — restore the backup the script
took before the deploy. Put the code back on the previous version first (the
manual recovery above, up to but not including `Start-Service`), or the
migration runs again as soon as the service starts. Then:

```powershell
Stop-Service Pinakes-Web
Get-ChildItem C:\Pinakes\backups | Sort-Object LastWriteTime -Descending | Select-Object -First 5
Copy-Item C:\Pinakes\backups\articles-<timestamp>-pre-<sha>.db C:\Pinakes\data\articles.db -Force
Remove-Item C:\Pinakes\data\articles.db-wal, C:\Pinakes\data\articles.db-shm -ErrorAction SilentlyContinue
Start-Service Pinakes-Web
```

In the file name, `<sha>` is the commit that was being deployed, not the one
before it. Once the service is up, re-apply the author-redaction ledger: a
copy older than a redaction brings the name back. See
[Author name-removal requests](#author-name-removal-requests).

What the backups folder holds:

- Nightly, pre-deploy and manual (`Backup-Pinakes.ps1 -Label …`) backups of
  `articles.db` share one set of `articles-*.db` files. Every backup prunes
  that set to the newest `PINAKES_BACKUP_KEEP` (default 10), so a pre-deploy
  backup is gone after about ten nights. Copy it elsewhere if you may need it
  later.
- The pre-deploy backup copies `articles.db` only. `Backup-Pinakes.ps1` (the
  nightly task, or a manual run) also copies `usage.db` when it exists, as
  `usage-*.db`, kept to the same count.
- Every copy is on the same disk as the database. These backups cover a bad
  deploy or migration, not the loss of the server.

---

## Visitor IP addresses

The app has to know which visitor sent each request. The rate limiter, the IP
denylist, the one-vote-per-reader rule on community tags, and every IP in the
logs depend on it. Requests reach the app from IIS on `127.0.0.1`, so the
visitor's address has to come from a header. The setting that names that
header is `PINAKES_TRUSTED_IP_HEADER`, and on this server it must be
`CF-Connecting-IP`:

```powershell
[Environment]::SetEnvironmentVariable('PINAKES_TRUSTED_IP_HEADER','CF-Connecting-IP','Machine')
Restart-Service Pinakes-Web
```

**Not `X-Forwarded-For`.** Waitress 3.x deletes `X-Forwarded-For`,
`X-Forwarded-Host`, `X-Forwarded-Proto` and `Forwarded` before the app sees
them unless it is started with `--trusted-proxy`, so a setting of
`X-Forwarded-For` finds nothing. Until this was set, every visitor looked like
`127.0.0.1` and the whole site shared one rate-limit budget: 60 requests a
minute for everyone, and 20 for the citation and stats tools.

`CF-Connecting-IP` is safe to trust because Cloudflare refuses any request
that arrives already carrying one (HTTP 403 at the edge). The only way to
forge it is to skip Cloudflare and reach this server directly. Allowing only
[Cloudflare's address ranges](https://www.cloudflare.com/ips/) through to IIS
on 443 closes that gap. The app believes the header only when the connection
reaching Waitress comes from this machine or from a private-network address,
so keep port 8080 closed to everything but loopback. Better, have Waitress
listen on loopback only: `nssm get Pinakes-Web AppParameters` should show
`--host=127.0.0.1`. If it shows `--host=0.0.0.0`, first confirm that IIS
reaches the app over loopback (the `Socket peer` line from the check below
reads `127.0.0.1`), then set it again with only the host changed, and
restart:

```powershell
nssm get Pinakes-Web AppParameters
nssm set Pinakes-Web AppParameters "<the same line, with --host=127.0.0.1>"
Restart-Service Pinakes-Web
```

**Verify it from outside**, through the public hostname. On the server:

```powershell
& C:\Pinakes\app\deploy\windows\Test-PinakesClientIp.ps1
```

From another machine, run `.\deploy\windows\Test-PinakesClientIp.ps1` from the
root of a clone, after `$env:PINAKES_ADMIN_TOKEN = '<token>'` if you want the
last check. On the server it reads the Machine variable itself.

`Separate visitors get separate budgets` needs no token, but it can only
decide on a machine with IPv6, because it uses the IPv4 and IPv6 addresses as
two different visitors. There it FAILs while the setting is missing, and
PASSes once it is in place. Without IPv6 it reports INFO. A WARN means it
could not tell (the rate-limit window reset, or other traffic arrived
mid-test); rerun it. `Forged X-Forwarded-For is ignored` and `Forged
Fly-Client-IP is ignored` each send a request carrying a made-up header and
PASS when it is charged to the same budget as an honest one. A FAIL on the
`Fly-Client-IP` check means the server is running code from before 7554ce5
(2026-10-15), which believed that header everywhere, so any visitor could name
their own address; update the server. With the token, `App reports the visitor IP` compares
the app's answer with what Cloudflare saw. It
also prints the `PINAKES_TRUSTED_IP_HEADER` value the running service actually
has. If that reads `(unset)` after the restart, the service did not pick up
the machine variable. Set it on the service instead, keeping the variables
already there:

```powershell
nssm get Pinakes-Web AppEnvironmentExtra
nssm set Pinakes-Web AppEnvironmentExtra <the existing lines> PINAKES_TRUSTED_IP_HEADER=CF-Connecting-IP
Restart-Service Pinakes-Web
```

### Links in emails

The same check reports `Absolute URLs use the public host`. Waitress strips
`X-Forwarded-Host` and `X-Forwarded-Proto` as well, so the app cannot learn its
public address from the request: it sees IIS connecting to
`http://127.0.0.1:8080`. Every link that leaves the site — the name-removal and
alert confirmation links, the admin review notice, the ORCID callback address —
is built from `PINAKES_SITE_URL` instead. That is the variable the install
guide (§6) already has you set, so nothing new is needed. The check PASSes
once the running service has it.

A WARN there means the service does not have `PINAKES_SITE_URL`, and the
confirmation links, the admin review notice and the ORCID callback would point
at `127.0.0.1`. (The saved-search digest and the Atom feeds fall back to
`https://pinakes.wacclearinghouse.org` instead, so on the test host they would
point at production.) The script prints only the URL the app built. To see
the variable itself, ask the endpoint directly:

```powershell
Invoke-RestMethod https://pinakes.wacclearinghouse.org/api/admin/client-ip -Headers @{ Authorization = "Bearer $env:PINAKES_ADMIN_TOKEN" }
```

`site_url` shows what the service has; `request_host_url` shows the
request-built address, which reads `http://127.0.0.1:8080/` on this server and
is expected to. Set the variable at Machine scope, or on the service the same
way as `PINAKES_TRUSTED_IP_HEADER` above, and restart:

```powershell
[Environment]::SetEnvironmentVariable('PINAKES_SITE_URL','https://pinakes.wacclearinghouse.org','Machine')
Restart-Service Pinakes-Web
```

Use `https://testpinakes.wacclearinghouse.org` on the test host. The deploy
scripts read the same Machine variable for the public URL they check.

**Not `--trusted-proxy`.** Starting Waitress with `--trusted-proxy 127.0.0.1`
and `--trusted-proxy-headers` would let the forwarding headers through, but
ARR writes the visitor as `ip:port` in `X-Forwarded-For`, which Waitress may
reject as malformed. It would also make the links depend on every hop of the
chain forwarding the headers. A fixed setting cannot be steered by a forged
`X-Forwarded-Host`.

---

## Usage statistics

Pinakes can record which pages and tools are used and identify the
institutions visits come from. Recording is off until it is switched on
here. What it records and how it protects visitors is in
[usage-statistics.md](usage-statistics.md). Get the Clearinghouse's
agreement before turning it on, because the site sits on its domain.

**Prerequisite:** [Visitor IP addresses](#visitor-ip-addresses) must pass
first. Without `PINAKES_TRUSTED_IP_HEADER`, every visitor is recorded as the
proxy and nothing can be resolved.

**1. Turn recording on:**

```powershell
[Environment]::SetEnvironmentVariable('PINAKES_USAGE_TRACKING','1','Machine')
Restart-Service Pinakes-Web
```

**2. Download the reference data** (about 70 MB, no account needed). Do this
after step 1: while recording is off the endpoint answers "recording is off;
nothing downloaded" and the script still logs it as accepted.

```powershell
& .\deploy\windows\Invoke-PinakesAdmin.ps1 -Endpoint UsageData
```

It runs in the background for a minute or two. The status panel in step 4
lists each file once it is in place.

**3. Register the two jobs** (re-running the installer is safe):

```powershell
& .\deploy\windows\Install-PinakesTasks.ps1 -IncludeUpdateCheck
```

`Pinakes-UsageResolve` (daily 05:10) resolves the day's addresses and then
deletes them. `Pinakes-UsageData` (Sundays 05:40) refreshes the IP databases
when they are more than 25 days old. The nightly backup now copies
`usage.db` too, and clears raw addresses and visitor-id salts from the copy
before keeping it.

**4. Check it.** Open `https://pinakes.wacclearinghouse.org/admin/usage`
and enter the admin token. The status panel says whether recording is on,
whether the IP header is set, how many requests are waiting for the
resolver, and how old each data file is. After the first night, the
dashboard fills in.

To switch recording off, remove the variable and restart the service. Keep
the resolve task: it is what deletes any addresses still held.

**The optional form** at `/tell-us`, where readers can say how they use
Pinakes, has its own switch and works with or without recording:

```powershell
[Environment]::SetEnvironmentVariable('PINAKES_USAGE_SURVEY','1','Machine')
Restart-Service Pinakes-Web
```

Responses appear on `/admin/usage`. Nothing else needs scheduling; the
nightly resolve task matches the institutions people type.

---

## Author name-removal requests

The public request form ships **off** (`PINAKES_REDACTION_FORM_ENABLED`). Its
value is an email round-trip proving the requester controls the address they
typed, and that needs working SMTP. With SMTP unconfigured the route still
rendered, still wrote a row, and still told the author "submitted" while
`send_email` logged a warning and returned `False` — a name-removal request
accepted and dropped. `/about` therefore asks authors to email instead, and
`/redaction-request` redirects there.

So requests arrive in your inbox. Satisfy yourself the sender is plausibly the
author — writing from an institutional address is the usual signal — then apply
it on the server:

```powershell
cd C:\Pinakes\app
C:\Pinakes\venv\Scripts\python.exe redaction.py redact "Jane Q. Author" `
    --variant "J. Q. Author" --variant "Jane Author" --by jlewis
```

Repeat `--variant` for every spelling the name appears under. `--by` is
recorded in the audit trail. It prints the token that replaces the name, for
example `Redacted 'Jane Q. Author' -> Redacted Author 7f3a2c`.

`redaction.py` works on the database named by `DB_PATH`. Check that
`$env:DB_PATH` prints `C:\Pinakes\data\articles.db` first; if it prints
nothing, set it for the session (`$env:DB_PATH = 'C:\Pinakes\data\articles.db'`),
or the tool opens `C:\Pinakes\app\articles.db` instead, creating it if need be.

Check it took:

```powershell
C:\Pinakes\venv\Scripts\python.exe redaction.py list
```

Each line reads `<token>  <-  <name>`. To reverse one, pass its token, in
quotes because it contains spaces:

```powershell
C:\Pinakes\venv\Scripts\python.exe redaction.py unredact "Redacted Author 7f3a2c"
```

After any change, keep a copy of the ledger off this server.
`redaction.py export-ledger <file>` writes every redacted name in plain text,
so write it outside `C:\Pinakes`, send it to Justin over a secure channel, and
delete the local copy. That export is what puts a redaction back if the
database is ever restored from a copy older than it.

**The ledger is the durable part, not the redacted rows.** It lives in
`articles.db`, and new rows pass through it as they are written: article and
book inserts and OpenAlex enrichment swap a ledgered name for its token, so a
later fetch cannot quietly restore a name. A full re-sweep of every table runs
at the end of a deep refresh, or by hand with `redaction.py resweep`. The
weekly maintenance task does not run it; the scheduled endpoint skips that
step. A redaction applied by hand directly to the `articles` table would be
undone by the next fetch; always go through `redaction.py`.

Because the ledger is inside the database, restoring a backup puts back that
backup's ledger. After restoring from `C:\Pinakes\backups` (see
[When an update fails](#when-an-update-fails)), with the service running:

- if the backup might be older than a redaction, run
  `redaction.py import-ledger <export>` with the latest off-box export. It
  merges the export, then re-sweeps;
- otherwise run `redaction.py resweep`.

`restore_local.py`, for the encrypted offsite backups, re-sweeps on its own;
give it the same export with `--redaction-ledger <export>`.

Turning the form back on, once a deployment has SMTP, is one machine variable
plus a service restart:

```powershell
[Environment]::SetEnvironmentVariable('PINAKES_REDACTION_FORM_ENABLED','1','Machine')
Restart-Service Pinakes-Web
```

If `/redaction-request` still redirects to `/about` after the restart, the
service did not pick up the variable; set it on the service as shown under
[Visitor IP addresses](#visitor-ip-addresses).

The verification link it emails is built from `PINAKES_SITE_URL`. Before
turning the form on, run `Test-PinakesClientIp.ps1` with the admin token and
confirm `Absolute URLs use the public host` PASSes (see
[Links in emails](#links-in-emails)). For the ORCID button, register
`<PINAKES_SITE_URL>/redaction-request/orcid/callback` as the redirect URI in
the ORCID developer console — on production,
`https://pinakes.wacclearinghouse.org/redaction-request/orcid/callback`.

---

## Reference

### The scripts

All in `C:\Pinakes\app\deploy\windows\`. Every `.ps1` except
`Pinakes.Common.ps1` supports `Get-Help <script> -Full`.

| Script | Purpose |
|---|---|
| `Update-Pinakes.ps1` | Deploy from GitHub, with backup, verification, and rollback. **Part B.** |
| `Test-PinakesHealth.ps1` | Health check, run on the server. Its HTTP checks go to 127.0.0.1 unless you give `-BaseUrl <public URL>`. `-Remote` runs only the HTTPS checks, from anywhere. |
| `Test-PinakesClientIp.ps1` | Does the app see real visitor IPs? Run from anywhere. See [Visitor IP addresses](#visitor-ip-addresses). |
| `Invoke-PinakesAdmin.ps1` | Call one admin endpoint: `Fetch`, `DeepFetch` (full revalidation, slow), `Prewarm`, `Maintenance`, `Digests`, `Backup` (offsite S3/B2; needs the `PINAKES_BACKUP_*` secrets), `UsageResolve`, `UsageData`. |
| `Backup-Pinakes.ps1` | Take a database backup on demand. |
| `Install-PinakesTasks.ps1` | Register, list, or remove the scheduled jobs. |
| `Watch-PinakesHealth.ps1` | Health watchdog, run every 5 min by `Pinakes-HealthWatchdog`. `-DryRun` to try it. |
| `Set-PinakesService.ps1` | NSSM service settings: log files, rotation, waitress threads. `-Show` to inspect. |
| `Pinakes.Common.ps1` | Shared config and helpers. Dot-sourced by the others; not run directly. |
| `sqlite_backup.py` | Hot-copy helper used by the backup path. |

### Everyday commands

```powershell
# Health
& C:\Pinakes\app\deploy\windows\Test-PinakesHealth.ps1

# What version is live right now?
Invoke-RestMethod http://127.0.0.1:8080/health

# Has this server fallen behind GitHub?
& C:\Pinakes\app\deploy\windows\Update-Pinakes.ps1 -Check

# Fetch new articles now, without waiting for 03:23
& C:\Pinakes\app\deploy\windows\Invoke-PinakesAdmin.ps1 -Endpoint Fetch

# Back up before doing something risky
& C:\Pinakes\app\deploy\windows\Backup-Pinakes.ps1 -Label before-<whatever>

# Restart the app (the service is `Pinakes` on the Clearinghouse box; see PINAKES_SERVICE_NAME)
Restart-Service Pinakes -Force

# Job history
Get-Content C:\Pinakes\logs\admin-tasks.log -Tail 40

# Live application log (the service's stderr file)
Get-Content C:\Pinakes\logs\web-stderr.log -Wait -Tail 30

# Scheduled task status
& C:\Pinakes\app\deploy\windows\Install-PinakesTasks.ps1 -List
```

### Logs

| File | Contents |
|---|---|
| `C:\Pinakes\logs\web-stderr.log` | Application log: warnings, errors, tracebacks, startup and migration failures. INFO-level progress lines most likely don't appear under Waitress (see [A7](#a7-run-the-initial-fetch)) |
| `C:\Pinakes\logs\web-stdout.log` | Anything the app prints rather than logs; usually little |
| `C:\Pinakes\logs\admin-tasks.log` | Two or three lines per `Invoke-PinakesAdmin.ps1` call, scheduled or by hand, and one or two per `Backup-Pinakes.ps1` run. The update check writes nothing here. |
| `C:\Pinakes\logs\watchdog.log` | Every time the watchdog had to retry or restart |
| `C:\Pinakes\logs\hang-<timestamp>.txt` | Thread dump taken just before a watchdog restart |
| `C:\Pinakes\logs\update-<timestamp>.log` | Full transcript of each `Update-Pinakes.ps1` run, except `-Check` |

The two `web-*` files exist only once the service is told to write them.
Until 2026-10-17 the production service had no `AppStdout` or `AppStderr`, so
neither file existed; `Set-PinakesService.ps1` ([A6b](#a6b-service-logging-and-threads))
sets both. To see what the service actually writes to, run
`nssm get Pinakes AppStdout` and `nssm get Pinakes AppStderr`.
`Invoke-PinakesAdmin.ps1` says to check `web-stdout.log` for progress; look in
`web-stderr.log` for errors, and at the article count in `/health/deep` for
progress.

### What Justin can do without server access

Given the admin token, from any machine:

```powershell
# Health and version, from outside
Invoke-RestMethod https://pinakes.wacclearinghouse.org/health

# Full external health check (clone the repo, then)
& .\deploy\windows\Test-PinakesHealth.ps1 -Remote

# Does the app see real visitor IPs?
& .\deploy\windows\Test-PinakesClientIp.ps1

# Trigger a fetch remotely
$h = @{ Authorization = "Bearer <token>" }
Invoke-RestMethod -Uri https://pinakes.wacclearinghouse.org/fetch -Method POST -Headers $h
```

A `409` from that last one means a fetch is already running — that is the guard
against two writers on one SQLite file, not an error. A `429` means the limit
of six authenticated `/fetch` calls an hour from one address was reached; wait
and try again.

What still needs someone with server access: deploying code, restarting the
service, reading logs, and anything to do with IIS.

### Troubleshooting

**`/health` reports an old version after an update.** The working tree moved but
the service did not restart. `Restart-Service Pinakes-Web`. The update script
checks for exactly this and will not report success without it.

**The site shows stale or empty data, but `Test-PinakesHealth.ps1` passes.**
IIS/ARR is serving from its cache. The health script goes straight to the app
on 127.0.0.1 by default, and adds a cache-buster to every request even with
`-BaseUrl`, so it never sees ARR's copies; that is why it disagrees with a
browser. Clear the ARR cache in IIS Manager, or wait out the hour. If the
check reports `Cache-Control: public`, the server is running a build older
than this fix — deploy.

**`403` from `Invoke-PinakesAdmin.ps1`.** The machine-scope
`PINAKES_ADMIN_TOKEN` does not match what the service is running with. Set them
to the same value and `Restart-Service Pinakes-Web`. If the service has its own
copy in `nssm get Pinakes-Web AppEnvironmentExtra`, change that one too.

**`429` from `Invoke-PinakesAdmin.ps1 -Endpoint Fetch`.** More than six
authenticated `/fetch` calls in an hour. Wait and run it again.

**`503` from `Invoke-PinakesAdmin.ps1`.** The service has no token in its
environment at all. See A3.

**A scheduled task shows a non-zero last result.** Read
`C:\Pinakes\logs\admin-tasks.log` for the reason, then run the same command by
hand to see it fail in front of you. `Pinakes-UpdateCheck` logs nothing there;
run `Update-Pinakes.ps1 -Check` by hand.

**A scheduled job was accepted but did nothing useful.** A green task means
the endpoint accepted the job; the work runs in the background inside the
service. Its progress and errors are in `web-stderr.log`.

**`database is locked`.** Confirm `DB_PATH` is on a local NTFS volume and not a
network share, and that no leftover `Pinakes-Scheduler` process is running as a
second writer. More in
[WINDOWS-SERVER-INSTALL.md](WINDOWS-SERVER-INSTALL.md#troubleshooting).

### When the site is down (502)

Cloudflare's plain `error code: 502` (or IIS's "Web server received an invalid
response while acting as a gateway") means IIS is up but waitress on 8080 did
not answer. On 2026-10-17 the waitress process was **alive and listening but
hung**: the service said Running, `Start-Service` did nothing, and NSSM never
restarted it because the process never exited. `Pinakes-HealthWatchdog` now
catches that within about 8 minutes. To handle it by hand:

```powershell
# 1. Is it dead or hung? Dead = nothing on 8080. Hung = listening but this times out.
Get-Service Pinakes; netstat -ano | findstr LISTENING | findstr ":8080"
Invoke-WebRequest http://127.0.0.1:8080/health -UseBasicParsing -TimeoutSec 30

# 2. If hung, take a thread dump BEFORE restarting - the restart destroys the evidence.
#    The PID is the python.exe running waitress-serve whose parent is also python.exe.
C:\Pinakes\venv\Scripts\py-spy.exe dump --pid <PID> > C:\Pinakes\logs\hang-manual.txt

# 3. Restart. -Force matters.
Restart-Service Pinakes -Force
```

Install py-spy once (`C:\Pinakes\venv\Scripts\pip.exe install py-spy`) and the
watchdog takes the dump for you. Send any `hang-*.txt` to Justin.

To keep the watchdog from restarting the app during planned work, create
`C:\Pinakes\watchdog.pause` and delete it afterwards. It also stands aside
during `Update-Pinakes.ps1` and while the service is Stopped.

For an alert that does not depend on the server itself, point an external
uptime monitor (UptimeRobot, Better Stack) at
`https://pinakes.wacclearinghouse.org/health`. The watchdog also writes event
1001 (restarted) / 1002 (restart failed) from source `Pinakes` to the Windows
Application log.

**502 Bad Gateway from IIS, but `http://127.0.0.1:8080` works.** The app is
fine; the reverse proxy is not. See the ARR section of the install guide.

---

*Runbook version 2026-10-17. Companion to
[WINDOWS-SERVER-INSTALL.md](WINDOWS-SERVER-INSTALL.md). Scripts in `deploy/windows/`.*
