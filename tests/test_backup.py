"""Tests for backup.py — the offline pieces (snapshot, compress, encrypt,
retention classification) run against real implementations; the boto3 S3
calls are mocked. No real S3 traffic, no real bucket required."""

import datetime
import io
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyrage
import pytest
import zstandard

import backup


# ── Real-key fixture (cheap; ~ms to generate) ───────────────────────────────


@pytest.fixture
def age_keypair():
    ident = pyrage.x25519.Identity.generate()
    return {"public": str(ident.to_public()), "private": str(ident)}


@pytest.fixture
def all_backup_env(monkeypatch, age_keypair):
    """Set every required env var so tests of run_backup() proceed past
    the validation gate."""
    monkeypatch.setenv("PINAKES_BACKUP_BUCKET", "test-bucket")
    monkeypatch.setenv("PINAKES_BACKUP_ENDPOINT", "https://s3.test.example/")
    monkeypatch.setenv("PINAKES_BACKUP_REGION", "test-region")
    monkeypatch.setenv("PINAKES_BACKUP_ACCESS_KEY_ID", "AK")
    monkeypatch.setenv("PINAKES_BACKUP_SECRET_KEY", "SK")
    monkeypatch.setenv("PINAKES_BACKUP_AGE_PUBLIC_KEY", age_keypair["public"])
    yield age_keypair


# ── create_snapshot ─────────────────────────────────────────────────────────


def test_create_snapshot_produces_valid_sqlite(seeded_db, tmp_path):
    """Snapshot the seeded DB, then re-open the snapshot and confirm
    article counts match."""
    dest = tmp_path / "snap.db"
    size = backup.create_snapshot(seeded_db, dest)
    assert size > 0
    assert dest.exists()
    with sqlite3.connect(str(dest)) as conn:
        n = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert n == 50  # seeded_db inserts 50


def test_create_snapshot_during_active_writes(seeded_db, tmp_path):
    """Run inserts on the source DB in a background thread while the
    snapshot is taken. The snapshot must be internally consistent — its
    article count is some valid value, and PRAGMA integrity_check is OK."""
    stop = threading.Event()

    def writer():
        for i in range(60000, 60100):
            if stop.is_set():
                break
            try:
                with sqlite3.connect(str(seeded_db), timeout=2) as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO articles "
                        "(url, title, journal, source) VALUES (?, ?, ?, ?)",
                        (f"https://noise.example/{i}", f"row {i}",
                         "College English", "crossref"),
                    )
                    conn.commit()
            except sqlite3.OperationalError:
                # Lock contention is fine — the snapshot is the consistency check.
                pass
            time.sleep(0.001)

    t = threading.Thread(target=writer, daemon=True)
    t.start()
    try:
        dest = tmp_path / "snap.db"
        backup.create_snapshot(seeded_db, dest)
    finally:
        stop.set()
        t.join(timeout=5)

    with sqlite3.connect(str(dest)) as conn:
        rows = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
        n = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    assert rows == ["ok"]
    assert n >= 50  # at least the original 50, possibly more if writer raced ahead


# ── compress / encrypt round-trips ──────────────────────────────────────────


def test_compress_round_trip(tmp_path):
    payload = b"composition theory " * 5_000  # ~95 KB, compresses well
    src = tmp_path / "in.bin"
    src.write_bytes(payload)
    out = backup.compress(src)
    assert out.suffix == ".zst"
    assert out.stat().st_size < src.stat().st_size  # actually compressed
    # backup.compress uses copy_stream (frame format); use stream_reader to round-trip.
    dctx = zstandard.ZstdDecompressor()
    with open(out, "rb") as fin:
        decompressed = dctx.stream_reader(fin).read()
    assert decompressed == payload


def test_encrypt_round_trip(tmp_path, age_keypair):
    payload = b"sensitive bytes " * 1_000
    src = tmp_path / "in.bin"
    src.write_bytes(payload)
    enc = backup.encrypt(src, age_keypair["public"])
    assert enc.suffix == ".age"
    blob = enc.read_bytes()
    assert backup.AGE_MAGIC in blob[:64]
    ident = pyrage.x25519.Identity.from_str(age_keypair["private"])
    decrypted = pyrage.decrypt(blob, [ident])
    assert decrypted == payload


# ── upload + key-path structure ─────────────────────────────────────────────


def test_upload_uses_correct_path_structure(tmp_path):
    """run_backup builds keys like `YYYY/MM/DD/articles-YYYYMMDDTHHMMSSZ.db.zst.age`."""
    src = tmp_path / "blob.bin"
    src.write_bytes(b"x" * 10)
    fake_s3 = MagicMock()
    backup.upload(src, "my-bucket", "2026/04/29/articles-20260429T030000Z.db.zst.age",
                  endpoint="https://example", s3_client=fake_s3)
    fake_s3.upload_file.assert_called_once_with(
        str(src), "my-bucket", "2026/04/29/articles-20260429T030000Z.db.zst.age",
    )


# ── _classify_for_retention ─────────────────────────────────────────────────


def _make_backup_set(now):
    """Generate two years of synthetic backups, one per day at 03:00 UTC."""
    backups = []
    for days_ago in range(0, 730):
        dt = now - datetime.timedelta(days=days_ago)
        dt = dt.replace(hour=3, minute=0, second=0, microsecond=0)
        ts = dt.strftime("%Y%m%dT%H%M%SZ")
        date_prefix = dt.strftime("%Y/%m/%d")
        backups.append((f"{date_prefix}/articles-{ts}.db.zst.age", dt))
    return backups


def test_prune_keeps_30_daily_26_weekly_12_monthly():
    now = datetime.datetime(2026, 4, 29, tzinfo=datetime.timezone.utc)
    backups = _make_backup_set(now)
    keep, delete = backup._classify_for_retention(backups, now=now)

    keep_set = set(keep)
    delete_set = set(delete)
    assert keep_set.isdisjoint(delete_set)
    assert len(keep_set) + len(delete_set) == len(backups)

    # Last 30 days: every backup kept
    daily_cutoff = now - datetime.timedelta(days=30)
    in_daily = [k for k, dt in backups if dt >= daily_cutoff]
    for k in in_daily:
        assert k in keep_set

    # Approximate weekly count: 26 unique ISO weeks expected
    weekly_cutoff = now - datetime.timedelta(days=26 * 7)
    weekly_kept = [
        (k, dt) for k, dt in backups
        if k in keep_set and weekly_cutoff <= dt < daily_cutoff
    ]
    weekly_weeks = {(dt.isocalendar().year, dt.isocalendar().week) for _, dt in weekly_kept}
    # 26 weeks - 1 (the last week may overlap the daily window) → about 22-26
    assert 20 <= len(weekly_weeks) <= 27, (
        f"unexpected weekly count: {len(weekly_weeks)}"
    )

    # Monthly count: 12 unique (year, month) tuples expected
    monthly_cutoff = now - datetime.timedelta(days=12 * 30)
    monthly_kept = [
        (k, dt) for k, dt in backups
        if k in keep_set and monthly_cutoff <= dt < weekly_cutoff
    ]
    months = {(dt.year, dt.month) for _, dt in monthly_kept}
    assert 4 <= len(months) <= 14, f"unexpected monthly count: {len(months)}"

    # Anything beyond 12 months is deleted
    for k, dt in backups:
        if dt < monthly_cutoff:
            assert k in delete_set


def test_classify_empty_input():
    keep, delete = backup._classify_for_retention([])
    assert keep == [] and delete == []


# ── _missing_env ────────────────────────────────────────────────────────────


def test_missing_env_lists_all_required(monkeypatch):
    for v in backup.REQUIRED_ENV:
        monkeypatch.delenv(v, raising=False)
    assert set(backup._missing_env()) == set(backup.REQUIRED_ENV)


# ── _parse_backup_timestamp ─────────────────────────────────────────────────


def test_parse_backup_timestamp_valid():
    dt = backup._parse_backup_timestamp(
        "2026/04/29/articles-20260429T030000Z.db.zst.age"
    )
    assert dt.year == 2026 and dt.month == 4 and dt.day == 29
    assert dt.tzinfo is datetime.timezone.utc


def test_parse_backup_timestamp_invalid():
    assert backup._parse_backup_timestamp("garbage") is None
    assert backup._parse_backup_timestamp("articles.db.zst.age") is None


# ── run_backup happy path ───────────────────────────────────────────────────


def test_run_backup_returns_failure_dict_when_secrets_missing(monkeypatch):
    for v in backup.REQUIRED_ENV:
        monkeypatch.delenv(v, raising=False)
    summary = backup.run_backup()
    assert summary["success"] is False
    assert "missing env vars" in summary["error"]
    assert summary["duration_seconds"] is not None


def test_run_backup_happy_path(seeded_db, all_backup_env, monkeypatch):
    """Mock the S3 client. Verify the orchestrator: snapshots, compresses,
    encrypts, uploads with the correct key, prunes."""
    fake_s3 = MagicMock()
    fake_s3.get_paginator.return_value.paginate.return_value = iter([{"Contents": []}])
    monkeypatch.setattr(backup, "_make_s3_client", lambda: fake_s3)
    monkeypatch.setenv("DB_PATH", str(seeded_db))

    summary = backup.run_backup()
    assert summary["success"] is True
    assert summary["snapshot_bytes"] > 0
    assert summary["compressed_bytes"] > 0
    assert summary["uploaded_to"].startswith("s3://test-bucket/")
    assert summary["uploaded_to"].endswith(".db.zst.age")

    # upload_file was called exactly once with the right bucket + key shape
    fake_s3.upload_file.assert_called_once()
    args = fake_s3.upload_file.call_args.args
    assert args[1] == "test-bucket"
    assert args[2].endswith(".db.zst.age")
    # YYYY/MM/DD/articles-YYYYMMDDTHHMMSSZ.db.zst.age
    assert args[2].count("/") == 3


def test_run_backup_reports_failure_to_sentry_on_upload_error(
    seeded_db, all_backup_env, monkeypatch,
):
    fake_s3 = MagicMock()
    fake_s3.upload_file.side_effect = RuntimeError("S3 down")
    monkeypatch.setattr(backup, "_make_s3_client", lambda: fake_s3)
    monkeypatch.setenv("DB_PATH", str(seeded_db))

    with patch("monitoring.capture_fetcher_error") as cap:
        summary = backup.run_backup()
    assert summary["success"] is False
    assert "S3 down" in summary["error"]
    cap.assert_called_once()
    args = cap.call_args.args
    assert args[0] == "backup"
    assert isinstance(args[2], RuntimeError)


def test_run_backup_returns_pruned_keys(seeded_db, all_backup_env, monkeypatch):
    """Populate a paginator response with one stale backup; verify it's
    listed as pruned in the summary."""
    fake_s3 = MagicMock()
    fake_s3.get_paginator.return_value.paginate.return_value = iter([{
        "Contents": [
            # > 12 months old → outside the monthly window → must be deleted
            {"Key": "2024/01/01/articles-20240101T030000Z.db.zst.age",
             "Size": 1024, "LastModified": datetime.datetime(2024, 1, 1,
                                                             tzinfo=datetime.timezone.utc)},
        ],
    }])
    monkeypatch.setattr(backup, "_make_s3_client", lambda: fake_s3)
    monkeypatch.setenv("DB_PATH", str(seeded_db))

    summary = backup.run_backup()
    assert summary["success"] is True
    assert "2024/01/01/articles-20240101T030000Z.db.zst.age" in summary["pruned_keys"]


# ── verify_latest_backup ────────────────────────────────────────────────────


def _build_encrypted_blob(payload: bytes, public_key: str) -> bytes:
    recipient = pyrage.x25519.Recipient.from_str(public_key)
    return pyrage.encrypt(payload, [recipient])


def _build_compressed_db_bytes(seeded_db_path) -> bytes:
    with open(seeded_db_path, "rb") as f:
        raw = f.read()
    cctx = zstandard.ZstdCompressor(level=3)
    return cctx.compress(raw)


def test_verify_latest_backup_partial_check_passes(
    seeded_db, all_backup_env, monkeypatch,
):
    """Without a private key, verify_latest_backup confirms presence,
    size, and age-format header. No decrypt/integrity step."""
    enc = _build_encrypted_blob(
        _build_compressed_db_bytes(seeded_db), all_backup_env["public"]
    )

    fake_s3 = MagicMock()
    listing_dt = datetime.datetime(2026, 4, 29, 3, 0, tzinfo=datetime.timezone.utc)
    fake_s3.get_paginator.return_value.paginate.return_value = iter([{
        "Contents": [
            {"Key": "2026/04/29/articles-20260429T030000Z.db.zst.age",
             "Size": len(enc), "LastModified": listing_dt},
        ],
    }])
    def _fake_download(bucket, key, dest):
        Path(dest).write_bytes(enc)
    fake_s3.download_file.side_effect = _fake_download
    monkeypatch.setattr(backup, "_make_s3_client", lambda: fake_s3)
    # No private key — partial check
    monkeypatch.delenv("PINAKES_BACKUP_AGE_PRIVATE_KEY", raising=False)

    result = backup.verify_latest_backup()
    assert result["success"] is True
    assert "age_header" in result["checks_run"]
    assert "decrypt" not in result["checks_run"]
    assert "integrity_check" not in result["checks_run"]


def test_verify_latest_backup_full_check_runs_integrity(
    seeded_db, all_backup_env, monkeypatch,
):
    """With a private key supplied, verify_latest_backup decrypts and
    runs PRAGMA integrity_check."""
    enc = _build_encrypted_blob(
        _build_compressed_db_bytes(seeded_db), all_backup_env["public"]
    )

    fake_s3 = MagicMock()
    listing_dt = datetime.datetime(2026, 4, 29, 3, 0, tzinfo=datetime.timezone.utc)
    fake_s3.get_paginator.return_value.paginate.return_value = iter([{
        "Contents": [
            {"Key": "2026/04/29/articles-20260429T030000Z.db.zst.age",
             "Size": len(enc), "LastModified": listing_dt},
        ],
    }])
    def _fake_download(bucket, key, dest):
        Path(dest).write_bytes(enc)
    fake_s3.download_file.side_effect = _fake_download
    monkeypatch.setattr(backup, "_make_s3_client", lambda: fake_s3)

    result = backup.verify_latest_backup(age_private_key=all_backup_env["private"])
    assert result["success"] is True
    assert "integrity_check" in result["checks_run"]
    assert result["integrity"] == ["ok"]


def test_verify_latest_backup_fails_on_zero_byte_upload(
    seeded_db, all_backup_env, monkeypatch,
):
    fake_s3 = MagicMock()
    fake_s3.get_paginator.return_value.paginate.return_value = iter([{
        "Contents": [
            {"Key": "2026/04/29/articles-20260429T030000Z.db.zst.age",
             "Size": 0,
             "LastModified": datetime.datetime(2026, 4, 29,
                                               tzinfo=datetime.timezone.utc)},
        ],
    }])
    monkeypatch.setattr(backup, "_make_s3_client", lambda: fake_s3)
    with patch("monitoring.capture_fetcher_error") as cap:
        result = backup.verify_latest_backup()
    assert result["success"] is False
    assert "zero bytes" in result["error"]
    cap.assert_called_once()


def test_verify_latest_backup_fails_on_no_backups(all_backup_env, monkeypatch):
    fake_s3 = MagicMock()
    fake_s3.get_paginator.return_value.paginate.return_value = iter([{"Contents": []}])
    monkeypatch.setattr(backup, "_make_s3_client", lambda: fake_s3)
    result = backup.verify_latest_backup()
    assert result["success"] is False
    assert "no backups" in result["error"]
