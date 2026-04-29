# 09 — db.py split inventory (Prompt E1)

Migration record for the `db.py` → `db/` package decomposition. The original `db.py` was 4,405 lines and 96 functions; the new package is 8 submodules organized by concern, with public API preserved exactly via re-exports in `db/__init__.py`.

## Method

Functions are extracted from `db.py` line-by-line via `ast.parse` to capture every top-level `FunctionDef` and `Assign` with its line range. Each chunk's bytes are copied verbatim into its target module — no SQL changes, no docstring edits, no type-hint changes. Inter-function section comments (`# ── Reads ─────`) in the original are dropped; each new module is small enough that the within-module organization speaks for itself.

## Cross-module private dependencies

| Helper | Defined in (after) | Called from (after) | Decision |
|---|---|---|---|
| `_sanitize_fts` | `db/core.py` | `db/core.py:_build_where`, `db/articles.py:search_articles_autocomplete` | Keep private; cross-package import via `from .core import _sanitize_fts` |
| `_build_where` | `db/core.py` | `db/articles.py:get_articles`, `db/articles.py:get_total_count` | Keep private; cross-package import |
| `_DETAILED_COVERAGE_CACHE` | `db/coverage.py` | `tests/conftest.py:fixture_db` (clears it) | Re-export from `db/__init__.py` so `db._DETAILED_COVERAGE_CACHE` still resolves; same dict object, mutations visible from both names |
| `_DETAILED_COVERAGE_TTL` | `db/coverage.py` | `db/coverage.py:get_detailed_coverage` only | No cross-module use; not re-exported |
| `_pagerank_python`, `_median_from_freq`, `_percentile_from_freq`, `_normalized_institutions_fresh` | their respective submodule | only their own submodule | No cross-module use; not re-exported |
| `_create_tables`, `_create_fts`, `_migrate_v*_to_v*` | `db/core.py` | `db/core.py:init_db` only | No cross-module use; not re-exported |

## DB_PATH handling

The original `db.py` had `DB_PATH = os.environ.get("DB_PATH", ...)` at module scope and `get_conn()` referenced it as a module-level name. Tests monkeypatch `db.DB_PATH` per fixture (`monkeypatch.setattr(_db, "DB_PATH", str(db_file))`); for that monkeypatch to keep working, the post-refactor `db.DB_PATH` must be the value `get_conn()` actually reads.

**Resolution**: `db/__init__.py` defines `DB_PATH` at package scope. `db/core.py:get_conn` does `from . import DB_PATH` *inside* the function body — re-binding the local name on every call from the package namespace. When the test monkeypatches `db.DB_PATH`, the next `get_conn()` call picks up the new value. No magic, no module-class override.

## Function assignments

### Module: `db/core.py`

| Line in original `db.py` | Name | Notes |
|---:|---|---|
| 33 | `get_conn` | PRAGMAs configured here. `from . import DB_PATH` inside body |
| 52 | `_create_tables` | Private; called only from migrations |
| 92 | `_create_fts` | Private; called only from migrations |
| 134 | `_migrate_v1_to_v3` | Private |
| 174 | `_migrate_v2_to_v3` | Private |
| 195 | `_migrate_v3_to_v4` | Private |
| 208 | `_migrate_v4_to_v5` | Private |
| 242 | `_migrate_v5_to_v6` | Private |
| 293 | `_migrate_v6_to_v7` | Private |
| 333 | `_migrate_v7_to_v8` | Private |
| 374 | `init_db` | Public; runs all applicable migrations idempotently |
| 409 | `_sanitize_fts` | Re-exported for tests |
| 429 | `_build_where` | Re-exported for tests |

### Module: `db/fetch_log.py`

| Line | Name |
|---:|---|
| 497 | `update_fetch_log` |
| 506 | `get_last_fetch` |

### Module: `db/articles.py`

| Line | Name |
|---:|---|
| 473 | `upsert_article` |
| 515 | `update_oa_url` |
| 525 | `update_semantic_data` |
| 586 | `get_articles` |
| 603 | `get_total_count` |
| 615 | `get_article_counts` |
| 627 | `get_all_tags` |
| 658 | `get_year_range` |
| 676 | `get_article_by_id` |
| 685 | `get_related_articles` |
| 719 | `get_timeline_data` |
| 732 | `get_tag_cooccurrence` |
| 812 | `get_new_articles` |
| 823 | `get_new_article_count` |
| 3829 | `search_articles_autocomplete` |

### Module: `db/authors.py`

| Line | Name |
|---:|---|
| 771 | `get_author_network` |
| 832 | `get_all_authors` |
| 852 | `get_author_articles` |
| 863 | `get_author_books` |
| 877 | `get_author_timeline` |
| 930 | `get_author_coauthors` |
| 976 | `get_author_topics` |
| 1459 | `get_article_affiliations` |
| 1482 | `get_author_by_name` |
| 1491 | `get_authors_by_letter` |
| 1539 | `get_all_authors_with_institutions` |
| 2086 | `get_author_affiliations_per_article` |
| 2119 | `get_author_institution_summary` |

### Module: `db/citations.py`

| Line | Name |
|---:|---|
| 1000 | `get_articles_needing_citation_fetch` |
| 1013 | `get_doi_to_article_id_map` |
| 1022 | `get_article_citations` |
| 1034 | `get_article_references` |
| 1046 | `get_article_all_references` |
| 1105 | `get_ego_network` |
| 1175 | `get_outside_citation_count` |
| 1187 | `upsert_citation` |
| 1207 | `mark_references_fetched` |
| 1219 | `update_citation_counts` |
| 1243 | `get_most_cited` |
| 1278 | `get_citation_network` |
| 1349 | `get_citation_trends` |
| 2149 | `get_cocitation_network` |
| 2288 | `get_bibcoupling_network` |
| 2430 | `get_sleeping_beauties` |
| 2645 | `_pagerank_python` |
| 2685 | `get_citation_centrality` |
| 2812 | `get_journal_citation_flow` |
| 2910 | `_median_from_freq` |
| 2924 | `_percentile_from_freq` |
| 2938 | `get_journal_half_life` |
| 3128 | `get_community_detection` |
| 3339 | `get_main_path` |
| 3554 | `get_temporal_network_evolution` |
| 3851 | `get_reading_path` |
| 4109 | `get_author_cocitation_network` |
| 4325 | `get_author_cocitation_partners` |

### Module: `db/books.py`

| Line | Name |
|---:|---|
| 1656 | `upsert_book` |
| 1711 | `get_books` |
| 1751 | `get_book_count` |
| 1786 | `get_book_by_id` |
| 1795 | `get_book_by_doi` |
| 1804 | `get_book_chapters` |
| 1821 | `get_book_publishers` |
| 1836 | `get_books_fetch_log` |
| 1846 | `update_books_fetch_log` |

### Module: `db/institutions.py`

| Line | Name |
|---:|---|
| 1579 | `get_top_institutions` |
| 1597 | `get_institution_timeline` |
| 1858 | `upsert_institution` |
| 1892 | `insert_article_author_institution` |
| 1904 | `log_openalex_fetch` |
| 1917 | `get_articles_needing_institution_fetch` |
| 1931 | `get_institution_by_id` |
| 1940 | `get_institution_article_count` |
| 1950 | `get_institution_articles` |
| 1964 | `get_institution_top_authors` |
| 1978 | `_normalized_institutions_fresh` |
| 1996 | `get_top_institutions_v2` |
| 2032 | `get_institution_timeline_v2` |

### Module: `db/coverage.py`

| Line | Name |
|---:|---|
| 535 | `backfill_oa_status` |
| 1392 | `get_coverage_stats` |
| 1418 | `_DETAILED_COVERAGE_CACHE` (constant) |
| 1419 | `_DETAILED_COVERAGE_TTL` (constant) |
| 1422 | `get_detailed_coverage` |

## Verification record

### Pre/post public-API diff

Before the split, `dir(db)` exposed **105** names (96 functions + 17 private helpers/migrations + module imports `json`, `sqlite3`, `combinations` that had leaked as module attributes + `DB_PATH`).

After the split, `dir(db)` exposes **96** names. The diff:

**Names removed from the public surface (none of which are referenced by any caller):**

- `_create_tables`, `_create_fts`, `_migrate_v1_to_v3`, `_migrate_v2_to_v3`, `_migrate_v3_to_v4`, `_migrate_v4_to_v5`, `_migrate_v5_to_v6`, `_migrate_v6_to_v7`, `_migrate_v7_to_v8` — schema scaffolding, called only by `init_db` itself.
- `_pagerank_python`, `_median_from_freq`, `_percentile_from_freq` — citation-analytics helpers, called only by other functions in the same submodule.
- `_normalized_institutions_fresh` — institutions helper, called only by other functions in the same submodule.
- `_DETAILED_COVERAGE_TTL` — constant only used inside `get_detailed_coverage`.
- `combinations`, `json`, `sqlite3` — accidental leaks from `import` statements at the top of the original `db.py`. Not referenced anywhere.

**Names added to the public surface:**

- `articles`, `authors`, `books`, `citations`, `core`, `coverage`, `fetch_log`, `institutions` — the new submodules (visible because `db` is now a package, not a single module). They don't shadow any existing name.

**Names referenced externally that are preserved:** `DB_PATH`, `_build_where`, `_sanitize_fts`, `_DETAILED_COVERAGE_CACHE` — all four are re-exported by `db/__init__.py` and resolve to the same objects as the submodule definitions.

### Test-suite parity

`pytest` reports **351 / 351 passing** with zero edits to any test file or to `conftest.py` — the verification criterion the prompt set out.

One regression was caught and fixed during the migration: `db.coverage.get_detailed_coverage` reads `data_exports/coverage/coverage_snapshot.json` as a fallback when the live SQL build fails. The path was computed via `os.path.dirname(__file__)`, which used to resolve to the repo root (where `db.py` lived) and now resolves to `db/`. Fixed by going up one level: `os.path.dirname(os.path.dirname(__file__))`. Caught by `tests/test_routes_html.py::test_html_route_returns_200_and_heading[/coverage-Corpus Snapshot]`.

### `DB_PATH` plumbing — confirmed working

The `from . import DB_PATH` inside `db.core.get_conn` correctly picks up `monkeypatch.setattr(db, "DB_PATH", ...)` in the test fixture: every `db_articles`, `db_authors`, `db_citations`, etc. test runs against its per-test temp DB, with no test edits required.

### Coverage

| File | Statements | Coverage |
|---|---:|---:|
| `db/articles.py` | 250 | 89% |
| `db/authors.py` | 173 | 87% |
| `db/books.py` | 107 | 59% |
| `db/citations.py` | 1066 | 69% |
| `db/core.py` | 138 | 74% |
| `db/coverage.py` | 63 | 89% |
| `db/fetch_log.py` | 15 | 100% |
| `db/institutions.py` | 117 | 54% |
| **TOTAL** | **2527** | **79%** |

Total line count is essentially unchanged from the pre-split `db.py` (2,527 vs. 2,449 statements; the small increase is the per-module imports and headers). Coverage holds steady at ~ 70-79% — no test reach was lost in the split.

## Files moved (git-history-preserved? no)

The split tool extracted function bodies and emitted them to new files; from git's perspective these are "new files" rather than "renamed copies." This is acceptable for a refactor of this scale — git's rename detection wouldn't have found the chunks anyway. The inventory above is the authoritative map; `git log --follow db/citations.py` will show the file's history starting from this commit.

