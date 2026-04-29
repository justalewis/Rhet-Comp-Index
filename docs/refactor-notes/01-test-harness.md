# 01 — Test harness (Prompt A1)

Audit trail for the addition of a pytest harness as the regression safety net for all subsequent work in the improvement plan.

## What was added

Test infrastructure:

- `pytest.ini` — testpaths, markers, `filterwarnings = error`, default `-m "not slow and not network"`.
- `conftest.py` (repo root) — sets `DB_PATH` to a session-temp file at module import time so production `articles.db` is never touched, and provides `fixture_db`, `seeded_db`, `app`, `client`, `empty_app`, `empty_client`, `freeze_time` fixtures. Resets `app._sidebar_cache`, `db._DETAILED_COVERAGE_CACHE` between tests.
- `requirements-dev.txt` — pulls in pytest, pytest-cov, responses, freezegun.
- `.github/workflows/test.yml` — runs pytest on every push and PR. The two existing deploy workflows were re-pointed to `workflow_run` so deploys fire only when Tests succeeds on `main`.

Fixtures and seed:

- `tests/_seed.py` — deterministic seed that produces a fully reproducible DB:
  50 articles spread 13/13/12/12 across `College English` (CrossRef), `Present Tense` (RSS), `Kairos` (scrape), `Pre/Text` (manual); 20 authors with realistic name variants; 30 directed citation edges; 5 books (3 monographs + 2 edited collections); 8 chapters under one book; 6 institutions.
- `tests/_route_schemas.py` — top-level JSON shape contracts per `/api/*` route and required heading per HTML route.
- `tests/fixtures/crossref_sample.json`, `rss_sample.xml`, `scraper_sample.html` — small synthetic ingestion payloads (no real personal data).

Test files (14, 259 tests total):

| File | Tests |
|---|---|
| `tests/test_db_articles.py` | 42 |
| `tests/test_db_authors.py` | 12 |
| `tests/test_db_books.py` | 12 |
| `tests/test_db_citations.py` | 19 |
| `tests/test_db_coverage.py` | 8 |
| `tests/test_routes_html.py` | 28 |
| `tests/test_routes_api.py` | 32 |
| `tests/test_routes_export.py` | 10 |
| `tests/test_routes_security_headers.py` | 7 |
| `tests/test_fetcher_crossref.py` | 16 |
| `tests/test_rss_fetcher.py` | 14 |
| `tests/test_scraper.py` | 13 |
| `tests/test_tagger.py` | 11 |
| `tests/test_input_validation.py` | 35 |
| **Total** | **259** |

## Ring 1 — `db.py` functions covered

Each function listed with its definition line in `db.py`:

| Function | Line |
|---|---|
| `init_db` | 374 |
| `_sanitize_fts` | 409 |
| `_build_where` | 429 |
| `upsert_article` | 473 |
| `backfill_oa_status` | 535 |
| `get_articles` | 586 |
| `get_total_count` | 603 |
| `get_all_tags` | 627 |
| `get_year_range` | 658 |
| `get_article_by_id` | 676 |
| `get_new_articles` | 812 |
| `get_new_article_count` | 823 |
| `get_all_authors` | 832 |
| `get_author_articles` | 852 |
| `get_author_by_name` | 1482 |
| `get_authors_by_letter` | 1491 |
| `get_top_institutions` | 1579 |
| `get_most_cited` | 1243 |
| `upsert_citation` | 1187 |
| `get_citation_network` | 1278 |
| `get_cocitation_network` | 2149 |
| `get_bibcoupling_network` | 2288 |
| `get_citation_centrality` | 2685 |
| `get_ego_network` | 1105 |
| `get_sleeping_beauties` | 2430 |
| `get_journal_half_life` | 2938 |
| `get_main_path` | 3339 |
| `get_community_detection` | 3128 |
| `get_books` | 1711 |
| `get_book_count` | 1751 |
| `get_book_by_id` | 1786 |
| `get_book_chapters` | 1804 |
| `get_book_publishers` | 1821 |
| `get_coverage_stats` | 1392 |
| `get_detailed_coverage` | 1422 |

## Bugs discovered (kept, marked `# XXX:`)

1. **`db.py:2558` — `get_sleeping_beauties` crashes with `ValueError('max() iterable argument is empty')`** when an article's publication year is later than the latest year of any citing article. The `full_timeline` range becomes empty and `max()` is called without `default=`. Triggered in tests by seed edge `(45, 6)` (a 2005 source citing a 2022 target — physically impossible but representative of dirty data). Test `test_db_citations.py::test_get_sleeping_beauties_crashes_on_invalid_timeline` asserts current behavior. Suspected fix: skip the article when `t0 > max_year`, or pass `default=t0` to `max()`.

No other bugs surfaced during this run.

## Coverage achieved

```
Name     Stmts   Miss  Cover
----------------------------
app.py     658     39    94%
db.py     1751    501    71%
----------------------------
TOTAL     2409    540    78%
```

Targets from the prompt: ≥ 60% on `db.py`, ≥ 75% on `app.py`. Both exceeded.

## Runtime

- Wall-clock: **6.35 s** for the full default suite on developer machine (Windows 11, Python 3.12.5).
- Target was < 30 s.
- `pytest --collect-only -q | wc -l`: **259** test items.

## Constraints met

- No production module was modified (verified via `git diff main -- '*.py' ':!conftest.py' ':!tests/'`).
- No network calls in the default suite (`responses` and `feedparser` stubbed throughout; no `@pytest.mark.network` tests yet).
- No mutation of the real `articles.db` (DB_PATH pointed at session-temp file before any `import app`/`import db`).
- All warnings treated as errors via `filterwarnings = error` (with one apscheduler ignore that the upstream library still emits).

## What this run does NOT do

- No bug fixes — the one `# XXX:` finding is documented but not fixed.
- No new dependencies in `requirements.txt` (only `requirements-dev.txt`).
- No refactoring of `db.py`/`app.py`/the ingesters.
- No tests for the live OAI-PMH or WordPress backends in `rss_fetcher.py` (only the feedparser path). These can be added in a later prompt if needed.
- No tests for `scheduler.py`, `enrich.py`, `enrich_openalex.py`, or any of the one-off ingestion scripts (`fetch_*.py`, `ingest_*.py`, `scrape_*.py`).
