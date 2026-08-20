"""alerts.py — saved-search email alerts.

The data spine of the email-digest feature: a subscription is an email address
plus a frozen copy of the index's own filter grammar. Nothing here knows how to
search; it stores a filter dict that `db._build_where` already understands, so
a subscriber's saved search and the same search on the website run through one
code path.

Canonicalization
----------------
Two visitors can reach the same result set by different routes — journals
listed in a different order, a stray capital in a tag, an empty `q=` left in
the URL by the form. `canonicalize_filters` flattens all of that to one dict,
and `filters_hash` turns that dict into the identity of the search. Paired with
the email in a UNIQUE index, the hash is what makes subscribing idempotent: a
double-submitted form cannot produce two subscriptions that both mail the
reader every week.

Tokens
------
Verification and unsubscribe links carry `secrets.token_urlsafe(32)`. The
verify token is stored as a sha256 and burned on use, following the redaction
request queue: a database read cannot forge a confirmation.

The unsubscribe token is stored in the clear and never burned. Every digest has
to regenerate the link, so it cannot be write-once, and an unsubscribe link
sitting in a two-year-old email has to keep working — a reader clicking it
should see "you are unsubscribed," never an error. See the v16 migration for
why the asymmetry is safe.

Why the watermark is an article id
----------------------------------
See `db.core._build_where`. Short version: pub_date lies about arrival and
fetched_at could be rewritten by a future backfill; the id cannot.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets

log = logging.getLogger(__name__)

# Cadences the signup form may request, mapped to the SQLite modifier used to
# decide whether a subscription is due.
CADENCES = {
    "weekly": "-7 days",
    "monthly": "-30 days",
}
DEFAULT_CADENCE = "weekly"

# Filter keys a subscription may carry. Deliberately a subset of the index's
# filter grammar: `page` is meaningless in a digest, and year_from/year_to are
# accepted because a subscriber may legitimately want "new work about the
# 1990s" as fresh scholarship on that period is indexed.
FILTER_KEYS = ("journal", "source", "q", "year_from", "year_to", "tag")

# Deliberately permissive. The purpose is to catch typos and obvious junk
# before we hand the address to an SMTP server, not to adjudicate RFC 5322 —
# over-strict local validation rejects real addresses and is a known way to
# lock people out of their own subscriptions.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

MAX_EMAIL_LEN = 254
MAX_SUBSCRIPTIONS_PER_EMAIL = 10


class TooManySubscriptions(Exception):
    """One address has hit MAX_SUBSCRIPTIONS_PER_EMAIL."""


def valid_email(addr) -> bool:
    addr = (addr or "").strip()
    return bool(addr) and len(addr) <= MAX_EMAIL_LEN and bool(_EMAIL_RE.match(addr))


def normalize_email(addr) -> str:
    """Lowercase and trim. Deliberately does NOT strip Gmail dots or +tags:
    those are the subscriber's business, and treating a+pinakes@ as a duplicate
    of a@ would silently merge two subscriptions somebody meant to keep apart.
    """
    return (addr or "").strip().lower()


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def canonicalize_filters(args) -> dict:
    """Request args (or a plain dict) → the canonical filter dict.

    Accepts anything with `getlist` (a Werkzeug MultiDict) or a plain mapping.
    Empty values are dropped rather than stored as "", so `?q=&tag=` and no
    query string at all produce the same subscription.
    """
    def _get_list(key):
        if hasattr(args, "getlist"):
            return args.getlist(key)
        value = args.get(key)
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    out = {}

    journals = sorted({j.strip() for j in _get_list("journal") if j and j.strip()})
    if journals:
        out["journal"] = journals

    for key in ("source", "q", "year_from", "year_to", "tag"):
        values = _get_list(key)
        value = (values[0] if values else "") or ""
        value = value.strip()
        if value:
            out[key] = value

    return out


def filters_hash(filters: dict) -> str:
    """Stable identity for a filter dict. sort_keys makes it order-independent;
    canonicalize_filters has already sorted the journal list."""
    return hashlib.sha256(
        json.dumps(filters, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def describe_filters(filters: dict) -> str:
    """One-line human summary, used as the email subject and shown back on the
    signup page so a subscriber can see what they are agreeing to."""
    if not filters:
        return "Everything Pinakes indexes"

    parts = []
    journals = filters.get("journal") or []
    if len(journals) == 1:
        parts.append(journals[0])
    elif len(journals) > 1:
        parts.append(f"{len(journals)} journals")

    if filters.get("q"):
        parts.append(f'matching "{filters["q"]}"')
    if filters.get("tag"):
        parts.append(f'tagged {filters["tag"]}')
    if filters.get("source"):
        parts.append(f'from {filters["source"]} sources')

    year_from, year_to = filters.get("year_from"), filters.get("year_to")
    if year_from and year_to:
        parts.append(f"published {year_from}–{year_to}")
    elif year_from:
        parts.append(f"published {year_from} or later")
    elif year_to:
        parts.append(f"published through {year_to}")

    return " · ".join(parts) if parts else "Everything Pinakes indexes"


def current_max_article_id() -> int:
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute("SELECT MAX(id) FROM articles").fetchone()
        return int(row[0] or 0)


def create_subscription(email, filters, cadence=DEFAULT_CADENCE, ip=None):
    """Create (or re-offer) a pending subscription.

    Returns (subscription_id, raw_verify_token, raw_unsub_token, already_active).

    Re-subscribing to a search you already have active does NOT reset the
    watermark or create a second row — it returns already_active=True and the
    caller says nothing different to the user, so the endpoint cannot be used
    to test whether an address is on the list.

    Re-subscribing to one you left, or one you never confirmed, mints a fresh
    verification token and reuses the row.
    """
    from db import get_conn

    email = normalize_email(email)
    filters = canonicalize_filters(filters)
    fhash = filters_hash(filters)
    cadence = cadence if cadence in CADENCES else DEFAULT_CADENCE

    raw_verify = secrets.token_urlsafe(32)
    raw_unsub = secrets.token_urlsafe(32)

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id, status FROM alert_subscriptions "
            "WHERE email = ? AND filters_hash = ?",
            (email, fhash),
        ).fetchone()

        if existing and existing["status"] == "active":
            return existing["id"], None, None, True

        if existing:
            conn.execute(
                "UPDATE alert_subscriptions SET status = 'pending', cadence = ?, "
                "verify_token_hash = ?, unsub_token = ?, "
                "created_at = datetime('now'), created_ip = ? WHERE id = ?",
                (cadence, _hash_token(raw_verify), raw_unsub, ip, existing["id"]),
            )
            conn.commit()
            return existing["id"], raw_verify, raw_unsub, False

        count = conn.execute(
            "SELECT COUNT(*) FROM alert_subscriptions WHERE email = ?", (email,)
        ).fetchone()[0]
        if count >= MAX_SUBSCRIPTIONS_PER_EMAIL:
            raise TooManySubscriptions(email)

        cur = conn.execute(
            "INSERT INTO alert_subscriptions "
            "(email, filters_json, filters_hash, label, cadence, status, "
            " verify_token_hash, unsub_token, created_ip) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
            (email, json.dumps(filters, sort_keys=True), fhash,
             describe_filters(filters), cadence,
             _hash_token(raw_verify), raw_unsub, ip),
        )
        conn.commit()
        return cur.lastrowid, raw_verify, raw_unsub, False


def verify_by_token(raw_token: str):
    """Burn a verification token, activate the subscription, and seed its
    watermark to the newest article that exists right now.

    The watermark seed is the whole reason this function does more than flip a
    status: without it the first digest would carry every article ever indexed.
    """
    from db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM alert_subscriptions WHERE verify_token_hash = ?",
            (_hash_token(raw_token),),
        ).fetchone()
        if not row:
            return None
        max_id = conn.execute("SELECT MAX(id) FROM articles").fetchone()[0] or 0
        conn.execute(
            "UPDATE alert_subscriptions SET status = 'active', "
            "verified_at = datetime('now'), verify_token_hash = NULL, "
            "watermark_id = ? WHERE id = ?",
            (int(max_id), row["id"]),
        )
        conn.commit()
        return row["id"]


def unsubscribe_by_token(raw_token: str):
    """Deactivate the subscription owning this token.

    Idempotent and non-burning: unsubscribe links outlive the subscription in
    people's mail archives, and a second click years later should still land on
    a calm confirmation page.
    """
    from db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, label FROM alert_subscriptions WHERE unsub_token = ?",
            (raw_token,),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE alert_subscriptions SET status = 'unsubscribed' WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
        return {"id": row["id"], "label": row["label"]}


def get_by_unsub_token(raw_token: str):
    from db import get_conn
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM alert_subscriptions WHERE unsub_token = ?",
            (raw_token,),
        ).fetchone()
        return dict(row) if row else None


def delete_by_unsub_token(raw_token: str) -> bool:
    """Hard-delete the subscription (the data-removal path on /alerts/manage).

    Its alert_sends rows go too: they exist to explain deliveries to the person
    who received them, so keeping them after that person asks to be forgotten
    would defeat the point.
    """
    from db import get_conn

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM alert_subscriptions WHERE unsub_token = ?",
            (raw_token,),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM alert_sends WHERE sub_id = ?", (row["id"],))
        conn.execute("DELETE FROM alert_subscriptions WHERE id = ?", (row["id"],))
        conn.commit()
        return True


def due_subscriptions(now_override=None):
    """Active subscriptions whose cadence window has elapsed.

    A never-sent subscription (last_sent_at IS NULL) is always due; it will
    still send nothing if no articles have arrived since its watermark, which
    is the common case in the first week.
    """
    from db import get_conn

    out = []
    with get_conn() as conn:
        for cadence, modifier in CADENCES.items():
            rows = conn.execute(
                "SELECT * FROM alert_subscriptions "
                "WHERE status = 'active' AND cadence = ? "
                "  AND (last_sent_at IS NULL OR last_sent_at <= datetime('now', ?)) "
                "ORDER BY id",
                (cadence, modifier),
            ).fetchall()
            out.extend(dict(r) for r in rows)
    return out


def subscription_filters(sub) -> dict:
    """Parse a row's stored filter JSON, tolerating corruption.

    A subscription whose JSON will not parse degrades to the unfiltered
    firehose rather than raising, because raising inside the digest loop would
    stop every later subscriber's mail. The log line is how it gets noticed.
    """
    try:
        return json.loads(sub["filters_json"]) or {}
    except (TypeError, ValueError):
        log.warning("Subscription #%s has unparseable filters_json; "
                    "treating as unfiltered.", sub.get("id"))
        return {}


def record_send(sub_id, article_count, max_id, ok=True):
    """Log a delivery and, if it succeeded, advance the watermark.

    A failed send deliberately leaves the watermark alone so the next run
    retries the same articles. Duplicates are recoverable; a silently skipped
    week is not.
    """
    from db import get_conn

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO alert_sends (sub_id, article_count, max_id, ok) "
            "VALUES (?, ?, ?, ?)",
            (sub_id, article_count, max_id, 1 if ok else 0),
        )
        if ok:
            conn.execute(
                "UPDATE alert_subscriptions SET watermark_id = ?, "
                "last_sent_at = datetime('now'), send_count = send_count + 1 "
                "WHERE id = ?",
                (max_id, sub_id),
            )
        conn.commit()


def purge_stale_pending(days=7) -> int:
    """Delete unconfirmed subscriptions older than `days`.

    Someone who never clicked the link did not consent, so the address should
    not sit in the table indefinitely. Also keeps a spammed signup form from
    accumulating a permanent list of other people's addresses.
    """
    from db import get_conn

    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM alert_subscriptions WHERE status = 'pending' "
            "AND created_at < datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        conn.commit()
        return cur.rowcount


def stats() -> dict:
    """Counts by status, for the admin surface and the digest run log."""
    from db import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM alert_subscriptions GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}
