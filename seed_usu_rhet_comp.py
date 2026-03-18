"""
seed_usu_rhet_comp.py
---------------------
One-time seeder for the six USU Press Rhetoric & Composition titles hosted at
https://digitalcommons.usu.edu/usupress_composition/

These books predate CrossRef DOI registration, so they are inserted manually
from Digital Commons metadata. The `doi` field is repurposed to store the
Digital Commons URL so the "Open Access Full Text" link still works on the
book detail page.

Usage:
    python seed_usu_rhet_comp.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from db import upsert_book

# Each entry uses the Digital Commons permalink as the `doi` so the
# book.html template renders an "Open Access Full Text ↗" link.
BOOKS = [
    {
        "doi":       "https://digitalcommons.usu.edu/usupress_pubs/30",
        "isbn":      "978-0-87421-434-5",
        "title":     "Noise from the Writing Center",
        "book_type": "monograph",
        "authors":   "Elizabeth H. Boquet",
        "editors":   None,
        "publisher": "Utah State University Press",
        "year":      2002,
        "abstract":  None,
        "subjects":  "Writing Centers; Rhetoric and Composition",
        "source":    "digitalcommons",
    },
    {
        "doi":       "https://digitalcommons.usu.edu/usupress_pubs/25",
        "isbn":      "978-0-87421-701-8",
        "title":     "Literacy, Sexuality, Pedagogy: Theory and Practice for Composition Studies",
        "book_type": "monograph",
        "authors":   "Jonathan Alexander",
        "editors":   None,
        "publisher": "Utah State University Press",
        "year":      2008,
        "abstract":  None,
        "subjects":  "Rhetoric and Composition; Sexuality Studies; Pedagogy",
        "source":    "digitalcommons",
    },
    {
        "doi":       "https://digitalcommons.usu.edu/usupress_pubs/29",
        "isbn":      "978-0-87421-699-8",
        "title":     "The Activist WPA: Changing Stories about Writing and Writers",
        "book_type": "monograph",
        "authors":   "Linda Adler-Kassner",
        "editors":   None,
        "publisher": "Utah State University Press",
        "year":      2008,
        "abstract":  None,
        "subjects":  "Writing Program Administration; Rhetoric and Composition",
        "source":    "digitalcommons",
    },
    {
        "doi":       "https://digitalcommons.usu.edu/usupress_pubs/26",
        "isbn":      "978-0-87421-728-5",
        "title":     "Who Owns This Text?: Plagiarism, Authorship, and Disciplinary Cultures",
        "book_type": "edited-collection",
        "authors":   None,
        "editors":   "Carol Petersen Haviland; Joan A. Mullin",
        "publisher": "Utah State University Press",
        "year":      2009,
        "abstract":  None,
        "subjects":  "Plagiarism; Authorship; Rhetoric and Composition",
        "source":    "digitalcommons",
    },
    {
        "doi":       "https://digitalcommons.usu.edu/usupress_pubs/27",
        "isbn":      "978-0-87421-763-6",
        "title":     "What We Are Becoming: Developments in Undergraduate Writing Majors",
        "book_type": "edited-collection",
        "authors":   None,
        "editors":   "Greg A. Giberson; Thomas A. Moriarty",
        "publisher": "Utah State University Press",
        "year":      2010,
        "abstract":  None,
        "subjects":  "Writing Majors; Rhetoric and Composition; Curriculum",
        "source":    "digitalcommons",
    },
    {
        "doi":       "https://digitalcommons.usu.edu/usupress_pubs/28",
        "isbn":      "978-0-87421-769-8",
        "title":     "Going Public: What Writing Programs Learn from Engagement",
        "book_type": "edited-collection",
        "authors":   None,
        "editors":   "Shirley K. Rose; Irwin Weiser",
        "publisher": "Utah State University Press",
        "year":      2010,
        "abstract":  None,
        "subjects":  "Writing Program Administration; Community Engagement; Rhetoric and Composition",
        "source":    "digitalcommons",
    },
]


def main():
    inserted = 0
    skipped  = 0
    for b in BOOKS:
        book_id, is_new = upsert_book(
            doi        = b["doi"],
            isbn       = b["isbn"],
            title      = b["title"],
            record_type= "book",
            book_type  = b["book_type"],
            parent_id  = None,
            editors    = b["editors"],
            authors    = b["authors"],
            publisher  = b["publisher"],
            year       = b["year"],
            pages      = None,
            abstract   = b["abstract"],
            subjects   = b["subjects"],
            cited_by   = 0,
            source     = b["source"],
        )
        if is_new:
            print(f"  INSERTED  [{book_id}] {b['title'][:70]}")
            inserted += 1
        else:
            print(f"  skipped   [{book_id}] {b['title'][:70]}")
            skipped += 1

    print(f"\nDone — {inserted} inserted, {skipped} already present.")


if __name__ == "__main__":
    main()
