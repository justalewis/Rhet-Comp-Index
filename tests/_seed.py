"""Deterministic seed for the Pinakes test harness.

Same seed function called twice produces byte-identical DBs. Counts:
  - 50 articles  (13 + 13 + 12 + 12 across 4 journals representing all 4 sources)
  - 20 authors   (in articles.authors text + authors table + affiliations)
  - 30 citation edges  (form a small hand-verifiable directed graph)
  - 5 books      (3 monographs, 2 edited collections)
  - 8 chapters   (6 inside one collection, 2 inside the other)
  - 6 institutions

Article IDs always start at 1 (autoincrement on a fresh DB). The article-DOI
plan is documented inline so citation tests can hand-verify edges.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# Deterministic "today" used by the seed for fetched_at timestamps. The
# freeze_time fixture pins datetime.utcnow() to this exact instant so
# date-relative queries (get_new_articles, etc.) line up.
FROZEN_NOW = datetime(2026, 4, 29, 12, 0, 0)


# ── Journals (cover all four source modes) ────────────────────────────────────
J_CROSSREF = "College English"  # not gold-OA
J_RSS      = "Present Tense: A Journal of Rhetoric in Society"  # gold-OA
J_SCRAPE   = "Kairos: A Journal of Rhetoric, Technology, and Pedagogy"  # gold-OA
J_MANUAL   = "Pre/Text"  # not gold-OA


# ── Authors (20 distinct, plus a few intentional name variants) ───────────────
# The first name in each tuple is the canonical form stored in authors;
# extra strings are name variants used in articles.authors so that
# get_author_articles can be tested for partial-match round-tripping.
AUTHORS_CANONICAL = [
    ("Jane Smith",       "Smith Hall, U of Iowa",         "0000-0001-1111-1111"),
    ("John Adams",       "Smith Hall, U of Iowa",         "0000-0002-2222-2222"),
    ("Aisha Bell",       "Boston U",                      "0000-0003-3333-3333"),
    ("Carlos Diaz",      "Boston U",                      None),
    ("Emma Frost",       "Stanford U",                    "0000-0005-5555-5555"),
    ("George Hu",        "Stanford U",                    None),
    ("Hannah Iyer",      "MIT",                           "0000-0007-7777-7777"),
    ("Ian Johnson",      "MIT",                           None),
    ("Kira Lee",         "U of Michigan",                 "0000-0009-9999-9999"),
    ("Liam Moore",       "U of Michigan",                 None),
    ("Nora Park",        "U of Texas at Austin",          None),
    ("Omar Quinn",       "U of Texas at Austin",          None),
    ("Priya Rao",        None,                            None),
    ("Quentin Stone",    None,                            None),
    ("Ravi Tan",         None,                            None),
    ("Sara Underhill",   None,                            None),
    ("Tomas Vega",       None,                            None),
    ("Una Wells",        None,                            None),
    ("Victor Xu",        None,                            None),
    ("Wendy Young",      None,                            None),
]

# Six institutions match the first six author affiliations.
INSTITUTIONS = [
    ("https://openalex.org/I001", "https://ror.org/01a", "U of Iowa",        "US", "education"),
    ("https://openalex.org/I002", "https://ror.org/01b", "Boston U",         "US", "education"),
    ("https://openalex.org/I003", "https://ror.org/01c", "Stanford U",       "US", "education"),
    ("https://openalex.org/I004", "https://ror.org/01d", "MIT",              "US", "education"),
    ("https://openalex.org/I005", "https://ror.org/01e", "U of Michigan",    "US", "education"),
    ("https://openalex.org/I006", "https://ror.org/01f", "U of Texas at Austin", "US", "education"),
]


# ── Article plan (50 articles, deterministic) ────────────────────────────────
# Field order: (offset_in_journal, year, month, tag_suffix, citers, abstract_topic)
# The first article in each journal is set up to be a "highly cited" hub.
def _articles_plan():
    """Yield 50 article-row tuples in deterministic order, IDs 1..50."""
    plan: list[tuple] = []

    # 13 College English (CrossRef, mixed years 2020-2025, varied tags)
    plan += _journal_articles(
        journal=J_CROSSREF, source="crossref",
        ids_start=1,
        years=[2020, 2020, 2021, 2021, 2022, 2022, 2023, 2023, 2024, 2024, 2024, 2025, 2025],
        tags=["composition theory", "revision", "first-year composition", "assessment",
              "genre theory", "digital rhetoric", "composition theory", "revision",
              "race and writing", "assessment", "genre theory", "digital rhetoric",
              "composition theory"],
    )
    # 13 Present Tense (RSS, 2021-2025, gold OA)
    plan += _journal_articles(
        journal=J_RSS, source="rss",
        ids_start=14,
        years=[2021, 2021, 2022, 2022, 2022, 2023, 2023, 2024, 2024, 2024, 2025, 2025, 2025],
        tags=["digital rhetoric", "race and writing", "disability studies",
              "digital rhetoric", "multilingual writers", "first-year composition",
              "writing program administration", "revision", "genre theory",
              "race and writing", "digital rhetoric", "disability studies",
              "composition theory"],
    )
    # 12 Kairos (scrape, 2020-2025, gold OA)
    plan += _journal_articles(
        journal=J_SCRAPE, source="scrape",
        ids_start=27,
        years=[2020, 2020, 2021, 2022, 2022, 2023, 2023, 2024, 2024, 2025, 2025, 2025],
        tags=["digital rhetoric", "digital rhetoric", "multilingual writers",
              "digital rhetoric", "race and writing", "digital rhetoric",
              "genre theory", "digital rhetoric", "disability studies",
              "digital rhetoric", "first-year composition", "digital rhetoric"],
    )
    # 12 Pre/Text (manual, 1990s-2010s — give us older years for year-range tests)
    plan += _journal_articles(
        journal=J_MANUAL, source="manual",
        ids_start=39,
        years=[1990, 1992, 1995, 1998, 2000, 2003, 2005, 2008, 2010, 2012, 2014, 2016],
        tags=["composition theory", "genre theory", "composition theory",
              "revision", "composition theory", "first-year composition",
              "composition theory", "genre theory", "revision", "assessment",
              "composition theory", "genre theory"],
    )
    return plan


def _journal_articles(journal, source, ids_start, years, tags):
    """Generate article tuples for one journal, deterministically."""
    rows = []
    for i, (year, tag) in enumerate(zip(years, tags)):
        article_id = ids_start + i
        # Spread publication months 1..12 across articles in the same year.
        # Two articles in the same (year, journal) get distinct months.
        month = ((i * 7) % 12) + 1
        pub_date = f"{year}-{month:02d}-15"
        # Author assignments: rotate through AUTHORS_CANONICAL.
        # First author shifts each row; second author shifts by +5.
        primary = AUTHORS_CANONICAL[i % len(AUTHORS_CANONICAL)][0]
        secondary = AUTHORS_CANONICAL[(i + 5) % len(AUTHORS_CANONICAL)][0]
        authors = f"{primary}; {secondary}"
        # Title varies by index so FTS tests can find specific articles.
        title = f"{journal[:6]} essay {article_id}: on {tag}"
        # DOI uses article_id so tests can hand-verify which doi belongs where.
        doi = f"10.0000/test.{article_id:04d}"
        url = f"https://doi.org/{doi}"
        abstract = (f"This article examines {tag} as it relates to {journal}. "
                    f"It contributes to ongoing scholarly conversation. "
                    f"Article id is {article_id}.")
        keywords = f"{tag}; rhetoric; composition"
        # Tags column uses pipe-delimited form
        tag_str = f"|{tag}|composition|"
        rows.append((article_id, url, doi, title, authors, abstract, pub_date,
                     journal, source, keywords, tag_str))
    return rows


# ── Citation edges (30 total) ────────────────────────────────────────────────
# Edges are described as (source_article_id, target_article_id). Tests can
# hand-verify the resulting graph by referencing this list directly.
CITATION_EDGES = [
    # Article 1 is the most-cited "hub" overall — receives 8 cites (top)
    (5, 1), (8, 1), (12, 1), (16, 1), (22, 1), (27, 1), (33, 1), (40, 1),
    # Article 14 (Present Tense hub) — receives 6 hub cites + 1 misc = 7 total
    (3, 14), (10, 14), (20, 14), (25, 14), (30, 14), (35, 14),
    # Article 27 (Kairos hub) — receives 5 hub cites + 1 misc = 6 total
    (15, 27), (29, 27), (34, 27), (38, 27), (42, 27),
    # Article 39 (Pre/Text oldest) — receives 4 hub cites + 1 misc = 5 total
    (44, 39), (46, 39), (48, 39), (50, 39),
    # Article 2 — receives 3 cites
    (4, 2), (6, 2), (9, 2),
    # Misc edges to create cocitation pairs (article cites both 1 and another hub)
    (5, 14), (8, 27), (12, 39),
    # One extra edge to keep total at 30 without re-tying the top
    (45, 6),
]


# ── Books / chapters ─────────────────────────────────────────────────────────
BOOKS = [
    # (id, doi, isbn, title, record_type, book_type, parent_id, editors, authors,
    #  publisher, year, pages, abstract, subjects, cited_by, source)
    (1, "10.1000/book.1", "978-1-1111", "Composition Pedagogy: An Introduction",
     "book", "monograph", None, None, "Jane Smith",
     "WAC Clearinghouse", 2018, "240", "An introduction to composition pedagogy.",
     "composition;pedagogy", 12, "crossref"),
    (2, "10.1000/book.2", "978-2-2222", "Rhetoric and Public Memory",
     "book", "monograph", None, None, "Aisha Bell; Carlos Diaz",
     "Utah State UP", 2020, "300", "Public memory studies.",
     "rhetoric;memory", 7, "crossref"),
    (3, "10.1000/book.3", "978-3-3333", "Digital Writing Studies",
     "book", "monograph", None, None, "Emma Frost",
     "WAC Clearinghouse", 2022, "275", "Digital writing in the twenty-first century.",
     "digital;writing", 4, "crossref"),
    (4, "10.1000/book.4", "978-4-4444", "Handbook of Writing Center Studies",
     "book", "edited-collection", None, "Hannah Iyer; Ian Johnson", None,
     "Utah State UP", 2019, "420", "Edited handbook on writing center research.",
     "writing centers", 9, "crossref"),
    (5, "10.1000/book.5", "978-5-5555", "Multilingual Writing Pedagogies",
     "book", "edited-collection", None, "Kira Lee", None,
     "WAC Clearinghouse", 2021, "350", "Edited collection on L2 writing pedagogy.",
     "multilingual;pedagogy", 5, "crossref"),
]

CHAPTERS = [
    # 6 chapters in book 4 (the writing-centers handbook)
    (10, "10.1000/book.4.ch1", None, "Front matter",
     "front-matter", None, 4, None, None, "Utah State UP", 2019, "i-x", None, None, 0, "crossref"),
    (11, "10.1000/book.4.ch2", None, "Tutoring methodologies",
     "chapter", None, 4, None, "Hannah Iyer", "Utah State UP", 2019, "1-22",
     "Methodologies for one-on-one tutoring.", "tutoring", 2, "crossref"),
    (12, "10.1000/book.4.ch3", None, "Online writing centers",
     "chapter", None, 4, None, "Liam Moore", "Utah State UP", 2019, "23-44",
     "Online writing-center practice.", "online", 1, "crossref"),
    (13, "10.1000/book.4.ch4", None, "Multilingual tutees",
     "chapter", None, 4, None, "Nora Park", "Utah State UP", 2019, "45-66",
     "Working with multilingual writers.", "multilingual", 1, "crossref"),
    (14, "10.1000/book.4.ch5", None, "Writing center assessment",
     "chapter", None, 4, None, "Omar Quinn", "Utah State UP", 2019, "67-88",
     "Assessment in writing-center contexts.", "assessment", 0, "crossref"),
    (15, "10.1000/book.4.ch6", None, "Director burnout",
     "chapter", None, 4, None, "Priya Rao", "Utah State UP", 2019, "89-110",
     "Burnout in writing-center directors.", "burnout", 0, "crossref"),
    # 2 chapters in book 5
    (16, "10.1000/book.5.ch1", None, "L2 writing in FYC",
     "chapter", None, 5, None, "Quentin Stone", "WAC Clearinghouse", 2021, "1-25",
     "L2 students in first-year composition.", "L2;FYC", 1, "crossref"),
    (17, "10.1000/book.5.ch2", None, "Heritage learners",
     "chapter", None, 5, None, "Ravi Tan", "WAC Clearinghouse", 2021, "26-50",
     "Heritage-language learners and writing.", "heritage;writing", 0, "crossref"),
]


# ── Driver ───────────────────────────────────────────────────────────────────
def seed_database(db_path: Path | str) -> None:
    """Populate the test DB at *db_path* with deterministic content.

    Caller must have already called db.init_db() against this path so all
    tables exist. Safe to call only once per fresh DB.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        _seed_articles(conn)
        _seed_authors(conn)
        _seed_institutions(conn)
        _seed_affiliations(conn)
        _seed_citations(conn)
        _seed_books(conn)
        _recompute_citation_counts(conn)
        # Stamp some articles as having had their references fetched so
        # coverage tests are non-trivial.
        conn.execute(
            "UPDATE articles SET references_fetched_at = ? "
            "WHERE journal = ? AND id <= 5",
            (FROZEN_NOW.isoformat(sep=" "), J_CROSSREF),
        )
        conn.execute(
            "UPDATE articles SET crossref_cited_by_count = id * 2 "
            "WHERE references_fetched_at IS NOT NULL"
        )
        conn.commit()
    finally:
        conn.close()


def _seed_articles(conn) -> None:
    """Insert 50 articles. Set fetched_at deterministically: 5 most-recent
    articles get fetched_at within the last 7 days so get_new_articles works."""
    plan = _articles_plan()
    # Default fetched_at: 30 days ago — older than the 7-day "new" window.
    default_fetched = (FROZEN_NOW - timedelta(days=30)).isoformat(sep=" ")
    # The 5 most-recent articles by pub_date should be tagged as "new".
    # To make this hand-verifiable: sort plan by pub_date desc, take ids of top 5.
    by_pub = sorted(plan, key=lambda r: (r[6], r[0]), reverse=True)
    new_ids = {row[0] for row in by_pub[:5]}

    for row in plan:
        article_id, url, doi, title, authors, abstract, pub_date, \
            journal, source, keywords, tag_str = row
        if article_id in new_ids:
            fetched_at = (FROZEN_NOW - timedelta(days=2)).isoformat(sep=" ")
        else:
            fetched_at = default_fetched
        # OA status: gold for J_RSS / J_SCRAPE journals.
        oa_status = "gold" if journal in (J_RSS, J_SCRAPE) else None
        oa_url = url if oa_status == "gold" else None
        conn.execute("""
            INSERT INTO articles
                (id, url, doi, title, authors, abstract, pub_date, journal,
                 source, keywords, tags, fetched_at, oa_status, oa_url)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (article_id, url, doi, title, authors, abstract, pub_date,
              journal, source, keywords, tag_str, fetched_at, oa_status, oa_url))


def _seed_authors(conn) -> None:
    for name, inst, orcid in AUTHORS_CANONICAL:
        conn.execute(
            "INSERT INTO authors (name, institution_name, orcid) VALUES (?, ?, ?)",
            (name, inst, orcid),
        )


def _seed_institutions(conn) -> None:
    for openalex_id, ror_id, display_name, country, type_ in INSTITUTIONS:
        conn.execute(
            "INSERT INTO institutions (openalex_id, ror_id, display_name, country_code, type) "
            "VALUES (?, ?, ?, ?, ?)",
            (openalex_id, ror_id, display_name, country, type_),
        )


def _seed_affiliations(conn) -> None:
    """Tag the first author of each of the first 30 articles to an institution.

    Goal: get_top_institutions returns predictable counts; one institution
    dominates so we have a stable head."""
    plan = _articles_plan()
    inst_names = [
        "U of Iowa", "Boston U", "Stanford U", "MIT", "U of Michigan",
        "U of Texas at Austin",
    ]
    inst_id_by_name = {
        row["display_name"]: row["id"]
        for row in conn.execute("SELECT id, display_name FROM institutions").fetchall()
    }

    for i, row in enumerate(plan[:30]):
        article_id = row[0]
        authors = row[4].split(";")
        first = authors[0].strip()
        # Distribute affiliations skewed: U of Iowa gets 10, Boston gets 6, etc.
        inst_idx = min(i // 5, len(inst_names) - 1)
        inst_name = inst_names[inst_idx]
        inst_id = inst_id_by_name.get(inst_name)
        conn.execute(
            "INSERT INTO author_article_affiliations "
            "(article_id, author_name, institution_name) VALUES (?, ?, ?)",
            (article_id, first, inst_name),
        )
        conn.execute(
            "INSERT INTO article_author_institutions "
            "(article_id, author_name, institution_id, author_position) "
            "VALUES (?, ?, ?, ?)",
            (article_id, first, inst_id, "first"),
        )


def _seed_citations(conn) -> None:
    """Insert the 30 directed citation edges. target_article_id is set so
    update_citation_counts produces internal counts."""
    # Build a doi-by-id lookup to populate target_doi from the article plan.
    doi_by_id = {
        row["id"]: row["doi"]
        for row in conn.execute("SELECT id, doi FROM articles").fetchall()
    }
    for src, tgt in CITATION_EDGES:
        conn.execute(
            "INSERT INTO citations "
            "(source_article_id, target_doi, target_article_id) "
            "VALUES (?, ?, ?)",
            (src, doi_by_id[tgt], tgt),
        )


def _seed_books(conn) -> None:
    for row in BOOKS + CHAPTERS:
        conn.execute("""
            INSERT INTO books
                (id, doi, isbn, title, record_type, book_type, parent_id,
                 editors, authors, publisher, year, pages, abstract,
                 subjects, cited_by, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, row)


def _recompute_citation_counts(conn) -> None:
    """Mirror db.update_citation_counts so internal_cited_by_count and
    internal_cites_count are populated for tests that rely on them."""
    conn.execute("""
        UPDATE articles
        SET internal_cited_by_count = (
            SELECT COUNT(*) FROM citations
            WHERE target_article_id = articles.id
        )
    """)
    conn.execute("""
        UPDATE articles
        SET internal_cites_count = (
            SELECT COUNT(*) FROM citations
            WHERE source_article_id = articles.id
              AND target_article_id IS NOT NULL
        )
    """)


# ── Hand-verification helpers (for tests) ────────────────────────────────────
def expected_total_articles() -> int:
    return 50


def expected_articles_by_journal() -> dict[str, int]:
    return {J_CROSSREF: 13, J_RSS: 13, J_SCRAPE: 12, J_MANUAL: 12}


def expected_year_range() -> tuple[int, int]:
    return (1990, 2025)


def expected_top_cited_id() -> int:
    """The hub article — receives 8 citations (article id 1)."""
    return 1


def expected_top_cited_count() -> int:
    return 8


def expected_citation_edge_count() -> int:
    return 30


def expected_journal_names() -> list[str]:
    return [J_CROSSREF, J_RSS, J_SCRAPE, J_MANUAL]
