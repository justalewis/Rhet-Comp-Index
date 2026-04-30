# 10 — app.py split into Blueprints (Prompt F1)

Migration record for the `app.py` (1,460 lines, 44 routes) → application factory + 8 Blueprints decomposition. Public URLs preserved exactly; internal endpoint names become `<bp>.<view>`.

## Discovered constraint

Templates use **zero `url_for()` calls**, and Python code has **zero `url_for()` calls**, so the post-refactor change in endpoint naming (`index` → `main.index`) doesn't break anything. The two `redirect()` calls in the codebase use raw paths. Standard Blueprint endpoint prefixing is safe.

## Test-driven shape constraints

Several tests reach into module state via `app.X`. These names MUST stay accessible at `app.X` after the refactor:

| Test reference | Why | Where it lives now |
|---|---|---|
| `app._run_background_fetch` | `patch("app._run_background_fetch", mock)` in test_routes_export, test_auth, test_rate_limit | Defined in `app.py` (factory module) |
| `app._sidebar_cache`, `app._sidebar_ts` | conftest sets them to invalidate the cache between tests | Defined in `app.py` |
| `app._safe_int`, `app._safe_float` | test_input_validation reads them | Re-exported from `web_helpers` |
| `app._bibtex_key`, `app._to_bibtex`, `app._to_ris` | test_input_validation reads them | Re-exported from `web_helpers` |
| `app.format_period` | test_input_validation reads it | Re-exported from `web_helpers` |

## Blueprint assignments

### `blueprints/admin.py` (4 routes)

| Method | URL | View | Decorators |
|---|---|---|---|
| POST | `/fetch` | `trigger_fetch` | `@require_admin_token`, `@limiter.limit(LIMITS["fetch"], exempt_when=fetch_auth_failing)` |
| GET | `/health` | `health` | `@limiter.exempt` |
| GET | `/health/ready` | `health_ready` | `@limiter.exempt` |
| GET | `/health/deep` | `health_deep` | `@require_admin_token` |

### `blueprints/main.py` (8 routes — HTML pages)

| URL | View |
|---|---|
| `/` | `index` |
| `/export` | `export` |
| `/tools` | `tools` |
| `/explore` | `explore` |
| `/citations` | `citation_network_page` |
| `/new` | `new_articles` |
| `/about` | `about` |
| `/coverage` | `coverage` |

### `blueprints/articles.py` (3 routes)

| URL | View | Decorators |
|---|---|---|
| `/api/articles` | `api_articles` | — |
| `/api/articles/search` | `api_article_search` | `@limiter.limit(LIMITS["search"])` |
| `/article/<int:article_id>` | `article_detail` | — |

### `blueprints/authors.py` (7 routes)

| URL | View |
|---|---|
| `/authors` | `authors_list` |
| `/author/<path:name>` | `author_detail` |
| `/api/author/<path:name>/timeline` | `author_timeline_api` |
| `/api/author/<path:name>/coauthors` | `author_coauthors_api` |
| `/api/author/<path:name>/topics` | `author_topics_api` |
| `/api/author/<path:name>/cocitation-partners` | `api_author_cocitation_partners` |
| `/api/author-cocitation` | `api_author_cocitation` (citations tier limit) |

### `blueprints/citations.py` (13 routes)

All decorated with `@limiter.limit(LIMITS["citations"])` and `@cache_response(...)`.

| URL | View |
|---|---|
| `/api/citations/ego` | `api_ego_network` |
| `/api/citations/network` | `api_citations_network` |
| `/api/citations/cocitation` | `api_cocitation_network` |
| `/api/citations/bibcoupling` | `api_bibcoupling_network` |
| `/api/citations/centrality` | `api_citation_centrality` |
| `/api/citations/sleeping-beauties` | `api_sleeping_beauties` |
| `/api/citations/journal-flow` | `api_journal_citation_flow` |
| `/api/citations/half-life` | `api_citation_half_life` |
| `/api/citations/communities` | `api_citation_communities` |
| `/api/citations/main-path` | `api_main_path` |
| `/api/citations/temporal-evolution` | `api_temporal_evolution` |
| `/api/citations/reading-path` | `api_reading_path` |
| `/api/stats/citation-trends` | `api_citation_trends` (citations tier) |

### `blueprints/stats.py` (6 routes)

| URL | View | Decorators |
|---|---|---|
| `/api/stats/timeline` | `api_timeline` | stats tier + cache |
| `/api/stats/tag-cooccurrence` | `api_tag_cooccurrence` | stats tier + cache |
| `/api/stats/author-network` | `api_author_network` | stats tier + cache |
| `/api/stats/most-cited` | `api_most_cited` | stats tier |
| `/api/stats/institutions` | `api_institutions` | stats tier + cache |
| `/most-cited` | `most_cited_page` | `@cache_response(seconds=1800)` |

### `blueprints/books.py` (2 routes)

| URL | View | Decorators |
|---|---|---|
| `/books` | `books` | `@cache_response(seconds=600)` |
| `/book/<int:book_id>` | `book_detail` | `@cache_response(seconds=600)` |

### `blueprints/institutions.py` (1 route)

| URL | View | Decorators |
|---|---|---|
| `/institution/<int:institution_id>` | `institution_detail` | `@cache_response(seconds=3600)` |

**Total: 4 + 8 + 3 + 7 + 13 + 6 + 2 + 1 = 44 routes.** Match.

## URL map verification

Captured `app.url_map` before and after the refactor. **45 paths in both** (44 user routes plus the static-file rule). Diff of `(rule.rule, methods)` tuples: zero entries missing, zero entries added. Endpoint names changed from bare (`index`) to prefixed (`main.index`) for every non-static rule — by design, and verified harmless because no `url_for()` call exists anywhere in templates or Python code (codebase searched).

## File sizes

| File | Lines | Target | Notes |
|---|---:|---|---|
| `app.py` | 175 | < 100 | over by 75; see below |
| `web_helpers.py` | 306 | — | helpers + filters + handlers + middleware + sidebar builder |
| `blueprints/admin.py` | 58 | < 600 | |
| `blueprints/articles.py` | 111 | < 600 | |
| `blueprints/authors.py` | 130 | < 600 | |
| `blueprints/books.py` | 106 | < 600 | |
| `blueprints/citations.py` | 265 | < 600 | largest Blueprint; 13 routes |
| `blueprints/institutions.py` | 52 | < 600 | |
| `blueprints/main.py` | 290 | < 600 | second-largest; 8 HTML routes |
| `blueprints/stats.py` | 179 | < 600 | |

The `app.py` factory exceeds the < 100-line target because three things must remain in it for test compatibility:

1. **`_run_background_fetch`** (~ 17 lines) — `tests/test_routes_export.py`, `tests/test_auth.py`, and `tests/test_rate_limit.py` all use `patch("app._run_background_fetch", mock)`. Moving it elsewhere would break the patch target. (Tests cannot be edited per the prompt.)
2. **`_get_sidebar` + cache state** (~ 13 lines) — `conftest.py` invalidates the sidebar cache between tests via `_app_module._sidebar_cache = None`. The cache state must live in `app.py` so the assignment hits the right module attribute. The pure cache-miss builder (`build_sidebar`) was moved to `web_helpers.py`.
3. **Re-exports of `_safe_int`, `_safe_float`, `_bibtex_key`, `_to_bibtex`, `_to_ris`, `format_period`** — `tests/test_input_validation.py` reads these via `app.X`. `from web_helpers import ...` re-binds them, so the tests find them.

The remaining content is the factory itself (~ 70 lines including DB warmup), required imports (~ 30 lines), and the `if __name__ == "__main__"` runner. None of it is "logic" in the sense the prompt warned against — the factory is wiring, the rest is required by test contracts.

## Test-suite parity

`pytest` reports **351 / 351 passing** with **zero edits to any test file or to `conftest.py`**. The verification criterion the prompt set out.

## Issues caught and fixed during the split

The split tool's first pass produced 10 test failures across 4 categories:

1. **Missing constant** `COVERAGE_SINCE_PRESETS` in `blueprints/main.py` — the tool extracted only function definitions, not module-level constants. Manually added to `main.py`.
2. **Missing import** `get_coverage_stats` in `blueprints/main.py` and `blueprints/articles.py` — the per-Blueprint import lists were eyeballed, not derived from each function's body. Added.
3. **`_run_background_fetch` resolution** — the extracted `trigger_fetch` view used the bare name; the test-patch contract requires `app._run_background_fetch` to be the actual lookup site. Fixed with `import app as _app` inside `trigger_fetch`.

All three were caught by the test suite — exactly the safety net A1 was meant to provide.

## Decisions explicitly NOT made in this run

- **No URL changes.** Endpoint names changed (`index` → `main.index`) but URLs are byte-identical.
- **No view-function renames.** Function names match the originals exactly.
- **No view-function consolidation.** Several routes share boilerplate (`_get_sidebar()`, `get_new_article_count()`); not consolidated. That's a separate code-quality pass.
- **No async views, no websocket support, no Flask-RESTful adoption.** Same direct-`render_template` / `jsonify` style.
- **No template edits.** Blueprints share the app's template folder by default.
- **No `register_error_handlers` for the 429 handler in a Blueprint.** Centralised on the app.

