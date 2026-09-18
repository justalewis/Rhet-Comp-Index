"""Tests for restore_local.py — restoring from a backup file already on disk.

restore.py only restores by downloading from S3/B2, which needs all six
PINAKES_BACKUP_* env vars set. These cover the local-file path against real
age/zstd implementations: no network, no bucket, no mocking of the crypto.

The redaction re-apply gets its own test because it is the step that a manual
decrypt silently skips — `restore.py --verify` does not run it either, so a
backup promoted that way can resurrect an author name that was removed on
request.
"""

import sqlite3

import pyrage
import pytest
import zstandard

import backup
import restore_local


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def age_keypair():
    ident = pyrage.x25519.Identity.generate()
    return {"public": str(ident.to_public()), "private": str(ident)}


@pytest.fixture
def key_file(tmp_path, age_keypair):
    path = tmp_path / "age.key"
    path.write_text(age_keypair["private"], encoding="utf-8")
    return path


@pytest.fixture
def encrypted_backup(seeded_db, tmp_path, age_keypair):
    """A real .db.zst.age built from the seeded 50-article database.

    Built the same way backup.py builds one: snapshot through SQLite's backup
    API, then compress, then encrypt. Reading the DB file directly would race
    the WAL and can capture a database with no rows in it yet.
    """
    snapshot = tmp_path / "snapshot.db"
    backup.create_snapshot(seeded_db, snapshot)
    compressed = zstandard.ZstdCompressor().compress(snapshot.read_bytes())
    recipient = pyrage.x25519.Recipient.from_str(age_keypair["public"])
    enc = tmp_path / "articles-20260918T083119Z.db.zst.age"
    enc.write_bytes(pyrage.encrypt(compressed, [recipient]))
    return enc


def _run(*args):
    return restore_local.main([str(a) for a in args])


# ── Happy path ──────────────────────────────────────────────────────────────


def test_restores_and_preserves_article_count(encrypted_backup, key_file, tmp_path):
    """Decrypt -> decompress -> integrity check round-trips the seeded corpus."""
    out = tmp_path / "restored.db"
    rc = _run("--file", encrypted_backup, "--age-key", key_file, "--out", out)
    assert rc == 0
    assert out.exists()

    with sqlite3.connect(str(out)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 50
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_restore_does_not_touch_the_source_backup(encrypted_backup, key_file, tmp_path):
    before = encrypted_backup.read_bytes()
    _run("--file", encrypted_backup, "--age-key", key_file,
         "--out", tmp_path / "restored.db")
    assert encrypted_backup.read_bytes() == before


def test_redaction_reapply_runs_by_default(encrypted_backup, key_file, tmp_path, capsys):
    """The step a manual decrypt skips must run unless explicitly disabled."""
    _run("--file", encrypted_backup, "--age-key", key_file,
         "--out", tmp_path / "restored.db")
    assert "Author-redaction re-applied" in capsys.readouterr().out


def test_skip_redaction_warns_loudly(encrypted_backup, key_file, tmp_path, capsys):
    rc = _run("--file", encrypted_backup, "--age-key", key_file,
              "--out", tmp_path / "restored.db", "--skip-redaction")
    assert rc == 0
    err = capsys.readouterr().err
    assert "redaction re-apply SKIPPED" in err
    assert "redaction.py resweep" in err  # tells the operator how to recover


# ── Refusals ────────────────────────────────────────────────────────────────


def test_wrong_age_key_fails_cleanly(encrypted_backup, tmp_path):
    """A key that did not encrypt this backup must fail, not write a partial DB."""
    wrong = tmp_path / "wrong.key"
    wrong.write_text(str(pyrage.x25519.Identity.generate()), encoding="utf-8")
    out = tmp_path / "restored.db"

    rc = _run("--file", encrypted_backup, "--age-key", wrong, "--out", out)
    assert rc == 1
    assert not out.exists()


def test_refuses_to_clobber_existing_output(encrypted_backup, key_file, tmp_path):
    out = tmp_path / "restored.db"
    out.write_text("precious", encoding="utf-8")

    assert _run("--file", encrypted_backup, "--age-key", key_file, "--out", out) == 2
    assert out.read_text(encoding="utf-8") == "precious"


def test_force_overwrites_existing_output(encrypted_backup, key_file, tmp_path):
    out = tmp_path / "restored.db"
    out.write_text("stale", encoding="utf-8")

    assert _run("--file", encrypted_backup, "--age-key", key_file,
                "--out", out, "--force") == 0
    assert out.read_bytes()[:16].startswith(b"SQLite format 3")


def test_rejects_a_file_that_is_not_an_age_key(encrypted_backup, tmp_path):
    """Catch the wrong file before spending time on a decrypt that cannot work."""
    notakey = tmp_path / "notes.txt"
    notakey.write_text("my password is hunter2", encoding="utf-8")

    assert _run("--file", encrypted_backup, "--age-key", notakey,
                "--out", tmp_path / "restored.db") == 2


def test_missing_source_backup(key_file, tmp_path):
    assert _run("--file", tmp_path / "nope.db.zst.age", "--age-key", key_file,
                "--out", tmp_path / "restored.db") == 2


def test_missing_age_key_file(encrypted_backup, tmp_path):
    assert _run("--file", encrypted_backup, "--age-key", tmp_path / "nokey.key",
                "--out", tmp_path / "restored.db") == 2
