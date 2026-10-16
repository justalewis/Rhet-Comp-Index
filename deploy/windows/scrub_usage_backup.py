"""Remove raw IP addresses and visitor-id salts from a COPY of usage.db.

The nightly backup runs at 02:00 and the resolver at 05:10, so the live
database still holds that day's unresolved addresses and the current salts
when it is copied. Kept for ten nights, the backups would break two promises
the site makes: addresses are deleted within 48 hours, and no visitor can be
followed from one day to the next. This runs on the copy only, never on the
live file: it nulls every address, drops the salts, and VACUUMs so the
deleted bytes are not left in free pages.

Usage: python scrub_usage_backup.py <backup.db>
"""

import sqlite3
import sys


def scrub(path: str) -> tuple[int, int]:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA secure_delete = ON")
        with conn:
            ips = conn.execute("UPDATE hits SET ip = NULL WHERE ip IS NOT NULL").rowcount
            salts = conn.execute("DELETE FROM salts").rowcount
        conn.execute("VACUUM")
        return ips, salts
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    try:
        ips, salts = scrub(argv[1])
    except sqlite3.Error as e:
        print(f"Could not scrub {argv[1]}: {e}", file=sys.stderr)
        return 1
    print(f"Scrubbed {argv[1]}: {ips} addresses cleared, {salts} salts dropped.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
