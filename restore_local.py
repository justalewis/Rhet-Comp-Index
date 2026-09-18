"""restore_local.py - Restore a Pinakes backup from a file ALREADY on disk.

restore.py only restores by downloading from S3/B2, which needs all six
PINAKES_BACKUP_* env vars. When you have already downloaded the .db.zst.age
yourself, this does the same work against the local file:

    decrypt (age) -> decompress (zstd) -> PRAGMA integrity_check
      -> re-apply the author-redaction ledger -> report article count

The redaction re-apply is the step you must not skip. restore.py runs it so a
restore can never resurrect an author name that someone asked to have removed;
`--verify` does NOT run it.

Run with the venv python, from the app directory:

    cd C:\\Pinakes\\app
    C:\\Pinakes\\venv\\Scripts\\python.exe restore_local.py ^
        --file "C:\\Users\\Justin\\Downloads\\articles-20260918T083119Z.db.zst.age" ^
        --age-key "C:\\Users\\Justin\\age.key" ^
        --out "C:\\Pinakes\\data\\restored.db"

This writes a NEW file. It never touches the live database.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

import zstandard


def _fmt(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", required=True, help="local .db.zst.age backup")
    ap.add_argument("--age-key", required=True,
                    help="file containing the AGE-SECRET-KEY-1... private key")
    ap.add_argument("--out", required=True, help="where to write the restored .db")
    ap.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    ap.add_argument("--redaction-ledger", default=None,
                    help="optional external ledger export to merge first")
    ap.add_argument("--skip-redaction", action="store_true",
                    help="NOT RECOMMENDED. Skip the redaction re-apply.")
    args = ap.parse_args(argv)

    enc_path = Path(args.file).expanduser().resolve()
    if not enc_path.is_file():
        print(f"ERROR: backup not found: {enc_path}", file=sys.stderr)
        return 2

    out_path = Path(args.out).expanduser().resolve()
    if out_path.exists() and not args.force:
        print(f"ERROR: {out_path} exists. Pass --force to overwrite.", file=sys.stderr)
        return 2

    key_path = Path(args.age_key).expanduser()
    if not key_path.is_file():
        print(f"ERROR: age key file not found: {key_path}", file=sys.stderr)
        return 2
    age_private_key = key_path.read_text(encoding="utf-8").strip()
    if not age_private_key.startswith("AGE-SECRET-KEY-1"):
        print("ERROR: that file does not look like an age private key "
              "(expected it to start with AGE-SECRET-KEY-1).", file=sys.stderr)
        return 2

    print(f"Source: {enc_path} ({_fmt(enc_path.stat().st_size)})")

    print("Decrypting...")
    import pyrage
    try:
        ident = pyrage.x25519.Identity.from_str(age_private_key)
        plaintext = pyrage.decrypt(enc_path.read_bytes(), [ident])
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: decryption failed: {exc}", file=sys.stderr)
        print("The age private key does not match the public key this backup "
              "was encrypted to. If the keys were rotated, you need the older "
              "private key.", file=sys.stderr)
        return 1

    print("Decompressing...")
    with tempfile.TemporaryDirectory(prefix="pinakes-restore-") as tmpdir:
        zst_path = Path(tmpdir) / "backup.db.zst"
        zst_path.write_bytes(plaintext)
        dctx = zstandard.ZstdDecompressor()
        with open(zst_path, "rb") as fin, open(out_path, "wb") as fout:
            dctx.copy_stream(fin, fout)

    print(f"Running PRAGMA integrity_check on {out_path}...")
    with sqlite3.connect(str(out_path)) as conn:
        rows = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
        count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    if rows != ["ok"]:
        print(f"FAIL: integrity check failed: {rows}", file=sys.stderr)
        return 1
    print(f"Integrity OK. {count} articles. ({_fmt(out_path.stat().st_size)})")

    if args.skip_redaction:
        print("WARNING: redaction re-apply SKIPPED at your request. Run "
              "`python redaction.py resweep` against the live DB after promoting.",
              file=sys.stderr)
    else:
        print("Re-applying author-redaction ledger...")
        try:
            import restore as _restore
            applied = _restore._apply_redactions(out_path, args.redaction_ledger)
            print(f"Author-redaction re-applied: {applied}")
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: redaction re-apply failed: {exc}", file=sys.stderr)
            print("Run `python redaction.py resweep` against the live DB "
                  "after promoting.", file=sys.stderr)
            return 1

    print()
    print("Restored file is ready. It is NOT live yet. To promote it:")
    print("  1. Stop-Service Pinakes-Web")
    print("  2. Move the current articles.db aside, then move this file into place")
    print("  3. Start-Service Pinakes-Web")
    print("  4. Confirm /health/ready reports db reachable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
