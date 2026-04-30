# Architecture

A technical overview of how Pinakes is built. Companion to [methodology.md](methodology.md), which describes what the visualisations measure rather than how the system is wired.

## Overview

Pinakes is a Flask web application backed by a single SQLite database, populated by three ingestion paths (CrossRef, RSS / OAI-PMH / WordPress, custom HTML scrapers) and enriched weekly by OpenAlex. The corpus covers 45 venues in Rhetoric and Composition, comprising over fifty thousand articles. The full venue list is in [journal-coverage.md](journal-coverage.md). The deployment is a single Fly.io machine with one gunicorn worker for HTTP and one APScheduler process for ingestion.

The system is intentionally small. Every component is replaceable in a few hours of work without a migration plan. The constraints driving this — single maintainer, scholarly publication horizon, no funded operations team — are documented per choice in the audit notes under [`refactor-notes/`](refactor-notes/).

## Data flow

```mermaid
flowchart LR
    A1[CrossRef API] -->|fetcher.py| U(upsert_article)
    A2[RSS / OAI / WP] -->|rss_fetcher.py| U
    A3[Custom HTML] -->|scraper.py| U
    M[Manual ingest] -->|ingest_*.py| U
    U --> DB[(SQLite + WAL<br/>articles.db)]

    DB -->|enrich_openalex.py| OA[abstracts<br/>OA status<br/>affiliations]
    OA --> DB

    DB -->|cite_fetcher.py| CR[CrossRef references]
    CR --> DB

    DB -->|tagger.py| T[auto-tags]
    T --> DB

    DB -->|FTS5 triggers| FTS[(articles_fts)]

    DB --> WEB[Flask + Jinja + D3]
    WEB --> USER[/explore, /coverage, /article, ...]
```

Ingestion writes through `db.articles.upsert_article`, which is idempotent on URL: re-running an ingester never produces duplicates. Enrichment passes (OpenAlex, CrossRef references) read from and write to the same SQLite file as the live web app, in WAL mode, with one writer at a time enforced architecturally rather than via locks.

## Storage layer

A single SQLite file at `/data/articles.db` on a 3 GB Fly volume. WAL mode is on; busy_timeout is 10 s; the cache is set to 20 MB and `mmap_size` to 128 MB. Configuration is in [`db/core.py:get_conn`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/core.py#L16) — read it before changing anything; the PRAGMAs are load-bearing.

The schema has nine tables across eight migration versions, all auto-applied at app startup by `db.core.init_db`:

| Table | Purpose |
|---|---|
| `articles` | One row per article. URL is the unique key; DOI is stored separately when available |
| `articles_fts` | FTS5 virtual table over `title`, `authors`, `abstract` |
| `fetch_log` | Per-source last-fetched timestamp for incremental ingestion |
| `citations` | Directed edges between articles (source → target DOI) |
| `authors` | Canonical author records with ORCID and institution |
| `author_article_affiliations` | Author-to-article-to-institution joins |
| `books` | Monographs and edited collections, with chapters as child rows |
| `institutions` | OpenAlex institution records |
| `article_author_institutions` | Per-article author institution affiliations |
| `openalex_fetch_log` | Per-article OpenAlex fetch status, for resumable enrichment |

The choice to stay on SQLite rather than Postgres is deliberate. The corpus is read-heavy with one writer; SQLite in WAL mode handles this cleanly. The full file is under 200 MB; a relational service would add operational surface (a second container, network round-trips, retry logic) for no measurable benefit at this scale. If the corpus ever grows past ~ 5 GB or horizontal scaling becomes necessary, the migration target is Postgres on Fly's managed offering — but Pinakes is nowhere near that.

## Ingestion paths

Three live ingestion paths plus a manual fallback:

**CrossRef** ([`fetcher.py`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/fetcher.py#L83)). Twenty-eight of the forty-five venues deposit DOIs with CrossRef and are fetched by ISSN with cursor pagination. Metadata quality is uniformly high: titles, abstracts, author lists with given/family split, publication dates, sometimes subject keywords. The `fetch_log` table records the last fetch per journal so subsequent runs are incremental.

**RSS / OAI-PMH / WordPress** ([`rss_fetcher.py`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/rss_fetcher.py#L349)). Four open-access journals expose machine-readable feeds. The fetcher prefers OAI-PMH or WP REST when present (full archive, supports `from=` for incremental harvests) and falls back to the RSS feed when not (typically capped at the most recent ten or twelve items, so initial backfill requires the alternate path).

**Custom scrapers** ([`scraper.py`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/scraper.py)). Twelve venues have no machine-readable feed. Each has a per-journal scraper function with inline ethics annotations: which `robots.txt` paths are allowed, what crawl-delay is observed, what era of the site is covered. The scraper uses BeautifulSoup with `lxml` and respects per-site rate limits (5–10 s between requests is typical). Metadata quality is uneven and intentionally so: titles and authors are usually present, abstracts are not. The full set of constraints is in [CONTRIBUTING.md](../CONTRIBUTING.md#scraping-ethics).

**Manual ingest**. One journal (Pre/Text) was published only in print and has no online presence. Its record was hand-compiled and ingested once via [`ingest_peer_review_1_1.py`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/ingest_peer_review_1_1.py) and similar one-off scripts. There is no automated re-ingest path.

A two-tier metadata distinction matters for downstream tools: scraped articles often lack abstracts and lack DOIs, which excludes them from analyses that depend on rich metadata (topic modelling) or citation linkage (the CrossRef-references pipeline only resolves DOIs). The methodology document calls out which visualisations are tier-1-only.

## Enrichment

Two enrichment passes run on top of the ingested base records:

**OpenAlex** ([`enrich_openalex.py`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/enrich_openalex.py#L134)). For each article with a DOI, OpenAlex returns abstracts (decoded from the inverted-index format), open-access status (`gold`/`green`/`hybrid`/`bronze`/`closed`), the canonical OpenAlex work ID, and author affiliations with institution ROR IDs. Runs weekly via the scheduler, processes only articles whose `openalex_enriched_at` is null, polite-pool rate-limited at 10 req/s.

**CrossRef references** ([`cite_fetcher.py`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/cite_fetcher.py#L139)). For each article with a DOI, CrossRef returns its reference list. References that carry a DOI are stored in the `citations` table; the `target_article_id` is set when the cited DOI matches an in-corpus article. References without DOIs are recorded with a synthetic key derived from the SHA-256 of the raw reference string, which preserves the citation count without inflating the in-corpus citation graph.

## Tagging

[`tagger.py`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/tagger.py#L946) applies a hand-curated controlled vocabulary of around seventy-five terms to article titles and abstracts via case-insensitive regex matching. Single-word triggers are wrapped with `\b` boundaries (so "grammar" does not fire on "programmatic"); multi-word phrases are literal substring matches. The vocabulary is partial and intentionally so: it is not a substitute for a learned topic model. Its purpose is faceted browsing on the site.

The vocabulary's scope notes are being refined against [CompPile](http://comppile.org)'s controlled vocabulary so terms map to disciplinary distinctions a Rhet/Comp scholar would recognise. The audit document for that refinement will land separately.

## Web app

Flask + Jinja2 templates + D3 (loaded from CDN) for the visualisations. The application factory [`app.py:create_app`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/app.py) wires together eight Blueprints (`admin`, `main`, `articles`, `authors`, `citations`, `stats`, `books`, `institutions`) and registers shared middleware: gzip compression, security headers (X-Frame-Options DENY, HSTS, CSP), the [Flask-Limiter](https://flask-limiter.readthedocs.io/) tiered rate limiter (60/min default, 20/min on graph computations, 120/min on search), and the [admin-token decorator](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/auth.py) on mutating endpoints.

Per-visualization JavaScript lives in [`static/js/viz/`](../static/js/viz/) — eighteen ES modules loaded eagerly at page load by [`static/js/explore-loader.js`](../static/js/explore-loader.js). Eager rather than lazy: inline `onclick=` handlers in the templates would otherwise race against module loads. Browser baseline: Chrome 91+, Firefox 89+, Safari 15+.

## Deployment

Fly.io, primary region IAD, single 1 GB / 1 CPU machine. Two process groups defined in `fly.toml`:

| Process | Entry point | Role |
|---|---|---|
| `app` | gunicorn `app:app` | Web server. One worker (single SQLite writer) |
| `scheduler` | `python scheduler.py` | Daily fetch + weekly OpenAlex enrichment + nightly backup. No HTTP |

A 3 GB persistent volume mounted at `/data` holds the SQLite file, the WAL files, and the scheduler heartbeat. The volume survives deploys; a deploy replaces the machine but leaves the data in place. Disaster recovery via off-machine backups is documented in [runbooks/disaster-recovery.md](runbooks/disaster-recovery.md).

The deploy pipeline is gated on tests via GitHub Actions: every push to `main` runs `pytest`, and the Fly deploy workflow only fires when tests pass.

## Observability

Three layers of operational visibility:

**Logs.** Standard Python logging at INFO in production; piped to `flyctl logs`. Log lines include the source IP (preferring the `Fly-Client-IP` header), the route path, and the structured event (`Auth required`, `rate limit hit`, `Backup OK`, etc.). No request bodies are logged.

**Sentry** (optional, gated on the `SENTRY_DSN` secret). Errors from the web process are tagged `component=web`; from the scheduler `component=scheduler`. Ingestion errors carry `source=crossref|rss|scrape|openalex|citations` and `journal=<name>`. PII scrubbing strips `Authorization` headers, `Cookie` headers, query parameters whose name contains `token`, and request bodies. Configuration in [`monitoring.py`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/monitoring.py).

**Health endpoints**:

| Endpoint | Auth | Purpose |
|---|---|---|
| `/health` | none | Liveness — process is up. <1 ms, no DB query |
| `/health/ready` | none | Readiness — DB reachable. Used by Fly's check loop |
| `/health/deep` | admin token | Full diagnostic — counts, last-fetch, disk free, scheduler heartbeat age, integrity check (cached 6 h), security-header config |

## Testing

Pytest harness with three rings, all pure-offline (HTTP stubbed via `responses` and `feedparser` mocks; isolated SQLite per test):

1. **Unit tests** for every public function in `db/`, with one happy path and one edge case each.
2. **Route smoke tests** for every URL in `app.url_map`, including the JSON shape contract for `/api/*`, the BibTeX/RIS grammar for `/export`, and the security-header presence on every response.
3. **Ingestion parser tests** with captured fixture payloads for CrossRef, RSS, and the scraper, exercising both happy paths and malformed inputs.

CI runs the full suite (currently 351 tests in around 8 seconds) on every push and pull request. The deploy workflow is gated on test passage. Test infrastructure is documented in [`refactor-notes/01-test-harness.md`](refactor-notes/01-test-harness.md).

## What we explicitly do not do

These choices are deliberate. Future maintainers should not "fix" them without first reading the linked audit note.

- **No live scraping during web requests.** Every viz reads pre-computed data from SQLite. Scraping is offline, scheduled, polite. ([CONTRIBUTING.md scraping ethics](../CONTRIBUTING.md#scraping-ethics).)
- **No user accounts.** A single shared bearer token gates the mutating `/fetch` endpoint. ([refactor-notes/02-admin-auth.md](refactor-notes/02-admin-auth.md).)
- **No write-through caching layer.** Citation graphs and other expensive computations run on demand against SQLite. The corpus is small enough that this is fast (sub-second for most graph endpoints, under three seconds for the heaviest). ([refactor-notes/03-rate-limiting.md](refactor-notes/03-rate-limiting.md) for the protective rate caps.)
- **No microservices.** One web process, one scheduler process, one SQLite file. The deploy is one Fly machine. The complexity of a service mesh is not justified by the workload.
- **No bundler for the JavaScript.** Native ES modules, D3 from CDN, no `package.json`. ([refactor-notes/11-explore-js-split-inventory.md](refactor-notes/11-explore-js-split-inventory.md).)
- **No ORM.** Direct `sqlite3` with parameterised queries. ([refactor-notes/09-db-split-inventory.md](refactor-notes/09-db-split-inventory.md).)

If any of these stop fitting Pinakes — different scale, different team, different deployment — the right move is to revisit the linked audit note and write a new one explaining the change, not to retrofit a heavier architecture into the existing code.
