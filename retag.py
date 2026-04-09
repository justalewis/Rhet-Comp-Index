"""
retag.py — One-time script to tag all existing articles and rebuild the FTS index.

Run this once after upgrading to v3 of the database schema:
    python retag.py

Safe to run multiple times — it overwrites existing tags and rebuilds FTS each time.
"""

import sqlite3
import os
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
log = logging.getLogger(__name__)

from db import get_conn, init_db
from tagger import auto_tag


def retag_all():
    init_db()

    with get_conn() as conn:
        conn.execute("PRAGMA busy_timeout = 60000")

        rows = conn.execute(
            "SELECT id, title, abstract FROM articles"
        ).fetchall()

        log.info("Tagging %d articles…", len(rows))

        tagged = 0
        for i, r in enumerate(rows):
            t = auto_tag(r["title"], r["abstract"])
            conn.execute(
                "UPDATE articles SET tags = ? WHERE id = ?",
                (t, r["id"])
            )
            if t:
                tagged += 1
            if (i + 1) % 500 == 0:
                conn.commit()

        conn.commit()
        log.info("Tagged %d of %d articles.", tagged, len(rows))

        log.info("Rebuilding FTS index…")
        conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
        conn.commit()
        log.info("Done. FTS index is up to date.")


if __name__ == "__main__":
    retag_all()
