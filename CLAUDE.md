# Working on Pinakes

A disciplinary index for rhetoric, composition and writing studies. Flask + SQLite,
no database server, no message broker, no paid API keys. The only persistent state is
one `.db` file.

Canonical host: **`pinakes.wacclearinghouse.org`**. `testpinakes.wacclearinghouse.org`
is a second binding on the *same* site, app and database — there is no separate test
environment.

Two live deployments run this code:

| | Host | Driven by |
|---|---|---|
| Fly.io | `pinakes.xyz` | `.github/workflows/*.yml` (cron, alerts, maintenance) |
| Windows Server | `pinakes.wacclearinghouse.org` | Windows scheduled tasks (`deploy/windows/`) |

They have **separate databases** and both run their own fetch, so they drift. See
*Open items*.

## Running it

```bash
pip install -r requirements-dev.txt
pytest                  # 645 tests, ~30s. Network and slow tests deselected by default.
python app.py           # dev server on :5000
```

`conftest.py` redirects `DB_PATH` to a temp file before importing any production module —
`app.py` runs `init_db()` at import time, so without that it would touch a real database.

CI is `.github/workflows/test.yml`: `pytest --cov` on 3.12, triggered on push and PR.

## Conventions

- **PowerShell targets 5.1** (what Server 2019/2022 ship). In `deploy/windows/`: no `&&`,
  no ternary, no null-coalescing, no `-AsHashtable`.
- **Configuration comes from environment variables**, read through `Get-PinakesSetting`
  (PowerShell) or `os.environ.get` with a default (Python). Nothing is hardcoded to one
  machine. A new setting follows that pattern or it will break the other deployment.
- **SQLite only.** WAL mode, `busy_timeout=30000`. Never copy a live `.db` file — use
  `deploy/windows/sqlite_backup.py`, which goes through SQLite's online backup API. A
  file copy can capture a torn page that looks fine until you need it.
- Tests assert against constants, not literals, where a value can move — see
  `tests/test_feeds.py` using `SITE_URL`.

## Invariants — do not "fix" these

**`feeds.TAG_AUTHORITY` is `"pinakes.xyz,2026"` and must stay that way.** Atom entry ids
are `tag:pinakes.xyz,2026:article/<id>` — RFC 4151 identifiers, not addresses. They never
resolve, so an authority naming a host the project no longer serves from is *correct*.
Repointing it re-mints every id in every feed, so every subscriber's reader re-shows the
entire backlog as unread: silent, un-notifiable, irreversible. `SITE_URL` in
`blueprints/feeds.py` is the one that tracks the canonical host.
`test_tag_authority_does_not_follow_the_site_domain` guards this.

**Never apply a redaction by editing `articles.authors` directly.** Go through
`redaction.py` (`redact`, `unredact`, `export`, `import`, `resweep`). The ledger is the
durable artifact — every fetch re-applies it, and `restore_local.py` re-applies it to a
restored database. A hand-edit is silently undone by the next fetch.

**`restore.py` re-applies the redaction ledger; `--verify` does not.** Promoting a
database any other way can resurrect a name someone asked to have removed. Use
`restore_local.py` for a backup file already on disk.

## Feature flags, both default off

| Flag | Guards | Why it ships off |
|---|---|---|
| `PINAKES_ALERTS_ENABLED` | email digests, `/alerts/*` | needs a verified sending domain |
| `PINAKES_REDACTION_FORM_ENABLED` | `/redaction-request` | needs working SMTP |

Both 404 the feature when off rather than failing softly. The redaction form in particular
*used* to accept a request, tell the author "submitted", and drop it, because
`notifications.send_email` logs and returns `False` when SMTP is unset and never raises.
With the flag off, `/about#author-privacy` asks authors to email instead and requests are
applied with the `redaction.py` CLI.

---

# The Windows Server deployment

This section is portable — a sibling app on the same box needs all of it.

| | |
|---|---|
| Host | Windows Server, IIS + Application Request Routing, **~40 Clearinghouse sites sharing one `applicationHost.config`** |
| Service manager | NSSM. On this box it lives at `C:\nssm\win64\nssm-2.24\nssm-2.24\win64\nssm.exe`, *not* on `PATH` in System32 as the install guide suggests |
| Service name | **`Pinakes`** — *not* `Pinakes-Web` as the install guide says. Scripts read `PINAKES_SERVICE_NAME` |
| App server | `waitress-serve --listen=0.0.0.0:8080 app:app`, run as `NT AUTHORITY\SYSTEM` |
| Python | venv at `C:\Pinakes\venv`; system 3.12 at `C:\Program Files\Python312` |
| Layout | `C:\Pinakes\{app,venv,data,logs,backups}`; override with `PINAKES_ROOT` |
| Reverse proxy | site `pinakes`, rule `ReverseProxyInboundRule1`: `(.*)` → `http://127.0.0.1:8080/{R:1}`. Physical path is a deliberately empty placeholder (`C:\inetpub\Pinakes`) |
| TLS | `*.wacclearinghouse.org` wildcard in the **`WebHosting`** store, on the `0.0.0.0:443` catch-all binding — not per-host SNI |
| Mail | no SMTP configured |
| Scheduled jobs | `Install-PinakesTasks.ps1`; all run as SYSTEM |

**Config layering:** Machine-scope environment variables, overlaid by NSSM's
`AppEnvironmentExtra`. **NSSM wins on conflict.** `Get-PinakesSetting` reads Machine scope
from the registry directly, so no restart is needed for script-facing settings — but the
*app* only sees a change after `Restart-Service`.

Read NSSM's block from the registry rather than parsing `nssm.exe` output — it's a
`REG_MULTI_SZ`, which PowerShell gives you as a clean string array:

```powershell
(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\Pinakes\Parameters').AppEnvironmentExtra
```

## Traps that cost real time

1. **`DB_PATH` unset is silent and catastrophic.** `db/__init__.py` falls back to a path
   relative to the package, and `sqlite3.connect()` *creates* a missing file. The result:
   every endpoint returns 200, `/health/ready` reports `db reachable`, and the index is
   empty. This ran undetected for weeks. `Test-PinakesHealth.ps1` checks
   `/api/articles?limit=1` for a non-zero `total` specifically to catch it.

2. **`preserveHostHeader` is `False` on ARR.** So Flask sees `Host: 127.0.0.1:8080` and
   `url_for(..., _external=True)` builds `http://127.0.0.1:8080/...`. `app.py` installs
   `ProxyFix(x_proto=1, x_host=1)`, but nothing sets `X-Forwarded-Host`.
   `reverseRewriteHostInResponseHeaders` is `True`, which fixes `Location:` headers only —
   so browsing looks healthy while emailed links and OAuth `redirect_uri` values are
   unusable. **Latent today** (no SMTP, no ORCID), but it must be fixed before either is
   switched on. Fix it **per-rule**, not server-wide: six other sites proxy to
   `wac.wacclearinghouse.org` and would break if ARR started forwarding the original Host.

3. **Scheduled tasks run as SYSTEM and read Machine-scope env only.** A token that exists
   only in NSSM's block gives `Get-PinakesAdminToken` nothing, and a *different* value
   there gives 403.

4. **`Update-Pinakes.ps1` runs `git reset --hard` then `git clean -fd`.** Untracked,
   non-ignored files in `C:\Pinakes\app` are deleted. Check `git status --short` before
   deploying; `-Force` additionally discards local modifications.

5. **`whoami /groups` in a non-elevated shell shows Administrators as "deny only".** That's
   UAC token filtering, not missing rights. Check from an elevated window.

6. **`$_` inside a `switch` scriptblock is the switch's item**, not the enclosing `catch`'s
   `ErrorRecord`. This swallowed every error message from `Invoke-PinakesAdmin.ps1`.
   Capture `$err = $_` before the switch.

7. **A PowerShell function returns everything it doesn't consume.** A bare native call
   inside one leaks its stdout into the return value. Assign it:
   `$out = & $exe $args`.

## Adding a sibling app to this box

Each needs its own: port (8080 is taken), NSSM service, IIS site + host binding, and ARR
rewrite rule. The wildcard cert on the catch-all binding covers any new
`*.wacclearinghouse.org` name, so TLS is usually free.

`deploy/windows/Pinakes.Common.ps1` is written so every path and name comes from an
environment variable — a sibling `*.Common.ps1` is largely a rename. Reuse
`sqlite_backup.py` verbatim; it's stdlib-only and takes source and destination as
arguments.

Before changing anything server-wide, take a restore point — it's additive:

```powershell
Backup-WebConfiguration -Name "before-change-$(Get-Date -Format yyyyMMdd-HHmm)"
# undo: Restore-WebConfiguration -Name "<that name>"
```

## Related: the Clearinghouse DOI prefix

`data/wac_crossref_dump.jsonl` holds 4,284 records under Crossref prefix **`10.37514`** —
the Clearinghouse's own. The suffix grammar is
`10.37514/<journal-slug>-j.<year>.<volume>.<issue>.<article>`, e.g.
`10.37514/wac-j.1997.8.01.14`, `10.37514/jbw-j.2009.28.2.06`. Anything minting new DOIs
under that prefix should read the convention off these records rather than invent one, and
should be built against Crossref's sandbox first — a deposit is hard to retract, and the
prefix is shared.

## Open items

- **`*.wacclearinghouse.org` wildcard expires 2026-10-06.** On the catch-all 443 binding,
  so it is not only Pinakes. Per-host certs run to late November and look automated; the
  wildcard may be on a manual renewal nobody tracks.
- **`pinakes.xyz` must keep serving or 301-ing its feed paths.** Existing RSS subscribers
  have that host in their readers, cannot be enumerated, and will not be told. Retiring it
  strands them silently.
- **Fly and Windows databases drift.** Both run their own daily fetch, and entry ids come
  from each one's SQLite primary key under the same tag authority — so the same id can
  eventually mean different articles. Whichever host is canonical should be the only one
  fetching.
- **The `X-Forwarded-Host` fix** (trap 2), needed before alerts or the redaction form.
