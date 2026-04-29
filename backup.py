"""backup.py — Online SQLite backup with off-machine storage and retention.

Runs as a scheduled job inside scheduler.py. Produces a consistent
point-in-time snapshot using SQLite's online .backup API, compresses
with zstd, encrypts with age (pyrage; standard age binary format),
and uploads to an S3-compatible bucket.

Pipeline:
    /data/articles.db
        → sqlite3 .backup       (online, no downtime)
        → zstd level 19         (high ratio, slow once-a-day acceptable)
        → age recipient         (one-way; private key NOT on Fly)
        → S3-compatible upload  (B2 by default; R2/S3/MinIO via endpoint)

Retention (default):
    daily   30 days
    weekly  26 weeks (one per ISO week beyond the daily window)
    monthly 12 months (one per calendar month beyond the weekly window)

Required env vars (validated at run_backup() entry, not import):
    PINAKES_BACKUP_BUCKET, PINAKES_BACKUP_ENDPOINT, PINAKES_BACKUP_REGION,
    PINAKES_BACKUP_ACCESS_KEY_ID, PINAKES_BACKUP_SECRET_KEY,
    PINAKES_BACKUP_AGE_PUBLIC_KEY

If any are unset, run_backup() logs CRITICAL and returns a failure dict
without crashing the scheduler.
"""

from __future__ import annotations

import datetime
import logging
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path

import zstandard

log = logging.getLogger(__name__)

# ── Env var contract ────────────────────────────────────────────────────────

ENV_BUCKET   = "PINAKES_BACKUP_BUCKET"
ENV_ENDPOINT = "PINAKES_BACKUP_ENDPOINT"
ENV_REGION   = "PINAKES_BACKUP_REGION"
ENV_AKID     = "PINAKES_BACKUP_ACCESS_KEY_ID"
ENV_SECRET   = "PINAKES_BACKUP_SECRET_KEY"
ENV_AGE_PUB  = "PINAKES_BACKUP_AGE_PUBLIC_KEY"

REQUIRED_ENV = (ENV_BUCKET, ENV_ENDPOINT, ENV_REGION,
                ENV_AKID, ENV_SECRET, ENV_AGE_PUB)

DEFAULT_RETENTION = {"daily_days": 30, "weekly_weeks": 26, "monthly_months": 12}

KEY_TIMESTAMP_RE = re.compile(r"(\d{8}T\d{6}Z)")
AGE_MAGIC = b"age-encryption.org/v1"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _missing_env() -> list[str]:
    """List of unset/empty required env vars."""
    return [v for v in REQUIRED_ENV if not os.environ.get(v)]


def _make_s3_client():
    """Build a boto3 S3 client. Factored so tests can monkeypatch this name."""
    import boto3  # imported lazily so test environments without boto3 still load this module
    return boto3.client(
        "s3",
        endpoint_url=os.environ[ENV_ENDPOINT],
        region_name=os.environ[ENV_REGION],
        aws_access_key_id=os.environ[ENV_AKID],
        aws_secret_access_key=os.environ[ENV_SECRET],
    )


def _parse_backup_timestamp(key: str) -> datetime.datetime | None:
    """Pull `YYYYMMDDTHHMMSSZ` from a backup key. Returns UTC datetime or None."""
    m = KEY_TIMESTAMP_RE.search(key)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(
            tzinfo=datetime.timezone.utc
        )
    except ValueError:
        return None


def _capture(exc: Exception, journal: str = "system") -> None:
    """Send a backup failure to Sentry. Swallow any failure of the capture
    itself — it must never crash the scheduler."""
    try:
        from monitoring import capture_fetcher_error
        capture_fetcher_error("backup", journal, exc)
    except Exception:
        pass


# ── Pipeline stages ─────────────────────────────────────────────────────────


def create_snapshot(source_path, dest_path) -> int:
    """Run SQLite's online .backup API: a consistent point-in-time copy of
    the source DB while the live writer (scheduler's fetch loop) keeps
    running. Returns the dest file size in bytes."""
    src = sqlite3.connect(str(source_path))
    dst = sqlite3.connect(str(dest_path))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    return os.path.getsize(dest_path)


def compress(snapshot_path) -> Path:
    """zstd level 19 — high ratio, slow but acceptable for a nightly job.
    Produces `<snapshot>.zst`."""
    snapshot_path = Path(snapshot_path)
    out = snapshot_path.with_suffix(snapshot_path.suffix + ".zst")
    cctx = zstandard.ZstdCompressor(level=19)
    with open(snapshot_path, "rb") as fin, open(out, "wb") as fout:
        cctx.copy_stream(fin, fout)
    return out


def encrypt(compressed_path, public_key_age: str) -> Path:
    """age public-key encryption to a single recipient. Produces standard
    age binary format (`age-encryption.org/v1` header), so the operator
    can decrypt with the regular `age` CLI as a fallback."""
    import pyrage
    compressed_path = Path(compressed_path)
    out = compressed_path.with_suffix(compressed_path.suffix + ".age")
    recipient = pyrage.x25519.Recipient.from_str(public_key_age.strip())
    out.write_bytes(pyrage.encrypt(compressed_path.read_bytes(), [recipient]))
    return out


def upload(encrypted_path, bucket: str, key: str, endpoint: str,
           s3_client=None) -> str:
    """Upload `encrypted_path` to `s3://bucket/key`. Returns an `s3://`-style
    URL. The `endpoint` arg is recorded but the actual endpoint is read
    from env by `_make_s3_client()`."""
    if s3_client is None:
        s3_client = _make_s3_client()
    s3_client.upload_file(str(encrypted_path), bucket, key)
    return f"s3://{bucket}/{key}"


def list_backups(bucket: str, prefix: str = "", s3_client=None) -> list[dict]:
    """List objects under prefix. Returns dicts with key, size, last_modified."""
    if s3_client is None:
        s3_client = _make_s3_client()
    paginator = s3_client.get_paginator("list_objects_v2")
    items = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            items.append({
                "key":           obj["Key"],
                "size":          obj["Size"],
                "last_modified": obj["LastModified"],
            })
    return items


def _classify_for_retention(backups, now=None, retention=None):
    """Pure function: given a list of (key, datetime) tuples and a retention
    policy, return (keep_keys, delete_keys). Easy to test in isolation."""
    if now is None:
        now = datetime.datetime.now(tz=datetime.timezone.utc)
    if retention is None:
        retention = DEFAULT_RETENTION

    daily_cutoff   = now - datetime.timedelta(days=retention["daily_days"])
    weekly_cutoff  = now - datetime.timedelta(days=retention["weekly_weeks"] * 7)
    monthly_cutoff = now - datetime.timedelta(days=retention["monthly_months"] * 30)

    # Newest first so "first per week" / "first per month" picks the most recent.
    sorted_backups = sorted(backups, key=lambda b: b[1], reverse=True)

    keep, delete = [], []
    seen_weeks: set[tuple[int, int]] = set()
    seen_months: set[tuple[int, int]] = set()

    for key, dt in sorted_backups:
        if dt >= daily_cutoff:
            keep.append(key)
        elif dt >= weekly_cutoff:
            iso = dt.isocalendar()
            wk = (iso.year, iso.week)
            if wk not in seen_weeks:
                seen_weeks.add(wk)
                keep.append(key)
            else:
                delete.append(key)
        elif dt >= monthly_cutoff:
            mo = (dt.year, dt.month)
            if mo not in seen_months:
                seen_months.add(mo)
                keep.append(key)
            else:
                delete.append(key)
        else:
            delete.append(key)

    return keep, delete


def prune(bucket: str, endpoint: str | None = None,
          retention_policy: dict | None = None, s3_client=None) -> list[str]:
    """List backups in the bucket, classify per retention, delete the
    losers. Returns the list of keys that were actually deleted."""
    if s3_client is None:
        s3_client = _make_s3_client()
    if retention_policy is None:
        retention_policy = DEFAULT_RETENTION

    items = list_backups(bucket, s3_client=s3_client)
    backups: list[tuple[str, datetime.datetime]] = []
    for it in items:
        dt = _parse_backup_timestamp(it["key"])
        if dt is not None:
            backups.append((it["key"], dt))

    _, delete_keys = _classify_for_retention(backups, retention=retention_policy)

    deleted: list[str] = []
    for key in delete_keys:
        try:
            s3_client.delete_object(Bucket=bucket, Key=key)
            deleted.append(key)
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to delete %s during prune: %s", key, e)
    return deleted


# ── Orchestrator ────────────────────────────────────────────────────────────


def run_backup(source_db_path: str | None = None) -> dict:
    """Snapshot → compress → encrypt → upload → prune. Always returns a
    summary dict; never raises. Reports failures to Sentry."""
    t0 = time.time()
    summary: dict = {
        "success": False,
        "snapshot_bytes": None,
        "compressed_bytes": None,
        "uploaded_to": None,
        "duration_seconds": None,
        "pruned_keys": [],
        "error": None,
    }

    missing = _missing_env()
    if missing:
        msg = f"backup skipped: missing env vars: {missing}"
        log.critical(msg)
        summary["error"] = msg
        # Critical config absence: report to Sentry.
        _capture(RuntimeError(msg), journal="config")
        summary["duration_seconds"] = round(time.time() - t0, 2)
        return summary

    if source_db_path is None:
        source_db_path = os.environ.get("DB_PATH", "articles.db")

    try:
        ts = datetime.datetime.now(tz=datetime.timezone.utc)
        ts_str = ts.strftime("%Y%m%dT%H%M%SZ")
        date_prefix = ts.strftime("%Y/%m/%d")
        key = f"{date_prefix}/articles-{ts_str}.db.zst.age"

        with tempfile.TemporaryDirectory(prefix="pinakes-backup-") as tmpdir:
            tmpdir_p = Path(tmpdir)
            snap = tmpdir_p / f"articles-{ts_str}.db"
            summary["snapshot_bytes"] = create_snapshot(source_db_path, snap)

            zst = compress(snap)
            summary["compressed_bytes"] = os.path.getsize(zst)

            enc = encrypt(zst, os.environ[ENV_AGE_PUB])

            s3 = _make_s3_client()
            url = upload(
                enc, os.environ[ENV_BUCKET], key, os.environ[ENV_ENDPOINT],
                s3_client=s3,
            )
            summary["uploaded_to"] = url

            summary["pruned_keys"] = prune(
                os.environ[ENV_BUCKET], retention_policy=DEFAULT_RETENTION,
                s3_client=s3,
            )

        summary["success"] = True
        log.info(
            "Backup OK: snapshot=%s bytes, compressed=%s bytes, key=%s, pruned=%d",
            summary["snapshot_bytes"], summary["compressed_bytes"],
            key, len(summary["pruned_keys"]),
        )
    except Exception as e:  # noqa: BLE001
        log.error("Backup failed: %s", e, exc_info=True)
        summary["error"] = str(e)
        _capture(e)

    summary["duration_seconds"] = round(time.time() - t0, 2)
    return summary


def verify_latest_backup(age_private_key: str | None = None) -> dict:
    """Weekly verification.

    Without a private key (the default on Fly, since the key lives off-machine):
        Confirms the most recent backup exists, has non-zero size, and starts
        with the age-format magic bytes. Catches upload-pipeline rot:
        rotated S3 credentials, lifecycle rules deleting too aggressively,
        zero-byte uploads, recipient drift.

    With `age_private_key` supplied (operator passes one explicitly via
    restore.py for a quarterly drill, or sets PINAKES_BACKUP_AGE_PRIVATE_KEY
    knowingly):
        Full check — decrypts, decompresses, runs PRAGMA integrity_check.

    Always returns a dict; reports failures to Sentry."""
    result: dict = {
        "success": False, "key": None, "size": None,
        "checks_run": [], "integrity": None, "error": None,
    }

    missing = _missing_env()
    if missing:
        result["error"] = f"missing env: {missing}"
        return result

    if age_private_key is None:
        age_private_key = os.environ.get("PINAKES_BACKUP_AGE_PRIVATE_KEY", "").strip() or None

    try:
        s3 = _make_s3_client()
        items = list_backups(os.environ[ENV_BUCKET], s3_client=s3)
        with_ts = [(it, _parse_backup_timestamp(it["key"])) for it in items]
        with_ts = [(it, dt) for it, dt in with_ts if dt is not None]
        if not with_ts:
            result["error"] = "no backups in bucket"
            _capture(RuntimeError(result["error"]), journal="verify")
            return result

        latest, _ = max(with_ts, key=lambda pair: pair[1])
        result["key"] = latest["key"]
        result["size"] = latest["size"]
        result["checks_run"].append("listing")

        if latest["size"] == 0:
            result["error"] = "latest backup is zero bytes"
            _capture(RuntimeError(result["error"]), journal="verify")
            return result
        result["checks_run"].append("size")

        # Download and inspect age header. We always check the header even
        # without a private key — catches a corrupt or wrong-format upload
        # without needing to decrypt.
        with tempfile.TemporaryDirectory(prefix="pinakes-verify-") as tmpdir:
            tmpdir_p = Path(tmpdir)
            enc_path = tmpdir_p / "latest.db.zst.age"
            s3.download_file(
                os.environ[ENV_BUCKET], latest["key"], str(enc_path),
            )
            head = enc_path.read_bytes()[:64]
            if AGE_MAGIC not in head:
                result["error"] = "downloaded file is not age-formatted"
                _capture(RuntimeError(result["error"]), journal="verify")
                return result
            result["checks_run"].append("age_header")

            if age_private_key:
                # Full path — decrypt and run integrity_check.
                import pyrage
                ident = pyrage.x25519.Identity.from_str(age_private_key)
                zst_path = tmpdir_p / "latest.db.zst"
                zst_path.write_bytes(
                    pyrage.decrypt(enc_path.read_bytes(), [ident])
                )
                result["checks_run"].append("decrypt")

                db_path = tmpdir_p / "latest.db"
                dctx = zstandard.ZstdDecompressor()
                with open(zst_path, "rb") as fin, open(db_path, "wb") as fout:
                    dctx.copy_stream(fin, fout)
                result["checks_run"].append("decompress")

                # Explicit close — `with sqlite3.connect(...)` only commits;
                # on Windows the file handle survives the block exit and
                # blocks tempdir cleanup.
                conn = sqlite3.connect(str(db_path))
                try:
                    rows = conn.execute("PRAGMA integrity_check").fetchall()
                finally:
                    conn.close()
                result["integrity"] = [r[0] for r in rows]
                result["checks_run"].append("integrity_check")
                if result["integrity"] != ["ok"]:
                    result["error"] = f"integrity check failed: {result['integrity']}"
                    _capture(RuntimeError(result["error"]), journal="verify")
                    return result

        result["success"] = True
        log.info("Backup verify OK: key=%s checks=%s", result["key"], result["checks_run"])
    except Exception as e:  # noqa: BLE001
        log.error("Backup verify failed: %s", e, exc_info=True)
        result["error"] = str(e)
        _capture(e, journal="verify")

    return result
