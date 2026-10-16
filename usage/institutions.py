"""usage/institutions.py — identify an institution from a domain or a name.

Two local lists, both downloaded by refresh.py:

    universities.json   Hipo's world universities and domains list (MIT):
                        ~10,000 institutions worldwide with their domains.
    ipeds_hd.csv        IPEDS institutional characteristics (public domain):
                        every US postsecondary institution with its website,
                        sector, Carnegie classification, and HBCU and tribal
                        college status.

The ROR API (netinfo.ror_match) covers organisation names neither list knows.
Matching is deliberately conservative: a domain or name shared by more than
one institution matches none of them.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import sqlite3
import unicodedata

log = logging.getLogger(__name__)

HIPO_FILE = "universities.json"
IPEDS_FILE = "ipeds_hd.csv"

EDUCATION_KINDS = frozenset({"university", "college", "community-college", "k12"})

SECTORS = {
    1: "Public, 4-year or above",
    2: "Private nonprofit, 4-year or above",
    3: "Private for-profit, 4-year or above",
    4: "Public, 2-year",
    5: "Private nonprofit, 2-year",
    6: "Private for-profit, 2-year",
    7: "Public, less than 2-year",
    8: "Private nonprofit, less than 2-year",
    9: "Private for-profit, less than 2-year",
}


def carnegie_bucket(code) -> str | None:
    """Group a 2021 Carnegie basic classification code into the categories a
    funder asks about. Codes outside the published range return None."""
    try:
        c = int(code)
    except (TypeError, ValueError):
        return None
    if 1 <= c <= 14:
        return "Associate's / two-year"
    if c == 15:
        return "Doctoral: very high research (R1)"
    if c == 16:
        return "Doctoral: high research (R2)"
    if c == 17:
        return "Doctoral/professional"
    if 18 <= c <= 20:
        return "Master's"
    if 21 <= c <= 23:
        return "Baccalaureate"
    if 24 <= c <= 32:
        return "Special focus"
    if c == 33:
        return "Tribal college"
    return None


def norm_name(name: str | None) -> str:
    """Lowercase, accents and punctuation stripped, '&' as 'and', no leading
    'the'. 'The University of Washington' and 'UNIVERSITY OF WASHINGTON'
    normalise alike."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    return s


def domain_of(url_or_host: str | None) -> str | None:
    """'https://www.uw.edu/home' → 'uw.edu'."""
    if not url_or_host:
        return None
    s = url_or_host.strip().lower()
    s = re.sub(r"^[a-z]+://", "", s)
    s = s.split("/")[0].split("?")[0].split(":")[0].strip(".")
    if s.startswith("www."):
        s = s[4:]
    return s or None


_KIND_PATTERNS = [
    (re.compile(r"school district|public schools|\bk-?12\b|\bisd\b|unified school|high school|"
                r"board of education|secondary school", re.I), "k12"),
    (re.compile(r"community college|technical college|junior college|\bctc\b|"
                r"community and technical", re.I), "community-college"),
    (re.compile(r"universit|universidad|universidade|universite|universitat|"
                r"hochschule|\bunam\b", re.I), "university"),
    (re.compile(r"college|colegio|faculdade|facultad|polytechnic|politecnic|"
                r"institute of technology|instituto|seminary|conservatory|academy", re.I), "college"),
    (re.compile(r"library|librar|biblioteca", re.I), "library"),
    (re.compile(r"\bgovernment\b|\bstate of\b|\bcounty\b|\bcity of\b|department of|ministry|"
                r"ministerio|\bfederal\b|national laborator", re.I), "government"),
]


def kind_from_name(name: str | None) -> str | None:
    if not name:
        return None
    for pattern, kind in _KIND_PATTERNS:
        if pattern.search(name):
            return kind
    return None


def looks_institutional(name: str | None) -> bool:
    return kind_from_name(name) is not None


class Registry:
    """Domain and name indexes over the downloaded lists."""

    def __init__(self, data_dir: str | None):
        self.by_domain_idx: dict[str, dict | None] = {}
        self.by_name_idx: dict[str, list[dict]] = {}
        self.ipeds_count = 0
        self.hipo_count = 0
        if data_dir:
            self._load_ipeds(os.path.join(data_dir, IPEDS_FILE))
            self._load_hipo(os.path.join(data_dir, HIPO_FILE))

    # -- loading --------------------------------------------------------------

    def _add_domain(self, domain: str | None, rec: dict) -> None:
        if not domain:
            return
        existing = self.by_domain_idx.get(domain, "absent")
        if existing == "absent":
            self.by_domain_idx[domain] = rec
        elif existing is not None and existing["key"] != rec["key"]:
            # Two different institutions claim it (a state system's shared
            # site, say). IPEDS beats Hipo for the same place; otherwise give up.
            if existing.get("source") == "ipeds" and rec.get("source") == "hipo":
                return
            if existing.get("source") == "hipo" and rec.get("source") == "ipeds":
                self.by_domain_idx[domain] = rec
                return
            self.by_domain_idx[domain] = None

    def _add_name(self, name: str | None, rec: dict) -> None:
        n = norm_name(name)
        if len(n) < 4:
            return
        bucket = self.by_name_idx.setdefault(n, [])
        if all(r["key"] != rec["key"] for r in bucket):
            bucket.append(rec)

    def _load_ipeds(self, path: str) -> None:
        if not os.path.exists(path):
            return
        with open(path, "rb") as fh:
            raw = fh.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        carnegie_col = None
        for row in reader:
            if carnegie_col is None:
                cols = [c for c in row.keys() if c and re.fullmatch(r"C\d\dBASIC", c.strip().upper())]
                carnegie_col = sorted(cols)[-1] if cols else ""
            row = {(k or "").strip().upper(): (v or "").strip() for k, v in row.items()}
            try:
                unitid = int(row.get("UNITID", ""))
            except ValueError:
                continue
            name = row.get("INSTNM")
            if not name:
                continue
            try:
                sector = int(row.get("SECTOR") or 0)
            except ValueError:
                sector = 0
            if sector == 0:
                continue            # administrative units, not campuses
            carnegie = row.get(carnegie_col.strip().upper()) if carnegie_col else None
            bucket = carnegie_bucket(carnegie)
            # Washington's community colleges, among others, now award some
            # bachelor's degrees and so report as 4-year; Carnegie still files
            # them as associate's-dominant.
            two_year = (sector in (4, 5, 6, 7, 8, 9) or bucket == "Associate's / two-year"
                        or kind_from_name(name) == "community-college")
            if two_year:
                kind = "community-college" if sector in (1, 4, 7) else "college"
            else:
                kind = "university" if "univ" in name.lower() else "college"
            rec = {
                "key": f"ipeds:{unitid}", "name": name, "kind": kind,
                "country": "US", "region": row.get("STABBR") or None,
                "city": row.get("CITY") or None,
                "domain": domain_of(row.get("WEBADDR")), "ror_id": None,
                "ipeds_unitid": unitid, "carnegie": bucket,
                "sector": SECTORS.get(sector),
                "hbcu": 1 if row.get("HBCU") == "1" else 0,
                "tribal": 1 if row.get("TRIBAL") == "1" else 0,
                "source": "ipeds",
            }
            self._add_domain(rec["domain"], rec)
            self._add_name(name, rec)
            for alias in re.split(r"[|,;]", row.get("IALIAS") or ""):
                if len(alias.strip()) > 6:
                    self._add_name(alias, rec)
            self.ipeds_count += 1

    def _load_hipo(self, path: str) -> None:
        if not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            log.warning("could not read %s: %s", path, e)
            return
        for item in data:
            name = item.get("name")
            domains = [domain_of(d) for d in (item.get("domains") or []) if d]
            if not name or not domains:
                continue
            rec = {
                "key": f"domain:{domains[0]}", "name": name,
                "kind": "university" if "univ" in name.lower() else (kind_from_name(name) or "college"),
                "country": (item.get("alpha_two_code") or "").upper() or None,
                "region": item.get("state-province") or None, "city": None,
                "domain": domains[0], "ror_id": None, "ipeds_unitid": None,
                "carnegie": None, "sector": None, "hbcu": None, "tribal": None,
                "source": "hipo",
            }
            for d in domains:
                self._add_domain(d, rec)
            self._add_name(name, rec)
            self.hipo_count += 1

    # -- matching -------------------------------------------------------------

    def by_domain(self, host: str | None) -> dict | None:
        """Longest known suffix of a hostname: 'wifi-12.cs.uw.edu' → uw.edu."""
        host = domain_of(host)
        if not host:
            return None
        labels = host.split(".")
        for i in range(len(labels) - 1):
            rec = self.by_domain_idx.get(".".join(labels[i:]))
            if rec:
                return rec
        return None

    def by_name(self, name: str | None, country: str | None = None,
                region: str | None = None) -> dict | None:
        bucket = self.by_name_idx.get(norm_name(name))
        if not bucket:
            return None
        if len(bucket) == 1:
            return bucket[0]
        narrowed = [r for r in bucket if not country or r.get("country") == country]
        if region:
            narrowed = [r for r in narrowed if r.get("region") == region] or narrowed
        return narrowed[0] if len(narrowed) == 1 else None

    @property
    def loaded(self) -> bool:
        return bool(self.ipeds_count or self.hipo_count)


# ── The corpus link ──────────────────────────────────────────────────────────

def corpus_index() -> tuple[set[str], set[str]]:
    """ROR ids and normalised names of the institutions Pinakes already knows
    from its authors' affiliations. Read-only; an unreadable articles.db just
    means no visitor institution is marked as in the corpus."""
    import db as _db
    rors, names = set(), set()
    path = os.path.abspath(_db.DB_PATH)
    if not os.path.exists(path):
        return rors, names
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return rors, names
    try:
        for ror_id, display_name in conn.execute(
                "SELECT ror_id, display_name FROM institutions"):
            if ror_id:
                rors.add(ror_id.strip().lower().rstrip("/").split("/")[-1])
            if display_name:
                names.add(norm_name(display_name))
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return rors, names


def short_ror(ror_id: str | None) -> str | None:
    if not ror_id:
        return None
    return ror_id.strip().lower().rstrip("/").split("/")[-1]


def upsert(conn: sqlite3.Connection, rec: dict, corpus: tuple[set, set] | None = None,
           now: str | None = None) -> int:
    """Insert or fill in one institution; return its id. Existing values are
    kept where the new record has nothing to add."""
    rors, names = corpus or (set(), set())
    in_corpus = 1 if (short_ror(rec.get("ror_id")) in rors or norm_name(rec["name"]) in names) else 0
    fields = ("name", "kind", "country", "region", "city", "domain", "ror_id",
              "ipeds_unitid", "carnegie", "sector", "hbcu", "tribal", "source")
    conn.execute(
        f"INSERT INTO institutions (key, {', '.join(fields)}, in_corpus, updated_at) "
        f"VALUES (?, {', '.join('?' * len(fields))}, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET "
        + ", ".join(f"{f} = COALESCE(institutions.{f}, excluded.{f})" for f in fields)
        + ", in_corpus = MAX(institutions.in_corpus, excluded.in_corpus), updated_at = excluded.updated_at",
        (rec["key"], *[rec.get(f) for f in fields], in_corpus, now),
    )
    return conn.execute("SELECT id FROM institutions WHERE key = ?", (rec["key"],)).fetchone()[0]


def refresh_corpus_flags(conn: sqlite3.Connection, corpus: tuple[set, set]) -> int:
    rors, names = corpus
    changed = 0
    for row in conn.execute("SELECT id, name, ror_id, in_corpus FROM institutions").fetchall():
        flag = 1 if (short_ror(row["ror_id"]) in rors or norm_name(row["name"]) in names) else 0
        if flag != row["in_corpus"]:
            conn.execute("UPDATE institutions SET in_corpus = ? WHERE id = ?", (flag, row["id"]))
            changed += 1
    return changed
