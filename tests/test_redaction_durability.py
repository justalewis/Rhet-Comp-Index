"""P2 durability tests: the invisible leaks — Datastories cache, restore
resurrection, and the maintenance-pipeline resweep wiring."""

import json
import pathlib

import pytest

import db as _db
import redaction
from redaction import redact_author
from tests._seed import seed_database

JANE = "Jane Smith"


def test_redaction_busts_datastories_cache(seeded_db):
    """The cache fingerprint is MAX(id)-COUNT(*), which a name swap does NOT
    move — so redaction must explicitly wipe the on-disk cache or it serves the
    real name forever. Highest-severity leak in the design."""
    from datastories_cache import _cache_dir
    stale = _cache_dir() / "ds_fake-deadbeef00.json"
    stale.write_text(
        json.dumps({"_fingerprint": "x", "data": {"byline": JANE}}),
        encoding="utf-8",
    )
    assert stale.exists()
    redact_author(JANE)
    assert not stale.exists(), "datastories cache not busted on redaction"


def test_restore_reapplies_redaction_from_external_ledger(tmp_path, monkeypatch):
    """Restoring a backup taken BEFORE a redaction must not resurrect the name.
    The off-box ledger export, fed to restore._apply_redactions, re-applies it
    to the restored file and preserves the original token."""
    restore = pytest.importorskip("restore")

    # 1) Live DB: seed, redact Jane, export the ledger off-box.
    live = tmp_path / "live.db"
    monkeypatch.setattr(_db, "DB_PATH", str(live))
    monkeypatch.setenv("DB_PATH", str(live))
    _db.init_db()
    seed_database(live)
    token = redact_author(JANE)["token"]
    ledger_file = tmp_path / "ledger.json"
    ledger_file.write_text(json.dumps(redaction.export_ledger()), encoding="utf-8")

    # 2) A "pre-redaction backup": a fresh seeded DB with Jane present, no ledger.
    backup_db = tmp_path / "backup.db"
    monkeypatch.setattr(_db, "DB_PATH", str(backup_db))
    monkeypatch.setenv("DB_PATH", str(backup_db))
    _db.init_db()
    seed_database(backup_db)
    with _db.get_conn() as c:
        assert c.execute(
            "SELECT COUNT(*) FROM articles WHERE authors LIKE '%Jane Smith%'"
        ).fetchone()[0] > 0
        assert c.execute(
            "SELECT COUNT(*) FROM redaction_ledger"
        ).fetchone()[0] == 0  # backup predates the redaction

    # 3) Re-apply redactions to the restored file using the external ledger.
    totals = restore._apply_redactions(backup_db, str(ledger_file))
    assert totals["entries"] >= 1

    # 4) Jane is gone from the restored DB; the ORIGINAL token took her place.
    monkeypatch.setattr(_db, "DB_PATH", str(backup_db))
    redaction._bust_suppression_cache()
    with _db.get_conn() as c:
        assert c.execute(
            "SELECT COUNT(*) FROM articles WHERE authors LIKE '%Jane Smith%'"
        ).fetchone()[0] == 0
        assert c.execute(
            "SELECT COUNT(*) FROM articles WHERE authors LIKE ?", (f"%{token}%",)
        ).fetchone()[0] > 0


def test_export_import_ledger_roundtrips(seeded_db, tmp_path):
    token = redact_author(JANE)["token"]
    entries = redaction.export_ledger()
    assert entries and entries[0]["token"] == token
    assert entries[0]["name"] == JANE
    # Re-importing the same entries is a no-op (idempotent on token).
    assert redaction.import_ledger(entries) == 0


@pytest.mark.parametrize("path,needle", [
    ("weekly_maintenance.py", "resweep_all"),
    ("deep_refresh.py", "resweep_all"),
    ("restore.py", "_apply_redactions"),
    ("monitoring.py", "include_local_variables"),
])
def test_maintenance_paths_are_wired(path, needle):
    """The resweep / restore-hook / Sentry-guard must stay wired so a future
    refactor can't silently drop the durability backstop."""
    text = pathlib.Path(path).read_text(encoding="utf-8")
    assert needle in text, f"{path} is missing `{needle}`"
