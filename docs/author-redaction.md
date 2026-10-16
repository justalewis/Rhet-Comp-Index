# Author redaction (the "right to be forgotten" feature)

How an author has their name removed from Pinakes while their scholarship stays in the index. This is the design/reference document — *how it is wired and why*. For operating it day to day (config, reviewing requests, undo, restore) see [runbooks/author-redaction.md](runbooks/author-redaction.md); for the public-facing policy and the legal/ethical framing see the **Author Privacy** section of [`/about`](../templates/about.html).

## What it does

A verified author can have their name removed from the index. The scholarship is **not** deleted — every article, citation edge, co-authorship link, and metric it contributes to stays exactly as it was. What changes is that the author's name and identifying traces are replaced, everywhere they appear, with the tag **"Name Redacted by Author Request."** The work remains part of the field's record; the byline comes off.

## The core problem

Two requirements pull in opposite directions:

- **Forget the name** — the string should be gone from every surface a visitor, crawler, citation manager, or API consumer can reach.
- **Keep counting the work** — co-authorship edges, betweenness, citation counts, and every Datastories metric must be byte-for-byte unchanged.

You cannot satisfy both by blanking the name, because **author identity in Pinakes *is* the name string.** There is no integer author id: every author query is `WHERE authors LIKE '%name%'`, the author page lives at `/author/<name>`, and every network node is keyed by the name. Blank the name and the person's papers scatter into anonymous fragments and the metrics break; and `authors.name` is `UNIQUE`, so two blanked authors would collide.

## The resolution: a stable per-author token

Each redacted author gets a deterministic, unique token of the form:

```
Redacted Author 7f3a2c
```

where the suffix is the first 6 hex of `sha256(normalize(name) + "|" + salt)`, lengthened two characters at a time if that prefix already belongs to another redacted author. The token **replaces the name in place** everywhere the name is stored. Because identity *is* the string, swapping it for a stable token preserves every metric for free — the token simply becomes the new identity key, and all the `LIKE`-based machinery (timeline, co-authors, citing venues, co-citation) keeps resolving through it unchanged.

Properties that matter:

- **Deterministic + injective** — the same name always maps to the same token, so co-authorship edges, lineage spans, and partnership weights are identical before and after. This is the single most important invariant.
- **`UNIQUE(name)`-safe** — the token is unique per author, so no constraint changes are needed.
- **Not a name-recovery oracle** — the `salt` (env `REDACTION_SALT`, or a value persisted in `redaction_meta`) means the published token can't be brute-forced back to the name with a dictionary of known authors.
- **URL-safe** — no `#` (a URL-fragment delimiter); the token doubles as the author-page slug.

The display string "Name Redacted by Author Request" is applied at the *template* layer (the `redact_authors` Jinja filter); the stored token remains the identity and the link target. Both live in [`redaction.py`](../redaction.py) (`TOKEN_PREFIX`, `DISPLAY_TEXT`, `is_redaction_token`).

## Data model

Four tables, added by migrations v11 (`redaction_ledger`, `redaction_meta`) and v12 (`redaction_requests`, `redaction_audit`) in [`db/core.py`](../db/core.py):

| Table | Purpose |
|---|---|
| `redaction_ledger` | **The locked table.** One row per redacted author: `id`, `token`, `name` (plaintext, retained), `name_hash`, `name_variants` (JSON), `redacted_at`, `request_id`, `created_by`. No render/API/export path reads it — only the suppression re-applier and admin review do. |
| `redaction_meta` | Key/value store; holds the per-install `salt` when `REDACTION_SALT` isn't set. |
| `redaction_requests` | Opt-out requests: `author_name_claimed`, `name_variants`, `requester_email`, `requester_orcid`, `verification_method`, `verification_token_hash`, `verification_status` (`pending`/`verified`/`approved`/`denied`), and the decision/timestamp columns. |
| `redaction_audit` | Append-only trail: `(request_id, event, actor, detail, at)`. Written *before* a redaction fires. |

The name itself lives, denormalized, across these existing tables — all of which the redaction has to touch:

`articles.authors` (a `;`-delimited string) · `articles_fts` (the search index) · `authors.name` (+ `orcid`, `openalex_id`, `institution_name`, `institution_ror`, all nulled) · `author_article_affiliations.author_name` and `article_author_institutions.author_name` (+ `openalex_author_id`, nulled; their institution columns are kept so institution metrics still count the work) · `books.authors` / `books.editors` · and, as free text, `citations.raw_reference` (other articles' bibliographies).

### Retain-name vs hash-only

The ledger keeps the **plaintext name** (not just a hash). This is a deliberate trade: it allows robust matching when an upstream re-fetch hands the name back, human-auditable review, and — importantly — **reversibility** (un-redaction restores the exact name). The cost is that the name persists in the locked ledger and therefore in every backup of the database: the unencrypted local copies on the server and any encrypted offsite copies (see [Backups](#scope-boundaries-v1) below). The About page says a removed name stays in the site's backups.

## The suppression spine (the resurrection problem)

Token-replacing today does nothing about tomorrow's fetch: CrossRef/OpenAlex re-supply the real name and the daily ingest would write it straight back. So suppression is not a one-time scrub — it is a *maintained commitment* that every write path consults.

Rather than guard ~20 write sites, suppression lives at the two real bottlenecks plus a sweep:

1. **[`db.articles.upsert_article`](../db/articles.py)** and **[`db.books.upsert_book`](../db/books.py)** call `redaction.apply_suppression(authors, conn=conn)` before the `INSERT`. Every ingested name funnels through these two. `apply_suppression` is exact-element match on the normalized name (never a substring — "Jane Smith" can't catch "Jane Smithson"), runs only *reads* on the connection already in hand, and is exception-safe (ingestion never breaks because suppression hiccuped).
2. **[`enrich_openalex.py`](../enrich_openalex.py)** writes the normalized author tables directly, bypassing `upsert_article`, so it carries its own guard.
3. **`redaction.resweep_all()`** re-applies the entire ledger across all tables, sweeping out any name an ingest path slipped past the choke-points. It runs at the end of every deep refresh ([`deep_refresh.py`](../deep_refresh.py)) and as step 10 of [`weekly_maintenance.py`](../weekly_maintenance.py) when that script runs in full. The scheduled paths don't call it: the daily incremental fetch relies on the choke-points alone, and `/api/admin/run-maintenance` (what the `Pinakes-WeeklyMaintenance` task calls) runs steps 4–9 and 12 and skips 10. `python redaction.py resweep` runs it by hand. It is idempotent, so running it extra is harmless.

`apply_suppression` is backed by a cached `{normalized_name → token}` map keyed by `(db_path, ledger_count, max_id)`, so it refreshes the instant a redaction lands and never bleeds across the per-test databases the harness swaps in.

All of this lives in [`redaction.py`](../redaction.py): `mint_token`, `apply_suppression`, `redact_author` (the full DB + FTS + cache pass for one author), `resweep_all`, `unredact_author`.

## The render layer

The data-layer swap is the authoritative redaction; the template layer is display polish plus defense-in-depth.

- **`redact_authors` Jinja filter** ([`web_helpers.py`](../web_helpers.py), registered in [`app.py`](../app.py)) renders any token as "Name Redacted by Author Request" while leaving real names untouched. Applied to ~30 byline sites (article pages, author pages, most-cited, books, institution pages, citations).
- **The author page** (`templates/author.html`) takes an `is_redacted` flag and hides the name-based affordances (the CompPile-by-author search, the ORCID link, the affiliation summary) so a redacted profile re-identifies as little as possible.
- **Old-name URL → 404.** [`blueprints/authors.py`](../blueprints/authors.py) 404s a request for the *real-name* URL of a redacted author rather than redirecting — a redirect would echo the suppressed name in the `Location` header and the logs.
- **Citation-manager metadata** (`citation_author`, `DC.creator`, COinS `rft.au` in `article.html`/`index.html`) is skipped for tokens, so a placeholder is never fed to Zotero/Mendeley/Google Scholar.

## Durability

`redact_author` and `resweep_all` each finish with:

- **FTS rebuild** — `INSERT INTO articles_fts(articles_fts) VALUES('rebuild')`, so searching the old name returns nothing.
- **Datastories cache bust** — `datastories_cache.clear_all()`. This is **load-bearing**: the cache's freshness fingerprint is `MAX(id)` and `COUNT(*)` of `articles` and `citations`, which a name swap does *not* move, so without an explicit bust the on-disk cache would serve the real name indefinitely.

Two more durability hooks:

- **Restore** — [`restore.py`](../restore.py) (downloads from the bucket) and [`restore_local.py`](../restore_local.py) (a backup file already on disk) both re-apply the ledger to the restored file before it goes live, so a restore can't resurrect a name. For a backup taken *before* a redaction (whose ledger predates it), `--redaction-ledger ledger.json` merges an off-box export first. Copying one of the Windows server's local backups into place re-applies nothing; `python redaction.py import-ledger ledger.json` (merge, then resweep) does that step by hand. See the [disaster-recovery runbook](runbooks/disaster-recovery.md).
- **Sentry** — `include_local_variables=False` ([`monitoring.py`](../monitoring.py)) so a name sitting in a local variable when a view raises isn't shipped to Sentry.

## The request → verify → review → decide flow

Implemented in [`blueprints/redaction.py`](../blueprints/redaction.py).

**The public form ships off.** `PINAKES_REDACTION_FORM_ENABLED` defaults to `0`; with it off, `GET /redaction-request` redirects (302) to `/about#author-privacy` and `POST` returns 404. The form's value is the email round-trip, and without working SMTP it accepted requests and dropped them (`send_email` logs and returns `False`). So the live path today is the one `/about` describes: authors email the operator, who applies the request with the `redaction.py` CLI on the server ([WINDOWS-SERVER-RUNBOOK.md](WINDOWS-SERVER-RUNBOOK.md#author-name-removal-requests)). A CLI redaction goes straight to `redact_author` with no request or audit row (the ledger's `created_by` records its `--by`), so the review queue below stays empty. The flow below is what runs once the form is switched on.

```
public form ──▶ create_request ──▶ verify (email link OR ORCID OAuth)
                                        │
                                        ▼
                          _notify_admin_of_verified  (emails the operator)
                                        │
                                        ▼
            admin review (/admin/redactions or the API) ──▶ approve / deny
                                        │ approve
                                        ▼
                       audit row, THEN redact_author(name)
```

1. **Submit** — `GET/POST /redaction-request` (public form; `POST` rate-limited 5/hour). Nothing on the site links to it: the nav's About submenu, the footer, and `/about` itself point to `/about#author-privacy`, whose text asks authors to email. The requester gives the byline, optional variants, an email, and optionally an ORCID.
2. **Verify identity** — one of:
   - **Email** — a one-time link is emailed; only its `sha256` hash is stored, and it's burned on use.
   - **ORCID OAuth** — three-legged flow; the `state` is signed with the app secret (`PINAKES_SECRET_KEY`) so the callback can't be forged. The redirect URI is `https://pinakes.wacclearinghouse.org/redaction-request/orcid/callback` and must be registered in the ORCID developer app. ([`orcid_oauth.py`](../orcid_oauth.py).)
3. **Notify** — on verification, `_notify_admin_of_verified` emails the operator (`REDACTION_NOTIFY_EMAIL`, falling back to `SMTP_REPLY_TO`) with the claimed name and a link to the review page. Fired on *verification*, not raw submission, so unverified/abandoned/spam attempts don't reach the inbox.
4. **Review** — the operator decides. Two equivalent surfaces:
   - **Admin page** `/admin/redactions` ([`templates/admin_redactions.html`](../templates/admin_redactions.html)) — a token-gated web UI. The page shell is public; the operator pastes `PINAKES_ADMIN_TOKEN` (held in `sessionStorage`, never embedded in the HTML), and all data/actions go through the token-gated API via `fetch`. Verified requests show with **Approve & redact** / **Deny** / **audit trail**.
   - **API** — `GET /api/admin/redaction-requests` (the queue; pending and verified by default, `?status=` filters), then per request, under the singular `/api/admin/redaction-request/<id>/`: `POST .../approve`, `POST .../deny`, `GET .../audit`. All `require_admin_token`.
5. **Decide** — `decide_request` writes the audit row **before** the redaction fires (proof-of-review survives even if the request is later purged). Only `verified` requests can be approved. Approve → `redact_author`; deny → audited, no change.

This is **admin-gated by design**: verification proves the requester controls the contact; a human still decides whether the request is legitimate and weighs the public-interest tension (an opt-out shouldn't become a way to scrub accountability — the governance posture mirrors the CJEU's *GC v CNIL* balancing rather than an absolute veto).

## Reversibility

`redaction.unredact_author(token)` reverses a redaction: it restores the real name from the locked ledger across the free-text fields, removes the ledger row, rebuilds FTS, and busts the cache. The normalized author/affiliation rows that were collapsed onto the token aren't reconstructed in full (their pre-redaction ORCID/OpenAlex detail is gone), but a subsequent OpenAlex enrichment pass repopulates them from the restored name. There is no admin-page button for this yet — it's a CLI command (`python redaction.py unredact "<token>"`); see the runbook.

## Scope boundaries (v1)

- **`citations.raw_reference`** — names inside *other* articles' bibliographies are scrubbed **best-effort**: exact, word-boundary replacement of the ledger's name forms only. Variant coverage is incomplete (these are arbitrary upstream citation strings), and the About page discloses this.
- **Co-authors** — a redacted author's co-authors on a shared paper are untouched. A co-author can't force-redact a byline they don't own.
- **Backups** — every backup holds the name: one taken before the redaction has it throughout the data, and one taken after still has it in the ledger, by design. On the Windows server the nightly and pre-deploy copies in `C:\Pinakes\backups` are unencrypted, and the newest 10 are kept. The encrypted offsite copies follow `backup.py`'s 30 daily / 26 weekly / 12 monthly policy, but that prune runs only inside `run_backup()`, and no offsite backup has been scheduled since 2026-09-19; old objects stay until someone runs a backup or deletes them, unless the bucket has its own lifecycle rule (check the bucket). If the former Fly app's volume (`/data/articles.db`) still exists, it is a separate copy that never received redactions made on the Clearinghouse server. The live index is where the redaction is maintained; About says a removed name stays in the site's backups. The runbook covers purging old copies for a hard-forget request.

## Configuration

Summarized here; the operational detail is in [the runbook](runbooks/author-redaction.md).

| Var | Purpose |
|---|---|
| `PINAKES_REDACTION_FORM_ENABLED` | `1` turns the public request form on. Default `0`: `GET /redaction-request` redirects to `/about#author-privacy` and `POST` returns 404. |
| `REDACTION_SALT` | Salts the token hash. Set it explicitly in production so tokens are stable; otherwise a random salt is generated once and stored in `redaction_meta`. |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | Outbound email for the verification link. When unset, the link is logged instead of sent. |
| `SMTP_REPLY_TO` | Reply-To for a no-reply sender; also the fallback recipient for admin notifications. |
| `REDACTION_NOTIFY_EMAIL` | Where to email the operator when a request is verified. Falls back to `SMTP_REPLY_TO`. |
| `ORCID_CLIENT_ID` / `ORCID_CLIENT_SECRET` | ORCID OAuth (the "Verify with ORCID" button appears only when both are set). `ORCID_ENV=sandbox` to test. |
| `PINAKES_ADMIN_TOKEN` | Gates the review queue/page (same token as the admin endpoints the scheduled tasks call). |
| `PINAKES_SECRET_KEY` | Signs the ORCID OAuth `state`. Set it in production; the app only refuses to start without it on Fly (`FLY_APP_NAME` set). Anywhere else an unset key becomes a random per-process key, so an ORCID verification in progress across a service restart fails its signature check. |

Note: external URLs (the email verify link, the admin review notice, the ORCID `redirect_uri`) are built by `web_helpers.public_url_for`. With `PINAKES_SITE_URL` set they come from it, which the Clearinghouse box requires: Waitress strips `X-Forwarded-Host` and `-Proto`, so a request-built URL there reads `http://127.0.0.1:8080/...`. With it unset (how the former Fly app runs) they come from the request, and `ProxyFix` makes them `https://` behind Fly's TLS-terminating proxy. Either way they must come out `https://`, or ORCID rejects the callback.

## Code map

| File | Role |
|---|---|
| [`redaction.py`](../redaction.py) | The spine: token minting, `apply_suppression`, `redact_author`, `resweep_all`, `unredact_author`, the request-queue + audit helpers, and a CLI (`redact`, `list`, `resweep`, `unredact`, `export-ledger`, `import-ledger`). |
| [`blueprints/redaction.py`](../blueprints/redaction.py) | Routes: the public form + verify, ORCID OAuth, the admin review page, and the admin API. |
| [`notifications.py`](../notifications.py) | Minimal SMTP send helper (swappable for a transactional provider). |
| [`orcid_oauth.py`](../orcid_oauth.py) | ORCID three-legged OAuth. |
| [`templates/redaction_request.html`](../templates/redaction_request.html) | The public request form (rendered only with `PINAKES_REDACTION_FORM_ENABLED=1`; its verify and ORCID-callback pages render either way). |
| [`templates/admin_redactions.html`](../templates/admin_redactions.html) | The admin review page. |
| `db/core.py`, `db/articles.py`, `db/books.py` | Migrations + the ingest choke-points. |
| `web_helpers.py`, `app.py` | The `redact_authors` filter + registration; `ProxyFix`. |

## Tests

Around fifty tests across `tests/test_redaction*.py` and `tests/test_notifications.py`: the metric-invariance guarantee (network structure identical before/after), no-real-name-in-rendered-HTML, the 404 on old URLs, FTS forgetting the name, the cache bust, restore re-application, the full submit → verify → review → decide flow, ORCID callback, the admin notification, and an import-completeness check that every ingest module references the suppression helper (so a future refactor can't silently drop the guard).
