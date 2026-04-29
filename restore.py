"""restore.py — Restore a Pinakes backup from off-machine storage.

Run by an operator after a disaster. Reads the same env vars as
backup.py for S3 access; reads the age private key from a file path
the operator provides on the command line (NEVER from env on Fly).

Usage:
    python restore.py --list
    python restore.py --latest --out ./restored.db --age-key ~/.pinakes/age.key
    python restore.py --date 2026-04-29 --out ./restored.db --age-key ~/.pinakes/age.key
    python restore.py --verify ./restored.db
"""

from __future__ import annotations

import argparse
import datetime
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import zstandard

import backup


def _format_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def cmd_list(args) -> int:
    missing = backup._missing_env()
    if missing:
        print(f"ERROR: missing env vars: {missing}", file=sys.stderr)
        return 2

    s3 = backup._make_s3_client()
    items = backup.list_backups(os.environ[backup.ENV_BUCKET], s3_client=s3)
    items = [
        (it, backup._parse_backup_timestamp(it["key"]))
        for it in items
    ]
    items = [(it, dt) for it, dt in items if dt is not None]
    items.sort(key=lambda pair: pair[1], reverse=True)

    if not items:
        print("No backups in bucket.")
        return 0

    print(f"{'Date (UTC)':<22} {'Size':>10}  Key")
    print("-" * 80)
    for it, dt in items:
        print(f"{dt.isoformat():<22} {_format_size(it['size']):>10}  {it['key']}")
    print(f"\n{len(items)} backup(s) total.")
    return 0


def _select_key(s3, mode: str, date_str: str | None) -> str | None:
    items = backup.list_backups(os.environ[backup.ENV_BUCKET], s3_client=s3)
    with_ts = [
        (it, backup._parse_backup_timestamp(it["key"]))
        for it in items
    ]
    with_ts = [(it, dt) for it, dt in with_ts if dt is not None]

    if not with_ts:
        return None

    if mode == "latest":
        latest, _ = max(with_ts, key=lambda pair: pair[1])
        return latest["key"]

    # mode == "date"
    target = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    matching = [(it, dt) for it, dt in with_ts if dt.date() == target]
    if not matching:
        return None
    # Most recent backup on that day
    latest, _ = max(matching, key=lambda pair: pair[1])
    return latest["key"]


def cmd_restore(args) -> int:
    missing = backup._missing_env()
    if missing:
        print(f"ERROR: missing env vars: {missing}", file=sys.stderr)
        return 2

    out_path = Path(args.out).expanduser().resolve()
    if out_path.exists() and not args.force:
        print(f"ERROR: {out_path} exists. Pass --force to overwrite.", file=sys.stderr)
        return 2

    age_key_path = Path(args.age_key).expanduser()
    if not age_key_path.is_file():
        print(f"ERROR: age key file not found: {age_key_path}", file=sys.stderr)
        return 2
    age_private_key = age_key_path.read_text(encoding="utf-8").strip()

    s3 = backup._make_s3_client()
    mode = "latest" if args.latest else "date"
    key = _select_key(s3, mode, args.date)
    if key is None:
        if mode == "date":
            print(f"ERROR: no backup found for {args.date}", file=sys.stderr)
        else:
            print("ERROR: no backups in bucket", file=sys.stderr)
        return 2

    # Look up size for the friendly print
    items = backup.list_backups(os.environ[backup.ENV_BUCKET], s3_client=s3)
    size = next((it["size"] for it in items if it["key"] == key), 0)

    print(f"Downloading {key} ({_format_size(size)})...")
    with tempfile.TemporaryDirectory(prefix="pinakes-restore-") as tmpdir:
        tmpdir_p = Path(tmpdir)
        enc_path = tmpdir_p / "backup.db.zst.age"
        s3.download_file(os.environ[backup.ENV_BUCKET], key, str(enc_path))

        print("Decrypting...")
        import pyrage
        ident = pyrage.x25519.Identity.from_str(age_private_key)
        zst_path = tmpdir_p / "backup.db.zst"
        zst_path.write_bytes(pyrage.decrypt(enc_path.read_bytes(), [ident]))

        print("Decompressing...")
        dctx = zstandard.ZstdDecompressor()
        with open(zst_path, "rb") as fin, open(out_path, "wb") as fout:
            dctx.copy_stream(fin, fout)

    print(f"Running PRAGMA integrity_check on {out_path}...")
    with sqlite3.connect(str(out_path)) as conn:
        rows = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    if rows == ["ok"]:
        print(f"Integrity OK. Restored to {out_path} ({_format_size(out_path.stat().st_size)}).")
    else:
        print(f"WARNING: integrity check failed: {rows}", file=sys.stderr)
        return 1

    print()
    print("Next steps to put this back into production:")
    print(f"  1. Stop Fly app:    flyctl scale count app=0 scheduler=0")
    print(f"  2. Upload to Fly:   flyctl ssh sftp shell")
    print(f"                      put {out_path} /data/articles.db")
    print(f"  3. Restart:         flyctl scale count app=1 scheduler=1")
    print(f"  4. Smoke check:     curl https://pinakes.xyz/health/ready")
    return 0


def cmd_verify(args) -> int:
    db_path = Path(args.verify).expanduser().resolve()
    if not db_path.is_file():
        print(f"ERROR: {db_path} not found", file=sys.stderr)
        return 2

    print(f"Running PRAGMA integrity_check on {db_path}...")
    with sqlite3.connect(str(db_path)) as conn:
        rows = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
        article_count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    if rows == ["ok"]:
        print(f"Integrity OK. {article_count} articles.")
        return 0
    print(f"FAIL: {rows}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="restore.py",
        description="Restore a Pinakes backup from off-machine storage.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true",
                       help="List available backups in the bucket.")
    group.add_argument("--latest", action="store_true",
                       help="Restore the most recent backup.")
    group.add_argument("--date", metavar="YYYY-MM-DD",
                       help="Restore the backup taken on this date.")
    group.add_argument("--verify", metavar="DB_FILE",
                       help="Run PRAGMA integrity_check on a local SQLite file.")

    parser.add_argument("--out", help="Output path for restored DB (required with --latest/--date).")
    parser.add_argument("--age-key", help="Path to the age private key file (required with --latest/--date).")
    parser.add_argument("--force", action="store_true", help="Overwrite --out if it exists.")

    args = parser.parse_args(argv)

    if args.list:
        return cmd_list(args)

    if args.verify:
        return cmd_verify(args)

    # --latest or --date: both need --out and --age-key
    if not args.out:
        parser.error("--out is required with --latest/--date")
    if not args.age_key:
        parser.error("--age-key is required with --latest/--date")
    return cmd_restore(args)


if __name__ == "__main__":
    sys.exit(main())
