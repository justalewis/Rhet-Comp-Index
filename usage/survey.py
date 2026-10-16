"""usage/survey.py — the optional "how you use Pinakes" form at /tell-us.

Server logs can only ever show a minimum: on-campus connections, counted as
visitor-days. The form fills the gap with what readers choose to say: where
they teach or study, how they use the index, and, if they agree, a note that
can be quoted in a grant application. Every field is optional, nothing is
tied to a visitor id or an address, and the form is off unless
PINAKES_USAGE_SURVEY is set.
"""

from __future__ import annotations

import json
import os

from . import store

ENABLE_ENV = "PINAKES_USAGE_SURVEY"

ROLES = {
    "faculty": "Faculty or instructor",
    "grad": "Graduate student",
    "undergrad": "Undergraduate student",
    "librarian": "Librarian",
    "admin": "Writing program or center administrator",
    "independent": "Independent scholar",
    "other": "Other",
}
USES = {
    "teaching": "Teaching a course",
    "research": "My own research",
    "assignment": "A student assignment or project",
    "instruction": "Library or research instruction",
    "program": "Program or curriculum work",
    "other": "Something else",
}
LIMITS = {"institution": 200, "course": 200, "note": 1500, "email": 254}


def enabled() -> bool:
    return os.environ.get(ENABLE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


class Invalid(ValueError):
    pass


def clean(form) -> dict:
    """Validate a submitted form. Raises Invalid with a message for the page."""
    from alerts import valid_email

    def text(name, limit):
        return " ".join((form.get(name) or "").split())[:limit] or None

    institution = text("institution", LIMITS["institution"])
    course = text("course", LIMITS["course"])
    note = (form.get("note") or "").strip()[:LIMITS["note"]] or None
    role = form.get("role") if form.get("role") in ROLES else None
    uses = [u for u in form.getlist("uses") if u in USES] if hasattr(form, "getlist") else []
    email = (form.get("email") or "").strip() or None
    if email and not valid_email(email):
        raise Invalid("That email address doesn't look complete. Leave it blank if you'd rather not give one.")
    if not any((institution, course, note, role, uses)):
        raise Invalid("There's nothing to send yet. Fill in at least one field.")
    return {
        "institution_text": institution, "role": role, "uses": uses, "course": course,
        "note": note, "quote_ok": 1 if (form.get("quote_ok") and note) else 0,
        "contact_email": email.lower() if email else None,
    }


def save(data: dict, came_from: str | None = None) -> int:
    conn = store.connect()
    try:
        with conn:
            return conn.execute(
                "INSERT INTO responses (ts, institution_text, role, uses, course, note, "
                "quote_ok, contact_email, came_from) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (store.utc_now().isoformat(timespec="seconds"), data["institution_text"],
                 data["role"], json.dumps(data["uses"]) if data["uses"] else None,
                 data["course"], data["note"], data["quote_ok"], data["contact_email"],
                 (came_from or "")[:200] or None),
            ).lastrowid
    finally:
        conn.close()


def _row(r) -> dict:
    d = dict(r)
    d["uses"] = json.loads(d["uses"]) if d.get("uses") else []
    d["role_label"] = ROLES.get(d.get("role") or "", "")
    d["use_labels"] = [USES[u] for u in d["uses"] if u in USES]
    return d


def list_responses(start: str, end: str) -> list[dict]:
    """Everything, contact addresses included. Admin only."""
    conn = store.connect()
    try:
        rows = conn.execute(
            "SELECT r.*, i.name AS institution_name, i.kind AS institution_kind "
            "FROM responses r LEFT JOIN institutions i ON i.id = r.institution_id "
            "WHERE substr(r.ts, 1, 10) BETWEEN ? AND ? ORDER BY r.ts DESC", (start, end)).fetchall()
    finally:
        conn.close()
    return [_row(r) for r in rows]


def delete(response_id: int) -> bool:
    conn = store.connect()
    try:
        with conn:
            return conn.execute("DELETE FROM responses WHERE id = ?", (response_id,)).rowcount > 0
    finally:
        conn.close()


def summarise(conn, start: str, end: str, *, kind_labels: dict) -> dict:
    """The aggregate half, safe for a public report: counts, the institutions
    respondents named, and the notes they agreed to have quoted. Never contact
    addresses or course names."""
    rows = [_row(r) for r in conn.execute(
        "SELECT r.*, i.name AS institution_name, i.kind AS institution_kind, i.carnegie, i.country "
        "FROM responses r LEFT JOIN institutions i ON i.id = r.institution_id "
        "WHERE substr(r.ts, 1, 10) BETWEEN ? AND ? ORDER BY r.ts", (start, end))]
    by_role, by_use, insts = {}, {}, {}
    for r in rows:
        if r["role_label"]:
            by_role[r["role_label"]] = by_role.get(r["role_label"], 0) + 1
        for u in r["use_labels"]:
            by_use[u] = by_use.get(u, 0) + 1
        name = r.get("institution_name") or r.get("institution_text")
        if name:
            key = name.strip().lower()
            slot = insts.setdefault(key, {"name": name, "responses": 0, "matched": bool(r.get("institution_name")),
                                          "kind": kind_labels.get(r.get("institution_kind") or "", ""),
                                          "carnegie": r.get("carnegie"), "country": r.get("country")})
            slot["responses"] += 1
    quotes = [{"note": r["note"], "role": r["role_label"],
               "institution": r.get("institution_name") or r.get("institution_text"),
               "date": r["ts"][:10]}
              for r in rows if r["quote_ok"] and r["note"]]
    return {
        "count": len(rows),
        "by_role": dict(sorted(by_role.items(), key=lambda kv: -kv[1])),
        "by_use": dict(sorted(by_use.items(), key=lambda kv: -kv[1])),
        "institutions": sorted(insts.values(), key=lambda x: (-x["responses"], x["name"])),
        "quotes": quotes,
        "contacts": sum(1 for r in rows if r.get("contact_email")),
    }
