# Disaster recovery runbook

Restore Pinakes from an off-machine backup. Written for a sleep-deprived operator at 2 AM. Read it twice; do it once.

## Before you start — what you need

- Your terminal authenticated to Fly: `flyctl auth whoami` returns your email.
- Your terminal authenticated to the backup bucket: `PINAKES_BACKUP_*` env vars exported, OR you've sourced the same secrets file you used to set the Fly secrets.
- **Your age private key.** This is `AGE-SECRET-KEY-1...`. It is NOT on Fly. It should be:
  - In your password manager.
  - On a printed paper you stored somewhere safe.
  - Never in a cloud document, never in a chat message.

If you don't have the private key, the backups are unrecoverable. Stop. Call whoever might have a copy.

## The seven steps

```bash
# 1. Stop both Fly process groups.
flyctl scale count app=0 scheduler=0
# Confirm:
flyctl status        # 0 machines running

# 2. List available backups.
python restore.py --list
# You should see something like:
#   Date (UTC)              Size     Key
#   2026-04-29T03:00:00+00  87.3 MB  2026/04/29/articles-20260429T030000Z.db.zst.age
#   2026-04-28T03:00:00+00  87.2 MB  2026/04/28/articles-20260428T030000Z.db.zst.age
#   ...

# 3. Pick a backup.
#    - For most disasters: pick the latest.
#    - If the latest backup is suspect (e.g. the corruption was already
#      in the snapshot taken at 03:00), pick yesterday's or earlier.
#    - Cross-reference with the most recent successful weekly verify in
#      Sentry — last green `level:info component:scheduler source:backup`
#      with `journal:verify`.

# 4. Download, decrypt, decompress, integrity-check (all in one command).
python restore.py --latest --out /tmp/restored.db --age-key ~/.pinakes/age.key
# OR for a specific date:
python restore.py --date 2026-04-28 --out /tmp/restored.db --age-key ~/.pinakes/age.key
# Expected last line: "Integrity OK. Restored to /tmp/restored.db (192.4 MB)."

# 5. Sanity-check the restored DB.
python restore.py --verify /tmp/restored.db
# Should print article count. Eyeball it: the count should match
# what you expect (~50k for the production corpus).

# 6. Upload to the Fly volume.
flyctl ssh sftp shell
sftp> put /tmp/restored.db /data/articles.db
sftp> ls -lh /data/                    # confirm size matches
sftp> exit

# 7. Restart both process groups.
flyctl scale count app=1 scheduler=1
# Wait ~30 seconds for the boot to complete, then:
curl https://pinakes.xyz/health/ready
# Expected: {"status": "ok", "db": "reachable"}

# Sanity check from outside:
curl -H "Authorization: Bearer $PINAKES_ADMIN_TOKEN" https://pinakes.xyz/health/deep | jq '.counts'
# Expected: counts roughly match what /verify printed in step 5.
```

## After you finish

- Notify any users you know depend on the site that it's back.
- Open a Sentry incident and link the relevant log lines (search for `level:error` in the hour before the outage).
- Write a short post-mortem in your notes: what failed, when you noticed, when you restored, what was lost. The corpus of past disasters is its own asset.
- If you restored to a backup older than the latest, the articles ingested between the backup time and the disaster are lost. The next scheduled fetch (or a manual `POST /fetch`) will recover most of them from CrossRef etc., but auto-tagged categories and any manual edits won't come back automatically.

## Practice

**Run this drill quarterly even when nothing is broken.** Add a calendar reminder. The drill:

```bash
# Off-production check — does NOT touch the live site.
python restore.py --latest --out /tmp/drill.db --age-key ~/.pinakes/age.key
python restore.py --verify /tmp/drill.db
rm /tmp/drill.db
```

If this command sequence fails, something has rotted: rotated S3 credentials, expired API keys, lost age key, deleted bucket. Find out before you need it for real.

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `restore.py --list` returns "no backups" | Bucket name wrong, or all backups expired | Check `PINAKES_BACKUP_BUCKET`, then check the bucket directly via the B2/R2 web console |
| `pyrage.decrypt` raises `decryption failed` | Wrong age key | Confirm the public key currently set as `PINAKES_BACKUP_AGE_PUBLIC_KEY` corresponds to the private key in your hand. If they were rotated and you don't have the older private key, older backups are unrecoverable |
| `flyctl ssh sftp` fails to connect | Fly machine has been replaced; sftp daemon not yet up | Wait 60 s, retry. If it still fails, `flyctl machine list` and `flyctl ssh console -s` to debug |
| `/health/ready` returns 503 after restart | DB file is at the wrong path or has wrong permissions | `flyctl ssh console`, check `/data/articles.db` ownership and size |

## Why we trust this procedure

- The backup pipeline runs nightly. The last successful run is in Sentry as a `source:backup` info event.
- The verification pipeline runs weekly. If the most recent verify failed, we'd know — Sentry alerts on `source:backup component:scheduler level:error`.
- The age private key has been used at least once successfully (the last quarterly drill). We don't trust a key we haven't used.

## Why we DON'T trust this procedure

- We've never restored from a fully destroyed Fly machine — only from a corrupted DB. The Fly side of step 6 is theoretical until exercised.
- The age private key is in a password manager. If the password manager itself is the disaster, recovery requires the paper backup. Confirm you can find the paper backup BEFORE you need it.
- Backup retention is best-effort: if something causes 30+ consecutive backup failures, we keep no daily backups. The weekly verify catches "no recent backup" but not "no recent backup AND we noticed late."

If any of these gaps materialise, treat the recovered DB as the authoritative state and write the gap into the next post-mortem.
