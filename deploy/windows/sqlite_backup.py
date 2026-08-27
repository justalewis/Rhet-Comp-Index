"""sqlite_backup.py — safe hot copy of the Pinakes SQLite database.

    python sqlite_backup.py <source.db> <destination.db>

Uses SQLite's online backup API rather than a file copy. The app is a
long-running writer against the same file, and copying a live SQLite database
with Copy-Item / robocopy can capture a torn page mid-transaction: the copy
looks fine until the day you try to restore it. The backup API takes a
consistent snapshot while the writer keeps working.

Called by Backup-PinakesDatabase in Pinakes.Common.ps1. Stands alone, so it can
also be run by hand. Requires only the standard library, so the venv Python is
enough — no dependency on sqlite3.exe being installed on the server.

Exit codes: 0 success, 1 usage/IO error, 2 SQLite error.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time


def human_mb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 1

    src_path, dst_path = argv[1], argv[2]

    if not os.path.exists(src_path):
        print(f"ERROR: source database does not exist: {src_path}", file=sys.stderr)
        return 1

    dst_dir = os.path.dirname(os.path.abspath(dst_path))
    os.makedirs(dst_dir, exist_ok=True)

    started = time.time()
    print(f"Backing up {src_path} -> {dst_path}")

    try:
        # A read-only source connection cannot be the reason a write fails, and
        # a generous timeout rides out the app's own write transactions instead
        # of erroring out with "database is locked" the moment they overlap.
        src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=60)
        dst = sqlite3.connect(dst_path)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
            src.close()
    except sqlite3.Error as exc:
        print(f"ERROR: SQLite backup failed: {exc}", file=sys.stderr)
        # Never leave a half-written file behind to be mistaken for a backup.
        if os.path.exists(dst_path):
            try:
                os.remove(dst_path)
                print(f"Removed incomplete backup {dst_path}", file=sys.stderr)
            except OSError:
                pass
        return 2

    # Verify the copy actually opens and passes a quick structural check. A
    # backup nobody has ever read is a guess, not a backup.
    try:
        check = sqlite3.connect(dst_path)
        try:
            result = check.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            check.close()
    except sqlite3.Error as exc:
        print(f"ERROR: backup written but unreadable: {exc}", file=sys.stderr)
        return 2

    if result != "ok":
        print(f"ERROR: backup failed quick_check: {result}", file=sys.stderr)
        return 2

    size = os.path.getsize(dst_path)
    print(f"OK: {human_mb(size)} in {time.time() - started:.1f}s, quick_check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
