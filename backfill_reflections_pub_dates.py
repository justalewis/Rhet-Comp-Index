"""
backfill_reflections_pub_dates.py — Patch bogus Reflections pub_dates.

Penn State Libraries' May-2026 CrossRef deposit for Reflections (ISSN 1541-2075)
stamped many articles with `issued = 2025-08-XX` — the DOI registration date,
not the original publication date. Our fetcher trusted CrossRef's `issued`
field, so those articles landed in the index with pub_date = 2025-08-XX.

The PSU OJS site exposes an OAI-PMH feed at /reflections/oai whose `<dc:date>`
field carries the *original* publication date (e.g. 2004-12-01), and whose
`<dc:identifier>` includes the DOI. We walk the feed, build a DOI -> date
map, and update affected rows by DOI match — far more reliable than the
earlier title-based archive-page heuristic, which missed special-issue
headings that don't render as <h2>/<h3>.

Usage:
    python backfill_reflections_pub_dates.py --dry-run
    python backfill_reflections_pub_dates.py
"""

import argparse
import logging
import time
import xml.etree.ElementTree as ET

from curl_cffi import requests as curl_requests

from db import get_conn, init_db

JOURNAL = "Reflections: A Journal of Community-Engaged Writing and Rhetoric"
OAI_BASE = "https://journals.psu.edu/reflections/oai"

# PSU's WAF rejects Python's default TLS fingerprint, so a plain UA header
# isn't enough — curl_cffi's Chrome impersonation is required.
IMPERSONATE = "chrome120"

OAI_NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "oai_dc": "http://www.openarchives.org/OAI/2.0/oai_dc/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _harvest_oai_dates() -> dict[str, str]:
    """Walk the Reflections OAI-PMH feed, return DOI -> dc:date map.

    A record's identifiers include both the article URL and the DOI; we
    pick whichever starts with "10." as the DOI. Records missing either
    a date or a DOI are skipped.
    """
    out: dict[str, str] = {}
    params = {"verb": "ListRecords", "metadataPrefix": "oai_dc"}
    page = 0
    while True:
        # PSU's WAF gets prickly with rapid OAI calls; one retry after a
        # backoff handles the occasional connection reset.
        resp = None
        for attempt in (1, 2, 3):
            try:
                resp = curl_requests.get(OAI_BASE, params=params,
                                         impersonate=IMPERSONATE, timeout=60)
                resp.raise_for_status()
                break
            except Exception as e:
                log.warning("OAI fetch attempt %d failed: %s", attempt, e)
                time.sleep(5 * attempt)
        if resp is None:
            log.error("OAI page %d giving up after retries.", page + 1)
            return out

        root = ET.fromstring(resp.content)
        for record in root.findall(".//oai:record", OAI_NS):
            md = record.find(".//oai_dc:dc", OAI_NS)
            if md is None:
                continue
            date_el = md.find("dc:date", OAI_NS)
            if date_el is None or not (date_el.text or "").strip():
                continue
            date = date_el.text.strip()[:10]

            doi = None
            for ident in md.findall("dc:identifier", OAI_NS):
                txt = (ident.text or "").strip()
                if txt.startswith("10."):
                    doi = txt.lower()
                    break
            if not doi:
                continue
            out[doi] = date

        page += 1
        token_el = root.find(".//oai:resumptionToken", OAI_NS)
        token = (token_el.text or "").strip() if token_el is not None else ""
        log.info("  OAI page %d  |  records collected: %d  |  token: %s",
                 page, len(out), token or "(end)")
        if not token:
            break
        params = {"verb": "ListRecords", "resumptionToken": token}
        time.sleep(3)

    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing.")
    args = parser.parse_args()

    init_db()

    log.info("Walking Reflections OAI-PMH feed...")
    doi_dates = _harvest_oai_dates()
    log.info("OAI records with DOI + date: %d", len(doi_dates))
    if not doi_dates:
        log.error("No OAI records — aborting.")
        return

    with get_conn() as conn:
        conn.execute("PRAGMA busy_timeout = 60000")
        # Update *every* Reflections row whose OAI date differs — not just
        # ones with the "2025-08" placeholder. The earlier title-based
        # backfill landed many rows on coarse season-derived dates like
        # "2004-01"; OAI gives us the precise "2004-12-01" and is the
        # authority for this journal.
        rows = conn.execute(
            "SELECT id, doi, title, pub_date FROM articles "
            "WHERE journal = ? AND doi IS NOT NULL",
            (JOURNAL,),
        ).fetchall()
        log.info("Candidate rows (all Reflections with DOI): %d", len(rows))

        updates: list[tuple[str, int]] = []
        unmatched: list[tuple[int, str, str]] = []
        for r in rows:
            doi = (r["doi"] or "").strip().lower()
            new_date = doi_dates.get(doi)
            if new_date and new_date != r["pub_date"]:
                updates.append((new_date, r["id"]))
            elif not new_date:
                unmatched.append((r["id"], r["doi"], r["title"]))

        log.info("Will update: %d   Unmatched: %d", len(updates), len(unmatched))
        if unmatched:
            log.info("First 10 unmatched (no OAI record for DOI):")
            for aid, doi, t in unmatched[:10]:
                log.info("  #%d  doi=%s  %s", aid, doi, t)

        if args.dry_run:
            log.info("Dry run — no writes performed.")
            if updates[:5]:
                log.info("Sample updates (first 5):")
                for d, i in updates[:5]:
                    log.info("  #%d -> %s", i, d)
            return

        conn.executemany(
            "UPDATE articles SET pub_date = ? WHERE id = ?", updates,
        )
        conn.commit()
        log.info("Done — %d rows updated.", len(updates))


if __name__ == "__main__":
    main()
