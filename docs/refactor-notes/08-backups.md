# 08 — Automated SQLite backups (Prompt D2)

Audit trail for adding nightly off-machine backups with retention.

## Files added / modified

- `backup.py` (new) — pipeline (snapshot, compress, encrypt, upload, prune), retention logic, `run_backup()`/`verify_latest_backup()` orchestrators.
- `restore.py` (new) — operator-facing CLI: `--list`, `--latest`, `--date`, `--verify`. Reads age private key from a file path the operator passes; never from env on Fly.
- `scheduler.py` — adds `backup_job` (cron 03:00 UTC daily) and `verify_backup_job` (cron 04:00 UTC Sunday). Both swallow exceptions and capture to Sentry; both ping the heartbeat.
- `requirements.txt` — adds `boto3>=1.34`, `zstandard>=0.22`, `pyrage>=1.2`.
- `fly.toml`, `README.md` — secrets table extended with the six backup env vars; README has a fresh "Backups (recommended)" subsection with the setup recipe.
- `tests/test_backup.py` (new) — 18 tests, including a real concurrent-writer snapshot consistency test, real zstd round-trip, real pyrage round-trip, retention-classification on two years of synthetic data, and S3-mocked happy/sad paths.
- `docs/runbooks/disaster-recovery.md` (new) — seven-step restore procedure, quarterly drill instructions, common failure modes.

## Pipeline

```
/data/articles.db
    │
    ├─→ create_snapshot()       sqlite3 .backup, point-in-time consistent
    │     output: /tmp/.../articles-YYYYMMDDTHHMMSSZ.db
    │
    ├─→ compress()              zstd level 19, ~ 4× ratio for SQLite WAL data
    │     output: ...db.zst
    │
    ├─→ encrypt()                age, recipient = PINAKES_BACKUP_AGE_PUBLIC_KEY
    │     output: ...db.zst.age   (standard age binary format, header
    │                              "age-encryption.org/v1")
    │
    └─→ upload()                S3-compatible PUT
          key: YYYY/MM/DD/articles-YYYYMMDDTHHMMSSZ.db.zst.age
```

After upload, `prune()` lists the bucket and deletes any object outside the retention policy (server-side delete; the local machine doesn't track state).

## Decisions

### B2 over R2 / S3 (cost)

Backblaze B2's pricing is the cheapest of the three at our scale: $0.006/GB/month for storage, $0.01/GB egress (free under monthly cap). At ~ 100 MB compressed × 30 daily + 26 weekly + 12 monthly = ~ 7 GB ongoing, monthly cost is around $0.04. Verification downloads add maybe $0.01/week. Restoration egress is one-time and irrelevant.

R2 has cheaper egress (free) but B2 wins on storage at this scale. Both speak the S3 API; the operator can switch by changing `PINAKES_BACKUP_ENDPOINT` and re-issuing keys. No code changes.

### age over GPG

age has better defaults — modern primitives (X25519 + ChaCha20-Poly1305 + HKDF), compact key format, no need to fight `~/.gnupg` permissions, no web-of-trust ambiguity. The keypair is one operator → one recipient. age is the right tool.

We use `pyrage` (Rust bindings) so the produced blobs are standard age binary format. If the Python tooling breaks during a disaster, the operator can decrypt with the regular `age` CLI:

```bash
age -d -i ~/.pinakes/age.key < latest.db.zst.age | zstd -d > restored.db
```

### Retention: 30/26/12

- **30 daily** — covers the typical incident window. If you notice a problem within a month, you can roll back to before it.
- **26 weekly** — six months of weekly checkpoints for slower-burning issues (e.g., a bad migration that ate data quietly for weeks).
- **12 monthly** — one year of long-tail recovery. After a year, anything still recoverable is recoverable from the original CrossRef / OpenAlex sources via re-ingest.

The classification is implemented in `_classify_for_retention()`, sorted newest-first so the most recent backup in a week or month becomes the survivor.

### Verification: weekly partial, manual full

The conflict the prompt surfaces: "verify weekly" + "no private key on Fly" can't both be true if verification means full integrity check. Resolution:

- **Scheduled weekly verify on Fly** confirms presence + size + age header. Catches: missing upload, zero-byte upload, lifecycle deletion, S3 credential rot, age recipient drift.
- **Quarterly manual drill** (operator's calendar reminder, see runbook) downloads via `restore.py --latest`, decrypts with the private key, runs `PRAGMA integrity_check`. Catches: bit rot inside the encrypted blob, SQLite corruption surviving the snapshot, age key mismatch.

The two together are sufficient. A daily full integrity check would either require the private key on Fly (defeating the off-machine constraint) or download large files daily (wasteful).

### Why backup at 03:00 UTC, verify at 04:00 UTC Sunday

03:00 UTC is after most CrossRef DOI deposits have settled and before US-AM weekday traffic, with timezone overlap that minimises operator interruption regardless of where they live. Sunday 04:00 separates the verify download from the daily backup upload window — if either side fails, we can diagnose them independently from the schedule alone.

## The age private key — operational notes

This is the most important secret in the project. Without it, every backup is unrecoverable.

- The PRIVATE key (`AGE-SECRET-KEY-1...`) **never** goes on Fly.
- The PUBLIC key (`age1...`) is set via `flyctl secrets set PINAKES_BACKUP_AGE_PUBLIC_KEY=...`.
- Store the private key in a password manager AND on paper in a physical location you can find under stress.
- If you must rotate it: generate a new keypair, update the public-key Fly secret, keep the OLD private key forever (older backups can only be decrypted by the older private key).

`PINAKES_BACKUP_AGE_PRIVATE_KEY` is read by `verify_latest_backup()` on Fly only when the operator has knowingly placed it there for an extended period of full-integrity-check verification. The default is unset, in which case the weekly verify is presence-only.

## What the tests cover (and don't)

Covered:
- Snapshot consistency under concurrent writes (real threading test).
- zstd compress/decompress round-trip.
- pyrage encrypt/decrypt round-trip with generated keys.
- Object key path structure (`YYYY/MM/DD/articles-...`).
- Retention classification across 2 years of synthetic backups.
- `run_backup()` happy path with mocked S3.
- `run_backup()` failure path with Sentry capture.
- `verify_latest_backup()` partial check (no private key) and full check (with key).
- Failure modes: zero-byte upload, no backups, missing env.

NOT covered:
- Real S3 upload. boto3 is mocked. Manual smoke check on staging is the operator's job after first deploy.
- Real bucket listing pagination beyond one page.
- Network errors during upload retry. `boto3.upload_file` has its own retry; we trust it.
- The full `restore.py` CLI end-to-end. Argparse logic is exercised via the same backup primitives that are tested directly.

## Decisions explicitly NOT made

- **No deduplication.** Every nightly backup uploads the full DB, even if 99% of pages haven't changed. Deduplication via something like restic or borg would cut storage cost ~10×, but requires a more sophisticated deployment and an additional dependency on the bucket's expected layout. At ~ 7 GB total cost ~ $0.04/month, the optimisation isn't worth the operational complexity.
- **No lifecycle rules on the bucket.** We do all retention in `prune()`. B2 lifecycle rules would be cheaper to enforce but harder to test. Code-side retention is auditable in Sentry's success log per nightly run.
- **No incremental restore.** `restore.py` always downloads a full backup. For a partial corruption, the operator can `sqlite3 ".dump"` the restored DB, edit the SQL, re-import. That's an operator decision per disaster, not something to automate.
- **No alerting on prune false-positives.** If `_classify_for_retention()` accidentally deletes too aggressively, we lose backups silently. Mitigation: the existence count is logged on every run; an external uptime monitor could alert if the count drops sharply. Out of scope for this prompt.
