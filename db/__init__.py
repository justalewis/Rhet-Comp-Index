"""db — Pinakes database layer.

Public API preserved across the prompt-E1 refactor: every name previously
importable from the monolithic db.py is re-exported here. Internal organization
is in submodules: core, articles, authors, citations, books, institutions,
coverage, fetch_log.
"""

import os
import logging

log = logging.getLogger(__name__)

# DB_PATH is read by db.core.get_conn() on every call (via `from . import
# DB_PATH`), so per-test `monkeypatch.setattr(_db, 'DB_PATH', ...)` continues
# to work after the package split.
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "articles.db"))
DB_PATH = os.path.abspath(DB_PATH)

from .core import (
    _build_where,
    _sanitize_fts,
    get_conn,
    init_db,
)

from .fetch_log import (
    get_last_fetch,
    update_fetch_log,
)

from .articles import (
    get_all_tags,
    get_article_by_id,
    get_article_counts,
    get_articles,
    get_new_article_count,
    get_new_articles,
    get_related_articles,
    get_tag_cooccurrence,
    get_timeline_data,
    get_total_count,
    get_year_range,
    search_articles_autocomplete,
    update_oa_url,
    update_semantic_data,
    upsert_article,
)

from .authors import (
    get_all_authors,
    get_all_authors_with_institutions,
    get_article_affiliations,
    get_author_affiliations_per_article,
    get_author_articles,
    get_author_books,
    get_author_by_name,
    get_author_citing_venues,
    get_author_coauthors,
    get_author_institution_summary,
    get_author_network,
    get_author_timeline,
    get_author_topics,
    get_authors_by_letter,
)

from .books import (
    get_book_by_doi,
    get_book_by_id,
    get_book_chapters,
    get_book_count,
    get_book_publishers,
    get_books,
    get_books_fetch_log,
    update_books_fetch_log,
    upsert_book,
)

from .institutions import (
    get_articles_needing_institution_fetch,
    get_institution_article_count,
    get_institution_articles,
    get_institution_by_id,
    get_institution_timeline,
    get_institution_timeline_v2,
    get_institution_top_authors,
    get_top_institutions,
    get_top_institutions_v2,
    insert_article_author_institution,
    log_openalex_fetch,
    upsert_institution,
)

from .coverage import (
    backfill_oa_status,
    get_coverage_stats,
    get_detailed_coverage,
)

from .citations import (
    get_article_all_references,
    get_article_citations,
    get_article_references,
    get_articles_needing_citation_fetch,
    get_author_cocitation_network,
    get_author_cocitation_partners,
    get_bibcoupling_network,
    get_citation_centrality,
    get_citation_network,
    get_citation_trends,
    get_cocitation_network,
    get_community_detection,
    get_doi_to_article_id_map,
    get_ego_network,
    get_journal_citation_flow,
    get_journal_half_life,
    get_main_path,
    get_most_cited,
    get_outside_citation_count,
    get_reading_path,
    get_sleeping_beauties,
    get_temporal_network_evolution,
    mark_references_fetched,
    update_citation_counts,
    upsert_citation,
)

from .coverage import _DETAILED_COVERAGE_CACHE  # noqa: F401

__all__ = [
    "DB_PATH",
    "_build_where",
    "_sanitize_fts",
    "backfill_oa_status",
    "get_all_authors",
    "get_all_authors_with_institutions",
    "get_all_tags",
    "get_article_affiliations",
    "get_article_all_references",
    "get_article_by_id",
    "get_article_citations",
    "get_article_counts",
    "get_article_references",
    "get_articles",
    "get_articles_needing_citation_fetch",
    "get_articles_needing_institution_fetch",
    "get_author_affiliations_per_article",
    "get_author_articles",
    "get_author_books",
    "get_author_by_name",
    "get_author_citing_venues",
    "get_author_coauthors",
    "get_author_cocitation_network",
    "get_author_cocitation_partners",
    "get_author_institution_summary",
    "get_author_network",
    "get_author_timeline",
    "get_author_topics",
    "get_authors_by_letter",
    "get_bibcoupling_network",
    "get_book_by_doi",
    "get_book_by_id",
    "get_book_chapters",
    "get_book_count",
    "get_book_publishers",
    "get_books",
    "get_books_fetch_log",
    "get_citation_centrality",
    "get_citation_network",
    "get_citation_trends",
    "get_cocitation_network",
    "get_community_detection",
    "get_conn",
    "get_coverage_stats",
    "get_detailed_coverage",
    "get_doi_to_article_id_map",
    "get_ego_network",
    "get_institution_article_count",
    "get_institution_articles",
    "get_institution_by_id",
    "get_institution_timeline",
    "get_institution_timeline_v2",
    "get_institution_top_authors",
    "get_journal_citation_flow",
    "get_journal_half_life",
    "get_last_fetch",
    "get_main_path",
    "get_most_cited",
    "get_new_article_count",
    "get_new_articles",
    "get_outside_citation_count",
    "get_reading_path",
    "get_related_articles",
    "get_sleeping_beauties",
    "get_tag_cooccurrence",
    "get_temporal_network_evolution",
    "get_timeline_data",
    "get_top_institutions",
    "get_top_institutions_v2",
    "get_total_count",
    "get_year_range",
    "init_db",
    "insert_article_author_institution",
    "log_openalex_fetch",
    "mark_references_fetched",
    "search_articles_autocomplete",
    "update_books_fetch_log",
    "update_citation_counts",
    "update_fetch_log",
    "update_oa_url",
    "update_semantic_data",
    "upsert_article",
    "upsert_book",
    "upsert_citation",
    "upsert_institution",
]
