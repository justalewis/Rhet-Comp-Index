# Installing Pinakes (Rhet-Comp Index) on Windows Server

**Audience:** A Windows Server administrator setting up a server like production. Since 2026-09-19 production has been https://pinakes.wacclearinghouse.org, on a Windows Server at the WAC Clearinghouse: Cloudflare in front of IIS/ARR, which proxies to Waitress on `127.0.0.1:8080`. The test host, https://testpinakes.wacclearinghouse.org, is set up the same way.

**What you are installing:** A self-contained Python 3.12 Flask web application that pulls article metadata from public sources (CrossRef, OpenAlex, journal RSS feeds, publisher sites) into a local SQLite database and serves a browsable index. It has **no external database server, no message broker, and no paid API keys**. Its persistent state is the SQLite database (`articles.db`); for usage statistics, `usage.db` and a `usage-data` folder beside it; and the backups the scripts keep in `C:\Pinakes\backups`.

**Target platform:** Windows Server 2019, 2022, or 2025 (Standard or Datacenter). It will also run on Windows 10/11 Pro if you just want to kick the tires, but this guide assumes Server.

**Estimated time:** 45–90 minutes for a first install, most of which is the initial article fetch.

> **Already installed? You want [WINDOWS-SERVER-RUNBOOK.md](WINDOWS-SERVER-RUNBOOK.md) instead.**
> This document covers a first install. The runbook covers the two things you
> do afterwards: bringing a broken deployment back to healthy, and pushing an
> update from GitHub. Both are scripted in `deploy/windows/`.

---

## 1. What differs from the Linux setup

The repository was first built for a Linux container on Fly.io. The former Fly.io deployment (`fly.toml`, `Dockerfile`, gunicorn) is no longer production. `.github/workflows/fly-deploy.yml` still deploys it after every green Tests run on `main`, and `pinakes.xyz` now redirects to the Clearinghouse host. Four pieces of that setup do not translate to Windows and require local substitutions:

| Linux/Fly.io (former production) | Windows Server | Why |
|---|---|---|
| `gunicorn` WSGI server (in `requirements.txt`) | `waitress` | Gunicorn uses the Unix `fork()` syscall and does not run on Windows. Waitress is the pure-Python, Windows-compatible equivalent. |
| Fly.io persistent volume at `/data/articles.db` | A local NTFS folder, e.g. `C:\Pinakes\data\articles.db` | You set the path with the `DB_PATH` environment variable. |
| Process supervised by Fly.io runtime | Windows Service via **NSSM** | Keeps the app running on reboot without requiring a logged-in console session. |
| HTTPS terminated by Fly's edge | IIS reverse proxy (production, behind Cloudflare) *or* Caddy for Windows | Windows has no built-in TLS-terminating proxy for arbitrary localhost apps. |

The Python code, the SQLite schema and the API integrations run unchanged on Windows. A few behaviours are tied to Fly or to Linux, though, and need a hand here:

- `PINAKES_SECRET_KEY` is only enforced when `FLY_APP_NAME` is set. Here an unset key is silently replaced by a random one each time the service starts (§6.1).
- The `/wac` dashboard tables are filled at startup only on Fly. Here you run `ingest_wac.py` yourself (§7).
- `mem_watchdog.py`, which recycled the Fly worker when its memory climbed, needs Linux's `/proc` and does nothing on Windows (see the FAQ on disk and RAM).

One more difference, added after this guide was first written: the repository no
longer contains `scheduler.py`. Recurring jobs are now triggered by POSTing to
admin endpoints (`/fetch`, `/api/admin/*`). On Fly that was done by GitHub
Actions against `pinakes.xyz`. Those workflows' schedules have been commented
out since 2026-09-19, and they never reached this server anyway, so this server
drives its own jobs with Windows scheduled tasks. See §8.3.

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

Required outbound network access (open in your egress firewall if it is restrictive), all over HTTPS 443:

- `api.crossref.org` and `api.openalex.org`.
- Individual journal publisher sites, for RSS feeds and scraping (Taylor & Francis, SAGE, Wiley, etc.).
- `github.com` — the clone, every update (`Update-Pinakes.ps1`), and the `Pinakes-UpdateCheck` task.
- `pypi.org` and `files.pythonhosted.org` — `pip install`, at install time and on every update.
- Your SMTP server, if you turn on email (§6.2).
- Once usage statistics are switched on: `download.db-ip.com`, `raw.githubusercontent.com` and `nces.ed.gov` for the weekly reference-data refresh, and `rdap.org` and `api.ror.org` for the nightly resolver. See [usage-statistics.md](usage-statistics.md).
- If you configure them (§6.2): your Sentry DSN's host, your offsite-backup endpoint, and `orcid.org`.

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

> The Dockerfile (the former Fly image) pins `python:3.12-slim`. Python 3.11 and 3.13 will probably work but are untested — stay on 3.12 if you want to match the reference environment.

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

The scripts create `C:\Pinakes\backups` the first time they need it.

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
python -c "import flask, flask_limiter, waitress, lxml, networkx, bs4, feedparser, curl_cffi, maxminddb; print('ok')"
```

---

## 6. Configure environment variables

The application and the deployment scripts read their settings from environment variables. Set them at the **Machine** scope: the Windows Service sees them, and so do the scheduled tasks, which run as SYSTEM and read Machine-scope values directly.

### 6.1 Required on a production box

| Variable | Read by | Purpose | Value |
|---|---|---|---|
| `DB_PATH` | `db/__init__.py`, the scripts | Absolute path to the SQLite file. Unset, the app uses `articles.db` inside the checkout. | `C:\Pinakes\data\articles.db` |
| `PORT` | `Pinakes.Common.ps1`, `python app.py` | **Not** the port the service listens on: Waitress takes that from its NSSM arguments (§8.2). The scripts build `http://127.0.0.1:<PORT>` from it (default `8080`), and `python app.py` binds it (default `5000`). Keep it equal to the Waitress port. Changing the port means changing the NSSM arguments, `PORT` and the IIS rewrite URL (§9.2) together. | `8080` |
| `FLASK_ENV` | `app.py` | `production` turns off the debugger when the app is run with `python app.py`. (It also starts the memory watchdog, which does nothing on Windows.) | `production` |
| `PINAKES_SITE_URL` | `web_helpers.py`, `digest.py`, `fetcher.py`, the scripts | Public address this host answers on. Digest emails, the confirmation links the app emails, the ORCID callback address, the CrossRef User-Agent and the scripts' public-URL checks are all built from it. Waitress strips the forwarding headers, so the app cannot read this address from a request. | `https://pinakes.wacclearinghouse.org` on production, `https://testpinakes.wacclearinghouse.org` on the test host |
| `PINAKES_ADMIN_TOKEN` | `auth.py`, the scripts | Bearer token for `/fetch`, `/api/admin/*` and the admin pages. Unset, those endpoints answer 503 and every scheduled job fails. | a long random string (below) |
| `PINAKES_TRUSTED_IP_HEADER` | `rate_limit.py` | The header that carries the visitor's IP. Unset behind IIS, every visitor looks like `127.0.0.1` and the whole site shares one rate-limit budget. See [Visitor IP addresses](WINDOWS-SERVER-RUNBOOK.md#visitor-ip-addresses) in the runbook. | `CF-Connecting-IP` |
| `PINAKES_SECRET_KEY` | `app.py` | Signs the Datastories login cookie and the ORCID `state` value. The app insists on it only on Fly; here, left unset, each start picks a random key, so every restart logs Datastories readers out and breaks any ORCID sign-in in progress. | a long random string (below) |
| `CROSSREF_MAILTO` | `fetcher.py` | Contact address in the CrossRef User-Agent, which puts the fetch in CrossRef's "polite pool" with its more generous rate limits. Unset, the fetch uses the anonymous pool and sees more 429s. | an address someone reads |
| `PINAKES_DATASTORIES_PASSWORD` | `auth_datastories.py` | Password for the Datastories tools. Unset, the `/datastories` landing page loads but the tools and their API answer 503. | the password you give readers |

Generate the token and the secret key with the venv's Python. Run it twice and use a different value for each:

```powershell
C:\Pinakes\venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Then set everything:

```powershell
[Environment]::SetEnvironmentVariable("DB_PATH",   "C:\Pinakes\data\articles.db", "Machine")
[Environment]::SetEnvironmentVariable("PORT",      "8080",                         "Machine")
[Environment]::SetEnvironmentVariable("FLASK_ENV", "production",                   "Machine")
[Environment]::SetEnvironmentVariable("PINAKES_SITE_URL", "https://pinakes.wacclearinghouse.org", "Machine")
[Environment]::SetEnvironmentVariable("PINAKES_ADMIN_TOKEN", "<token>", "Machine")
[Environment]::SetEnvironmentVariable("PINAKES_TRUSTED_IP_HEADER", "CF-Connecting-IP", "Machine")
[Environment]::SetEnvironmentVariable("PINAKES_SECRET_KEY", "<secret key>", "Machine")
[Environment]::SetEnvironmentVariable("CROSSREF_MAILTO", "<contact address>", "Machine")
[Environment]::SetEnvironmentVariable("PINAKES_DATASTORIES_PASSWORD", "<password>", "Machine")
```

Close and reopen PowerShell to pick them up in your current session, then confirm:

```powershell
$env:DB_PATH; $env:PORT; $env:FLASK_ENV; $env:PINAKES_SITE_URL; $env:PINAKES_TRUSTED_IP_HEADER
```

The current session matters: §7 runs `fetcher.py` and `ingest_wac.py` by hand, and without `DB_PATH` in the session they write to `C:\Pinakes\app\articles.db` instead.

> Set `PINAKES_SITE_URL` on **both** hosts. `deploy/windows/Pinakes.Common.ps1` falls back to the production address when it is unset, and so do the app's feeds, digest emails and CrossRef User-Agent. On the test host, an unset value points `Update-Pinakes.ps1`'s public-URL check, `Test-PinakesHealth.ps1 -Remote` and `Test-PinakesClientIp.ps1` at production, and the links the app emails come out as `http://127.0.0.1:8080/…`.

> Restart `Pinakes-Web` after changing any of these; the process reads its environment when it starts. Then check that the running service has them: `/health` reports `"admin_auth":"configured"`, and `Test-PinakesClientIp.ps1` prints the IP header and site URL the service is using. If a Machine-scope value does not reach the service, set it on the service itself with `nssm set Pinakes-Web AppEnvironmentExtra`, keeping the entries already there (the runbook's [Visitor IP addresses](WINDOWS-SERVER-RUNBOOK.md#visitor-ip-addresses) shows how).

> **Do not put the database on a network share (SMB/UNC path).** SQLite's file locking is unreliable over SMB and will eventually corrupt the database. Keep `DB_PATH` on a local NTFS volume. For an off-box copy, copy the snapshots in `C:\Pinakes\backups`, never the live `.db` file — see §12.

### 6.2 Optional features

Each feature is off, or does nothing, until its variables are set, except the last two, which run with the defaults shown.

| Feature | Variables | Notes |
|---|---|---|
| Email | `SMTP_HOST`, `SMTP_PORT` (default `587`), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_REPLY_TO`, `SMTP_STARTTLS` (default `1`; `0` turns STARTTLS off) | `notifications.py`. Mail goes out only when both `SMTP_HOST` and `SMTP_FROM` are set; otherwise each message is written to the log instead. Alerts and the name-removal form need it. |
| Saved-search alerts | `PINAKES_ALERTS_ENABLED=1`; `PINAKES_DIGEST_DELAY` (seconds between digest sends, default `0.4`) | Off by default: the signup pages answer 404 and the digest run does nothing. Needs email. Register the weekly task with `-IncludeDigests` (§8.3). |
| Name-removal request form | `PINAKES_REDACTION_FORM_ENABLED=1`; `REDACTION_NOTIFY_EMAIL` (who is told about verified requests; falls back to `SMTP_REPLY_TO`) | Off by default; authors email instead. Needs email. See the runbook's [Author name-removal requests](WINDOWS-SERVER-RUNBOOK.md#author-name-removal-requests). |
| Redaction salt | `REDACTION_SALT` | Optional. Without it, `redaction.py` generates a salt and stores it in the database. |
| ORCID sign-in on the name-removal form | `ORCID_CLIENT_ID`, `ORCID_CLIENT_SECRET`; `ORCID_ENV=sandbox` to use ORCID's sandbox | Register `<PINAKES_SITE_URL>/redaction-request/orcid/callback` with ORCID. Needs a fixed `PINAKES_SECRET_KEY`. |
| Usage statistics | `PINAKES_USAGE_TRACKING`, `PINAKES_USAGE_SURVEY` | Both off by default. Read [usage-statistics.md](usage-statistics.md) and the runbook's [Usage statistics](WINDOWS-SERVER-RUNBOOK.md#usage-statistics) section before turning either on. |
| Offsite backup | `PINAKES_BACKUP_BUCKET`, `PINAKES_BACKUP_ENDPOINT`, `PINAKES_BACKUP_REGION`, `PINAKES_BACKUP_ACCESS_KEY_ID`, `PINAKES_BACKUP_SECRET_KEY`, `PINAKES_BACKUP_AGE_PUBLIC_KEY` | `backup.py`: an encrypted upload to S3-compatible storage, run with `Invoke-PinakesAdmin.ps1 -Endpoint Backup`. All six are required. No scheduled task runs it on this server (§12). |
| Error reporting | `SENTRY_DSN` | Sends errors to Sentry. Unset, nothing is sent. |
| JWA preview | `PINAKES_JWA_USER`, `PINAKES_JWA_PASSWORD` | HTTP Basic Auth for `/jwa`. Unset, `/jwa` answers 503. |
| Load shedding | `PINAKES_MAX_EXPENSIVE_INFLIGHT` (default `6`) | How many expensive requests may run at once. Keep it below Waitress's thread count (§8.2). |
| IP denylist | `PINAKES_BLOCKED_CIDRS` (default `47.79.200.0/21`) | Comma-separated CIDRs answered with 403. Setting it replaces the default, so include that range if you want to keep it. |

`PINAKES_CLOUDFLARE_ONLY` does nothing on this box. It keys off the `Fly-Client-IP` header that Fly's proxy added, which nothing here sets. Restrict 443 to Cloudflare at the firewall instead (§9.2).

### 6.3 Script settings

The PowerShell scripts in `deploy/windows/` read these through `Get-PinakesSetting` in `Pinakes.Common.ps1` (Machine scope first, then the current session). The defaults match this guide's layout; set one only if this server differs.

| Variable | Default | Purpose |
|---|---|---|
| `PINAKES_ROOT` | `C:\Pinakes` | Root for `app`, `venv`, `logs` and `backups`, and for the scripts' default `DB_PATH` |
| `PINAKES_BACKUP_DIR` | `<root>\backups` | Where `Backup-Pinakes.ps1` and `Update-Pinakes.ps1` write |
| `PINAKES_BACKUP_KEEP` | `10` | Backups kept per database before the oldest are pruned |
| `PINAKES_SERVICE_NAME` | `Pinakes-Web` | Service the scripts stop, start and check |
| `PINAKES_GIT_BRANCH` | `main` | Branch `Update-Pinakes.ps1` deploys and the health check compares against |
| `PINAKES_GIT_REMOTE` | `origin` | Remote they fetch from |

They also read `DB_PATH`, `PORT`, `PINAKES_SITE_URL` and `PINAKES_ADMIN_TOKEN` from §6.1.

---

## 7. Seed the database with the initial article fetch

This is the long-running one-time step. Run it in a PowerShell window opened after §6, so `DB_PATH` is set in the session:

```powershell
Set-Location C:\Pinakes\app
C:\Pinakes\venv\Scripts\Activate.ps1
python fetcher.py
```

`fetcher.py` walks the full CrossRef history of every journal in `CROSSREF_JOURNALS` in `journals.py` (51 of the 69 journals listed there as of this revision). It does not touch the RSS-fed and scraped journals; those arrive with the first fetch through the service (§8.4) or the first scheduled `Pinakes-DailyFetch`. CrossRef rate-limits aggressive clients, so this takes a while. For each journal you will see `Fetching <journal>  (<ISSN>)  [full]`, a `page N — … new so far` line per page of results, and then `Done: <journal> — N new articles`. The run ends with `CrossRef fetch complete. Total new: N`, and `C:\Pinakes\data\articles.db` now exists.

Then fill the tables behind the `/wac` dashboard, which the app does on its own only on Fly:

```powershell
python ingest_wac.py
```

It reads `data\wac_crossref_dump.jsonl` from the checkout, so run it from `C:\Pinakes\app`. It rebuilds `wac_works` and `wac_authors` from scratch each time, so it is safe to re-run; running it again after the first Sunday maintenance pass picks up the institution data that pass adds. Without it, `/wac` is empty.

> **A fresh seed is not a copy of production.** Some content is loaded only by one-off scripts that no scheduled job runs (JAC, the *Peer Review* 1.1 references and *Rhetoric, Politics & Culture* 4.1 from `data/seeds/`; `journals.py` credits Pre/Text to `ingest_pretext.py`, which is not in the repository). A database that has been in service also holds records nothing can refetch — the redaction ledger, tag votes and suggestions, alert subscriptions, the suppressed-article list. To build a replacement for production, skip the seed: copy a snapshot from production's `C:\Pinakes\backups` (§12) to `C:\Pinakes\data\articles.db` before you create the service (and a `usage-*.db` snapshot to `C:\Pinakes\data\usage.db` if production records usage statistics). Run `ingest_wac.py` only if `/wac` turns out empty.

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
nssm install Pinakes-Web "C:\Pinakes\venv\Scripts\waitress-serve.exe" "--host=127.0.0.1 --port=8080 --threads=8 app:app"
nssm set Pinakes-Web AppDirectory    "C:\Pinakes\app"
nssm set Pinakes-Web DisplayName     "Pinakes (Rhet-Comp Index) — Web"
nssm set Pinakes-Web Description     "Flask/waitress web server for the Pinakes article index."
nssm set Pinakes-Web Start           SERVICE_AUTO_START
nssm set Pinakes-Web AppStdout       "C:\Pinakes\logs\web-stdout.log"
nssm set Pinakes-Web AppStderr       "C:\Pinakes\logs\web-stderr.log"
nssm set Pinakes-Web AppRotateFiles  1
nssm set Pinakes-Web AppRotateOnline 1               # rotate while running, not only at service start
nssm set Pinakes-Web AppRotateBytes  10485760        # rotate at 10 MB
nssm set Pinakes-Web AppEnvironmentExtra "DB_PATH=C:\Pinakes\data\articles.db" "PORT=8080" "FLASK_ENV=production"

Start-Service Pinakes-Web
Get-Service  Pinakes-Web
```

Two of the Waitress arguments matter:

- **`--host=127.0.0.1`.** With IIS in front (§9.2), Waitress should listen on loopback only. With `PINAKES_TRUSTED_IP_HEADER` set, `rate_limit.py` treats any loopback or private (RFC 1918) address as a proxy on this machine and believes the visitor IP it forwards, so a host on the LAN that could reach port 8080 could pick its own IP. Use `--host=0.0.0.0` only for the LAN-only setup in §9.1.
- **`--threads=8`.** Waitress runs 4 threads unless told otherwise. The app caps concurrent expensive requests (`/explore`, `/export`, `/citations`, `/article/…`, `/author/…`) at `PINAKES_MAX_EXPENSIVE_INFLIGHT`, default 6, so that cheap pages and `/health` always find a free thread. That only works with more threads than the cap.

`AppRotateFiles` alone rotates the logs only when the service starts; `AppRotateOnline` also rotates them when they pass `AppRotateBytes` while it runs.

To see what an existing server runs, or to change it:

```powershell
nssm get Pinakes-Web AppParameters
nssm set Pinakes-Web AppParameters "--host=127.0.0.1 --port=8080 --threads=8 app:app"
Restart-Service Pinakes-Web
```

### 8.3 Register the scheduled jobs

The tasks run as SYSTEM and read `PINAKES_ADMIN_TOKEN` from the Machine scope
you set in §6.1, so the token never appears in a task definition. If you set or
changed the token after the service started, restart the service first so both
sides hold the same value:

```powershell
Restart-Service Pinakes-Web
```

Then register the tasks:

```powershell
& C:\Pinakes\app\deploy\windows\Install-PinakesTasks.ps1 -IncludeUpdateCheck
```

That creates seven tasks. Times are the server's local time.

| Task | When | What it does |
|---|---|---|
| `Pinakes-NightlyBackup` | 02:00 daily | `Backup-Pinakes.ps1`: local SQLite backup to `C:\Pinakes\backups` (§12) |
| `Pinakes-DailyFetch` | 03:23 daily | `POST /fetch` — incremental CrossRef fetch, then RSS and scrapers |
| `Pinakes-Prewarm` | 04:15 daily | `POST /api/admin/prewarm` — rebuild cached aggregates |
| `Pinakes-WeeklyMaintenance` | 04:45 Sundays | `POST /api/admin/run-maintenance` |
| `Pinakes-UsageResolve` | 05:10 daily | Usage statistics: resolves recorded addresses, then deletes them. Registered whether or not recording is on; with nothing recorded it finishes at once. |
| `Pinakes-UsageData` | 05:40 Sundays | Usage statistics: refreshes the IP databases when they are out of date. Skips its downloads while recording is off. |
| `Pinakes-UpdateCheck` | 08:00 daily | Runs `Update-Pinakes.ps1 -Check`. Registered only with `-IncludeUpdateCheck`; never deploys. |

`Pinakes-UpdateCheck` does not report anywhere you can read. In `-Check` mode
the script writes only to the console, which Task Scheduler discards; it starts
no transcript, writes nothing to `admin-tasks.log`, and exits 0 whether or not
the server is behind. To find out whether this server has fallen behind GitHub,
run `Update-Pinakes.ps1 -Check` by hand, or read `Code matches GitHub` in
`Test-PinakesHealth.ps1`.

Add `-IncludeDigests` for the weekly saved-search emails (`Pinakes-WeeklyDigests`,
14:17 Fridays), but only once `PINAKES_ALERTS_ENABLED=1`, `SMTP_HOST` and
`SMTP_FROM` are set, and the sending domain is verified.

Check them any time with `Install-PinakesTasks.ps1 -List`, and read their
history in `C:\Pinakes\logs\admin-tasks.log` (every task but
`Pinakes-UpdateCheck` writes a line there).

### 8.4 Confirm everything is running

```powershell
Get-Service Pinakes-Web | Format-Table Name, Status, StartType
& C:\Pinakes\app\deploy\windows\Install-PinakesTasks.ps1 -List
Get-Content C:\Pinakes\logs\web-stderr.log -Tail 20
```

Browse to http://127.0.0.1:8080 — the page should load. The app's log output
goes to `web-stderr.log`, because Python logging writes to stderr;
`web-stdout.log` gets only what the code `print()`s.

Then run the first fetch through the service rather than waiting for 03:23. It
adds the RSS and scraped journals that §7 skipped; its CrossRef pass only asks
for articles published since the seed.

```powershell
& C:\Pinakes\app\deploy\windows\Invoke-PinakesAdmin.ps1 -Endpoint Fetch
```

---

## 9. Expose the site to the network

How far you go here depends on who needs access.

### 9.1 Internal LAN only (simplest)

This option has LAN machines talk to Waitress directly, so Waitress has to
listen on the network: set its arguments to `--host=0.0.0.0` (§8.2). Any host
that can reach port 8080 can then set the forwarding headers the app reads its
visitor IP from, so keep this to a trusted internal network and never combine
it with a public proxy.

Open port 8080 on the Windows firewall to your internal subnet:

```powershell
New-NetFirewallRule -DisplayName "Pinakes Web (8080, internal)" `
                    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8080 `
                    -RemoteAddress 10.0.0.0/8,192.168.0.0/16,172.16.0.0/12
```

LAN users can reach the site at `http://<server-hostname>:8080`.

### 9.2 Public-facing with HTTPS (recommended for anything external)

Do **not** expose waitress directly to the internet. Put a reverse proxy in front of it that terminates TLS, and keep Waitress on `127.0.0.1` (§8.2). Production uses Option A, with Cloudflare in front of IIS: the public hostname is proxied through Cloudflare, which forwards requests to IIS.

**Option A — IIS with URL Rewrite + ARR (fully Microsoft-stack)**

1. `Install-WindowsFeature -Name Web-Server -IncludeManagementTools`
2. Install the IIS extensions: **URL Rewrite 2.1** and **Application Request Routing 3.0** from https://www.iis.net/downloads.
3. In IIS Manager, at the server level, enable **Application Request Routing Cache → Server Proxy Settings → Enable proxy**.
4. Create a site bound to `https://<your-fqdn>` with a certificate (Let's Encrypt via `win-acme`, or your organization's PKI).
5. Add a URL Rewrite rule: inbound pattern `(.*)`, action type **Rewrite**, rewrite URL `http://127.0.0.1:8080/{R:1}`.
6. Firewall: allow 443 inbound only from [Cloudflare's address ranges](https://www.cloudflare.com/ips/), and check that no broader rule still allows 443 from anywhere. That restriction is what makes it safe to trust `CF-Connecting-IP`: the only way to forge the header is to reach IIS without going through Cloudflare. Port 8080 needs no rule; Waitress on `127.0.0.1` cannot be reached from off the box.
7. Set `PINAKES_TRUSTED_IP_HEADER=CF-Connecting-IP` (§6.1), restart the service, and from another machine run `deploy\windows\Test-PinakesClientIp.ps1` through the public hostname. The runbook's [Visitor IP addresses](WINDOWS-SERVER-RUNBOOK.md#visitor-ip-addresses) explains each result.

ARR keeps its own response cache and honours `Cache-Control: public`. The app marks its API responses `private` so ARR does not store them, but anything cached before that, or by a rule of your own, is served without reaching the app. `Test-PinakesHealth.ps1 -BaseUrl https://<your-fqdn>` checks for this (§10); the runbook's [A8](WINDOWS-SERVER-RUNBOOK.md#a8-verify) shows how to clear the cache.

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

Caddy passes the visitor's address in `X-Forwarded-For`, which Waitress deletes before the app sees it. Without Cloudflare in front, the app sees every visitor as `127.0.0.1` unless Caddy sends the address in a header Waitress keeps and `PINAKES_TRUSTED_IP_HEADER` names it. Production does not use this option, so test it with `Test-PinakesClientIp.ps1` before relying on it.

> If you are using IIS on the same box, you cannot also bind Caddy to 80/443. Pick one.

---

## 10. Verify the install

Run these checks end-to-end:

One command checks the service, the app on `127.0.0.1`, the database, and
whether the code matches GitHub:

```powershell
& C:\Pinakes\app\deploy\windows\Test-PinakesHealth.ps1
```

By default every HTTP check goes to `127.0.0.1`, so it does not touch IIS or
Cloudflare. Once §9.2 is in place, send the HTTP checks through the public
hostname as well; the service, database and git checks still run on the
server:

```powershell
& C:\Pinakes\app\deploy\windows\Test-PinakesHealth.ps1 -BaseUrl https://pinakes.wacclearinghouse.org
```

Every line prints PASS, FAIL, or WARN, and the script exits non-zero if anything
FAILed. After §7 and §8.4, `Index contains articles` should report a real
count. If it reports 0, the seed wrote somewhere other than `DB_PATH` — most
likely `C:\Pinakes\app\articles.db`, because `DB_PATH` was not set in that
session (§6.1).

To check from a different machine, over HTTPS only (it tests `PINAKES_SITE_URL`
if that machine has it, otherwise production; add `-BaseUrl` for the test host):

```powershell
& .\Test-PinakesHealth.ps1 -Remote
```

If you would rather check by hand:

```powershell
Get-Service Pinakes-Web | Format-Table Name, Status
Invoke-RestMethod http://127.0.0.1:8080/health
Invoke-RestMethod http://127.0.0.1:8080/health/ready
Get-Item C:\Pinakes\data\articles.db | Select-Object FullName, @{n='MB';e={[math]::Round($_.Length/1MB,1)}}
Get-Content C:\Pinakes\logs\web-stderr.log -Tail 30
```

When a fetch through the service finishes, the app logs
`CrossRef fetch complete. Total new: N`, then `RSS fetch complete. Total new: N`
and `Scrape fetch complete. Total new: N`. Look for them in `web-stderr.log`.
They are INFO-level lines, and under `waitress-serve` the app's INFO lines may
not reach the log at all; warnings and errors always do. The dependable sign
that a fetch worked is the article count (`Index contains articles`, or the
`total` in `/api/articles?limit=1`).

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
& C:\Pinakes\app\deploy\windows\Backup-Pinakes.ps1 -Label pre-manual-update

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

`Pinakes-NightlyBackup` (§8.3) already takes one every night at 02:00. It runs `Backup-Pinakes.ps1`, which copies the database through SQLite's online backup API (`deploy/windows/sqlite_backup.py`), checks the copy with `PRAGMA quick_check`, and does the same for `usage.db` whenever that file exists. The copies land in `C:\Pinakes\backups` as `articles-<timestamp>-<label>.db` (and `usage-<timestamp>-<label>.db`).

Take one by hand before anything risky:

```powershell
& C:\Pinakes\app\deploy\windows\Backup-Pinakes.ps1 -Label before-schema-change
```

Do not copy the live `.db` file while the service is running, and do not substitute `Copy-Item` or Robocopy for the script: the app writes to the file all day, and a plain copy can capture a torn page mid-transaction. The `.db-wal` and `.db-shm` sidecar files are part of WAL mode — the online backup captures their content into a single clean file, so you do not need to copy them separately.

Two limits to know:

- **One pool of ten.** The scripts keep the newest `PINAKES_BACKUP_KEEP` (default 10) `articles-*.db` files and delete the rest, whatever their label. The nightly copies, the `pre-<sha>` copy `Update-Pinakes.ps1` takes before each deploy, and your hand-labelled ones all count against the same ten, so a run of deploys or manual backups pushes older nightlies out. Raise `PINAKES_BACKUP_KEEP` (§6.3) before a schema change lands if you want more history.
- **Local only.** The backups sit on the same server as the database. They cover a bad migration or a bad deploy, not the loss of the machine. For an off-box copy, point Windows Server Backup or a Robocopy job (scheduled away from 02:00) at `C:\Pinakes\backups`, or configure the offsite backup in §6.2 and run `Invoke-PinakesAdmin.ps1 -Endpoint Backup`; nothing schedules that on this server.

After applying a name removal, also keep an off-box export of the redaction ledger, `redaction.py export-ledger <path>`. A restore from a backup taken before the removal would not contain it, and `redaction.py import-ledger <path>` is how it goes back in. The export holds the names in plain text, so store it securely.

---

## 13. Uninstall

```powershell
& C:\Pinakes\app\deploy\windows\Install-PinakesTasks.ps1 -Remove

Stop-Service Pinakes-Web
nssm remove Pinakes-Web confirm
nssm remove Pinakes-Scheduler confirm     # only if an old one still exists

Remove-NetFirewallRule -DisplayName "Pinakes Web (8080, internal)"   # only if you created it in §9.1

# Machine-scope settings from §6. Review the list before removing:
# PORT, DB_PATH and FLASK_ENV are generic names another app may also use.
$names = [Environment]::GetEnvironmentVariables("Machine").Keys | Where-Object {
    $_ -match '^(PINAKES_|SMTP_|ORCID_|REDACTION_|USAGE_|ROR_CLIENT_ID$|SENTRY_DSN$|CROSSREF_MAILTO$|DB_PATH$|PORT$|FLASK_ENV$)'
}
$names
foreach ($n in $names) { [Environment]::SetEnvironmentVariable($n, $null, "Machine") }

Remove-Item -Recurse -Force C:\Pinakes     # deletes app, venv, data, logs AND backups — be sure first
```

Copy `C:\Pinakes\backups` off the box first if you may need the data again. Remove the IIS site or the Caddy service from §9.2 separately.

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
On the former Fly deployment that was GitHub Actions; here it is Windows Task
Scheduler (§8.3). The upside on Windows is that jobs no longer hold a second
writer open against the same SQLite file.

**Q: How do I add my email to the API User-Agent?**
Set `CROSSREF_MAILTO` at Machine scope (§6.1) and restart `Pinakes-Web`. `fetcher.py`, which does the daily CrossRef fetch, builds its User-Agent from it and from `PINAKES_SITE_URL`; no code edit is needed. The other harvesters (`book_fetcher.py`, `enrich_openalex.py`, `cite_fetcher.py` and others) already send the project's contact address, which is written into their code. Changing those is a code change to make upstream, not on the server: local edits block `Update-Pinakes.ps1`, which refuses to discard uncommitted changes unless run with `-Force`, and `-Force` discards them.

**Q: Can I run this on a Windows Server Core install (no GUI)?**
Yes. Everything in this guide is command-line. Use IIS (ServerManager cmdlets) or Caddy (which needs no GUI) for the reverse proxy. You cannot use the IIS Manager MMC on Core — configure IIS from another machine with Remote Management, or use `appcmd.exe` locally.

**Q: Does this application store personal data?**
Some. Most of the database is bibliographic metadata (titles, authors, DOIs, abstracts) from public sources. It also stores:

- the IP address of each reader who votes on a tag or suggests one (`user_tags`, `tag_feedback`), which is how one vote per reader is enforced;
- email addresses, and the IP address at signup, for saved-search alerts (`alert_subscriptions`) once alerts are on;
- requester email addresses and ORCID iDs for name-removal requests (`redaction_requests`), and the removed names themselves in the redaction ledger;
- answers to the optional `/tell-us` form, when it is on.

Usage statistics, off by default, record requests, including visitor IP addresses, in a separate `usage.db`; what is recorded and how long it is kept is in [usage-statistics.md](usage-statistics.md). Waitress writes no access log, but IIS keeps its own request logs unless logging is turned off there, and the app logs the client IP on failed admin-token attempts.

**Q: How much disk and RAM will it use?**
The full database is a few hundred MB and grows as sources are added and the weekly enrichment fills it in. `C:\Pinakes\backups` holds up to ten copies of it by default (§12), plus copies of `usage.db` when that file exists.

RAM is one Python process, which grows with use of the heavy network-analysis tools because CPython keeps the memory it has used. On the former Fly deployment it climbed to its 1 GB ceiling in about three days (see `mem_watchdog.py`). The watchdog that recycled it there needs Linux and does nothing on Windows, so give the VM at least 2 GB, watch the service's memory, and `Restart-Service Pinakes-Web` if it keeps climbing.

**Q: Can multiple people use the site at once?**
Yes. Waitress serves concurrent readers on its thread pool (§8.2), and SQLite in WAL mode (which `db/core.py` sets on every connection) allows readers during a writer. The bottleneck is the single writer, which only matters during the scheduled fetch.

**Q: Is there an admin UI or a login screen?**
There are no user accounts, but several pages are gated:

- `/admin/curate`, `/admin/user-tags`, `/admin/redactions` and `/admin/usage` load for anyone but do nothing until you paste the admin token. The page keeps it in the browser tab and sends it as a bearer token.
- `/datastories` asks for `PINAKES_DATASTORIES_PASSWORD` before the tools open.
- `/jwa` uses HTTP Basic Auth (`PINAKES_JWA_USER`, `PINAKES_JWA_PASSWORD`).

The sidebar's **Refresh all sources** and **Deep refresh** buttons POST to `/fetch` without the admin token, so the server refuses them. Trigger fetches with `Invoke-PinakesAdmin.ps1 -Endpoint Fetch` (or `-Endpoint DeepFetch`) instead. Visitors can write to the database in a few places: tag votes and tag suggestions on article pages, saved-search alert signup (when alerts are on), the `/tell-us` form (when it is on), and the name-removal form (when it is on).

**Q: What happens if CrossRef or OpenAlex is down when a fetch runs?**
The daily fetch does not use OpenAlex; OpenAlex enrichment runs in the Sunday
maintenance job. For CrossRef, a request that still fails after its retries is
logged (`Request failed for <ISSN>: …`) and the fetch moves on to the next
journal. The failed journal's fetch-log entry is still stamped with that day's
date, though, and the next incremental run asks CrossRef only for articles
published since then, so what that journal published in the gap can be missed
for good. The CrossRef, RSS and scraper passes also share one `try` block: an
unexpected error in one ends that run (`Background fetch error: …` in the log),
and the passes after it wait for the next day.

After an outage, run a deep refresh, which walks every journal's full catalog
and fills in what the incremental runs missed. It is slow, so run it
deliberately rather than on a timer:

```powershell
& C:\Pinakes\app\deploy\windows\Invoke-PinakesAdmin.ps1 -Endpoint DeepFetch
```

A fetch that is still running when the next one fires is not doubled up:
`POST /fetch` returns 409 and the task logs it as a skip, which is the guard
against two writers on one SQLite file.

**Q: Can I change the fetch schedule?**
Yes — it is a Windows scheduled task now, so either edit the trigger times in
`deploy/windows/Install-PinakesTasks.ps1` and re-run it, or adjust the task
directly:

```powershell
Set-ScheduledTask -TaskName Pinakes-DailyFetch `
    -Trigger (New-ScheduledTaskTrigger -Daily -At '05:00')
```

A change made with `Set-ScheduledTask` lasts only until `Install-PinakesTasks.ps1`
runs again: it re-registers every task with the times in the script. The
runbook's usage-statistics steps re-run it, so re-apply the change after that,
or edit the script instead.

---

## Troubleshooting

### The `Pinakes-Web` service starts and immediately stops

Check `C:\Pinakes\logs\web-stderr.log`. The usual suspects:

- **`ModuleNotFoundError`** — you activated the wrong venv or installed dependencies into the system Python. Re-run `pip install -r requirements.txt` with `C:\Pinakes\venv\Scripts\Activate.ps1` active, and make sure NSSM points at `C:\Pinakes\venv\Scripts\waitress-serve.exe`, not a system-wide one.
- **`OSError: [WinError 10048] Only one usage of each socket address...`** — port 8080 is already bound. Find the offender with `Get-NetTCPConnection -LocalPort 8080` and either stop it or move Pinakes to another port: the `--port` in the NSSM arguments, `PORT`, and the IIS rewrite URL together (§6.1).
- **`sqlite3.OperationalError: unable to open database file`** — the service account cannot write to `C:\Pinakes\data\`. Fix permissions: `icacls C:\Pinakes\data /grant "NT AUTHORITY\LocalService:(OI)(CI)M"` (or whichever account NSSM runs as — default is `LocalSystem`, which can write anywhere local).

### `pip install lxml` fails with a compiler error

Very rare on Python 3.12 because prebuilt wheels exist for Windows x64. If it happens, you are probably on an unusual CPU architecture (ARM64) or a 32-bit Python. Install 64-bit Python 3.12 from python.org and retry. As a last resort: `pip install --only-binary=:all: lxml`.

### The **Refresh all sources** button says the fetch started, but nothing happens

The sidebar's **Refresh all sources** and **Deep refresh** buttons POST to
`/fetch` without the admin token, so the server refuses them with 401 and
nothing runs. The page does not check the response, so it reports that the
fetch started anyway. Trigger a fetch from the server instead:

```powershell
& C:\Pinakes\app\deploy\windows\Invoke-PinakesAdmin.ps1 -Endpoint Fetch
```

An accepted fetch runs in a background thread and the endpoint answers
immediately, so a quick return with no new articles is also what a fetch that
found nothing new looks like. Check the article count (§10), `web-stderr.log`
for warnings and errors, and `C:\Pinakes\logs\admin-tasks.log` for the
scheduled runs.

If the fetch is failing rather than finding nothing, look for CrossRef
`400 sort-criteria-incompatible-with-cursor` in the log. That means the server is
running a build from before commit `138f4cd`: CrossRef stopped accepting a sort
order combined with cursor paging, so every deep page fails and no articles are
written. Update the server (§11).

### Database reports "database is locked"

This should be rare because `db/core.py` sets WAL mode and a 30-second busy timeout on every connection. If it recurs:

1. Confirm `DB_PATH` is on a local NTFS volume, not SMB/UNC. **This is the single most common cause.**
2. Confirm only one `Pinakes-Web` is running, and that no stale `Pinakes-Scheduler` survives from an older install: `Get-Process python, waitress-serve -ErrorAction SilentlyContinue`.
3. Check for antivirus / EDR scanning the `.db` file mid-write. Add `C:\Pinakes\data\*.db*` to the exclusions list in Defender: `Add-MpPreference -ExclusionPath "C:\Pinakes\data"`.

### Log shows `Request failed for <ISSN>: HTTPSConnectionPool(...): Max retries exceeded`

Outbound 443 is being blocked or rate-limited. Confirm with `Test-NetConnection api.crossref.org -Port 443`. If the server is behind an outbound proxy, add `HTTPS_PROXY` to the service environment. `nssm set … AppEnvironmentExtra` replaces the whole list, so read it first and repeat the existing entries:

```powershell
nssm get Pinakes-Web AppEnvironmentExtra
nssm set Pinakes-Web AppEnvironmentExtra <the existing lines> HTTPS_PROXY=http://proxy.corp.example:8080
Restart-Service Pinakes-Web
```

### Browser shows **502 Bad Gateway** from IIS but http://127.0.0.1:8080 works locally

ARR is not proxying correctly. Check:

1. ARR proxy is enabled at the **server** level in IIS Manager, not just the site.
2. The rewrite rule's action URL is `http://127.0.0.1:8080/{R:1}` — note `http`, not `https`, and exactly one slash before `{R:1}`.
3. The IIS service account can resolve `127.0.0.1`. Rare, but some hardened images break loopback.

### The articles.db file keeps growing indefinitely

The full database is a few hundred MB. If it grows far beyond that, the WAL file (`articles.db-wal`) may not be checkpointing — stop the service and run:

```powershell
C:\Pinakes\venv\Scripts\python.exe -c "import sqlite3,os; c=sqlite3.connect(os.environ['DB_PATH']); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"
Start-Service Pinakes-Web
```

### I want to rebuild the database from scratch

Take a backup first — this is not reversible, and a refetch does not bring
everything back. The fetch restores the bibliographic records from the live
sources, but not what exists only in this database: the redaction ledger
(without it, removed names come back with the next fetch), alert
subscriptions, tag votes and suggestions, the suppressed-article list, the
one-off collections described in §7, and the WAC dashboard tables
(`ingest_wac.py` rebuilds those). Export the ledger before deleting anything
and import it again before the fetch.

```powershell
& C:\Pinakes\app\deploy\windows\Backup-Pinakes.ps1 -Label before-rebuild
Set-Location C:\Pinakes\app
C:\Pinakes\venv\Scripts\python.exe redaction.py export-ledger C:\Pinakes\ledger-before-rebuild.json

Stop-Service Pinakes-Web
Remove-Item C:\Pinakes\data\articles.db*     # removes .db, .db-wal, .db-shm
Start-Service Pinakes-Web                    # startup recreates an empty schema

C:\Pinakes\venv\Scripts\python.exe redaction.py import-ledger C:\Pinakes\ledger-before-rebuild.json
C:\Pinakes\venv\Scripts\python.exe ingest_wac.py
# The export holds every removed name in plain text. Copy it off the server,
# then delete it here:
Remove-Item C:\Pinakes\ledger-before-rebuild.json

# Reseed. This takes 30-90 minutes for the full corpus. Progress shows as a
# rising article count in /health/deep (see §10), not as log lines.
& C:\Pinakes\app\deploy\windows\Invoke-PinakesAdmin.ps1 -Endpoint Fetch
```

The ledger export holds the removed names in plain text. Once the rebuild is
done, move it off the server and delete the local copy.

### How do I get a shell as the service account for debugging?

```powershell
psexec -s -i C:\Windows\System32\cmd.exe     # opens a cmd.exe running as LocalSystem
```

`psexec` is part of Microsoft Sysinternals. Use this to reproduce a permissions problem exactly as the service sees it.

---

## Appendix: one-shot install script

For a repeatable install (lab rebuilds, staging VMs), the block below is the minimum set of commands from §3–§8 collapsed into a single PowerShell script. Review it, adapt the paths and the public address, and run as Administrator. It leaves the reverse proxy (§9.2) to you.

```powershell
# install-pinakes.ps1 — run elevated
$ErrorActionPreference = "Stop"

$Root    = "C:\Pinakes"
$Port    = "8080"
$SiteUrl = "https://pinakes.example.org"     # this host's public address

New-Item -ItemType Directory -Force -Path "$Root\data","$Root\logs" | Out-Null

winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
winget install --id Git.Git             --silent --accept-source-agreements --accept-package-agreements
winget install --id NSSM.NSSM           --silent --accept-source-agreements --accept-package-agreements

# Refresh PATH in current session
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")

git clone https://github.com/justalewis/Rhet-Comp-Index.git "$Root\app"
python -m venv "$Root\venv"
$Py = "$Root\venv\Scripts\python.exe"
& $Py -m pip install --upgrade pip
& "$Root\venv\Scripts\pip.exe" install -r "$Root\app\requirements.txt"
& "$Root\venv\Scripts\pip.exe" install waitress

# Settings (§6.1). SetEnvironmentVariable(..., "Machine") does not change this
# session, so each value is also copied into it: without that, fetcher.py below
# would seed $Root\app\articles.db instead of DB_PATH.
$Settings = [ordered]@{
    DB_PATH                   = "$Root\data\articles.db"
    PORT                      = $Port
    FLASK_ENV                 = "production"
    PINAKES_SITE_URL          = $SiteUrl
    PINAKES_TRUSTED_IP_HEADER = "CF-Connecting-IP"     # behind Cloudflare
    PINAKES_ADMIN_TOKEN       = (& $Py -c "import secrets; print(secrets.token_urlsafe(48))")
    PINAKES_SECRET_KEY        = (& $Py -c "import secrets; print(secrets.token_urlsafe(48))")
}
foreach ($name in $Settings.Keys) {
    [Environment]::SetEnvironmentVariable($name, $Settings[$name], "Machine")
    Set-Item -Path "Env:$name" -Value $Settings[$name]
}
# Also set CROSSREF_MAILTO and PINAKES_DATASTORIES_PASSWORD (§6.1) by hand.

# Initial seed (blocking; CrossRef journals only) and the /wac tables (§7)
Push-Location "$Root\app"
& $Py fetcher.py
& $Py ingest_wac.py
Pop-Location

nssm install Pinakes-Web "$Root\venv\Scripts\waitress-serve.exe" "--host=127.0.0.1 --port=$Port --threads=8 app:app"
nssm set    Pinakes-Web AppDirectory    "$Root\app"
nssm set    Pinakes-Web AppStdout       "$Root\logs\web-stdout.log"
nssm set    Pinakes-Web AppStderr       "$Root\logs\web-stderr.log"
nssm set    Pinakes-Web AppRotateFiles  1
nssm set    Pinakes-Web AppRotateOnline 1
nssm set    Pinakes-Web AppRotateBytes  10485760
nssm set    Pinakes-Web AppEnvironmentExtra "DB_PATH=$Root\data\articles.db" "PORT=$Port" "FLASK_ENV=production"
nssm set    Pinakes-Web Start SERVICE_AUTO_START

Start-Service Pinakes-Web

# Recurring jobs. Reads PINAKES_ADMIN_TOKEN from Machine scope, set above.
& "$Root\app\deploy\windows\Install-PinakesTasks.ps1" -IncludeUpdateCheck

# First fetch through the service: adds the RSS and scraped journals.
& "$Root\app\deploy\windows\Invoke-PinakesAdmin.ps1" -Endpoint Fetch

Write-Host "Install complete. Browse to http://127.0.0.1:$Port, then set up the reverse proxy (section 9.2)." -ForegroundColor Green
```

---

*Document version: 2026-10-15. Tracks Rhet-Comp-Index `main` (Python 3.12,
schema v17, `requirements.txt`: flask, flask-compress, flask-limiter,
python-dotenv, requests, curl_cffi, feedparser, beautifulsoup4, lxml, networkx,
maxminddb, sentry-sdk, boto3, zstandard, pyrage, plus gunicorn and apscheduler,
which nothing runs on Windows; nothing imports apscheduler since `scheduler.py`
was removed). This revision describes the production setup at the WAC
Clearinghouse: the full environment-variable set (§6), Waitress on
`127.0.0.1` with 8 threads, Cloudflare in front of IIS, the usage-statistics
tasks, and the steps a fresh seed misses. The 2026-08-27 revision dropped the
`Pinakes-Scheduler` service, which was defined against `scheduler.py` — a file
removed from the repository in commit `fa25cae`. Recurring jobs run as Windows
scheduled tasks (§8.3), and updates run through
`deploy/windows/Update-Pinakes.ps1` (§11).*
