# Disaster recovery runbook

Restore the Pinakes database on the production server, the Windows Server at the WAC Clearinghouse that runs `pinakes.wacclearinghouse.org`. Written for a sleep-deprived operator at 2 AM. Read it twice; do it once.

Every command runs in an elevated PowerShell window on the server. Paths are the defaults from the operations runbook's [Conventions](../WINDOWS-SERVER-RUNBOOK.md#conventions): the database is `C:\Pinakes\data\articles.db`, the app is the service `Pinakes-Web`.

## What backups exist

| Copy | Where | Made by | How many are kept | Encrypted | Survives losing the server |
|---|---|---|---|---|---|
| Nightly | `C:\Pinakes\backups\articles-<yyyyMMdd-HHmmss>-nightly.db` | The `Pinakes-NightlyBackup` task at 02:00 server time: `Backup-Pinakes.ps1` → `sqlite_backup.py` (SQLite online backup, then `PRAGMA quick_check`) | The newest 10 `articles-*.db` files (`PINAKES_BACKUP_KEEP`) | No | No — same disk |
| Pre-deploy | `C:\Pinakes\backups\articles-<yyyyMMdd-HHmmss>-pre-<sha>.db` | `Update-Pinakes.ps1`, before every deploy | Count toward the same 10 | No | No |
| Usage | `C:\Pinakes\backups\usage-<yyyyMMdd-HHmmss>-nightly.db` | The same nightly task, once `usage.db` exists | The newest 10 `usage-*.db` files | No | No |
| Offsite | An S3-compatible bucket (Backblaze B2), keys like `2026/09/18/articles-20260918T083119Z.db.zst.age` (UTC) | `POST /api/admin/run-backup` → `backup.py` `run_backup()`. On this server that happens only when someone runs `Invoke-PinakesAdmin.ps1 -Endpoint Backup`; no scheduled task calls it | 30 daily / 26 weekly / 12 monthly, pruned only during a `run_backup()` | Yes (age) | Yes |

Three things about those copies matter when you are choosing one:

- **The offsite copy may be old.** GitHub Actions (`cron.yml`) used to call the offsite backup nightly against `pinakes.xyz`; its schedule has been commented out since 2026-09-19, and nothing on this server replaced it. The newest object in the bucket is from the last manual run, or from the former Fly deployment. `restore.py --list` (below) shows the dates. A successful offsite run touches `C:\Pinakes\data\scheduler.heartbeat`, which `/health/deep` reports as `scheduler.heartbeat_age_seconds`.
- **Nothing checks the bucket on a schedule.** `backup.py`'s `verify_latest_backup()` is called only by the tests. Backup failures reach Sentry only as exceptions, and only when `SENTRY_DSN` is set.
- **A nightly copy that failed its check is still in the folder.** `sqlite_backup.py` deletes its output only when the copy itself fails; a copy that fails `quick_check` stays under the normal name, and that night's line in `C:\Pinakes\logs\admin-tasks.log` reads `ERROR`. Step A3 checks the file before you trust it.

## Before you start — what you need

- Administrator access to the server.
- For an offsite restore only:
  - **The age private key** (`AGE-SECRET-KEY-1...`). It belongs off the server:
    - In your password manager.
    - On a printed paper you stored somewhere safe.
    - Never in a cloud document, never in a chat message.

    If you don't have the private key, the offsite backups are unrecoverable. Stop. Call whoever might have a copy. The local copies need no key.
  - Either the six `PINAKES_BACKUP_*` variables (bucket, endpoint, region, access key id, secret key, age public key) in the shell you run `restore.py` from, or access to the bucket's web console so you can download the backup file yourself.
- The newest off-box export of the redaction ledger, if you keep one (see [Keep the ledger off-box](author-redaction.md#keep-the-ledger-off-box)). A backup only knows about the redactions made before it was taken.

## Which restore

- **The server is fine but the database is not** (corruption, a bad deploy, a bad migration): [Restore A](#restore-a--from-a-local-backup), from `C:\Pinakes\backups`.
- **The server or its disk is gone, or every local copy is bad**: [Restore B](#restore-b--from-the-encrypted-offsite-backup), from the bucket.

For most disasters, pick the newest good backup. If the newest is suspect (the corruption was already in the 02:00 snapshot), pick an older one.

## Restore A — from a local backup

This is the restore under [When an update fails](../WINDOWS-SERVER-RUNBOOK.md#when-an-update-fails) in the operations runbook, with two additions: the damaged database is kept for the post-mortem instead of overwritten, and redactions are re-applied before the app serves the file. If you are undoing a bad migration, also put the code back to the version that matches the backup, as that section says, or the migration will run again.

```powershell
# A1. Stop the app, and keep the nightly backup from firing mid-restore.
#     A backup run prunes C:\Pinakes\backups to the newest 10 files.
Stop-Service Pinakes-Web
Disable-ScheduledTask -TaskName Pinakes-NightlyBackup | Out-Null

# A2. List the backups, and copy the one you'll use out of the rotation.
Get-ChildItem C:\Pinakes\backups\articles-*.db |
    Sort-Object LastWriteTime -Descending |
    Format-Table Name, LastWriteTime, Length
Get-Content C:\Pinakes\logs\admin-tasks.log -Tail 40   # ERROR on a "backup" line = that night's copy failed its check
New-Item -ItemType Directory -Force C:\Pinakes\restore | Out-Null
Copy-Item C:\Pinakes\backups\articles-<timestamp>-<label>.db C:\Pinakes\restore\

# A3. Check it.
cd C:\Pinakes\app
C:\Pinakes\venv\Scripts\python.exe restore.py --verify C:\Pinakes\restore\articles-<timestamp>-<label>.db
# Expected: "Integrity OK. <N> articles." Anything else: go back to A2 and pick an older file.
# Eyeball N: it should be close to what the site showed before the failure.

# A4. Move the damaged database aside, together with its -wal and -shm files.
#     A -wal left beside the restored file would be read as belonging to it.
$aside = "C:\Pinakes\restore\replaced-$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory $aside | Out-Null
Move-Item C:\Pinakes\data\articles.db $aside
Move-Item C:\Pinakes\data\articles.db-wal, C:\Pinakes\data\articles.db-shm $aside -ErrorAction SilentlyContinue

# A5. Put the backup in place.
Copy-Item C:\Pinakes\restore\articles-<timestamp>-<label>.db C:\Pinakes\data\articles.db

# A6. Re-apply redactions before anyone can see the file.
#     import-ledger merges the export (rows already present are skipped), then resweeps.
C:\Pinakes\venv\Scripts\python.exe redaction.py import-ledger <path to your newest ledger export>
#     If you're sure nobody was redacted after the backup was taken, this is enough instead:
#     C:\Pinakes\venv\Scripts\python.exe redaction.py resweep

# A7. Start the app, turn the nightly backup back on, and check.
Start-Service Pinakes-Web
Enable-ScheduledTask -TaskName Pinakes-NightlyBackup | Out-Null
Invoke-RestMethod http://127.0.0.1:8080/health/ready
# Expected: status "ok", db "reachable". If the connection is refused, wait a
# minute: the app runs its schema migrations before it starts answering.
& C:\Pinakes\app\deploy\windows\Test-PinakesHealth.ps1
# Expected: all PASS, including "Index contains articles".

# A8. Compare counts with what A3 printed, then warm the caches.
$h = @{ Authorization = "Bearer " + [Environment]::GetEnvironmentVariable('PINAKES_ADMIN_TOKEN', 'Machine') }
(Invoke-RestMethod http://127.0.0.1:8080/health/deep -Headers $h).counts
& C:\Pinakes\app\deploy\windows\Invoke-PinakesAdmin.ps1 -Endpoint Prewarm
```

`redaction.py` works on the database that `DB_PATH` names, which is the Machine-scope variable pointing at `C:\Pinakes\data\articles.db`. **With no ledger export**, the database you moved aside holds the newer ledger if it still opens. Export it from there, put `DB_PATH` back, and import:

```powershell
$env:DB_PATH = "$aside\articles.db"
C:\Pinakes\venv\Scripts\python.exe redaction.py export-ledger C:\Pinakes\restore\ledger.json
$env:DB_PATH = [Environment]::GetEnvironmentVariable('DB_PATH', 'Machine')
C:\Pinakes\venv\Scripts\python.exe redaction.py import-ledger C:\Pinakes\restore\ledger.json
```

**`usage.db`**, if it is damaged too: with the service stopped, move `C:\Pinakes\data\usage.db` and its `-wal` / `-shm` aside the same way and copy the matching `usage-*.db` from `C:\Pinakes\backups` into place as `usage.db`.

## Restore B — from the encrypted offsite backup

On a rebuilt server, follow [WINDOWS-SERVER-INSTALL.md](../WINDOWS-SERVER-INSTALL.md) until the `Pinakes-Web` service exists, then restore here rather than seeding the database with a full fetch. Both restore tools write a new file, run `PRAGMA integrity_check`, and re-apply the redaction ledger to it. Neither touches the live database.

```powershell
cd C:\Pinakes\app
New-Item -ItemType Directory -Force C:\Pinakes\restore | Out-Null

# B1. See what's in the bucket (needs the six PINAKES_BACKUP_* variables).
C:\Pinakes\venv\Scripts\python.exe restore.py --list
# Something like:
#   Date (UTC)                   Size  Key
#   --------------------------------------------------------------------------------
#   2026-09-18T08:31:19+00:00   87.3 MB  2026/09/18/articles-20260918T083119Z.db.zst.age
#   ...
# Without the variables, find the file in the bucket's web console instead and
# download it to C:\Pinakes\restore.

# B2. Write the private key to a file holding ONLY the AGE-SECRET-KEY-1... line.
#     A file straight from age-keygen, with its "# created" and "# public key"
#     comment lines, is rejected by both tools.
#     Path used below: C:\Pinakes\restore\age.key

# B3a. Restore a file you downloaded:
C:\Pinakes\venv\Scripts\python.exe restore_local.py `
    --file "C:\Pinakes\restore\articles-20260918T083119Z.db.zst.age" `
    --age-key "C:\Pinakes\restore\age.key" `
    --out "C:\Pinakes\restore\restored.db" `
    --redaction-ledger "C:\Pinakes\restore\ledger.json"
# Leave out --redaction-ledger if you have no export.
# Expected, in order: "Integrity OK. <N> articles. (<size>)",
# "Author-redaction re-applied: {...}", then "Restored file is ready. It is NOT live yet."
# If the redaction re-apply fails, restore_local.py stops with exit code 1. Fix that before promoting.

# B3b. Or download and restore in one step (needs the six variables):
C:\Pinakes\venv\Scripts\python.exe restore.py --latest `
    --out C:\Pinakes\restore\restored.db `
    --age-key C:\Pinakes\restore\age.key `
    --redaction-ledger C:\Pinakes\restore\ledger.json
# or --date 2026-09-18 for the newest backup taken on that UTC day.
# Expected: "Integrity OK. Restored to ...", then "Author-redaction re-applied to restored DB: {...}".
# restore.py then prints "Next steps" for the former Fly app. Ignore them.
# Unlike restore_local.py, restore.py only prints a WARNING if the re-apply fails
# and still exits 0. Look for that line.

# B4. Promote it. Same moves as Restore A, steps A1, A4 and A7.
Stop-Service Pinakes-Web -ErrorAction SilentlyContinue
Disable-ScheduledTask -TaskName Pinakes-NightlyBackup | Out-Null
Get-ChildItem C:\Pinakes\restore\restored.db*      # should list only restored.db
# If a restored.db-wal is there, fold it into the file first:
#   C:\Pinakes\venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect(r'C:\Pinakes\restore\restored.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"
# On a rebuilt server there may be no old database to move aside; skip those lines.
$aside = "C:\Pinakes\restore\replaced-$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory $aside | Out-Null
Move-Item C:\Pinakes\data\articles.db $aside
Move-Item C:\Pinakes\data\articles.db-wal, C:\Pinakes\data\articles.db-shm $aside -ErrorAction SilentlyContinue
Move-Item C:\Pinakes\restore\restored.db C:\Pinakes\data\articles.db
Start-Service Pinakes-Web
Enable-ScheduledTask -TaskName Pinakes-NightlyBackup | Out-Null
Invoke-RestMethod http://127.0.0.1:8080/health/ready
& C:\Pinakes\app\deploy\windows\Test-PinakesHealth.ps1

# B5. Take the key off the server.
Remove-Item C:\Pinakes\restore\age.key
```

If you restored without `--redaction-ledger` and find an export later, run `redaction.py import-ledger` on the live database (Restore A, step A6). It is safe to run more than once.

## After you finish

- Delete the working files that hold plaintext author names: any `ledger.json` under `C:\Pinakes\restore`. Keep the moved-aside database until the post-mortem is written, then delete it.
- Notify any users you know depend on the site that it's back.
- Collect the evidence while it's fresh: `C:\Pinakes\logs\web-stderr.log` around the failure (application log lines land there), `admin-tasks.log`, and the `update-<timestamp>.log` of any recent deploy.
- Write a short post-mortem in your notes: what failed, when you noticed, when you restored, what was lost. The corpus of past disasters is its own asset.
- If you restored to a backup older than the latest, the articles ingested between the backup time and the disaster are lost. The next `Pinakes-DailyFetch` run (03:23) or `Invoke-PinakesAdmin.ps1 -Endpoint Fetch` recovers most of them from CrossRef etc., because the incremental fetch starts from the restored database's own fetch log. Anything else written since the backup — tag suggestions and votes, alert subscriptions, name-removal requests, hand edits — doesn't come back.
- If the offsite variables are set, take a fresh offsite backup so the bucket has a post-restore copy: `& C:\Pinakes\app\deploy\windows\Invoke-PinakesAdmin.ps1 -Endpoint Backup`.

## Practice

**Run this drill quarterly even when nothing is broken.** Add a calendar reminder. Neither half touches the live database.

```powershell
cd C:\Pinakes\app

# Local: the newest nightly copy opens and passes integrity_check.
$latest = Get-ChildItem C:\Pinakes\backups\articles-*.db | Sort-Object LastWriteTime -Descending | Select-Object -First 1
C:\Pinakes\venv\Scripts\python.exe restore.py --verify $latest.FullName

# Offsite: needs the six PINAKES_BACKUP_* variables and the key file.
C:\Pinakes\venv\Scripts\python.exe restore.py --latest --out C:\Pinakes\restore\drill.db --age-key <key file>
C:\Pinakes\venv\Scripts\python.exe restore.py --verify C:\Pinakes\restore\drill.db
Remove-Item C:\Pinakes\restore\drill.db
```

The offsite half runs from any checkout with the requirements installed, which keeps the key off the server. Run on the server, its redaction re-apply can clear the live app's Datastories cache (it lives under `C:\Pinakes\app\data`), so the next Datastories visits are slow while it refills.

If this sequence fails, something has rotted: rotated bucket credentials, a lost age key, a deleted bucket, or a backup too old to be useful. Find out before you need it for real.

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `restore.py --verify` prints `FAIL` or a SQLite error | That backup is damaged | Pick an older file. `admin-tasks.log` marks nights whose copy failed its check with `ERROR` |
| `Move-Item` says the file is in use | The service is still running, or something else has the file open | `Get-Service Pinakes-Web` should say `Stopped`. Close any open SQLite browser |
| `restore.py --list` prints `ERROR: missing env vars` | The six `PINAKES_BACKUP_*` variables aren't set in this shell | Set them, or download the file from the bucket console and use `restore_local.py` |
| `restore.py --list` prints `No backups in bucket.` | Wrong bucket name, or the bucket was emptied | Check `PINAKES_BACKUP_BUCKET`, then check the bucket directly in the B2 web console |
| `that file does not look like an age private key` (`restore_local.py`), or `IdentityError: invalid Bech32 encoding` (`restore.py`) | The key file has age-keygen's comment lines, or isn't the key | Put only the `AGE-SECRET-KEY-1...` line in the file |
| `decryption failed` (`restore_local.py`), or a `DecryptError` traceback (`restore.py`) | Wrong age key | Confirm the public key that was set as `PINAKES_BACKUP_AGE_PUBLIC_KEY` when the backup was taken corresponds to the private key in your hand. If they were rotated and you don't have the older private key, older backups are unrecoverable |
| `/health/ready` returns 503 with `missing: <path>` | The file isn't where `DB_PATH` points | `[Environment]::GetEnvironmentVariable('DB_PATH','Machine')`, then check `articles.db` is at that path |
| The service starts and stops again | Startup failed | `Get-Content C:\Pinakes\logs\web-stderr.log -Tail 60` |

## Why we trust this procedure

- Every local backup goes through SQLite's online backup API and `PRAGMA quick_check` before the task reports success, and `Update-Pinakes.ps1` takes one before every deploy.
- Both restore tools run `PRAGMA integrity_check` and re-apply the redaction ledger before a file goes live. `tests/test_restore_local.py` and `tests/test_redaction_durability.py` cover both.
- The quarterly drill decrypts a real backup with the private key. We don't trust a key we haven't used.

## Why we DON'T trust this procedure

- The local copies are unencrypted and on the same disk as the database. Losing the server loses them, and with them every restore point newer than the last offsite run.
- No task runs the offsite backup on this server, and nothing checks the bucket. The newest offsite copy is as old as the last manual `Invoke-PinakesAdmin.ps1 -Endpoint Backup`. `Backup-Pinakes.ps1`'s help names the other way to get copies off the box: point Windows Server Backup at `C:\Pinakes\backups`.
- The local history is short. Ten files, shared between nightly and pre-deploy copies, so a day with several deploys pushes older nightly copies out.
- Treat Restore B as untested until a drill has run it on this server.
- The age private key is in a password manager. If the password manager itself is the disaster, recovery requires the paper backup. Confirm you can find the paper backup BEFORE you need it.

If any of these gaps materialise, treat the recovered DB as the authoritative state and write the gap into the next post-mortem.

## The former Fly.io deployment

The former Fly.io deployment (`fly.toml`, `Dockerfile`, gunicorn) is no longer production. `.github/workflows/fly-deploy.yml` still deploys it after every green Tests run on `main`, and `pinakes.xyz` now redirects to the Clearinghouse host. This runbook was first written for it.

`restore.py` still ends by printing that app's steps: `flyctl scale count app=0 scheduler=0`, an sftp upload to `/data/articles.db`, and `curl https://pinakes.xyz/health/ready`. None of them apply to the Windows server. The `scheduler` process group they name was removed in `fa25cae`, and `pinakes.xyz/health` now answers with a redirect.
