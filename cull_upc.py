"""
cull_upc.py — Remove non-rhetoric/composition books from the UP Colorado
              (Utah State University Press) records in the books table.

Keeps any book whose title contains at least one rhet/comp keyword.
Deletes the book record AND all child chapter/front-matter records.

Usage:
    python cull_upc.py          # dry-run: print what would be deleted
    python cull_upc.py --delete # actually delete
"""

import re
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from db import init_db, get_conn

# ── Keywords that mark a book as rhet/comp ──────────────────────────────────────
# Each term is a prefix or exact-word pattern — no trailing \b so stems work.
_KEEP_TERMS = [
    r"\bwrit",              # writing, writer, writers, written, writes, write, rewriting
    r"\brhetor",            # rhetoric, rhetorics, rhetorical, rhetorician
    r"\bcomposit",          # composition, compositional
    r"\bcomposing\b",       # composing (separate so "compost" isn't caught)
    r"\bliterac",           # literacy, literacies
    r"\bliterate\b",        # literate
    r"\bmultilingual",      # multilingual, multilingualism
    r"\btranslingual",      # translingual, translingualism
    r"\bmultimodal",        # multimodal, multimodality
    r"\bpedagog",           # pedagogy, pedagogical, pedagogics
    r"\btutor",             # tutor, tutoring, tutors
    r"\bdiscourse",         # discourse, discourses
    r"\bWAC\b",             # writing across the curriculum
    r"\bWAW\b",             # writing about writing
    r"\bWPA\b",             # writing program administration
    r"first.year",          # first-year / first year
    r"technical communication",
    r"\bauthorship\b",      # authorship studies (rhet/comp topic)
    r"\bplagiarism\b",      # plagiarism (rhet/comp topic)
]
KEEP_PATTERN = re.compile("|".join(_KEEP_TERMS), re.IGNORECASE)

# Publisher label as stored in DB
UPC_LABEL = "Utah State University Press"

def is_rhet_comp(title: str) -> bool:
    return bool(KEEP_PATTERN.search(title or ""))


def run(dry_run: bool = True):
    init_db()
    with get_conn() as conn:
        # Get all book-level records for USU Press
        books = conn.execute(
            "SELECT id, title, year FROM books WHERE publisher = ? AND record_type = 'book'",
            (UPC_LABEL,)
        ).fetchall()

        keep_ids   = []
        delete_ids = []

        for b in books:
            if is_rhet_comp(b["title"]):
                keep_ids.append(b["id"])
            else:
                delete_ids.append(b["id"])

        print(f"Utah State University Press books in DB: {len(books)}")
        print(f"  Keep  (rhet/comp): {len(keep_ids)}")
        print(f"  Delete (off-topic): {len(delete_ids)}")
        print()

        # Count chapters that would be deleted
        if delete_ids:
            placeholders = ",".join("?" * len(delete_ids))
            ch_count = conn.execute(
                f"SELECT COUNT(*) FROM books WHERE parent_id IN ({placeholders})",
                delete_ids,
            ).fetchone()[0]

            print(f"  Child records (chapters/front-matter) to delete: {ch_count}")
            print()

            print("=== Books to DELETE ===")
            for bid in delete_ids:
                row = next(b for b in books if b["id"] == bid)
                print(f"  [{row['year']}] {row['title']}")

            print()
            print("=== Books to KEEP ===")
            for bid in keep_ids:
                row = next(b for b in books if b["id"] == bid)
                print(f"  [{row['year']}] {row['title']}")

        if not dry_run:
            if delete_ids:
                placeholders = ",".join("?" * len(delete_ids))
                # Delete children first (chapters, front-matter)
                conn.execute(
                    f"DELETE FROM books WHERE parent_id IN ({placeholders})",
                    delete_ids,
                )
                # Delete the books themselves
                conn.execute(
                    f"DELETE FROM books WHERE id IN ({placeholders})",
                    delete_ids,
                )
                conn.commit()
                print(f"\nDeleted {len(delete_ids)} books and {ch_count} child records.")
            else:
                print("Nothing to delete.")
        else:
            print("\n[DRY RUN — pass --delete to apply]")


if __name__ == "__main__":
    dry = "--delete" not in sys.argv
    run(dry_run=dry)
