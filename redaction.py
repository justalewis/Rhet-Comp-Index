"""redaction.py — author redaction ("Name Redacted by Author Request").

The data-layer spine of the author opt-out feature. An author who verifies
their identity can have their name and traces removed from the index while
the scholarship — and its place in every citation/co-authorship metric —
stays put.

Why a token, not a blank
------------------------
Author identity in Pinakes *is* the name string. There is no integer author
id: every query is ``WHERE authors LIKE '%name%'``, the author page lives at
``/author/<name>``, and every network keys its nodes by the name. So the way
to remove a name without dissolving the scholarship is to replace it, in
place, with a stable per-author **token**. The token becomes the new identity
key, and all the LIKE-based machinery (timeline, co-authors, citing venues,
co-citation, betweenness) keeps resolving through it unchanged. Metrics are
preserved for free; only the human-readable name is gone.

Token form:  ``Redacted Author 7f3a2c``
    No ``#`` — that character is a URL-fragment delimiter and would break the
    author-page URL. The 6-hex suffix is ``sha256(normalized_name || salt)``
    and is unique per author, so:
      * UNIQUE(name) on the authors table is satisfied (no constraint change);
      * two different redacted authors never collapse into one network node;
      * the published token can't be brute-forced back to the name (the salt
        is a per-install secret, env ``REDACTION_SALT`` or a DB-persisted one).

The display string ("Name Redacted by Author Request") is applied at the
template layer via the ``redact_authors`` Jinja filter; the token remains the
stored identity and the link target.

The ledger
----------
``redaction_ledger`` retains the plaintext name (the locked-table design): no
render/API/export path reads it, but the suppression re-applier and admin
review do. Retaining the name buys two things — robust variant matching when
upstream hands the name back, and reversibility (un-redaction restores the
real name from the ledger).

The resurrection problem
------------------------
Token-replacing today does nothing about tomorrow's fetch: CrossRef/OpenAlex
re-supply the real name and ``upsert_article`` would write it straight back.
So every ingest path must consult the ledger. Rather than guard ~20 write
sites, suppression lives at the two real bottlenecks (``upsert_article``,
``upsert_book``) plus :func:`resweep_all`, run after each fetch, which makes
redaction idempotent and self-healing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets

log = logging.getLogger(__name__)

# ── Token / display constants ────────────────────────────────────────────────

TOKEN_PREFIX = "Redacted Author "
DISPLAY_TEXT = "Name Redacted by Author Request"
_TOKEN_RE = re.compile(r"^Redacted Author [0-9a-f]{6,}$")


def is_redaction_token(value: str | None) -> bool:
    """True if *value* is a stored redaction token (not a real author name)."""
    return bool(value) and bool(_TOKEN_RE.match(value.strip()))


# ── Name normalization ───────────────────────────────────────────────────────

_WS_RE = re.compile(r"\s+")
_STRIP_PUNCT = " \t\r\n.,;"


def _normalize(name: str) -> str:
    """Canonical form for matching: casefold, collapse whitespace, trim
    surrounding punctuation. Conservative on purpose — Pinakes already treats
    'Smith, A.' and 'Alice Smith' as distinct authors, and we match only the
    name forms the requester gave us (plus the canonical), so we must not
    over-normalize and accidentally fold two real people together."""
    if not name:
        return ""
    n = _WS_RE.sub(" ", name).strip(_STRIP_PUNCT)
    return n.casefold()


# ── Salt + hashing ───────────────────────────────────────────────────────────

def _get_salt(conn) -> str:
    """Per-install secret salt. Prefer env REDACTION_SALT; otherwise read (or
    lazily create and persist) one in redaction_meta so the token suffix is
    not a bare dictionary-reversible hash and stays stable across runs."""
    env = os.environ.get("REDACTION_SALT")
    if env:
        return env
    row = conn.execute(
        "SELECT value FROM redaction_meta WHERE key = 'salt'"
    ).fetchone()
    if row and row["value"]:
        return row["value"]
    salt = secrets.token_hex(16)
    conn.execute(
        "INSERT OR REPLACE INTO redaction_meta (key, value) VALUES ('salt', ?)",
        (salt,),
    )
    conn.commit()
    return salt


def _name_hash(name: str, salt: str) -> str:
    return hashlib.sha256((_normalize(name) + "|" + salt).encode("utf-8")).hexdigest()


def mint_token(name: str, salt: str, conn=None) -> str:
    """Deterministic, collision-checked token for *name*. Same name + salt
    always yields the same token; a prefix collision with a *different*
    already-redacted name extends the suffix."""
    digest = _name_hash(name, salt)
    for length in range(6, 33, 2):
        tok = TOKEN_PREFIX + digest[:length]
        if conn is not None:
            clash = conn.execute(
                "SELECT 1 FROM redaction_ledger WHERE token = ?", (tok,)
            ).fetchone()
            if clash:
                continue
        return tok
    return TOKEN_PREFIX + digest  # pragma: no cover — astronomically unlikely


# ── Suppression map (cached) ─────────────────────────────────────────────────
#
# upsert_article/upsert_book consult this on every write. The cache is keyed by
# (db path, ledger row count, max ledger id) so it refreshes the instant a
# redaction lands and never bleeds across the per-test DBs the harness swaps in.

_SMAP_CACHE: dict | None = None
_SMAP_VERSION: tuple | None = None


def _ledger_version(conn) -> tuple:
    import db as _dbpkg
    try:
        n, mx = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM redaction_ledger"
        ).fetchone()
    except Exception:  # noqa: BLE001 — table missing (pre-migration): no suppression
        n, mx = 0, 0
    return (_dbpkg.DB_PATH, n, mx)


def _build_suppression_map(conn) -> dict:
    smap: dict[str, str] = {}
    try:
        rows = conn.execute(
            "SELECT token, name, name_variants FROM redaction_ledger"
        ).fetchall()
    except Exception:  # noqa: BLE001 — table missing: empty map (safe default)
        return smap
    for row in rows:
        smap[_normalize(row["name"])] = row["token"]
        if row["name_variants"]:
            try:
                for v in json.loads(row["name_variants"]):
                    if v:
                        smap[_normalize(v)] = row["token"]
            except (ValueError, TypeError):
                pass
    return smap


def _suppression_map(conn=None) -> dict:
    """Return {normalized_name -> token}. Cheap: a COUNT/MAX probe per call,
    rebuilding the dict only when the ledger actually changes."""
    global _SMAP_CACHE, _SMAP_VERSION
    own = conn is None
    if own:
        from db import get_conn
        conn = get_conn()
    try:
        ver = _ledger_version(conn)
        if _SMAP_CACHE is not None and _SMAP_VERSION == ver:
            return _SMAP_CACHE
        smap = _build_suppression_map(conn)
        _SMAP_CACHE = smap
        _SMAP_VERSION = ver
        return smap
    finally:
        if own:
            conn.close()


def _bust_suppression_cache() -> None:
    global _SMAP_CACHE, _SMAP_VERSION
    _SMAP_CACHE = None
    _SMAP_VERSION = None


def apply_suppression(authors_text: str | None, conn=None, smap=None) -> str | None:
    """Replace any suppressed name in a ``;``-delimited authors/editors string
    with its token, in place, leaving co-authors untouched. Exact element
    match on the normalized name — never a substring match — so 'Jane Smith'
    can't catch 'Jane Smithson'. Exception-safe: ingestion must never break
    because suppression hiccuped."""
    if not authors_text:
        return authors_text
    try:
        if smap is None:
            smap = _suppression_map(conn)
        if not smap:
            return authors_text
        parts = authors_text.split(";")
        out, changed = [], False
        for p in parts:
            name = p.strip()
            tok = smap.get(_normalize(name)) if name else None
            if tok:
                out.append(tok)
                changed = True
            else:
                out.append(name)
        return "; ".join(out) if changed else authors_text
    except Exception as exc:  # noqa: BLE001
        log.warning("apply_suppression failed (passing through): %s", exc)
        return authors_text


# ── In-place token substitution for free-text author fields ──────────────────

def _subst_text(authors_text: str | None, norm_targets: set[str], token: str) -> str | None:
    if not authors_text:
        return authors_text
    out, changed = [], False
    for p in authors_text.split(";"):
        name = p.strip()
        if name and _normalize(name) in norm_targets:
            out.append(token)
            changed = True
        else:
            out.append(name)
    return "; ".join(out) if changed else authors_text


def _scrub_raw_references(conn, token: str, names: list[str]) -> int:
    """Best-effort, v1: word-boundary replace each exact name form with the
    token inside citations.raw_reference (free-text bibliography blobs from
    other articles citing this author). Coverage is intentionally incomplete —
    these are arbitrary upstream citation strings and we only match the exact
    forms in the ledger. Disclosed as a limitation on the About page."""
    patterns = [
        (re.compile(r"\b" + re.escape(n) + r"\b"), token)
        for n in names if n and len(n) >= 4
    ]
    if not patterns:
        return 0
    like_clause = " OR ".join(["raw_reference LIKE ?"] * len(names))
    like_params = [f"%{n}%" for n in names]
    rows = conn.execute(
        f"SELECT id, raw_reference FROM citations "
        f"WHERE raw_reference IS NOT NULL AND ({like_clause})",
        like_params,
    ).fetchall()
    n_changed = 0
    for r in rows:
        text = r["raw_reference"]
        new = text
        for pat, repl in patterns:
            new = pat.sub(repl, new)
        if new != text:
            conn.execute(
                "UPDATE citations SET raw_reference = ? WHERE id = ?", (new, r["id"])
            )
            n_changed += 1
    return n_changed


def _resweep_entry(conn, token: str, names: list[str]) -> dict:
    """Apply one ledger entry across every table that stores the name. Uses
    UPDATE OR REPLACE on the UNIQUE-keyed normalized tables so re-runs collapse
    cleanly onto the single token row (idempotent)."""
    names = [n for n in names if n]
    norm_targets = {_normalize(n) for n in names}
    if not norm_targets:
        return {}
    name_ph = ",".join("?" * len(names))
    like_clause = " OR ".join(["authors LIKE ?"] * len(names))
    like_params = [f"%{n}%" for n in names]
    stats = {"articles": 0, "books": 0}

    # articles.authors (free text) — element-wise, co-authors preserved.
    for r in conn.execute(
        f"SELECT id, authors FROM articles "
        f"WHERE authors IS NOT NULL AND ({like_clause})",
        like_params,
    ).fetchall():
        new = _subst_text(r["authors"], norm_targets, token)
        if new != r["authors"]:
            conn.execute("UPDATE articles SET authors = ? WHERE id = ?", (new, r["id"]))
            stats["articles"] += 1

    # books.authors / books.editors (free text).
    book_clause = " OR ".join(
        ["authors LIKE ?"] * len(names) + ["editors LIKE ?"] * len(names)
    )
    for r in conn.execute(
        f"SELECT id, authors, editors FROM books WHERE {book_clause}",
        like_params + like_params,
    ).fetchall():
        na = _subst_text(r["authors"], norm_targets, token)
        ne = _subst_text(r["editors"], norm_targets, token)
        if na != r["authors"] or ne != r["editors"]:
            conn.execute(
                "UPDATE books SET authors = ?, editors = ? WHERE id = ?",
                (na, ne, r["id"]),
            )
            stats["books"] += 1

    # authors table — the display record. De-identify fully: name -> token and
    # null the identity/affiliation traces (ORCID, OpenAlex id, institution).
    # Institution-level METRICS are preserved separately via the affiliation
    # tables below, which keep institution_id.
    conn.execute(
        f"UPDATE OR REPLACE authors "
        f"SET name = ?, orcid = NULL, openalex_id = NULL, "
        f"    institution_name = NULL, institution_ror = NULL "
        f"WHERE name IN ({name_ph})",
        [token, *names],
    )

    # Normalized affiliation/institution tables — author_name -> token, drop the
    # OpenAlex author id (a name-recovery vector); KEEP institution_id so
    # institution metrics still count the work.
    conn.execute(
        f"UPDATE OR REPLACE author_article_affiliations "
        f"SET author_name = ?, openalex_author_id = NULL "
        f"WHERE author_name IN ({name_ph})",
        [token, *names],
    )
    conn.execute(
        f"UPDATE OR REPLACE article_author_institutions "
        f"SET author_name = ?, openalex_author_id = NULL "
        f"WHERE author_name IN ({name_ph})",
        [token, *names],
    )

    # Best-effort raw_reference scrub.
    stats["raw_references"] = _scrub_raw_references(conn, token, names)
    return stats


# ── Ledger operations ────────────────────────────────────────────────────────

def _entry_names(name: str, variants=None) -> list[str]:
    seen, out = set(), []
    for n in [name, *(variants or [])]:
        if n and n.strip() and n.strip() not in seen:
            seen.add(n.strip())
            out.append(n.strip())
    return out


def _existing_token(conn, name: str, salt: str) -> str | None:
    h = _name_hash(name, salt)
    row = conn.execute(
        "SELECT token FROM redaction_ledger WHERE name_hash = ?", (h,)
    ).fetchone()
    return row["token"] if row else None


def _add_to_ledger(conn, name, variants, request_id, created_by, salt) -> str:
    """Insert (or return existing) ledger row for *name*. Idempotent on the
    canonical name's hash."""
    existing = _existing_token(conn, name, salt)
    if existing:
        return existing
    token = mint_token(name, salt, conn=conn)
    conn.execute(
        "INSERT INTO redaction_ledger "
        "(token, name, name_hash, name_variants, request_id, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            token,
            name,
            _name_hash(name, salt),
            json.dumps(variants) if variants else None,
            request_id,
            created_by,
        ),
    )
    return token


def _rebuild_fts(conn) -> None:
    """Resync the external-content FTS index. The articles_fts_au trigger fires
    on each authors UPDATE, but a single rebuild after a batch is the cheap,
    certain backstop so searching the old name returns nothing."""
    try:
        conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
    except Exception as exc:  # noqa: BLE001
        log.warning("FTS rebuild after redaction failed: %s", exc)


def _bust_datastories_cache() -> int:
    """Wipe the on-disk Datastories cache. Its fingerprint is MAX(id)-COUNT(*),
    which a name swap does NOT move — so without this the cache would serve the
    real name indefinitely. The single highest-severity leak in the design."""
    try:
        from datastories_cache import clear_all
        return clear_all()
    except Exception as exc:  # noqa: BLE001
        log.warning("datastories cache bust failed: %s", exc)
        return 0


def redact_author(name, variants=None, request_id=None, created_by=None) -> dict:
    """Redact one author: ledger them, swap their name for the token across
    every table, rebuild FTS, bust the Datastories cache. Idempotent."""
    from db import get_conn
    names = _entry_names(name, variants)
    with get_conn() as conn:
        salt = _get_salt(conn)
        token = _add_to_ledger(conn, name, variants, request_id, created_by, salt)
        stats = _resweep_entry(conn, token, names)
        _rebuild_fts(conn)
        conn.commit()
    _bust_suppression_cache()
    cache_files = _bust_datastories_cache()
    log.info("Redacted '%s' -> %s (%s; cache files cleared: %d)",
             name, token, stats, cache_files)
    return {"token": token, "name": name, "stats": stats}


def resweep_all() -> dict:
    """Re-apply the entire ledger across all tables. The self-healing backstop:
    run after every fetch/enrichment so any name an ingest path slipped past the
    two choke-points is swept back out. Cheap (a handful of ledger rows)."""
    from db import get_conn
    totals = {"entries": 0, "articles": 0, "books": 0, "raw_references": 0}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT token, name, name_variants FROM redaction_ledger"
        ).fetchall()
        for row in rows:
            variants = json.loads(row["name_variants"]) if row["name_variants"] else []
            names = _entry_names(row["name"], variants)
            stats = _resweep_entry(conn, row["token"], names)
            totals["entries"] += 1
            for k in ("articles", "books", "raw_references"):
                totals[k] += stats.get(k, 0)
        if rows:
            _rebuild_fts(conn)
        conn.commit()
    _bust_suppression_cache()
    if totals["entries"]:
        _bust_datastories_cache()
    return totals


def unredact_author(token: str) -> dict:
    """Reverse a redaction: restore the real name from the ledger across the
    free-text fields and remove the ledger row. Possible only because the
    locked ledger retains the plaintext name. The normalized author/affiliation
    rows that were collapsed onto the token are NOT reconstructed (their
    pre-redaction detail — ORCID, OpenAlex ids — is gone); a subsequent
    OpenAlex enrichment pass repopulates them from the restored name."""
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name, name_variants FROM redaction_ledger WHERE token = ?", (token,)
        ).fetchone()
        if not row:
            return {"restored": False, "reason": "token not in ledger"}
        name = row["name"]
        # Remove the ledger row FIRST so the suppression map no longer carries
        # this name, then swap the token back to the canonical name in free text.
        conn.execute("DELETE FROM redaction_ledger WHERE token = ?", (token,))
        n_articles = 0
        for r in conn.execute(
            "SELECT id, authors FROM articles WHERE authors LIKE ?", (f"%{token}%",)
        ).fetchall():
            new = r["authors"].replace(token, name)
            if new != r["authors"]:
                conn.execute("UPDATE articles SET authors = ? WHERE id = ?", (new, r["id"]))
                n_articles += 1
        for r in conn.execute(
            "SELECT id, authors, editors FROM books "
            "WHERE authors LIKE ? OR editors LIKE ?", (f"%{token}%", f"%{token}%")
        ).fetchall():
            na = (r["authors"] or "").replace(token, name) or r["authors"]
            ne = (r["editors"] or "").replace(token, name) or r["editors"]
            conn.execute("UPDATE books SET authors = ?, editors = ? WHERE id = ?",
                         (na, ne, r["id"]))
        conn.execute("UPDATE authors SET name = ? WHERE name = ?", (name, token))
        conn.execute("UPDATE author_article_affiliations SET author_name = ? "
                     "WHERE author_name = ?", (name, token))
        conn.execute("UPDATE article_author_institutions SET author_name = ? "
                     "WHERE author_name = ?", (name, token))
        _rebuild_fts(conn)
        conn.commit()
    _bust_suppression_cache()
    _bust_datastories_cache()
    log.info("Un-redacted %s -> '%s' (%d articles)", token, name, n_articles)
    return {"restored": True, "name": name, "articles": n_articles}


def list_redactions(conn=None) -> list[dict]:
    """Admin view of the ledger (includes plaintext names — caller must gate)."""
    from db import get_conn
    own = conn is None
    if own:
        conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, token, name, name_variants, redacted_at, request_id, created_by "
            "FROM redaction_ledger ORDER BY redacted_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def export_ledger() -> list[dict]:
    """Full ledger rows (all columns) for an off-box snapshot. Keep a copy
    somewhere durable: a restore from a backup taken BEFORE a redaction won't
    contain the ledger, and this is what re-applies it (see restore.py
    --redaction-ledger). Contains plaintext names — store it securely."""
    from db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT token, name, name_hash, name_variants, redacted_at, "
            "request_id, created_by FROM redaction_ledger ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def import_ledger(entries: list[dict]) -> int:
    """Merge ledger rows from an export into this DB (idempotent on token).
    Used by restore.py to re-apply redactions to a pre-redaction restore."""
    from db import get_conn
    n = 0
    with get_conn() as conn:
        for e in entries:
            cur = conn.execute(
                "INSERT OR IGNORE INTO redaction_ledger "
                "(token, name, name_hash, name_variants, redacted_at, request_id, created_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (e["token"], e["name"], e.get("name_hash", ""),
                 e.get("name_variants"), e.get("redacted_at"),
                 e.get("request_id"), e.get("created_by")),
            )
            n += cur.rowcount or 0
        conn.commit()
    _bust_suppression_cache()
    return n


# ── Request queue + audit (P3) ───────────────────────────────────────────────

def _audit(conn, request_id, event, actor=None, detail=None) -> None:
    conn.execute(
        "INSERT INTO redaction_audit (request_id, event, actor, detail) "
        "VALUES (?, ?, ?, ?)",
        (request_id, event, actor, detail),
    )


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_request(author_name, email=None, orcid=None, method="email",
                   variants=None) -> tuple[int, str | None]:
    """Create a redaction request. For the email method, also mints a one-time
    verification token (only its hash is stored) and returns the raw token for
    the caller to email. Returns (request_id, raw_token_or_None)."""
    from db import get_conn
    raw_token = secrets.token_urlsafe(32) if method == "email" else None
    token_hash = _hash_token(raw_token) if raw_token else None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO redaction_requests "
            "(author_name_claimed, name_variants, requester_email, requester_orcid, "
            " verification_method, verification_token_hash, verification_status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (author_name, json.dumps(variants) if variants else None,
             email, orcid, method, token_hash),
        )
        rid = cur.lastrowid
        _audit(conn, rid, "created", actor=email or orcid, detail=f"method={method}")
        conn.commit()
    return rid, raw_token


def verify_request_by_token(raw_token: str) -> int | None:
    """Mark the request owning this one-time email token as verified and burn
    the token. Returns the request id, or None if the token is unknown/spent."""
    from db import get_conn
    token_hash = _hash_token(raw_token)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM redaction_requests WHERE verification_token_hash = ?",
            (token_hash,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE redaction_requests SET verification_status = 'verified', "
            "verified_at = datetime('now'), verification_token_hash = NULL WHERE id = ?",
            (row["id"],),
        )
        _audit(conn, row["id"], "verified", detail="email one-time link")
        conn.commit()
        return row["id"]


def mark_request_verified(rid: int, detail: str = "orcid oauth") -> None:
    """Mark a request verified without a token (the ORCID OAuth path, P4)."""
    from db import get_conn
    with get_conn() as conn:
        conn.execute(
            "UPDATE redaction_requests SET verification_status = 'verified', "
            "verified_at = datetime('now') WHERE id = ?", (rid,),
        )
        _audit(conn, rid, "verified", detail=detail)
        conn.commit()


def _loose_name_match(claimed: str, verified: str) -> bool:
    """Advisory check for admin review: do the claimed byline and the
    ORCID-verified name share a surname? Deliberately loose — name forms vary —
    and never the sole gate (an admin always decides)."""
    if not claimed or not verified:
        return False
    cw = {w for w in _normalize(claimed).split() if len(w) > 1}
    vw = {w for w in _normalize(verified).split() if len(w) > 1}
    return bool(cw & vw)


def attach_orcid_verification(rid: int, orcid: str | None, verified_name: str | None) -> bool:
    """Record an ORCID-OAuth verification on a request: store the verified iD,
    mark the request verified, and audit the verified name + whether it loosely
    matches the claimed byline (a signal for the admin, not a hard gate).
    Returns the loose name-match result."""
    from db import get_conn
    req = get_request(rid)
    claimed = req["author_name_claimed"] if req else ""
    matches = _loose_name_match(claimed, verified_name or "")
    with get_conn() as conn:
        conn.execute(
            "UPDATE redaction_requests SET requester_orcid = COALESCE(?, requester_orcid), "
            "verification_status = 'verified', verified_at = datetime('now') WHERE id = ?",
            (orcid, rid),
        )
        _audit(conn, rid, "verified", actor=orcid,
               detail=f"orcid oauth; verified_name={verified_name!r}; name_match={matches}")
        conn.commit()
    return matches


def get_request(rid: int) -> dict | None:
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM redaction_requests WHERE id = ?", (rid,)
        ).fetchone()
        return dict(row) if row else None


def list_requests(status=None) -> list[dict]:
    from db import get_conn
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM redaction_requests WHERE verification_status = ? "
                "ORDER BY created_at DESC", (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM redaction_requests ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def get_audit(request_id: int) -> list[dict]:
    from db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM redaction_audit WHERE request_id = ? ORDER BY at", (request_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def decide_request(rid: int, decision: str, actor: str) -> dict | None:
    """Admin decision on a request. decision ∈ {'approved','denied'}. The audit
    row is written BEFORE any redaction fires, so proof-of-review survives even
    if the request is later purged. On approval, the claimed author is redacted
    (ledger + full sweep). Returns the request dict, or None if not found."""
    if decision not in ("approved", "denied"):
        raise ValueError("decision must be 'approved' or 'denied'")
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM redaction_requests WHERE id = ?", (rid,)
        ).fetchone()
        if not row:
            return None
        row = dict(row)
        _audit(conn, rid, decision, actor=actor)  # audit first
        conn.execute(
            "UPDATE redaction_requests SET verification_status = ?, "
            "decided_at = datetime('now'), decided_by = ? WHERE id = ?",
            (decision, actor, rid),
        )
        conn.commit()
    if decision == "approved":
        variants = json.loads(row["name_variants"]) if row["name_variants"] else None
        redact_author(row["author_name_claimed"], variants=variants,
                      request_id=rid, created_by=actor)
    return row


# ── CLI ──────────────────────────────────────────────────────────────────────

def _main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Pinakes author redaction tool.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_redact = sub.add_parser("redact", help="Redact an author by name.")
    p_redact.add_argument("name")
    p_redact.add_argument("--variant", action="append", default=[],
                          help="Additional name form to suppress (repeatable).")
    p_redact.add_argument("--by", default="cli", help="Who applied this (audit).")

    p_unredact = sub.add_parser("unredact", help="Reverse a redaction by token.")
    p_unredact.add_argument("token")

    sub.add_parser("list", help="List the redaction ledger.")
    sub.add_parser("resweep", help="Re-apply the whole ledger across all tables.")

    p_export = sub.add_parser("export-ledger", help="Dump the ledger to a JSON file (keep off-box).")
    p_export.add_argument("path")

    p_import = sub.add_parser("import-ledger", help="Merge ledger rows from a JSON file, then resweep.")
    p_import.add_argument("path")

    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.cmd == "redact":
        result = redact_author(args.name, variants=args.variant, created_by=args.by)
        print(f"Redacted '{args.name}' -> {result['token']}")
        print(f"  {result['stats']}")
    elif args.cmd == "unredact":
        result = unredact_author(args.token)
        print(result)
    elif args.cmd == "list":
        for row in list_redactions():
            print(f"{row['token']}  <-  {row['name']}  "
                  f"(variants={row['name_variants']}, at {row['redacted_at']})")
    elif args.cmd == "resweep":
        print(resweep_all())
    elif args.cmd == "export-ledger":
        import pathlib
        entries = export_ledger()
        pathlib.Path(args.path).write_text(json.dumps(entries, indent=2), encoding="utf-8")
        print(f"Exported {len(entries)} ledger entries to {args.path}")
    elif args.cmd == "import-ledger":
        import pathlib
        entries = json.loads(pathlib.Path(args.path).read_text(encoding="utf-8"))
        added = import_ledger(entries)
        print(f"Imported {added} new ledger entries from {args.path}; resweeping…")
        print(resweep_all())


if __name__ == "__main__":
    _main()
