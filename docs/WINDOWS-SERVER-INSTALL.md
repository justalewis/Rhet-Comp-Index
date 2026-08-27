# Installing Pinakes (Rhet-Comp Index) on Windows Server

**Audience:** A Windows Server administrator who wants to run this application on their own infrastructure instead of the Linux/Fly.io setup the repository ships with.

**What you are installing:** A self-contained Python 3.12 Flask web application that pulls article metadata from public APIs (CrossRef, OpenAlex, journal RSS feeds) into a local SQLite database and serves a browsable index. It has **no external database server, no message broker, and no paid API keys**. The only persistent state is a single `.db` file.

**Target platform:** Windows Server 2019, 2022, or 2025 (Standard or Datacenter). It will also run on Windows 10/11 Pro if you just want to kick the tires, but this guide assumes Server.

**Estimated time:** 45–90 minutes for a first install, most of which is the initial article fetch.

> **Already installed? You want [WINDOWS-SERVER-RUNBOOK.md](WINDOWS-SERVER-RUNBOOK.md) instead.**
> This document covers a first install. The runbook covers the two things you
> do afterwards: bringing a broken deployment back to healthy, and pushing an
> update from GitHub. Both are scripted in `deploy/windows/`.

---

## 1. What differs from the Linux setup

The repository was built for a Linux container deployed to Fly.io. Three pieces of that setup do not translate to Windows and require local substitutions:

| Linux/Fly.io | Windows Server | Why |
|---|---|---|
| `gunicorn` WSGI server (in `requirements.txt`) | `waitress` | Gunicorn uses the Unix `fork()` syscall and does not run on Windows. Waitress is the pure-Python, Windows-compatible equivalent. |
| Fly.io persistent volume at `/data/articles.db` | A local NTFS folder, e.g. `C:\Pinakes\data\articles.db` | You set the path with the `DB_PATH` environment variable. |
| Process supervised by Fly.io runtime | Windows Service via **NSSM** | Keeps the app running on reboot without requiring a logged-in console session. |
| HTTPS terminated by Fly's edge | IIS reverse proxy *or* Caddy for Windows | Windows has no built-in TLS-terminating proxy for arbitrary localhost apps. |

Everything else — the Python code, the SQLite schema, the API integrations — runs unchanged on Windows.

One more difference, added after this guide was first written: the repository no
longer contains `scheduler.py`. Recurring jobs are now triggered by POSTing to
admin endpoints (`/fetch`, `/api/admin/*`). On Fly that is done by GitHub
Actions against `pinakes.xyz`, which does nothing for this server, so this
server drives its own jobs with Windows scheduled tasks. See §8.3.

---

## 2. Prerequisites

Run all of the following in an **elevated PowerShell** window (Right-click Start → Windows PowerShell (Admin), or "Terminal (Admin)" on Server 2025).

Confirm you have administrator rights and an internet connection:

```powershell
whoami /groups | Select-String "S-1-5-32-544"   # Administrators SID; non-empty output = admin
Test-NetConnection api.crossref.org -Port 443   # should return TcpTestSucceeded : True
Test-NetConnection api.openalex.org -Port 443
```

Allow script execution for the current user (required for venv activation):

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Required outbound network access (open in your egress firewall if it is restrictive):

- `api.crossref.org:443`
- `api.openalex.org:443`
- Individual journal publisher domains for RSS (Taylor & Francis, SAGE, Wiley, etc.) — all over HTTPS 443.

No inbound ports are needed from the internet unless you want external users to reach the site (see §9).

---

## 3. Install Python 3.12 and Git

Using **winget** (present by default on Server 2025 and installable from the Microsoft Store on 2022):

```powershell
winget install --id Python.Python.3.12 --source winget --accept-source-agreements --accept-package-agreements
winget install --id Git.Git             --source winget --accept-source-agreements --accept-package-agreements
```

If winget is not available, download the installers directly:

- Python 3.12 → https://www.python.org/downloads/windows/ (choose **Windows installer (64-bit)**; on the first screen check **Add python.exe to PATH**)
- Git for Windows → https://git-scm.com/download/win

Close and reopen PowerShell so the new `PATH` takes effect, then verify:

```powershell
python --version   # expect: Python 3.12.x
git --version
```

> The Dockerfile pins `python:3.12-slim`. Python 3.11 and 3.13 will probably work but are untested — stay on 3.12 if you want to match the reference environment.

---

## 4. Create the application directory and clone the repo

Pick a path outside `C:\Program Files\` (which is protected) and outside a user profile (so the service account can read it):

```powershell
New-Item -ItemType Directory -Path "C:\Pinakes"        | Out-Null
New-Item -ItemType Directory -Path "C:\Pinakes\data"   | Out-Null
New-Item -ItemType Directory -Path "C:\Pinakes\logs"   | Out-Null

Set-Location C:\Pinakes
git clone https://github.com/justalewis/Rhet-Comp-Index.git app
Set-Location C:\Pinakes\app
```

You should now have:

```
C:\Pinakes\
├── app\           (cloned repository)
├── data\          (will hold articles.db)
└── logs\          (service stdout/stderr)
```

---

## 5. Create a virtual environment and install dependencies

```powershell
python -m venv C:\Pinakes\venv
C:\Pinakes\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r C:\Pinakes\app\requirements.txt
pip install waitress        # Windows-compatible WSGI server (replaces gunicorn)
```

`pip install` may print a warning that gunicorn's `fcntl` import failed. That is expected on Windows and harmless — gunicorn is in the requirements file but will never be invoked here.

Verify the install:

```powershell
python -c "import flask, waitress, apscheduler, lxml, networkx, bs4, feedparser; print('ok')"
```

---

## 6. Configure environment variables

The application reads three environment variables:

| Variable | Purpose | Recommended value |
|---|---|---|
| `DB_PATH` | Absolute path to the SQLite file | `C:\Pinakes\data\articles.db` |
| `PORT` | Port the web server binds on | `8080` |
| `FLASK_ENV` | Set to `production` to disable debug mode | `production` |

Set them at the **Machine** scope so they are visible to the Windows Service account:

```powershell
[Environment]::SetEnvironmentVariable("DB_PATH",   "C:\Pinakes\data\articles.db", "Machine")
[Environment]::SetEnvironmentVariable("PORT",      "8080",                         "Machine")
[Environment]::SetEnvironmentVariable("FLASK_ENV", "production",                   "Machine")
```

Close and reopen PowerShell to pick them up in your current session, then confirm:

```powershell
$env:DB_PATH; $env:PORT; $env:FLASK_ENV
```

> **Do not put the database on a network share (SMB/UNC path).** SQLite's file locking is unreliable over SMB and will eventually corrupt the database. Keep `DB_PATH` on a local NTFS volume. If you need off-box backup, back up the file from the local volume using Robocopy or Windows Server Backup — see §12.

---

## 7. Seed the database with the initial article fetch

This is the long-running one-time step. CrossRef rate-limits aggressive clients, so the first sweep of all 14 journals takes several minutes:

```powershell
Set-Location C:\Pinakes\app
C:\Pinakes\venv\Scripts\Activate.ps1
python fetcher.py
```

Expect output like `[Journal Name] +N articles` for each of the 14 journals. When it finishes, the file `C:\Pinakes\data\articles.db` will exist and be a few hundred MB.

Smoke-test the web server **interactively** before turning it into a service:

```powershell
waitress-serve --host=127.0.0.1 --port=8080 app:app
```

Open a browser on the server to http://127.0.0.1:8080 — you should see the article index. Press `Ctrl+C` in the PowerShell window to stop it.

---

## 8. Run the web app as a Windows Service (NSSM), and register the jobs

You need **one** service and **a set of scheduled tasks**:

1. **Pinakes-Web** — the Flask app behind waitress (serves HTTP requests). A
   Windows Service, so it starts on boot and restarts on crash.
2. **Scheduled tasks** — the recurring fetch, backup, and maintenance jobs.

> **If you are following an older copy of this guide, note the change.** There
> used to be a second service, `Pinakes-Scheduler`, running `scheduler.py`. That
> file no longer exists — it was removed in commit `fa25cae`, which replaced the
> standalone scheduler with admin endpoints triggered on a schedule. A
> `Pinakes-Scheduler` service defined against it will fail to start. If one
> exists on this machine, remove it: `nssm remove Pinakes-Scheduler confirm`.

The simplest, battle-tested tool for running the web app as a service on Windows
is **NSSM** (the Non-Sucking Service Manager).

### 8.1 Install NSSM

```powershell
winget install --id NSSM.NSSM --accept-source-agreements --accept-package-agreements
```

Or download from https://nssm.cc/download, unzip, and copy `nssm.exe` (the `win64` build) to `C:\Windows\System32\`.

### 8.2 Create the web service

```powershell
nssm install Pinakes-Web "C:\Pinakes\venv\Scripts\waitress-serve.exe" "--host=0.0.0.0 --port=8080 app:app"
nssm set Pinakes-Web AppDirectory    "C:\Pinakes\app"
nssm set Pinakes-Web DisplayName     "Pinakes (Rhet-Comp Index) — Web"
nssm set Pinakes-Web Description     "Flask/waitress web server for the Pinakes article index."
nssm set Pinakes-Web Start           SERVICE_AUTO_START
nssm set Pinakes-Web AppStdout       "C:\Pinakes\logs\web-stdout.log"
nssm set Pinakes-Web AppStderr       "C:\Pinakes\logs\web-stderr.log"
nssm set Pinakes-Web AppRotateFiles  1
nssm set Pinakes-Web AppRotateBytes  10485760        # rotate at 10 MB
nssm set Pinakes-Web AppEnvironmentExtra "DB_PATH=C:\Pinakes\data\articles.db" "PORT=8080" "FLASK_ENV=production"

Start-Service Pinakes-Web
Get-Service  Pinakes-Web
```

### 8.3 Register the scheduled jobs

The admin token must be set at Machine scope first — the tasks run as SYSTEM and
read it from there, so it never appears in a task definition:

```powershell
[Environment]::SetEnvironmentVariable("PINAKES_ADMIN_TOKEN", "<your-token>", "Machine")
Restart-Service Pinakes-Web
```

Then register the tasks:

```powershell
& C:\Pinakes\app\deploy\windows\Install-PinakesTasks.ps1 -IncludeUpdateCheck
```

That creates:

| Task | When | What it does |
|---|---|---|
| `Pinakes-NightlyBackup` | 02:00 daily | Local SQLite backup to `C:\Pinakes\backups` |
| `Pinakes-DailyFetch` | 03:23 daily | `POST /fetch` — incremental CrossRef/RSS fetch |
| `Pinakes-Prewarm` | 04:15 daily | `POST /api/admin/prewarm` — rebuild cached aggregates |
| `Pinakes-WeeklyMaintenance` | 04:45 Sundays | `POST /api/admin/run-maintenance` |
| `Pinakes-UpdateCheck` | 08:00 daily | Reports whether the server has fallen behind GitHub |

Add `-IncludeDigests` for the weekly saved-search emails, but only once
`PINAKES_ALERTS_ENABLED=1` and the sending domain is verified.

Check them any time with `Install-PinakesTasks.ps1 -List`, and read their
history in `C:\Pinakes\logs\admin-tasks.log`.

### 8.4 Confirm everything is running

```powershell
Get-Service Pinakes-Web | Format-Table Name, Status, StartType
& C:\Pinakes\app\deploy\windows\Install-PinakesTasks.ps1 -List
Get-Content C:\Pinakes\logs\web-stdout.log -Tail 20
```

Browse to http://127.0.0.1:8080 — the page should load.

---

## 9. Expose the site to the network

How far you go here depends on who needs access.

### 9.1 Internal LAN only (simplest)

Open port 8080 on the Windows firewall to your internal subnet:

```powershell
New-NetFirewallRule -DisplayName "Pinakes Web (8080, internal)" `
                    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8080 `
                    -RemoteAddress 10.0.0.0/8,192.168.0.0/16,172.16.0.0/12
```

LAN users can reach the site at `http://<server-hostname>:8080`.

### 9.2 Public-facing with HTTPS (recommended for anything external)

Do **not** expose waitress directly to the internet. Put a reverse proxy in front of it that terminates TLS. Two low-friction options:

**Option A — IIS with URL Rewrite + ARR (fully Microsoft-stack)**

1. `Install-WindowsFeature -Name Web-Server -IncludeManagementTools`
2. Install the IIS extensions: **URL Rewrite 2.1** and **Application Request Routing 3.0** from https://www.iis.net/downloads.
3. In IIS Manager, at the server level, enable **Application Request Routing Cache → Server Proxy Settings → Enable proxy**.
4. Create a site bound to `https://<your-fqdn>` with a certificate (Let's Encrypt via `win-acme`, or your organization's PKI).
5. Add a URL Rewrite rule: inbound pattern `(.*)`, action type **Rewrite**, rewrite URL `http://127.0.0.1:8080/{R:1}`.
6. Firewall: allow 443 inbound; close 8080 to anything but `127.0.0.1`.

**Option B — Caddy for Windows (simpler, automatic TLS)**

Caddy handles Let's Encrypt automatically:

```powershell
winget install --id CaddyServer.Caddy --accept-source-agreements --accept-package-agreements
```

Create `C:\Pinakes\Caddyfile`:

```caddy
pinakes.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

Then register Caddy itself as a service (it ships with a `caddy run` and service install command — see https://caddyserver.com/docs/running#windows-service).

Open 443 and 80 (Caddy needs 80 for the ACME HTTP-01 challenge):

```powershell
New-NetFirewallRule -DisplayName "HTTP 80"  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 80
New-NetFirewallRule -DisplayName "HTTPS 443" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 443
```

> If you are using IIS on the same box, you cannot also bind Caddy to 80/443. Pick one.

---

## 10. Verify the install

Run these checks end-to-end:

One command checks the service, the app, the database, the reverse proxy, and
whether the code matches GitHub:

```powershell
& C:\Pinakes\app\deploy\windows\Test-PinakesHealth.ps1
```

Every line prints PASS, FAIL, or WARN, and the script exits non-zero if anything
FAILed. A fresh install should show one warning — `Index contains articles` will
report 0 until the first fetch completes.

To check from a different machine, over HTTPS only:

```powershell
& .\Test-PinakesHealth.ps1 -Remote
```

If you would rather check by hand:

```powershell
Get-Service Pinakes-Web | Format-Table Name, Status
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8080/health/ready
Get-Item C:\Pinakes\data\articles.db | Select-Object FullName, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}
Get-Content C:\Pinakes\logs\web-stdout.log -Tail 30
```

You should see `Fetch complete — N total new articles` in the web log within a
few minutes of the fetch starting.

---

## 11. Updating to a new release

**Use the update script.** It backs up the database, deploys, verifies that the
running service reports the new commit, and rolls back automatically if it does
not:

```powershell
& C:\Pinakes\app\deploy\windows\Update-Pinakes.ps1 -Check    # see what would change
& C:\Pinakes\app\deploy\windows\Update-Pinakes.ps1           # deploy it
```

Full procedure, including what to do when it fails:
[WINDOWS-SERVER-RUNBOOK.md](WINDOWS-SERVER-RUNBOOK.md), Part B.

The database migrates itself — `db/core.py` applies schema migrations on
startup — so there is no separate migration step. But that is also why the
script takes a backup first, and why you should not skip it: a migration that
fails halfway leaves a database the previous version may not be able to read.

<details>
<summary>Doing it by hand, if the script is unavailable</summary>

```powershell
Stop-Service Pinakes-Web

Set-Location C:\Pinakes\app
git fetch origin
git reset --hard origin/main

C:\Pinakes\venv\Scripts\Activate.ps1
pip install -r requirements.txt          # in case deps changed

Start-Service Pinakes-Web

# Confirm the service is running the new code, not just running.
Invoke-RestMethod http://127.0.0.1:8080/health   # .version must equal the new short SHA
Get-Content C:\Pinakes\logs\web-stderr.log -Tail 30
```

`/health` reports the version from `git rev-parse --short HEAD`, read once when
the process starts. If it still shows the old SHA, the service did not actually
restart.

</details>

---

## 12. Backups

SQLite is a single file, so backup is trivial — **but** you must do it safely because the app is writing to it concurrently. Do not copy the `.db` file while the service is running; use the SQLite online backup API:

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
C:\Pinakes\venv\Scripts\python.exe -c "import sqlite3, os; src=sqlite3.connect(os.environ['DB_PATH']); dst=sqlite3.connect(r'C:\Pinakes\backups\articles-$stamp.db'); src.backup(dst); src.close(); dst.close()"
```

Wire that into a Task Scheduler job (daily, 3 AM) or your existing Windows Server Backup policy. Retain at least 14 daily snapshots before a schema change lands.

The `.db-wal` and `.db-shm` sidecar files are part of WAL mode — the online backup above captures their content into a single clean file, so you do not need to copy them separately.

---

## 13. Uninstall

```powershell
& C:\Pinakes\app\deploy\windows\Install-PinakesTasks.ps1 -Remove

Stop-Service Pinakes-Web
nssm remove Pinakes-Web confirm
nssm remove Pinakes-Scheduler confirm     # only if an old one still exists

Remove-NetFirewallRule -DisplayName "Pinakes Web (8080, internal)"

[Environment]::SetEnvironmentVariable("DB_PATH",   $null, "Machine")
[Environment]::SetEnvironmentVariable("PORT",      $null, "Machine")
[Environment]::SetEnvironmentVariable("FLASK_ENV", $null, "Machine")

Remove-Item -Recurse -Force C:\Pinakes     # deletes app, venv, data, logs — be sure first
```

---

## FAQ

**Q: Do I need Docker Desktop or WSL?**
No. This guide installs Python natively on Windows. Docker Desktop is not required and is not recommended on a production Server — it adds a large dependency and a Hyper-V requirement just to run a single Python process.

**Q: Why `waitress` instead of `gunicorn`?**
Gunicorn's worker model relies on the Unix `fork()` system call, which does not exist on Windows. Waitress is a production-quality pure-Python WSGI server maintained by the Pylons project and is the standard recommendation for running Flask on Windows. Performance is comparable for this workload (the SQLite single-writer design means more workers would not help).

**Q: Do I need to remove gunicorn from `requirements.txt`?**
No. It installs cleanly — it just can't *run* on Windows, and you are not running it. Leaving it alone keeps your working tree in sync with upstream.

**Q: Why is there no scheduler service any more?**
There used to be one. It ran `scheduler.py`, which was removed in commit
`fa25cae`. The reason was a Fly.io constraint — volumes are single-attach, so a
scheduler on its own machine had no way to share the database with the web app.
The jobs became admin endpoints that something external POSTs to on a schedule.
On Fly that something is GitHub Actions; here it is Windows Task Scheduler
(§8.3). The upside on Windows is that jobs no longer hold a second writer open
against the same SQLite file.

**Q: How do I add my email to the API User-Agent?**
Both CrossRef and OpenAlex give better service to clients that identify themselves. Edit the `User-Agent` constants in `fetcher.py`, `book_fetcher.py`, and `enrich_openalex.py` to include `mailto:you@yourdomain.example`. Restart `Pinakes-Web` after editing.

Note that local edits like this will block `Update-Pinakes.ps1`, which refuses to
discard uncommitted changes. Commit them to a fork, or re-apply them after each
update with `-Force`.

**Q: Can I run this on a Windows Server Core install (no GUI)?**
Yes. Everything in this guide is command-line. Use IIS (ServerManager cmdlets) or Caddy (which needs no GUI) for the reverse proxy. You cannot use the IIS Manager MMC on Core — configure IIS from another machine with Remote Management, or use `appcmd.exe` locally.

**Q: Does this application store personal data?**
No. It stores bibliographic metadata (titles, authors, DOIs, abstracts) fetched from public APIs. There are no user accounts, no analytics, and no request logging beyond standard waitress access output.

**Q: How much disk and RAM will it use?**
The SQLite database is ~1–3 GB after full seeding and the weekly OpenAlex enrichment. RAM usage is ~200–400 MB steady-state across both services. A 2-vCPU / 2-GB VM is plenty.

**Q: Can multiple people use the site at once?**
Yes. Waitress serves concurrent readers without issue, and SQLite in WAL mode (which `db.py` sets by default) allows readers during a writer. The bottleneck is the single writer, which only matters during the scheduled fetch.

**Q: Is there an admin UI or a login screen?**
No. This is a read-only public-facing index. There is a **Refresh from CrossRef** button in the sidebar that triggers an incremental fetch, which is the only write operation exposed by the UI. If you need to restrict that, put the site behind an IIS Basic Auth rule or a front-door authentication proxy.

**Q: What happens if CrossRef or OpenAlex is down when a fetch runs?**
Each source is wrapped in its own try/except — a failure in one source is logged
and the others still run. The next scheduled run (24 h later) picks up anything
that was missed. A fetch that is still running when the next one fires is not
doubled up: `POST /fetch` returns 409 and the task logs it as a skip, which is
the guard against two writers on one SQLite file.

**Q: Can I change the fetch schedule?**
Yes — it is a Windows scheduled task now, so either edit the trigger times in
`deploy/windows/Install-PinakesTasks.ps1` and re-run it, or adjust the task
directly:

```powershell
Set-ScheduledTask -TaskName Pinakes-DailyFetch `
    -Trigger (New-ScheduledTaskTrigger -Daily -At '05:00')
```

---

## Troubleshooting

### The `Pinakes-Web` service starts and immediately stops

Check `C:\Pinakes\logs\web-stderr.log`. The usual suspects:

- **`ModuleNotFoundError`** — you activated the wrong venv or installed dependencies into the system Python. Re-run `pip install -r requirements.txt` with `C:\Pinakes\venv\Scripts\Activate.ps1` active, and make sure NSSM points at `C:\Pinakes\venv\Scripts\waitress-serve.exe`, not a system-wide one.
- **`OSError: [WinError 10048] Only one usage of each socket address...`** — port 8080 is already bound. Find the offender with `Get-NetTCPConnection -LocalPort 8080` and either stop it or change `PORT` for Pinakes.
- **`sqlite3.OperationalError: unable to open database file`** — the service account cannot write to `C:\Pinakes\data\`. Fix permissions: `icacls C:\Pinakes\data /grant "NT AUTHORITY\LocalService:(OI)(CI)M"` (or whichever account NSSM runs as — default is `LocalSystem`, which can write anywhere local).

### `pip install lxml` fails with a compiler error

Very rare on Python 3.12 because prebuilt wheels exist for Windows x64. If it happens, you are probably on an unusual CPU architecture (ARM64) or a 32-bit Python. Install 64-bit Python 3.12 from python.org and retry. As a last resort: `pip install --only-binary=:all: lxml`.

### The **Refresh from CrossRef** button returns instantly but no new articles appear

That is correct behavior when there are no new articles since the last fetch —
the fetch runs in a background thread and the endpoint answers immediately.
Check `C:\Pinakes\logs\web-stdout.log` for the most recent `Fetch complete` line,
and `C:\Pinakes\logs\admin-tasks.log` for the scheduled runs.

If the fetch is failing rather than finding nothing, look for CrossRef
`400 sort-criteria-incompatible-with-cursor` in the log. That means the server is
running a build from before commit `138f4cd`: CrossRef stopped accepting a sort
order combined with cursor paging, so every deep page fails and no articles are
written. Update the server (§11).

### Database reports "database is locked"

This should be rare because `db.py` sets WAL mode and a 10-second busy timeout. If it recurs:

1. Confirm `DB_PATH` is on a local NTFS volume, not SMB/UNC. **This is the single most common cause.**
2. Confirm only one `Pinakes-Web` is running, and that no stale `Pinakes-Scheduler` survives from an older install: `Get-Process python, waitress-serve -ErrorAction SilentlyContinue`.
3. Check for antivirus / EDR scanning the `.db` file mid-write. Add `C:\Pinakes\data\*.db*` to the exclusions list in Defender: `Add-MpPreference -ExclusionPath "C:\Pinakes\data"`.

### Log shows `CrossRef fetch failed: HTTPSConnectionPool(...): Max retries exceeded`

Outbound 443 is being blocked or rate-limited. Confirm with `Test-NetConnection api.crossref.org -Port 443`. If the server is behind an outbound proxy, set `HTTPS_PROXY` in the service environment:

```powershell
nssm set Pinakes-Web AppEnvironmentExtra "DB_PATH=C:\Pinakes\data\articles.db" "HTTPS_PROXY=http://proxy.corp.example:8080"
Restart-Service Pinakes-Web
```

### Browser shows **502 Bad Gateway** from IIS but http://127.0.0.1:8080 works locally

ARR is not proxying correctly. Check:

1. ARR proxy is enabled at the **server** level in IIS Manager, not just the site.
2. The rewrite rule's action URL is `http://127.0.0.1:8080/{R:1}` — note `http`, not `https`, and no trailing slash before `{R:1}`.
3. The IIS service account can resolve `127.0.0.1`. Rare, but some hardened images break loopback.

### The articles.db file keeps growing indefinitely

Expected up to ~2–3 GB after full enrichment. If it grows much beyond that, the WAL file (`articles.db-wal`) may not be checkpointing — stop both services and run:

```powershell
C:\Pinakes\venv\Scripts\python.exe -c "import sqlite3,os; c=sqlite3.connect(os.environ['DB_PATH']); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"
```

### I want to rebuild the database from scratch

Take a backup first — this is not reversible.

```powershell
& C:\Pinakes\app\deploy\windows\Backup-Pinakes.ps1 -Label before-rebuild

Stop-Service Pinakes-Web
Remove-Item C:\Pinakes\data\articles.db*     # removes .db, .db-wal, .db-shm
Start-Service Pinakes-Web                    # startup recreates an empty schema

# Reseed. This takes 30-90 minutes for the full corpus.
& C:\Pinakes\app\deploy\windows\Invoke-PinakesAdmin.ps1 -Endpoint Fetch
Get-Content C:\Pinakes\logs\web-stdout.log -Wait -Tail 20
```

### How do I get a shell as the service account for debugging?

```powershell
psexec -s -i C:\Windows\System32\cmd.exe     # opens a cmd.exe running as LocalSystem
```

`psexec` is part of Microsoft Sysinternals. Use this to reproduce a permissions problem exactly as the service sees it.

---

## Appendix: one-shot install script

For a repeatable install (lab rebuilds, staging VMs), the block below is the minimum set of commands from §3–§8 collapsed into a single PowerShell script. Review it, adapt the paths, and run as Administrator.

```powershell
# install-pinakes.ps1 — run elevated
$ErrorActionPreference = "Stop"

$Root = "C:\Pinakes"
$Port = "8080"

New-Item -ItemType Directory -Force -Path "$Root\data","$Root\logs" | Out-Null

winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
winget install --id Git.Git             --silent --accept-source-agreements --accept-package-agreements
winget install --id NSSM.NSSM           --silent --accept-source-agreements --accept-package-agreements

# Refresh PATH in current session
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")

git clone https://github.com/justalewis/Rhet-Comp-Index.git "$Root\app"
python -m venv "$Root\venv"
& "$Root\venv\Scripts\python.exe" -m pip install --upgrade pip
& "$Root\venv\Scripts\pip.exe" install -r "$Root\app\requirements.txt"
& "$Root\venv\Scripts\pip.exe" install waitress

[Environment]::SetEnvironmentVariable("DB_PATH",   "$Root\data\articles.db", "Machine")
[Environment]::SetEnvironmentVariable("PORT",      $Port,                    "Machine")
[Environment]::SetEnvironmentVariable("FLASK_ENV", "production",             "Machine")

# Initial seed (blocking, several minutes)
& "$Root\venv\Scripts\python.exe" "$Root\app\fetcher.py"

nssm install Pinakes-Web "$Root\venv\Scripts\waitress-serve.exe" "--host=0.0.0.0 --port=$Port app:app"
nssm set    Pinakes-Web AppDirectory "$Root\app"
nssm set    Pinakes-Web AppStdout    "$Root\logs\web-stdout.log"
nssm set    Pinakes-Web AppStderr    "$Root\logs\web-stderr.log"
nssm set    Pinakes-Web AppRotateFiles 1
nssm set    Pinakes-Web AppRotateBytes 10485760
nssm set    Pinakes-Web Start SERVICE_AUTO_START

Start-Service Pinakes-Web

# Recurring jobs. Requires PINAKES_ADMIN_TOKEN at Machine scope first.
& "$Root\app\deploy\windows\Install-PinakesTasks.ps1" -IncludeUpdateCheck

New-NetFirewallRule -DisplayName "Pinakes Web ($Port, internal)" `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
    -RemoteAddress 10.0.0.0/8,192.168.0.0/16,172.16.0.0/12

Write-Host "Install complete. Browse to http://localhost:$Port" -ForegroundColor Green
```

---

*Document version: 2026-08-27. Tracks Rhet-Comp-Index `main` branch (Python 3.12,
schema v15, requirements: flask/flask-compress/requests/feedparser/bs4/lxml/
apscheduler/gunicorn/networkx). Revised to drop the `Pinakes-Scheduler` service,
which was defined against `scheduler.py` — a file removed from the repository in
commit `fa25cae`. Recurring jobs now run as Windows scheduled tasks (§8.3), and
updates run through `deploy/windows/Update-Pinakes.ps1` (§11).*
