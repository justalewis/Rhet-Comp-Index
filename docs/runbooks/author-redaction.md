# Author redaction runbook

How to run the author opt-out ("Name Redacted by Author Request") feature: configure it, review requests, redact by hand, and handle the durability edge cases (restore, backups). For how it works under the hood — the token model, the suppression spine, the data model — see the design doc, [author-redaction.md](../author-redaction.md).

> **The public request form ships off.** `PINAKES_REDACTION_FORM_ENABLED`
> defaults to `0`, and `/redaction-request` redirects to `/about#author-privacy`
> until it is set to `1`. The form's value is an email round-trip proving the
> requester controls the address they typed, and without working SMTP it
> accepted requests, told the author "submitted", and dropped them —
> `send_email` logs and returns `False` rather than raising. A deployment
> without SMTP takes requests by email and applies them with the CLI below;
> `/about` says so. Everything else in this runbook — the review queue, the
> admin page, the ledger, the durability rules — applies either way.

## What it does, in one paragraph

A redacted author's name is replaced everywhere it is used as identity — `articles.authors`, the normalized author/affiliation/institution tables, books, and (best-effort) the free-text reference blobs — with a stable per-author token like `Redacted Author 7f3a2c`. Templates render that token as "Name Redacted by Author Request." The scholarship stays in the index and keeps counting in every metric, because the token becomes the new identity key. The `redaction_ledger` table retains the real name (locked: no render/API/export path reads it) so the suppression can be re-applied after a fetch and reversed on request.

## Configuration (machine environment variables)

On the Clearinghouse server these are Machine-scope environment variables. Set one, then restart the service so the app picks it up:

```powershell
[Environment]::SetEnvironmentVariable('REDACTION_SALT', '<value>', 'Machine')
Restart-Service Pinakes        # Pinakes-Web on a server installed from the guide
```

If the running service still doesn't see a variable, set it on the NSSM service instead, keeping the ones already there (the operations runbook shows how under [Visitor IP addresses](../WINDOWS-SERVER-RUNBOOK.md#visitor-ip-addresses)).

| Var | Purpose | Required? |
|---|---|---|
| `PINAKES_REDACTION_FORM_ENABLED` | `1` turns the public request form on. Default `0`: `/redaction-request` redirects to `/about#author-privacy` and a `POST` to it returns 404. | Off unless SMTP works |
| `PINAKES_SITE_URL` | Base for every link that leaves the site: the emailed verification link, the admin notice, and the ORCID `redirect_uri`. Waitress strips the forwarding headers, so without it those links point at `http://127.0.0.1:8080`. | Yes, on the server |
| `REDACTION_SALT` | Salts the token hash so the public token can't be brute-forced to the name. If unset, a random salt is generated once and stored in `redaction_meta`. Set it explicitly in production so it's stable and known. | Recommended |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Send the email-verification link. If unset, the link is logged instead of sent (fine for dev; not for prod). | For the email path |
| `SMTP_REPLY_TO` | Reply-To on outgoing mail; also the fallback recipient for the "request awaiting review" notification. | Recommended |
| `REDACTION_NOTIFY_EMAIL` | Where to email you when a request is verified and waiting. Falls back to `SMTP_REPLY_TO`; no notice if neither is set. | Optional |
| `ORCID_CLIENT_ID`, `ORCID_CLIENT_SECRET` | ORCID OAuth verification (the "Verify with ORCID" button appears only when both are set). `ORCID_ENV=sandbox` to test against sandbox.orcid.org. Register the redirect URI `https://pinakes.wacclearinghouse.org/redaction-request/orcid/callback` in the ORCID developer app. | For the ORCID path |

`PINAKES_ADMIN_TOKEN` (already set for the admin endpoints the scheduled tasks call) gates the review queue and the admin page. `PINAKES_SECRET_KEY` signs the ORCID `state`. Set it in production: the app refuses to start without it only on Fly (`FLY_APP_NAME` set). On the Windows server an unset key becomes a random per-process key (the app logs a warning at startup and keeps going), so an ORCID verification that spans a service restart fails.

**SMTP host:** any SMTP provider works. A transactional provider sending from a verified `pinakes.wacclearinghouse.org` subdomain (e.g. Resend) gives the best deliverability; a Gmail account with an App Password (`smtp.gmail.com:587`, `SMTP_FROM` = the Gmail address) is the zero-DNS fallback.

## The request flow

**With the form off (the default),** requests arrive by email, as `/about#author-privacy` asks. Check the sender is plausibly the author and apply the request with the CLI ([Redact by hand](#redact-by-hand-cli-no-request-needed) below; the operations runbook walks through it under [Author name-removal requests](../WINDOWS-SERVER-RUNBOOK.md#author-name-removal-requests)). Nothing reaches the review queue, which only holds requests made through the form.

**With the form on:**

1. Author submits `/redaction-request`. Turning the form on doesn't add a link to it: the nav's About submenu, the footer, and `/about` all point to `/about#author-privacy`, whose text asks authors to email. Give an author the URL directly.
2. They verify identity — ORCID OAuth, or an emailed one-time link.
3. On verification you get an email (to `REDACTION_NOTIFY_EMAIL`, or your `SMTP_REPLY_TO`) with the claimed name and a link to the review page. The request sits in the queue; **you approve by hand** before anything changes.

### Review and decide — the admin page (primary)

Go to **`https://pinakes.wacclearinghouse.org/admin/redactions`**, paste your `PINAKES_ADMIN_TOKEN`, and click **Connect**. Verified requests appear under "Awaiting your review" with **Approve & redact** (confirms first) / **Deny** / an **audit trail** toggle; "Load decided history" shows past decisions. The token is held only in that browser tab (`sessionStorage`) and never embedded in the page — all data loads through the token-gated API below.

### Review and decide — the API (equivalent)

```bash
TOKEN=...   # PINAKES_ADMIN_TOKEN

# List pending + verified requests (includes requester email/ORCID — admin only)
curl -s -H "Authorization: Bearer $TOKEN" \
  https://pinakes.wacclearinghouse.org/api/admin/redaction-requests | jq

# Audit trail for one request (created / verified / approved / denied)
curl -s -H "Authorization: Bearer $TOKEN" \
  https://pinakes.wacclearinghouse.org/api/admin/redaction-request/42/audit | jq

# Approve request #42 → writes the audit row, then redacts the author
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  https://pinakes.wacclearinghouse.org/api/admin/redaction-request/42/approve

# Or deny (audited; no change)
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  https://pinakes.wacclearinghouse.org/api/admin/redaction-request/42/deny
```

Only `verified` requests can be approved. The audit row is written *before* the redaction fires, so there's proof-of-review even if the request is later purged.

## Redact by hand (CLI, no request needed)

Run on the server, from the app directory, with the venv Python. The CLI works on the database `DB_PATH` names — the Machine-scope variable points it at `C:\Pinakes\data\articles.db`. (Locally, set `DB_PATH` to a copy.)

```powershell
cd C:\Pinakes\app
C:\Pinakes\venv\Scripts\python.exe redaction.py redact "Jane Q. Smith" --variant "J. Smith" --variant "Jane Smith" --by jlewis
C:\Pinakes\venv\Scripts\python.exe redaction.py list        # show the ledger
C:\Pinakes\venv\Scripts\python.exe redaction.py resweep     # re-apply the whole ledger (idempotent)
C:\Pinakes\venv\Scripts\python.exe redaction.py unredact "Redacted Author 7f3a2c"   # reverse a redaction
```

Repeat `--variant` for every spelling the name appears under. `--by` is stored in the ledger's `created_by`; a CLI redaction writes no request or audit row. Quote the token for `unredact`: it contains a space.

`redact` and `resweep` each rebuild the FTS index and bust the Datastories cache. After redacting in production, also re-run the cache pre-warm so the first visitor isn't slow:

```powershell
& C:\Pinakes\app\deploy\windows\Invoke-PinakesAdmin.ps1 -Endpoint Prewarm
```

From off the server, the same thing is `curl -s -X POST -H "Authorization: Bearer $TOKEN" https://pinakes.wacclearinghouse.org/api/admin/prewarm`.

Nothing scheduled runs `resweep` on the server. The ingest choke-points keep a redacted name out of new rows; the full resweep runs at the end of a deep refresh (`Invoke-PinakesAdmin.ps1 -Endpoint DeepFetch`) or when you run it here.

## Keep the ledger off-box

A restore from a backup taken *before* a redaction won't contain that ledger row, so the restored DB wouldn't know to re-apply it. Snapshot the ledger somewhere durable (your password manager / a private gist) whenever you add a redaction:

```powershell
cd C:\Pinakes\app
New-Item -ItemType Directory -Force C:\Pinakes\restore | Out-Null
C:\Pinakes\venv\Scripts\python.exe redaction.py export-ledger C:\Pinakes\restore\ledger.json
```

The file contains plaintext names. Copy it off the server to secure storage, then delete the copy on the server.

## Restore (durability)

A backup's own ledger holds only the redactions made before it was taken. Whether the restore re-applies redactions for you depends on how you restore:

| Restore path | Re-applies the ledger? |
|---|---|
| `restore_local.py --file <downloaded .db.zst.age>` | Yes: the restored file's own ledger, after merging `--redaction-ledger ledger.json` if given. Stops with exit code 1 if the re-apply fails |
| `restore.py --latest` / `--date` (downloads from the bucket) | Yes, the same way. If the re-apply fails it prints a `WARNING` and still exits 0 |
| Copying a local backup from `C:\Pinakes\backups` into place | No. Run `import-ledger` yourself, before starting the service |
| `restore.py --verify` | No. It only checks the file |

For any restore from a backup that might predate a redaction, merge the newest off-box export into the restored database. `import-ledger` skips rows it already has, then resweeps:

```powershell
cd C:\Pinakes\app
C:\Pinakes\venv\Scripts\python.exe redaction.py import-ledger C:\Pinakes\restore\ledger.json
```

`redaction.py resweep` only re-applies the ledger the database already has, so it is enough only when the backup postdates every redaction. The full procedure, including how to recover a newer ledger from the database you replaced, is in the [disaster-recovery runbook](disaster-recovery.md).

## Hard forget: backups

Every backup of the database holds the name. A backup taken before the redaction has it throughout the data; one taken after has it in the ledger, which keeps the plaintext name by design (that is what makes re-applying and reversing possible). The live index is where the redaction is maintained: the ingest choke-points keep the name out of new rows, and a resweep (deep refresh, or by hand) catches anything that slipped past them. The About page says a removed name stays in the site's backups.

The copies taken before the redaction are in:

- **`C:\Pinakes\backups` on the server.** Nightly and pre-deploy copies of `articles.db`, unencrypted. The newest 10 are kept (`PINAKES_BACKUP_KEEP`), so they rotate out as new backups are taken, sooner when there are deploys. To purge them early, take a fresh backup first (`& C:\Pinakes\app\deploy\windows\Backup-Pinakes.ps1 -Label after-redaction`), then delete the older `articles-*.db` files by hand. That also removes those restore points.
- **The offsite bucket.** Encrypted. `backup.py` prunes to 30 daily / 26 weekly / 12 monthly, but only inside a `run_backup()`, and no offsite backup has been scheduled since 2026-09-19. Old objects stay until someone runs `Invoke-PinakesAdmin.ps1 -Endpoint Backup` or deletes them, unless the bucket has its own lifecycle rule (check the bucket). To purge by hand, `restore.py --list` shows the keys; delete them in the B2 console or with `aws s3 rm` against the bucket.
- **The former Fly app's volume.** If it still exists, `/data/articles.db` there is a separate copy that never received redactions made on the Clearinghouse server. `fly-deploy.yml` still deploys that app, so check with `flyctl` before telling an author the name is gone.
- **Anything else that copies `C:\Pinakes`**, such as a server-level backup, if the Clearinghouse runs one. Check with them.

Off-box ledger exports hold the plaintext name too.

## What's deliberately out of scope (v1)

- **Names inside other articles' bibliographies** (`citations.raw_reference`) are scrubbed best-effort — exact ledger name-forms only. Variant coverage is incomplete; the About page says so.
- **Co-authors' names** on a shared paper are untouched. A co-author can't force-redact a byline they don't own.
