"""usage/store.py — the usage database: where it lives, its schema, and the
daily salts that make visitor ids unlinkable across days.

usage.db is a separate SQLite file from articles.db on purpose. SQLite locks
per file, so recording page views can never contend with the fetch and
maintenance jobs that write articles.db ("database is locked" was a real
outage). Nothing in here touches articles.db except read-only lookups in the
report.

Paths are resolved on every call rather than at import, so tests that
monkeypatch db.DB_PATH get a usage.db beside their temporary database.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import secrets
import sqlite3
import threading

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- One row per recorded request. Columns from `resolved` down are filled in
-- by the nightly resolver, which also nulls `ip`.
CREATE TABLE IF NOT EXISTS hits (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,
    day         TEXT NOT NULL,
    host        TEXT,
    method      TEXT,
    endpoint    TEXT,
    rule        TEXT,
    area        TEXT,
    tool        TEXT,
    object      TEXT,
    params      TEXT,
    query       TEXT,
    status      INTEGER,
    ms          INTEGER,
    is_api      INTEGER NOT NULL DEFAULT 0,
    ref_host    TEXT,
    ref_path    TEXT,
    lang        TEXT,
    ua_family   TEXT,
    ua_os       TEXT,
    ua_device   TEXT,
    bot         TEXT,
    bot_kind    TEXT,
    feed_subs   INTEGER,
    visitor     TEXT NOT NULL,
    gpc         INTEGER NOT NULL DEFAULT 0,
    ip          TEXT,
    cf_country  TEXT,
    cf_region   TEXT,
    cf_city     TEXT,
    resolved    INTEGER NOT NULL DEFAULT 0,
    country     TEXT,
    region      TEXT,
    city        TEXT,
    asn         INTEGER,
    as_org      TEXT,
    net_class   TEXT,
    institution_id INTEGER
);
CREATE INDEX IF NOT EXISTS hits_day        ON hits(day);
CREATE INDEX IF NOT EXISTS hits_day_vis    ON hits(day, visitor);
CREATE INDEX IF NOT EXISTS hits_unresolved ON hits(resolved) WHERE resolved = 0;
CREATE INDEX IF NOT EXISTS hits_with_ip    ON hits(ts) WHERE ip IS NOT NULL;
CREATE INDEX IF NOT EXISTS hits_ip         ON hits(ip) WHERE ip IS NOT NULL;

-- What the browser reports: page views (proof a person with a browser was
-- there), tool switches inside a page, outbound and download clicks, and
-- time on page. No IP is stored here at all.
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY,
    ts      TEXT NOT NULL,
    day     TEXT NOT NULL,
    visitor TEXT NOT NULL,
    type    TEXT NOT NULL,
    page    TEXT,
    area    TEXT,
    tool    TEXT,
    target  TEXT,
    value   INTEGER,
    pv      TEXT
);
CREATE INDEX IF NOT EXISTS events_day_vis ON events(day, visitor);

-- One row per visitor per day, rebuilt by the resolver. Reports count from
-- here so that bots can be left out with a single WHERE clause.
CREATE TABLE IF NOT EXISTS visitor_days (
    day            TEXT NOT NULL,
    visitor        TEXT NOT NULL,
    class          TEXT NOT NULL,
    beacon         INTEGER NOT NULL DEFAULT 0,
    hits           INTEGER NOT NULL DEFAULT 0,
    pages          INTEGER NOT NULL DEFAULT 0,
    tool_runs      INTEGER NOT NULL DEFAULT 0,
    tools          INTEGER NOT NULL DEFAULT 0,
    peak_per_min   INTEGER NOT NULL DEFAULT 0,
    engaged_ms     INTEGER NOT NULL DEFAULT 0,
    first_ts       TEXT,
    last_ts        TEXT,
    country        TEXT,
    region         TEXT,
    city           TEXT,
    net_class      TEXT,
    institution_id INTEGER,
    device         TEXT,
    lang           TEXT,
    ref_host       TEXT,
    bot            TEXT,
    PRIMARY KEY (day, visitor)
);
CREATE INDEX IF NOT EXISTS vd_class ON visitor_days(class, day);
CREATE INDEX IF NOT EXISTS vd_inst  ON visitor_days(institution_id);

CREATE TABLE IF NOT EXISTS institutions (
    id           INTEGER PRIMARY KEY,
    key          TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    kind         TEXT,
    country      TEXT,
    region       TEXT,
    city         TEXT,
    domain       TEXT,
    ror_id       TEXT,
    ipeds_unitid INTEGER,
    carnegie     TEXT,
    sector       TEXT,
    hbcu         INTEGER,
    tribal       INTEGER,
    in_corpus    INTEGER NOT NULL DEFAULT 0,
    source       TEXT,
    updated_at   TEXT
);

-- Resolution cache, one row per /24 (IPv4) or /48 (IPv6). Holds network
-- facts only, never a visitor's address.
CREATE TABLE IF NOT EXISTS networks (
    prefix         TEXT PRIMARY KEY,
    asn            INTEGER,
    as_org         TEXT,
    as_domain      TEXT,
    rdns           TEXT,
    rdns_verified  INTEGER NOT NULL DEFAULT 0,
    rdap_name      TEXT,
    rdap_org       TEXT,
    net_class      TEXT,
    institution_id INTEGER,
    match_via      TEXT,
    checked_at     TEXT NOT NULL
);

-- ROR affiliation-match cache, keyed by normalised organisation name. A miss
-- is cached too (ror_id NULL) so the same name is not asked about nightly.
CREATE TABLE IF NOT EXISTS org_lookups (
    name_norm  TEXT PRIMARY KEY,
    ror_id     TEXT,
    payload    TEXT,
    checked_at TEXT NOT NULL
);

-- What readers chose to tell us on /tell-us. Every field is optional and
-- volunteered; nothing here is linked to a visitor id or an address.
CREATE TABLE IF NOT EXISTS responses (
    id               INTEGER PRIMARY KEY,
    ts               TEXT NOT NULL,
    institution_text TEXT,
    institution_id   INTEGER,
    matched_at       TEXT,
    role             TEXT,
    uses             TEXT,
    course           TEXT,
    note             TEXT,
    quote_ok         INTEGER NOT NULL DEFAULT 0,
    contact_email    TEXT,
    came_from        TEXT
);
CREATE INDEX IF NOT EXISTS responses_ts ON responses(ts);

CREATE TABLE IF NOT EXISTS salts (
    day  TEXT PRIMARY KEY,
    salt BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    job         TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    ok          INTEGER,
    summary     TEXT
);
"""

_init_lock = threading.Lock()
_initialised: set[str] = set()


def usage_db_path() -> str:
    """USAGE_DB_PATH, else usage.db in the same directory as articles.db."""
    explicit = (os.environ.get("USAGE_DB_PATH") or "").strip()
    if explicit:
        return explicit
    import db as _db
    return os.path.join(os.path.dirname(os.path.abspath(_db.DB_PATH)), "usage.db")


def data_dir() -> str:
    """USAGE_DATA_DIR, else usage-data/ beside articles.db. Holds the IP
    databases and institution lists that refresh.py downloads."""
    explicit = (os.environ.get("USAGE_DATA_DIR") or "").strip()
    if explicit:
        return explicit
    import db as _db
    return os.path.join(os.path.dirname(os.path.abspath(_db.DB_PATH)), "usage-data")


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open usage.db, creating the schema on first use of each path. Callers
    close the connection; `with connect() as conn` only commits."""
    path = path or usage_db_path()
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    # Overwrite deleted content instead of leaving it in free pages. Without
    # this, an IP address nulled by the resolver or a salt dropped after two
    # days can survive in the file, and SQLite's backup API copies free pages
    # into every backup.
    conn.execute("PRAGMA secure_delete = ON")
    if path not in _initialised:
        with _init_lock:
            if path not in _initialised:
                _init_schema(conn)
                _initialised.add(path)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


# ── Daily salts ──────────────────────────────────────────────────────────────
# A visitor id is HMAC(salt-of-the-day, ip|user-agent|host). The salt is random
# and deleted two days later, after which nobody, including the operator, can
# recompute which address produced an id or link one day's visitor to the
# next. Two days rather than one so a page left open over midnight can still
# report its time-on-page.

_salt_lock = threading.Lock()
_salt_cache: dict[tuple[str, str], bytes] = {}


def salt_for(day: str) -> bytes:
    path = usage_db_path()
    key = (path, day)
    salt = _salt_cache.get(key)
    if salt is not None:
        return salt
    with _salt_lock:
        salt = _salt_cache.get(key)
        if salt is not None:
            return salt
        conn = connect(path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO salts (day, salt) VALUES (?, ?)",
                (day, secrets.token_bytes(32)),
            )
            conn.commit()
            salt = conn.execute(
                "SELECT salt FROM salts WHERE day = ?", (day,)
            ).fetchone()["salt"]
        finally:
            conn.close()
        # Keep the in-memory cache to the days still in use.
        for k in [k for k in _salt_cache if k[0] == path and k[1] < day]:
            _salt_cache.pop(k, None)
        _salt_cache[key] = salt
        return salt


def drop_old_salts(conn: sqlite3.Connection, today: str) -> int:
    """Delete salts older than yesterday. Returns how many went."""
    cutoff = (_dt.date.fromisoformat(today) - _dt.timedelta(days=1)).isoformat()
    cur = conn.execute("DELETE FROM salts WHERE day < ?", (cutoff,))
    with _salt_lock:
        for k in [k for k in _salt_cache if k[1] < cutoff]:
            _salt_cache.pop(k, None)
    return cur.rowcount


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
