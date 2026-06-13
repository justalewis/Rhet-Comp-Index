# Author redaction runbook

How to run the author opt-out ("Name Redacted by Author Request") feature: configure it, review requests, redact by hand, and handle the durability edge cases (restore, backups).

## What it does, in one paragraph

A redacted author's name is replaced everywhere it is used as identity — `articles.authors`, the normalized author/affiliation/institution tables, books, and (best-effort) the free-text reference blobs — with a stable per-author token like `Redacted Author 7f3a2c`. Templates render that token as "Name Redacted by Author Request." The scholarship stays in the index and keeps counting in every metric, because the token becomes the new identity key. The `redaction_ledger` table retains the real name (locked: no render/API/export path reads it) so the suppression can be re-applied after a fetch and reversed on request.

## Configuration (env / fly secrets)

| Var | Purpose | Required? |
|---|---|---|
| `REDACTION_SALT` | Salts the token hash so the public token can't be brute-forced to the name. If unset, a random salt is generated once and stored in `redaction_meta`. Set it explicitly in production so it's stable and known. | Recommended |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Send the email-verification link. If unset, the link is logged instead of sent (fine for dev; not for prod). | For the email path |
| `ORCID_CLIENT_ID`, `ORCID_CLIENT_SECRET` | ORCID OAuth verification (the "Verify with ORCID" button appears only when both are set). `ORCID_ENV=sandbox` to test against sandbox.orcid.org. | For the ORCID path |

`PINAKES_ADMIN_TOKEN` (already set for the cron endpoints) gates the review queue.

## The request flow

1. Author submits `/redaction-request` (linked from `/about` and the About-page footer).
2. They verify identity — ORCID OAuth, or an emailed one-time link.
3. Verified requests land in the review queue. **You approve by hand** before anything changes.

### Review and decide (admin API)

```bash
TOKEN=...   # PINAKES_ADMIN_TOKEN

# List pending + verified requests (includes requester email/ORCID — admin only)
curl -s -H "Authorization: Bearer $TOKEN" \
  https://pinakes.xyz/api/admin/redaction-requests | jq

# Approve request #42 → writes the audit row, then redacts the author
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  https://pinakes.xyz/api/admin/redaction-request/42/approve

# Or deny (audited; no change)
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  https://pinakes.xyz/api/admin/redaction-request/42/deny
```

Only `verified` requests can be approved. The audit row is written *before* the redaction fires, so there's proof-of-review even if the request is later purged.

## Redact by hand (CLI, no request needed)

Run on the box with the live DB (or locally against a copy):

```bash
python redaction.py redact "Jane Q. Smith" --variant "J. Smith" --variant "Jane Smith"
python redaction.py list                       # show the ledger
python redaction.py resweep                    # re-apply the whole ledger (idempotent)
python redaction.py unredact "Redacted Author 7f3a2c"   # reverse a redaction
```

`redact` and `resweep` each rebuild the FTS index and bust the Datastories cache. After redacting in production, also re-run the cache pre-warm so the first visitor isn't slow:

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" https://pinakes.xyz/api/admin/prewarm
```

## Keep the ledger off-box

A restore from a backup taken *before* a redaction won't contain that ledger row, so the restored DB wouldn't know to re-apply it. Snapshot the ledger somewhere durable (your password manager / a private gist) whenever you add a redaction:

```bash
python redaction.py export-ledger ledger.json   # contains plaintext names — store securely
```

## Restore (durability)

`restore.py` re-applies redactions to the restored file automatically, using the restored DB's own ledger. If you're restoring a backup that predates a redaction, feed the off-box ledger:

```bash
python restore.py --latest --out ./restored.db --age-key ~/.pinakes/age.key \
  --redaction-ledger ledger.json
```

(See `disaster-recovery.md` for the full restore procedure.) If for any reason the re-apply is skipped, run `python redaction.py resweep` against the live DB after promoting the restored file.

## Hard forget: encrypted backups

A redacted name still exists inside the encrypted off-site backups until they age out of the retention window (**30 daily / 26 weekly / 12 monthly** — up to ~12 months). The live index re-applies the redaction on every fetch, so the name never resurfaces *there*. If an author requires the name purged from cold storage too, delete the relevant B2 objects by hand (`python restore.py --list` shows the keys; remove them via the B2 console / `aws s3 rm` against the bucket). This is the one place the "forgotten" guarantee is bounded by retention, and the About page discloses it.

## What's deliberately out of scope (v1)

- **Names inside other articles' bibliographies** (`citations.raw_reference`) are scrubbed best-effort — exact ledger name-forms only. Variant coverage is incomplete; the About page says so.
- **Co-authors' names** on a shared paper are untouched. A co-author can't force-redact a byline they don't own.
