"""Repo-root conftest. Sets DB_PATH to a session-scoped temp file BEFORE importing
any production module, so app.py's import-time init_db()/backfill_oa()/pre-warm
queries do not touch the real articles.db. Per-test fixtures redirect db.DB_PATH
to a fresh tmp_path file for full isolation."""

import os
import sys
import tempfile
import pathlib

# ── Session-level DB redirection (must run before any `import app`/`import db`).
# Module-import side-effects in app.py (init_db, backfill_oa_status, prewarm)
# would otherwise hit the developer's real ./articles.db.
_SESSION_DB = pathlib.Path(tempfile.gettempdir()) / "pinakes-pytest-session.db"
if _SESSION_DB.exists():
    _SESSION_DB.unlink()
os.environ["DB_PATH"] = str(_SESSION_DB)

# Make sure repo root is importable (pytest adds it via rootdir, but be explicit).
_REPO_ROOT = pathlib.Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pytest  # noqa: E402

# Now safe to import — app.py at import time creates the session DB schema.
import db as _db  # noqa: E402
import app as _app_module  # noqa: E402
from tests._seed import seed_database  # noqa: E402


@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    """Fresh empty DB for one test. Schema initialised; no rows."""
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(_db, "DB_PATH", str(db_file))
    monkeypatch.setenv("DB_PATH", str(db_file))
    _db.init_db()
    # Module-level caches in app.py and db.py must be reset so they don't
    # leak state between tests.
    _app_module._sidebar_cache = None
    _app_module._sidebar_ts = 0.0
    _db._DETAILED_COVERAGE_CACHE.clear()
    yield db_file


@pytest.fixture
def seeded_db(fixture_db):
    """fixture_db + deterministic seed (50 articles, 20 authors, citations, books)."""
    seed_database(fixture_db)
    # Reset caches again post-seed in case anything was warmed during seeding.
    _app_module._sidebar_cache = None
    _app_module._sidebar_ts = 0.0
    _db._DETAILED_COVERAGE_CACHE.clear()
    yield fixture_db


@pytest.fixture
def app(seeded_db):
    """Flask app object, bound to the seeded test DB."""
    _app_module.app.config["TESTING"] = True
    yield _app_module.app


@pytest.fixture
def empty_app(fixture_db):
    """Flask app object with the empty (schema-only) test DB."""
    _app_module.app.config["TESTING"] = True
    yield _app_module.app


@pytest.fixture
def client(app):
    """Flask test client against the seeded DB."""
    return app.test_client()


@pytest.fixture
def empty_client(empty_app):
    """Flask test client against the empty DB (schema only)."""
    return empty_app.test_client()


@pytest.fixture
def freeze_time(monkeypatch):
    """Pin datetime.utcnow / datetime.now to a fixed instant.
    Returns the frozen datetime object so tests can reference it."""
    import datetime as _dt
    FROZEN = _dt.datetime(2026, 4, 29, 12, 0, 0)

    class _FrozenDateTime(_dt.datetime):
        @classmethod
        def utcnow(cls):
            return FROZEN

        @classmethod
        def now(cls, tz=None):
            return FROZEN if tz is None else FROZEN.replace(tzinfo=tz)

    monkeypatch.setattr(_dt, "datetime", _FrozenDateTime)
    return FROZEN
