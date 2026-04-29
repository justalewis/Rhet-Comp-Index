"""db.books — Books, chapters, publishers, book-publisher fetch log."""

import json
import os
import sqlite3
import logging
from itertools import combinations

from .core import get_conn

log = logging.getLogger(__name__)


def upsert_book(doi, isbn, title, record_type, book_type,
                editors, authors, publisher, year,
                pages=None, abstract=None, subjects=None,
                cited_by=0, parent_id=None, source="crossref"):
    """
    Insert or update a books-table record (book, chapter, or front-matter).

    Returns (book_id, is_new).

    For books (record_type='book'):  doi is the primary dedup key.
    For chapters (record_type='chapter'/'front-matter'):  doi is also used when
    available; otherwise uniqueness falls through to the UNIQUE(doi) constraint
    with None, which SQLite allows for multiple NULL values.
    """
    with get_conn() as conn:
        # Update-if-exists by DOI
        if doi:
            row = conn.execute(
                "SELECT id FROM books WHERE doi = ?", (doi,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE books SET cited_by=?, fetched_at=datetime('now') WHERE id=?",
                    (cited_by or 0, row["id"])
                )
                conn.commit()
                return row["id"], False

        conn.execute("""
            INSERT OR IGNORE INTO books
                (doi, isbn, title, record_type, book_type, parent_id,
                 editors, authors, publisher, year, pages,
                 abstract, subjects, cited_by, source)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (doi, isbn, title, record_type, book_type, parent_id,
              editors, authors, publisher, year, pages,
              abstract, subjects, cited_by or 0, source))
        conn.commit()
        changes = conn.execute("SELECT changes()").fetchone()[0]
        if changes:
            book_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            return book_id, True

        # No DOI and INSERT was ignored — try title+year dedup fallback
        if title and year:
            row = conn.execute(
                "SELECT id FROM books WHERE title = ? AND year = ? AND record_type = ?",
                (title, year, record_type)
            ).fetchone()
            if row:
                return row["id"], False

        return None, False


def get_books(publisher=None, book_type=None, year_from=None, year_to=None,
              q=None, limit=50, offset=0):
    """Return book-level records (record_type='book') with optional filters."""
    where = ["record_type = 'book'"]
    params = []

    if publisher:
        where.append("publisher = ?")
        params.append(publisher)
    if book_type:
        where.append("book_type = ?")
        params.append(book_type)
    if year_from:
        try:
            where.append("year >= ?")
            params.append(int(year_from))
        except (ValueError, TypeError):
            pass
    if year_to:
        try:
            where.append("year <= ?")
            params.append(int(year_to))
        except (ValueError, TypeError):
            pass
    if q:
        like = f"%{q}%"
        where.append("(title LIKE ? OR editors LIKE ? OR authors LIKE ?)")
        params.extend([like, like, like])

    clause = "WHERE " + " AND ".join(where)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM books {clause} "
            f"ORDER BY year DESC, title ASC "
            f"LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
        return [dict(r) for r in rows]


def get_book_count(publisher=None, book_type=None, year_from=None, year_to=None, q=None):
    """Return total count of book-level records matching the given filters."""
    where = ["record_type = 'book'"]
    params = []

    if publisher:
        where.append("publisher = ?")
        params.append(publisher)
    if book_type:
        where.append("book_type = ?")
        params.append(book_type)
    if year_from:
        try:
            where.append("year >= ?")
            params.append(int(year_from))
        except (ValueError, TypeError):
            pass
    if year_to:
        try:
            where.append("year <= ?")
            params.append(int(year_to))
        except (ValueError, TypeError):
            pass
    if q:
        like = f"%{q}%"
        where.append("(title LIKE ? OR editors LIKE ? OR authors LIKE ?)")
        params.extend([like, like, like])

    clause = "WHERE " + " AND ".join(where)
    with get_conn() as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM books {clause}", params
        ).fetchone()[0]


def get_book_by_id(book_id):
    """Return a single books row by primary key, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM books WHERE id = ?", (book_id,)
        ).fetchone()
        return dict(row) if row else None


def get_book_by_doi(doi):
    """Return a single books row by DOI, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM books WHERE doi = ?", (doi,)
        ).fetchone()
        return dict(row) if row else None


def get_book_chapters(book_id):
    """Return all chapter/front-matter rows for a given parent book id,
    ordered by DOI suffix (which preserves chapter number order for WAC)
    then by pages."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM books
               WHERE parent_id = ?
               ORDER BY
                 CASE record_type WHEN 'front-matter' THEN 0 ELSE 1 END,
                 doi ASC,
                 pages ASC""",
            (book_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_book_publishers():
    """Return list of (publisher, book_count, chapter_count) for the sidebar/filter."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT publisher,
                   SUM(CASE WHEN record_type='book' THEN 1 ELSE 0 END) AS book_count,
                   SUM(CASE WHEN record_type IN ('chapter','front-matter') THEN 1 ELSE 0 END) AS chapter_count
            FROM books
            WHERE publisher IS NOT NULL
            GROUP BY publisher
            ORDER BY book_count DESC
        """).fetchall()
        return [dict(r) for r in rows]


def get_books_fetch_log(publisher):
    """Return last-fetched datetime for a publisher's book harvest, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_fetched FROM fetch_log WHERE journal = ?",
            (f"books:{publisher}",)
        ).fetchone()
        return row["last_fetched"] if row else None


def update_books_fetch_log(publisher):
    """Record that we just completed a book harvest for this publisher."""
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO fetch_log (journal, last_fetched)
            VALUES (?, datetime('now'))
        """, (f"books:{publisher}",))
        conn.commit()
