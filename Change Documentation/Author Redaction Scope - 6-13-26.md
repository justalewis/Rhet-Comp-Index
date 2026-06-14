# Author Redaction ("Name Redacted by Author Request") — Scope + Build Plan

**Rescope of:** the old "Right to Be Forgotten" spec (no prior written spec found in the repo or git history — treat this as greenfield).
**Date:** 2026-06-13
**Owner:** Justin

> Produced from a full-codebase sweep of every author-name surface and write-path. The core finding — that author *identity in Pinakes is the raw name string itself* (no author ID; `/author/<path:name>` puts the name in the URL; every query is `WHERE authors LIKE '%name%'`) — was verified directly against `blueprints/authors.py`, `db/authors.py`, and `db/core.py`. That fact is what makes this feature tractable without a schema refactor.

---

## 1. The core design tension

The ask contains two requirements that pull in opposite directions:

- **"Forget the name."** A true right-to-be-forgotten wants the string *Alice Smith* gone from every surface a visitor (or a crawler, or a citation manager, or a backup) can reach.
- **"Keep counting the work."** Co-authorship edges, betweenness, citation counts, and every Datastories metric must stay identical. That requires a **stable identity** for the author so their papers don't collapse into one super-node, split into noise, or break `UNIQUE(name)`.

You can't have both with a raw name. The resolution is a **stable pseudonymous token** that is *deterministic and injective per real author*, and that replaces the name everywhere the name currently lives.

### Recommended resolution

For each approved redaction, mint a token of the form:

```
Redacted Author #a1b2c3
```

where `a1b2c3` is the first 6 hex of `sha256(normalized_name || per-install secret salt)`. Properties this buys us:

- **Injective + deterministic** — every occurrence of the same real name maps to the same token, so the "CRITICAL if non-injective" cases (`ds_academic_lineages`, `ds_lasting_partnerships`, `get_author_network`, `get_author_coauthors`) keep their edges, weights, and first/last-year spans exactly. This is the single most important invariant in the whole feature: **the token is the new identity key, and it must be stable.**
- **Preserves delimiter semantics** — in `articles.authors` (semicolon-separated) we swap *the one name token* in place: `Alice Smith; Bob Jones` → `Redacted Author #a1b2c3; Bob Jones`. `_split_authors()` (db/datastories.py:2295) and `combinations(sorted(authors), 2)` keep working untouched; team-size counts in `ds_solo_to_squad` don't move.
- **Salted, so the token is not a name-recovery oracle** — a bare `sha256(name)` is reversible by dictionary attack against a known author list. The per-install secret salt (an env var, never committed) means the published token can't be brute-forced back to the name.

### Where does the real name live afterward? — the ledger question

This is the sharpest sub-decision. To **re-apply** suppression after an upstream re-fetch (CrossRef/OpenAlex will hand us *Alice Smith* again tomorrow), the ingest path has to recognize "this incoming name is suppressed." That needs a match key.

Two honest options:

- **(A) Hash-only ledger (recommended default).** Store only `name_hash = sha256(normalize(name) || salt)` and the `token`. Ingest normalizes each incoming author name, hashes it, and checks the ledger. **The plaintext name is never persisted anywhere after redaction** — which is what "forgotten" should mean. Cost: an admin can't read the ledger to see *who* is suppressed; reversal/audit shows only tokens. Name-variant coverage ("Smith, A." vs "Alice Smith") is limited to whatever variants you hashed in.
- **(B) Name retained in a locked ledger.** Keep the plaintext in a `redaction_ledger` table that is *never* read by any render/API/export path — only by the suppression re-applier and admin review. Easier variant matching and human-auditable, but it means the name still exists in your production DB and your nightly B2 backups. That's a weaker "forgotten" guarantee and a more awkward thing to promise on the About page.

**Recommendation:** ship **(A) hash-only** as the spine. If variant-coverage proves too lossy in practice, add an admin-only, separately-access-controlled variant table later — but make that an explicit, logged choice, not the default. (Flagged as an open question for you at the end.)

> Note on normalization: Pinakes has **no name de-duplication** — `Smith, A.` / `Alice Smith` / `Smith, Alice L.` are already separate author records today. Hash-only matching inherits that limitation exactly: we suppress the variants we were given. That's a defensible v1 boundary as long as the request form lets the requester list their name variants.

---

## 2. Every surface that must change, by layer

Pulled directly from the seven surface maps. Nothing named there is omitted.

### Data model (db/core.py, db/__init__.py)

| Location | Action | Risk if missed |
|---|---|---|
| `db/core.py:38-54` `articles.authors` TEXT | Replace the matching name token in the semicolon string with the pseudonym (in place). | Largest single surface; every list/export/FTS read leaks the name. |
| `db/core.py:85-113` `articles_fts` + triggers `articles_fts_ai/ad/au` | The `UPDATE OF ... authors` trigger (lines 106-112) *does* fire on an `authors` update — but **confirm with a test**, then run `INSERT INTO articles_fts(articles_fts) VALUES('rebuild')` after a batch to be safe. | Stale tokens stay searchable via FTS `MATCH`; the name is recoverable from search even after the column is changed. |
| `authors.name` (`db/core.py` ~243-250, `UNIQUE(name)`) | Set `name = token`. The token is unique per author, so **`UNIQUE(name)` is satisfied** — no constraint drop needed (this is the payoff of the per-author token vs. a shared blank). Also blank/redact `orcid` (it's a name-recovery vector). | If you tried to blank to a shared NULL/empty you'd hit `UNIQUE` violations; the token avoids that. |
| `author_article_affiliations` (`db/core.py:256-266`, `UNIQUE(article_id, author_name)`) | Set `author_name = token`. Token preserves per-article uniqueness. | A shared blank would collide on `UNIQUE(article_id, author_name)` and collapse multiple authors' institutions into one dict key in `get_article_affiliations()`. |
| `article_author_institutions` (`db/core.py:331-338`, `UNIQUE(article_id, author_name, institution_id)`) | Set `author_name = token`; keep `institution_id`, `openalex_author_id`. | Institution-join queries (`get_institution_top_authors`) collapse if blanked; token keeps them distinct. |
| `books.authors` / `books.editors` (`db/books.py:14-50`) | Token-replace matching names in both fields. | `get_author_books()` LIKE queries silently hide all book associations otherwise. |

### Write / resurrection paths (the daily/weekly crons un-redact you if missed)

Every path below writes a name and will **resurrect** a redacted name on the next run. See §3 for the choke-point strategy.

| Path | What |
|---|---|
| `db/articles.py:15-36` `upsert_article` | The article INSERT bottleneck (40+ callers). |
| `db/books.py:14-66` `upsert_book` | Book INSERT bottleneck (8+ callers). |
| `enrich_openalex.py:288, 295-302, 311-322` | `name_matches` gating + author/affiliation INSERTs (weekly enrichment). |
| `fetcher.py:172` `parse_authors` | Daily incremental fetch. |
| `deep_refresh.py:177-194` | Weekly catalog walk INSERT/UPDATE. |
| `scraper.py:398+, 412` | Scraper upserts + UPDATE backfill (the backfill UPDATE actively *resurrects*). |
| `rss_fetcher.py:259` | RSS harvest upsert. |
| `book_fetcher.py:268+`, `fetch_pitt.py:284`, `fetch_parlor.py:213`, `fetch_routledge.py` | Curated book lists. |
| `ingest_jac.py:175`, `ingest_peer_review_1_1.py:57`, `scrape_ccdp.py:501/548`, `seed_usu_rhet_comp.py:109` | CSV/archive ingests. |
| `weekly_maintenance.py:1-5` | Orchestrates the above weekly. |

### Render / templates

| Location | Action |
|---|---|
| `templates/author.html:3,20` | Title tag + H2 → token (these are the indexed-by-Google surfaces). |
| `templates/author.html:29,42-45` | Suppress `.split()` surname; replace CompPile hidden input / button `title` / button text with a no-name variant. |
| `templates/author.html:97,104,109` | Co-citation hint, "Who Reads [Name]", work-impact prose → "this scholar". |
| `templates/author.html:278,288` | `AUTHOR_NAME` JS constant + the `encodeURIComponent(AUTHOR_NAME)` fetch URL → token. |
| `templates/author.html:506` | Co-cited partner links: API must carry a redaction flag so a redacted *partner* renders as token too. |
| `templates/authors.html:66-68,88` | Browse list: redact **both** display text *and* `data-name` (client filter reads `data-name`; mismatch leaks the name to filtering). |
| `templates/article.html:8-12, 23-27, 53` | `citation_author` / `DC.creator` meta tags + COinS `rft.au` — these feed Zotero/Mendeley/Google Scholar; **redaction is pointless if these leak**. Skip or token-replace per author. |
| `templates/article.html:88-99, 93, 207-209` | Author display block, author `<a href>` (render plain token, no link to a name URL), cite byline surname. |
| `templates/index.html:250, 280-281` | Homepage COinS `rft.au` + author text. |
| `templates/new.html:56-57` | "What's New" author text. |
| `templates/most-cited.html:167, 186, 215, 245, 278` | First-author byline across all four view modes — centralize so none is missed. |
| `templates/institution.html:38` | Top-authors list link → token, no link. |
| `templates/book.html:60,63,150,183,194`, `templates/books.html:127` | Book editor/author byline + citation text. |
| `templates/citations.html:29` | Ego-authors byline. |
| `templates/datastories.html` / `_datastories_panels.html` | Password-gated, but data structures must still carry tokens (defense in depth). |
| `templates/about.html` | **Add the policy + link to the request page** — a new `<h2>` section placed immediately after the existing **"Values"** section (line ~57), where the existing bibliometric-skepticism framing makes it thematically at home; plus a nav/footer link. (see §5). |

**Strong recommendation:** do template-layer redaction through a **single Jinja filter** (e.g. `{{ name | redact_authors }}`) that consults the suppression set, rather than hand-editing 30+ call sites. But the *authoritative* redaction is at the data layer (§1) — the filter is belt-and-suspenders for anything the DB pass missed, and the only thing standing between a cache-miss and a leak.

### Routes / API / URL scheme

| Location | Action |
|---|---|
| `blueprints/authors.py:60` `/author/<path:name>` | The URL *is* the name. See §6. Serve the token page at `/author/Redacted%20Author%20%23a1b2c3`; the old name URL must **404** (not redirect — a redirect echoes the name in the response/Location). |
| `blueprints/authors.py:90-106` timeline/coauthors/topics/cocitation-partners | Path uses `name`; responses serialize names. Token in, tokens out. |
| `blueprints/authors.py:108-125`, `db/citations.py` `get_author_cocitation_network` | `nodes[].id/name`, `pairs[].author1/2` → tokens. |
| `blueprints/stats.py:63-72`, `db/authors.py:14-52` `get_author_network` | `node.id` + link source/target → tokens. |
| `blueprints/articles.py:30-59` `/api/articles`, `/article/<id>`, autocomplete | `authors` field + affiliations dict keys + autocomplete suggestions → tokens. |
| `blueprints/citations.py:22-29, 41-57, 102-120` ego / cocitation / centrality | Node `authors` fields → tokens. |
| `blueprints/institutions.py:25-51`, `db/institutions.py` `get_institution_top_authors` | Token, no link. |
| `blueprints/datastories.py:171-405` (15+ endpoints) | Audit every `ds_*` return for author serialization. |
| `db/authors.py` `get_all_authors`, `get_author_articles`, `get_author_coauthors`, `get_article_affiliations`, `get_author_by_name` | All key off the name string; after the data-layer pass the token simply flows through them unchanged. **No refactor to integer IDs is required** if we token-replace in place, which is the big simplification over the "introduce slug column / join table" alternative. |

> **Scope call:** an alternative design migrates to integer/slug author keys + an `articles_authors` join table + 301 redirects. **Reject that for v1.** It's a large, risky refactor of the entire author query layer for a feature that touches a handful of authors. The token-in-place approach gives identical metric preservation with no schema-shape change. Revisit only if name de-duplication becomes a separate project.

### Metrics / network — the good news

- `ds_border_crossers` (db/datastories.py:759-873): node = `article.id`, names are display-only metadata. **Zero structural impact.** Sampled betweenness (k=256) unaffected.
- `ds_solo_to_squad`, `ds_academic_lineages`, `ds_lasting_partnerships`, `get_author_network`, `get_author_coauthors`, `ds_long_tail`: **structurally identical** under a stable injective token. This is exactly why §1 insists the token be deterministic.
- Citation counts: redaction changes *names*, not citation edges or article rows, so `internal_cited_by_count` is untouched. **But** anything in this feature that ever deletes/repoints a citation row (it shouldn't, in v1) must end with `update_citation_counts()` (db/citations.py) — the standing project lesson.

### Cache — a real trap

`datastories_cache.py:40-60` `_db_fingerprint()` is `MAX(id)-COUNT(*)` for articles and citations. **Token-replacing a name in place changes neither MAX(id) nor COUNT(\*)**, so the fingerprint does **not** move and the on-disk JSON at `/data/datastories_cache/*.json` keeps serving the **real name indefinitely**. This is the single highest-severity leak in the whole design.

**Action:** the redaction helper must explicitly **bust the Datastories cache** (delete the cache dir / bump a fingerprint salt) and then re-run `/api/admin/prewarm`. Don't rely on the natural fingerprint.

### FTS / search

Covered above: rely on trigger `articles_fts_au` firing on the `authors` UPDATE, then force `INSERT INTO articles_fts(articles_fts) VALUES('rebuild')`. `weekly_maintenance.py` `step_7_retag` already does a rebuild weekly — but don't wait for Sunday; rebuild in the helper.

### Backup / restore — the deepest resurrection vector

- `backup.py` nightly 03:00 UTC → zstd + age-encrypted snapshot to B2. Retention: 30 daily / 26 weekly / 12 monthly (~up to ~1 year cold).
- `restore.py:89-145`: a restore from any pre-redaction snapshot **silently brings every redacted name back**, with no hook to re-apply suppression.

**Action:** the suppression ledger (§3) is the thing that survives this. `restore.py` must gain a **post-restore step that re-applies the suppression ledger** before the DB is served. Document the honest SLA: a name persists in encrypted cold backups for up to the retention window unless you manually purge B2 objects. That's an acceptable, disclosed limitation — say so on the About page.

### Exports / logs

- `blueprints/main.py:126-167` `/export` → `web_helpers._to_bibtex`, `_to_ris`, `_bibtex_key`. These serialize `article.authors` live (not cached). Token-replace there. For the BibTeX key, prefer `article.id`/DOI so the key stops being name-derived. Replace the author field with the token rather than omitting it (omitting breaks BibTeX validity; the token reads as an intentional redaction).
- `monitoring.py:36-76` `_scrub_pii` cleans HTTP headers/bodies but **not Sentry local-variable capture**. If a view has `name = "Alice Smith"` in a local when it raises, Sentry gets it. **Action:** set `include_local_variables=False` in the Sentry SDK config, or wrap author-name handling so it can't leak via tracebacks.
- `data_exports/coverage/*`, `health.py`, `/health/deep`: aggregate counts only — no names. No action, but note that a *delete* (vs. token-replace) would move counts and trip monitoring; another reason to prefer in-place token-replace.

---

## 3. The resurrection problem (first-class requirement)

Token-replacing today does nothing about tomorrow's fetch: CrossRef hands back *Alice Smith*, `upsert_article` writes her in, and she's un-redacted. **A persistent suppression list that every ingest path consults is not optional — it is the feature.**

### Design

A **`author_suppression`** table (the ledger):

```
author_suppression(
  token        TEXT PRIMARY KEY,   -- "Redacted Author #a1b2c3"
  name_hash    TEXT NOT NULL,      -- sha256(normalize(name) || salt); the match key
  redacted_at  TEXT NOT NULL,
  request_id   INTEGER             -- FK to redaction_requests, for audit
)
```

(Plaintext `name` stored here **only** if you choose ledger option B in §1.)

### Single choke-point, not scattered guards

It's tempting to guard ~20 write sites. **Don't.** Scattered guards rot — one new ingest script and you've leaked. Instead, push the check **into the two real bottlenecks** plus a sweep:

1. **`db/articles.py:upsert_article`** and **`db/books.py:upsert_book`** — every name eventually flows through these two. Add a `_apply_suppression(authors_string)` call that hashes each semicolon token and swaps suppressed ones for their stored pseudonym **before** the INSERT. One helper, two call sites, covers the 40+/8+ callers.
2. **`enrich_openalex.py`** writes to the normalized author tables *without* going through `upsert_article` (lines 295-322). Add the same helper at those INSERTs and gate `name_matches` (288) so a suppressed `display_name` resolves to its token.
3. **A post-fetch sweep** run at the end of `weekly_maintenance.py` (and after `deep_refresh.py`): re-apply the whole ledger across `articles.authors`, the normalized tables, and books — a cheap `UPDATE ... WHERE` per ledger row — then FTS rebuild + cache bust + `update_citation_counts()` if anything moved. This catches any path that bypassed the bottleneck and makes redaction **idempotent and self-healing**, matching the deep-refresh design ethos.

**One module: `redaction.py`** holding `mint_token()`, `apply_suppression(text)`, `redact_author(token_or_name)` (the full DB+FTS+cache pass), and `resweep_all()`. Everything else calls into it. Per the project lesson on `jflowAbbrev` (a module extraction that silently dropped an import for months), the resweep must have an **import-completeness check** — a test that imports `redaction` and asserts every ingest module references the helper — so a future module extraction can't silently drop the guard.

---

## 4. `citations.raw_reference` — honest treatment

When *other* articles cite a redacted author, that author's name sits as free text inside `citations.raw_reference` (a JSON blob, populated by `cite_fetcher.py` from CrossRef and by `scrape_lics_refs.py`). It surfaces in the reference-list UI/API via `get_article_all_references()` (the `in_index=False` branch renders `raw.get('author')`).

**Feasibility:** Low-to-medium, and lossy. These are unstructured bibliographic strings in arbitrary CrossRef formats (`Smith, J.`, `Smith, John A.`, et al.). Scrubbing them means substring-matching name variants across every reference blob in the corpus — high false-negative rate (variants you didn't hash) and non-trivial false-positive risk (redacting an unrelated *Smith*). It also touches canonical bibliographic records that aren't "about" the redacted author so much as *cite* them.

**Recommended scope boundary:**

- **In scope, v1:** fully redact all **structured** author fields — `articles.authors`, the normalized author tables, `books.authors/editors`. This is where the author's *own* identity-as-creator lives, which is what the request is really about.
- **Best-effort / opt-in, v1:** for `raw_reference`, run a best-effort pass that token-replaces the exact normalized name forms in the ledger within the JSON `author` field, gated behind a flag, with the explicit caveat that variant coverage is incomplete.
- **Documented limitation:** the About page states plainly that *the index redacts an author's own bylines and profile, but cannot guarantee removal of their name where it appears inside the bibliographies of other works* — because those are third-party citation records. This is defensible and honest, and avoids over-promising a scrub we can't actually deliver.

---

## 5. Verification + request workflow

### Tables

```
redaction_requests(
  id, author_name_claimed, requester_email, requester_orcid,
  verification_method,            -- 'orcid' | 'email'
  verification_token_hash,        -- argon2/bcrypt, one-time, NULL after use
  verification_status,            -- 'pending' | 'verified' | 'approved' | 'denied'
  created_at, verified_at, decided_at, decided_by
)
audit_log(  -- append-only; survives request deletion
  id, request_id, event, actor, detail_json, at
)
```

### Two verification paths

- **Email (P3, ships first — lowest dependency).** Public form → store request → email a one-time link (token stored **hashed**, to avoid plaintext-token DB-dump exposure) → click sets `verified`. **Open dependency:** the codebase has *no* existing SMTP/transactional-email integration (confirmed — nothing in `backup.py`/`monitoring.py`). Pick a provider before P3: Flask-Mail+SMTP is the smallest; SendGrid/SES if you want deliverability. This is a real prerequisite, not a footnote.
- **ORCID OAuth (P4).** Three-legged OAuth: requester logs into ORCID, we get their verified ORCID iD + name from the token response, match against claimed name. `client_id` non-secret; `client_secret` in `.env` only (loaded the way app.py already loads env), **never committed** — same discipline as `PINAKES_ADMIN_TOKEN`. ORCID is the stronger proof; prefer it when both are offered.

### Admin review queue — reuse existing auth

- New routes in `blueprints/admin.py`, all decorated `@require_admin_token` (auth.py), so the review surface reuses the bearer-token gate you already run the crons with — no new auth system.
  - `GET /api/admin/redaction-requests` — list pending. **Requester email/ORCID returned only to authenticated admin**, never on any public surface.
  - `POST /api/admin/redaction-request/<id>/approve` — write `audit_log` **before** calling `redaction.redact_author(...)`, so there's proof-of-review even if the request row is later purged.
  - `POST /api/admin/redaction-request/<id>/deny`.
- **Rate limiting** (rate_limit.py): add `LIMITS['redaction_request'] = '5 per hour'` and apply `@limiter.limit(...)` to the public form POST, so a script can't flood the queue / trigger email spam. (Storage is in-memory single-worker today — fine for this.)

### Policy framing for the About page

Write it as an **opt-out courtesy**, not a statutory obligation. Pinakes aggregates already-public scholarly metadata; it is not acting as a GDPR data controller in the regulatory sense, and the page should not imply a legal right it isn't promising to adjudicate. Read responsibly: *we will, on verified request by an author, replace their name with a neutral placeholder across the index while preserving the scholarship and its place in the field's citation record; the work still counts, the name comes off.* State the honest limits (third-party bibliographies; encrypted-backup retention window). Courtesy + transparency, not legalese. Run the actual page copy through the `justin-tool-copy` skill so it sounds like you, not a privacy-policy generator.

---

## 6. The URL / slug problem

Today `/author/<path:name>` (blueprints/authors.py:60) puts the name *in the URL*. After redaction:

- **Token URL is canonical.** The redacted author's page lives at `/author/Redacted%20Author%20%23a1b2c3` and renders the token, the (preserved) article list, co-author network, etc. — everything except a name.
- **Old name URL must 404, not redirect.** A 301 to the token URL would echo the real name in the request line / `Location` header / access logs. Return a clean 404 (optionally a generic "this author profile is not available" body with no name). The route handler checks: if the incoming `name` hashes to a suppressed entry, 404; the token path serves the page.
- **Internal links already point at the token** because the data layer replaced the name everywhere (§2), so nothing inside the site links to the old URL. External inbound links to the old name URL break — that's acceptable and is the correct privacy behavior (a working link *is* a name leak).

No slug column, no redirect table, no integer-ID migration needed — the token doubles as the slug.

---

## 7. Phased build plan

Smallest-first. Each phase is independently shippable and leaves the site working.

### P0 — Data spine + ledger + one helper + manual redaction *(ships the actual capability)*
- **Ships:** `redaction.py` (`mint_token`, `apply_suppression`, `redact_author`, `resweep_all`); `author_suppression` table via a new migration registered in `db/__init__.py`; a CLI/admin entry to redact one author by name. After this, *you can manually honor a request* even before the public form exists.
- **Files:** `redaction.py` (new), `db/__init__.py` + migration, `db/articles.py:upsert_article`, `db/books.py:upsert_book`, `enrich_openalex.py:288/295-322`.
- **Verify:** redact a test author; assert (a) `authors`/normalized tables/books hold the token, (b) `UNIQUE(name)` not violated, (c) re-running `upsert_article` with the real name re-suppresses it, (d) `get_author_network`/`get_author_coauthors` edge counts **identical** before/after (the metric-invariance test), (e) `update_citation_counts()` run if any citation row moved (it shouldn't). Import-completeness test: every ingest module references the helper.

### P1 — Render + FTS + URL handling *(closes the visible leaks)*
- **Ships:** `redact_authors` Jinja filter wired into all template sites in §2; FTS rebuild inside `redact_author`; old-name-URL → 404 in blueprints/authors.py.
- **Files:** the `templates/*` set in §2, `app.py` (register filter), `blueprints/authors.py`, `blueprints/articles.py`, `blueprints/institutions.py`, `db/core.py` (FTS rebuild call), `web_helpers.py` (exports).
- **Verify:** grep the rendered HTML of author/article/most-cited/institution/book pages + meta tags + COinS for the real name → zero hits. FTS: `MATCH` the old name → zero rows. Old name URL → 404. Export a `.bib`/`.ris` → token, valid format.

### P2 — Metrics / cache / backup correctness *(closes the invisible leaks)*
- **Ships:** cache-bust + prewarm inside `redact_author`; the `resweep_all()` post-fetch sweep wired into `weekly_maintenance.py` and `deep_refresh.py`; `restore.py` post-restore re-apply hook; Sentry `include_local_variables=False`.
- **Files:** `datastories_cache.py` (bust helper), `weekly_maintenance.py`, `deep_refresh.py`, `restore.py`, `monitoring.py`.
- **Verify:** redact → hit a cached Datastories endpoint → token, not name (the high-severity cache test). Run a simulated incremental fetch that re-supplies the name → still suppressed after the cron. Restore a pre-redaction snapshot into a scratch DB → run the hook → name suppressed again.

### P3 — Public request page + email verification
- **Ships:** `/redaction-request` public form, `redaction_requests` + `audit_log` tables, email provider integration, hashed one-time tokens, `LIMITS['redaction_request']`, admin queue routes in `blueprints/admin.py`.
- **Files:** new `blueprints/redaction.py` (or extend admin), new template, `rate_limit.py`, `auth.py` (reuse), email config in `.env`.
- **Verify:** submit form → hashed token in DB (not plaintext) → click verifies → admin approve writes `audit_log` *before* redaction fires → P0 helper runs. Rate limit blocks the 6th request/hour. Public list endpoint never returns requester email without the bearer token.

### P4 — ORCID OAuth
- **Ships:** ORCID three-legged OAuth as the preferred verification path; `client_secret` in `.env`.
- **Files:** `blueprints/redaction.py`, `.env`, About-page note.
- **Verify:** OAuth round-trip returns verified ORCID iD + name; mismatch with claimed name is rejected; secret never logged.

### P5 — About-page policy + audit/ops
- **Ships:** policy section (new `<h2>` after "Values") + request link on `templates/about.html` (run through `justin-tool-copy`); documented backup-retention SLA and raw_reference limitation; ops runbook for manual B2 purge if a hard "forget" is demanded.
- **Verify:** copy review; links resolve; SLA language matches actual retention config in `backup.py`.

---

## 8. Open questions / decisions for you

1. **Ledger: hash-only (A) or retain-name (B)?** Hash-only is the truer "forgotten" and the default; retain-name gives better variant matching and human-auditable review at the cost of the name living in prod + backups. Pick one — it shapes the whole ledger schema.
2. **Auto-apply vs. admin-gated.** Approve-then-redact-by-hand (P0) vs. verified-request-auto-redacts. Recommend keeping a human in the loop at least until the verification paths are battle-tested.
3. **`raw_reference` scope.** Best-effort token-replace of exact name forms, or out-of-scope-v1 with an honest About-page caveat? (Lean: best-effort + caveat.)
4. **Co-author consent.** When a redacted author shares a paper, the *co-authors'* names stay. Confirm that's intended (it preserves the citation record and is the normal expectation), and that a co-author can't force-redact a shared byline they don't own.
5. **What does the no-name author page show?** Article list + networks + institution, all under "Redacted Author #…"? Or a stripped stub with just a notice? More content = more metric utility but more re-identification surface (publication set + institution can re-identify). Your call on where the line sits.
6. **ORCID on the redacted profile.** Remove it entirely (it's a traceable identity) — confirm. (Default: blank it.)
7. **Email provider.** No email infra exists today. Flask-Mail+SMTP (smallest) vs. SendGrid/SES (deliverability)? Needed before P3.
8. **Backup "forget" SLA.** Accept the name persisting in encrypted cold backups for the retention window (disclosed on About), or commit to manually purging B2 objects on each hard-forget request? The latter is real ops work per request.
9. **reCAPTCHA on the public form?** Rate limiting (5/hour) may be enough; add reCAPTCHA v3 only if you see bot spam.
