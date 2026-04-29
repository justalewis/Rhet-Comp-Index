"""db.fetch_log — Fetch-log timestamps for incremental ingestion."""

import json
import os
import sqlite3
import logging
from itertools import combinations

from .core import get_conn

log = logging.getLogger(__name__)


def update_fetch_log(journal, last_pub_date=None):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO fetch_log (journal, last_fetched, last_pub_date)
            VALUES (?, datetime('now'), ?)
        """, (journal, last_pub_date))
        conn.commit()


def get_last_fetch(journal):
    """Return ISO datetime string of last fetch for this journal, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_fetched FROM fetch_log WHERE journal = ?", (journal,)
        ).fetchone()
        return row["last_fetched"] if row else None
