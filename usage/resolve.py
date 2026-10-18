"""usage/resolve.py — the nightly job.

1. For every recorded address not yet resolved, work out its network (cached
   per /24 or /48): owner, class, and the institution behind it if one can be
   identified with confidence.
2. Copy those facts onto the hits and delete the raw address.
3. Rebuild visitor_days for the affected days, classing each visitor as a
   human, likely human, bot, feed reader or datacenter client.
4. Drop expired salts, purge any raw address older than the retention limit
   (a backstop for a run that died halfway), and age out the caches.

Identification order for a network, most to least specific:
    verified reverse DNS → network owner's domain (IPinfo) → address-registry
    registrant's email domains → registrant name → AS name → ROR's matcher →
    an institutional-looking registrant name with no registry entry.
Visitors who send Global Privacy Control or Do Not Track keep only country
and network class: no city, no network owner, no institution.
"""

from __future__ import annotations

import collections
import datetime as _dt
import json
import logging
import os
import threading

from . import store
from .geo import Geo
from .institutions import (Registry, corpus_index, domain_of, kind_from_name,
                           looks_institutional, norm_name, refresh_corpus_flags, upsert)
from .tools import is_auto
from .netinfo import (RateLimited, is_public, network_address, network_class, pause, prefix_of,
                      rdap_lookup, reverse_dns, ror_kind, ror_match)

log = logging.getLogger(__name__)

JOB_LOCK = threading.Lock()          # one resolve or refresh at a time
NETWORK_TTL_DAYS = 30
MAX_FULL_LOOKUPS = 1500              # networks given reverse DNS + RDAP per run
NOISE_CLASSES = ("bot", "unverified", "datacenter")
ORG_TTL_DAYS = 180
REBUILD_DAYS = 3


def prune_after_days() -> int:
    try:
        return max(1, int(os.environ.get("USAGE_PRUNE_DAYS", "7")))
    except ValueError:
        return 7


def prune_noise(conn, now: _dt.datetime) -> tuple[int, int]:
    """Delete the raw rows behind bot, unverified and datacenter visitor-days
    older than USAGE_PRUNE_DAYS (default 7). Their visitor_days rows, with the
    counts, stay; only the per-request detail goes. Feed readers are kept for
    their subscriber counts, and people's rows are never pruned.

    Safe because rebuild_days only revisits the last few days and days that
    still had unresolved rows, and this runs after the rebuild in the same
    job, so a pruned day is never rebuilt from what is left of it."""
    cutoff = (now.date() - _dt.timedelta(days=prune_after_days())).isoformat()
    marks = ",".join("?" * len(NOISE_CLASSES))
    hits = conn.execute(
        "DELETE FROM hits WHERE day < ? AND EXISTS (SELECT 1 FROM visitor_days v "
        f"WHERE v.day = hits.day AND v.visitor = hits.visitor AND v.class IN ({marks}))",
        (cutoff, *NOISE_CLASSES)).rowcount
    events = conn.execute(
        "DELETE FROM events WHERE day < ? AND EXISTS (SELECT 1 FROM visitor_days v "
        f"WHERE v.day = events.day AND v.visitor = events.visitor AND v.class IN ({marks}))",
        (cutoff, *NOISE_CLASSES)).rowcount
    return hits, events


def _light_network(info: dict, registry) -> dict:
    """Network facts from the local databases alone, for an address with no
    browser-confirmed visitor: owner and class, and no institution. The local
    lists can still say a network belongs to a university, which keeps the
    class right without a lookup."""
    rec = None
    if info.get("as_domain"):
        rec = registry.by_domain(info["as_domain"])
    if rec is None and info.get("as_org"):
        rec = registry.by_name(info["as_org"], info.get("country"))
    return {
        "asn": info.get("asn"), "as_org": info.get("as_org"),
        "net_class": network_class(as_org=info.get("as_org"), rdns=None, rdns_verified=False,
                                   rdap_org=None, institution_kind=rec.get("kind") if rec else None),
        "institution_id": None,
    }


def raw_ip_hours() -> int:
    try:
        return max(1, int(os.environ.get("USAGE_RAW_IP_HOURS", "48")))
    except ValueError:
        return 48


# ── Visitor classification ───────────────────────────────────────────────────

# A visitor-day with no browser event counts as a likely person only with
# evidence a crawler doesn't leave: pages spread over at least this long, or
# arrival from another site. Proxy-rotating crawlers send one or two requests
# per address within seconds and no referrer (2026-10-17: ~73,000 such
# visitor-days a day, against ~375 that ran the page's script).
LIKELY_MIN_PAGES = 2
LIKELY_MIN_SPAN_SECONDS = 30


def classify_visitor(v: dict) -> str:
    """feed-reader | bot | human | datacenter | likely-human | unverified."""
    if v.get("bot_kind") == "feed":
        return "feed-reader"
    if v.get("bot"):
        return "bot"
    if v.get("beacon"):
        return "human"
    if v.get("net_class") == "hosting":
        return "datacenter"
    if v.get("family") == "Other":
        return "bot"
    if v.get("peak_per_min", 0) > 40 or v.get("hits", 0) > 400:
        return "bot"
    if v.get("pages", 0) == 0:
        return "bot"            # called the API without ever loading a page
    if v.get("ref_host"):
        return "likely-human"   # followed a link from another site
    if v.get("pages", 0) >= LIKELY_MIN_PAGES and v.get("span_s", 0) >= LIKELY_MIN_SPAN_SECONDS:
        return "likely-human"   # browsed several pages over time
    return "unverified"


def _span_seconds(stamps: list[str]) -> float:
    if len(stamps) < 2:
        return 0.0
    try:
        return (_dt.datetime.fromisoformat(max(stamps)) -
                _dt.datetime.fromisoformat(min(stamps))).total_seconds()
    except ValueError:
        return 0.0


def _peak_per_minute(stamps: list[str]) -> int:
    secs = []
    for s in stamps:
        try:
            secs.append(_dt.datetime.fromisoformat(s).timestamp())
        except ValueError:
            continue
    secs.sort()
    best, lo = 0, 0
    for hi in range(len(secs)):
        while secs[hi] - secs[lo] >= 60:
            lo += 1
        best = max(best, hi - lo + 1)
    return best


def _mode(counter: collections.Counter):
    return counter.most_common(1)[0][0] if counter else None


def rebuild_days(conn, days) -> int:
    """Recompute visitor_days for each day given. Returns rows written."""
    written = 0
    for day in sorted(set(days)):
        agg: dict[str, dict] = {}

        def slot(visitor):
            if visitor not in agg:
                agg[visitor] = {
                    "hits": 0, "pages": 0, "tool_runs": 0, "tools": set(), "stamps": [],
                    "bot": None, "bot_kind": None, "family": None, "device": None, "gpc": 0,
                    "lang": None, "ref_host": None, "beacon": 0, "engaged_ms": 0,
                    "country": collections.Counter(), "region": collections.Counter(),
                    "city": collections.Counter(), "net_class": collections.Counter(),
                    "institution_id": collections.Counter(),
                }
            return agg[visitor]

        for r in conn.execute(
                "SELECT visitor, ts, is_api, tool, bot, bot_kind, ua_family, ua_device, "
                "lang, ref_host, country, cf_country, region, city, net_class, institution_id, gpc "
                "FROM hits WHERE day = ? ORDER BY ts", (day,)):
            v = slot(r["visitor"])
            v["hits"] += 1
            v["stamps"].append(r["ts"])
            if not r["is_api"] and r["tool"] != "feed":
                v["pages"] += 1
            # Tools that open by themselves (Explore's default tab, the author
            # page charts) say nothing about what the reader chose to use.
            if r["tool"] and r["tool"] != "typeahead" and not is_auto(r["tool"]):
                v["tools"].add(r["tool"])
                if r["is_api"] or r["tool"].startswith("export"):
                    v["tool_runs"] += 1
            v["bot"] = v["bot"] or r["bot"]
            v["bot_kind"] = v["bot_kind"] or r["bot_kind"]
            v["family"] = v["family"] or r["ua_family"]
            v["device"] = v["device"] or r["ua_device"]
            v["lang"] = v["lang"] or r["lang"]
            v["ref_host"] = v["ref_host"] or r["ref_host"]
            v["gpc"] = v["gpc"] or r["gpc"]
            country = r["country"] or r["cf_country"]
            for key, val in (("country", country), ("region", r["region"]), ("city", r["city"]),
                             ("net_class", r["net_class"]), ("institution_id", r["institution_id"])):
                if val is not None:
                    v[key][val] += 1

        for e in conn.execute("SELECT visitor, type, tool, value FROM events WHERE day = ?", (day,)):
            v = slot(e["visitor"])
            v["beacon"] = 1         # every event type needs a browser running our script
            if e["type"] == "nav" and e["tool"] and not is_auto(e["tool"]):
                v["tools"].add(e["tool"])
            elif e["type"] == "leave" and e["value"]:
                v["engaged_ms"] = min(v["engaged_ms"] + int(e["value"]), 8 * 3600 * 1000)

        rows = []
        for visitor, v in agg.items():
            flat = {
                "hits": v["hits"], "pages": v["pages"], "beacon": v["beacon"],
                "bot": v["bot"], "bot_kind": v["bot_kind"], "family": v["family"],
                "peak_per_min": _peak_per_minute(v["stamps"]),
                "net_class": _mode(v["net_class"]),
                "ref_host": v["ref_host"], "span_s": _span_seconds(v["stamps"]),
            }
            # A visitor seen only through browser events has a real browser.
            if v["hits"] == 0:
                flat["family"] = "browser"
            cls = classify_visitor(flat)
            if v["gpc"]:
                # Sent Global Privacy Control or Do Not Track at any point
                # that day: the visitor-day keeps country and network only.
                for key in ("region", "city", "institution_id"):
                    v[key] = collections.Counter()
            rows.append((
                day, visitor, cls, v["beacon"], v["hits"], v["pages"], v["tool_runs"],
                len(v["tools"]), flat["peak_per_min"], v["engaged_ms"],
                min(v["stamps"]) if v["stamps"] else None, max(v["stamps"]) if v["stamps"] else None,
                _mode(v["country"]), _mode(v["region"]), _mode(v["city"]), flat["net_class"],
                _mode(v["institution_id"]), v["device"], v["lang"], v["ref_host"], v["bot"],
            ))
        conn.execute("DELETE FROM visitor_days WHERE day = ?", (day,))
        conn.executemany(
            "INSERT INTO visitor_days (day, visitor, class, beacon, hits, pages, tool_runs, tools, "
            "peak_per_min, engaged_ms, first_ts, last_ts, country, region, city, net_class, "
            "institution_id, device, lang, ref_host, bot) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        written += len(rows)
    return written


# ── Network identification ───────────────────────────────────────────────────

class Lookups:
    """The network-facing calls, swappable in tests."""

    def __init__(self, *, enabled=True, max_rdap=250, max_ror=120,
                 rdap_pause=1.0, ror_pause=0.6):
        self.enabled = enabled
        self.max_rdap, self.max_ror = max_rdap, max_ror
        self.rdap_pause, self.ror_pause = rdap_pause, ror_pause
        self.rdap_used = self.ror_used = 0
        self.rdap_stopped = self.ror_stopped = False
        self._session = None
        self.ror_client_id = os.environ.get("ROR_CLIENT_ID") or None

    @property
    def session(self):
        if self._session is None:
            from .netinfo import http_session
            self._session = http_session()
        return self._session

    def rdns(self, ips):
        return reverse_dns(ips) if self.enabled else {ip: (None, False) for ip in ips}

    def rdap(self, ip):
        if not self.enabled or self.rdap_stopped or self.rdap_used >= self.max_rdap:
            return None
        self.rdap_used += 1
        try:
            result = rdap_lookup(ip, self.session)
        except RateLimited:
            log.warning("RDAP rate limit reached; stopping RDAP for this run")
            self.rdap_stopped = True
            return None
        pause(self.rdap_pause)
        return result

    def ror(self, name):
        if not self.enabled or self.ror_stopped or self.ror_used >= self.max_ror:
            return None, False
        self.ror_used += 1
        try:
            result = ror_match(name, self.session, self.ror_client_id)
        except RateLimited:
            log.warning("ROR rate limit reached; stopping ROR for this run")
            self.ror_stopped = True
            return None, False
        pause(self.ror_pause)
        return result, True


def _ror_cached(conn, lookups: Lookups, name: str, now_iso: str, ttl_cutoff: str):
    key = norm_name(name)
    if not key:
        return None
    row = conn.execute("SELECT ror_id, payload, checked_at FROM org_lookups WHERE name_norm = ?",
                       (key,)).fetchone()
    if row and row["checked_at"] >= ttl_cutoff:
        return json.loads(row["payload"]) if row["payload"] else None
    result, asked = lookups.ror(name)
    if asked:
        conn.execute(
            "INSERT INTO org_lookups (name_norm, ror_id, payload, checked_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name_norm) DO UPDATE SET ror_id = excluded.ror_id, "
            "payload = excluded.payload, checked_at = excluded.checked_at",
            (key, result.get("ror_id") if result else None,
             json.dumps(result) if result else None, now_iso))
    return result


def scrub_removed_names(conn) -> int:
    """Apply the author-removal ledger to rows already stored, so an author
    who asks to be removed after visitors looked them up disappears from
    usage.db too. Returns the number of rows changed."""
    from .capture import removed_names
    smap, normalise = removed_names()
    if not smap:
        return 0
    changed = 0
    for (obj,) in conn.execute("SELECT DISTINCT object FROM hits WHERE endpoint LIKE 'authors.%' "
                               "AND object IS NOT NULL").fetchall():
        token = smap.get(normalise(obj))
        if token and token != obj:
            changed += conn.execute("UPDATE hits SET object = ? WHERE object = ? AND endpoint LIKE 'authors.%'",
                                    (token, obj)).rowcount
    for (q,) in conn.execute("SELECT DISTINCT query FROM hits WHERE query IS NOT NULL").fetchall():
        nq = normalise(q)
        if any(name and name in nq for name in smap):
            changed += conn.execute("UPDATE hits SET query = NULL WHERE query = ?", (q,)).rowcount
    return changed


def _from_ror(ror: dict | None, registry: Registry) -> dict | None:
    """An institution record from a ROR match, joined to IPEDS when the
    organisation is in the US so it carries Carnegie class and sector."""
    if not ror or not ror.get("name") or not ror.get("ror_id"):
        return None
    domain = domain_of(ror.get("website")) or next(iter(ror.get("domains") or []), None)
    ipeds = None
    if ror.get("country") == "US":
        ipeds = registry.by_domain(domain) or registry.by_name(ror["name"], "US")
    if ipeds:
        return dict(ipeds, ror_id=ror["ror_id"])
    return {
        "key": "ror:" + ror["ror_id"].rstrip("/").split("/")[-1],
        "name": ror["name"], "kind": ror_kind(ror.get("types") or [], ror["name"]),
        "country": ror.get("country"), "region": ror.get("region"),
        "city": ror.get("city"), "domain": domain, "ror_id": ror["ror_id"],
        "ipeds_unitid": None, "carnegie": None, "sector": None,
        "hbcu": None, "tribal": None, "source": "ror",
    }


def match_responses(conn, *, registry: Registry, lookups: Lookups, corpus,
                    now_iso: str, org_cutoff: str) -> int:
    """Link the institutions readers typed into /tell-us to the same records
    the network resolver uses. Each response is tried once; one that matches
    nothing keeps its text and is still counted under that name."""
    matched = 0
    rows = conn.execute("SELECT id, institution_text FROM responses "
                        "WHERE matched_at IS NULL AND institution_text IS NOT NULL").fetchall()
    for r in rows:
        text = r["institution_text"].strip()
        rec = None
        if "." in text and " " not in text:          # someone typed a domain
            rec = registry.by_domain(text)
        rec = rec or registry.by_name(text)
        if not rec and looks_institutional(text):
            rec = _from_ror(_ror_cached(conn, lookups, text, now_iso, org_cutoff), registry)
        inst_id = upsert(conn, rec, corpus, now_iso) if rec else None
        conn.execute("UPDATE responses SET institution_id = ?, matched_at = ? WHERE id = ?",
                     (inst_id, now_iso, r["id"]))
        matched += 1 if inst_id else 0
    return matched


def identify(conn, *, registry: Registry, lookups: Lookups, geo_info: dict,
             rdns: str | None, rdns_verified: bool, rdap: dict | None,
             now_iso: str, org_cutoff: str) -> tuple[dict | None, str | None]:
    """The institution record for one network, and how it was found."""
    country = geo_info.get("country") or (rdap or {}).get("country")
    if rdns and rdns_verified:
        rec = registry.by_domain(rdns)
        if rec:
            return rec, "reverse-dns"
    if geo_info.get("as_domain"):
        rec = registry.by_domain(geo_info["as_domain"])
        if rec:
            return rec, "network-domain"
    rdap_org = (rdap or {}).get("org")
    for d in (rdap or {}).get("domains") or []:
        rec = registry.by_domain(d)
        if rec:
            return rec, "registry-email"
    for name, via in ((rdap_org, "registry-name"), (geo_info.get("as_org"), "network-name")):
        if name:
            rec = registry.by_name(name, country)
            if rec:
                return rec, via
    # ROR is asked about the address block's registered holder only, never
    # the network operator's (AS) name: operators include national telecoms
    # with institutional-sounding names (Instituto Costarricense de
    # Electricidad came back as a research facility).
    if rdap_org and looks_institutional(rdap_org):
        rec = _from_ror(_ror_cached(conn, lookups, rdap_org, now_iso, org_cutoff), registry)
        if rec:
            return rec, "ror"
    if rdap_org and looks_institutional(rdap_org):
        return {
            "key": "name:" + norm_name(rdap_org), "name": rdap_org.strip(),
            "kind": kind_from_name(rdap_org), "country": country, "region": None,
            "city": None, "domain": None, "ror_id": None, "ipeds_unitid": None,
            "carnegie": None, "sector": None, "hbcu": None, "tribal": None,
            "source": "registry",
        }, "registry-name-only"
    return None, None


# ── The job ──────────────────────────────────────────────────────────────────

def run(*, now: _dt.datetime | None = None, lookups: Lookups | None = None,
        geo: Geo | None = None, registry: Registry | None = None) -> dict:
    """Resolve, classify and purge. Returns a summary dict."""
    if not JOB_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "a usage job is already running"}
    try:
        return _run(now=now, lookups=lookups, geo=geo, registry=registry)
    finally:
        JOB_LOCK.release()


def _run(*, now, lookups, geo, registry) -> dict:
    now = now or store.utc_now()
    now_iso = now.isoformat(timespec="seconds")
    today = now.date().isoformat()
    net_cutoff = (now - _dt.timedelta(days=NETWORK_TTL_DAYS)).isoformat(timespec="seconds")
    org_cutoff = (now - _dt.timedelta(days=ORG_TTL_DAYS)).isoformat(timespec="seconds")
    ip_cutoff = (now - _dt.timedelta(hours=raw_ip_hours())).isoformat(timespec="seconds")
    lookups = lookups or Lookups()
    data_dir = store.data_dir()
    own_geo = geo is None
    geo = geo or Geo(data_dir)
    registry = registry or Registry(data_dir)
    corpus = corpus_index()
    summary = collections.Counter()

    conn = store.connect()
    run_id = None
    try:
        with conn:
            run_id = conn.execute("INSERT INTO runs (job, started_at) VALUES ('resolve', ?)",
                                  (now_iso,)).lastrowid
            summary["salts_dropped"] = store.drop_old_salts(conn, today)

        pending: list[str] = []
        hits_per_ip: collections.Counter = collections.Counter()
        for r in conn.execute("SELECT ip, COUNT(*) AS n FROM hits "
                              "WHERE resolved = 0 AND ip IS NOT NULL GROUP BY ip"):
            pending.append(r["ip"])
            hits_per_ip[r["ip"]] = r["n"]
        summary["addresses"] = len(pending)

        # Only networks with a browser-confirmed visitor get the full lookups
        # (reverse DNS, RDAP, ROR) and a row in the networks cache. Everything
        # else gets the local location database and nothing more: an
        # address-rotating crawler can bring 200,000 new addresses a day, and
        # neither the institution count nor anyone's DNS server needs them.
        confirmed = {r[0] for r in conn.execute(
            "SELECT DISTINCT h.ip FROM hits h WHERE h.resolved = 0 AND h.ip IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM events e WHERE e.day = h.day AND e.visitor = h.visitor)")}
        summary["confirmed_addresses"] = len(confirmed)
        by_prefix: dict[str, list[str]] = collections.defaultdict(list)
        for ip in confirmed:
            if is_public(ip):
                p = prefix_of(ip)
                if p:
                    by_prefix[p].append(ip)
        cached = {}
        for p in {prefix_of(ip) for ip in pending if is_public(ip)} - {None}:
            row = conn.execute("SELECT * FROM networks WHERE prefix = ? AND checked_at >= ?",
                               (p, net_cutoff)).fetchone()
            if row:
                cached[p] = dict(row)
        todo = sorted((p for p in by_prefix if p not in cached),
                      key=lambda p: -sum(hits_per_ip[ip] for ip in by_prefix[p]))
        summary["networks_skipped_over_cap"] = max(0, len(todo) - MAX_FULL_LOOKUPS)
        todo = todo[:MAX_FULL_LOOKUPS]
        reps = {p: by_prefix[p][0] for p in todo}
        ptr = lookups.rdns(list(reps.values()))
        summary["networks_cached"] = len(cached)
        summary["networks_new"] = len(todo)

        for p in todo:
            ip = reps[p]
            info = geo.lookup(ip)
            host, verified = ptr.get(ip, (None, False))
            rdap = None
            if not (info.get("as_org") and network_class(
                    as_org=info["as_org"], rdns=None, rdns_verified=False,
                    rdap_org=None, institution_kind=None) == "hosting"):
                rdap = lookups.rdap(network_address(p))
            with conn:
                rec, via = identify(conn, registry=registry, lookups=lookups, geo_info=info,
                                    rdns=host, rdns_verified=verified, rdap=rdap,
                                    now_iso=now_iso, org_cutoff=org_cutoff)
                inst_id = upsert(conn, rec, corpus, now_iso) if rec else None
                net = network_class(as_org=info.get("as_org"), rdns=host, rdns_verified=verified,
                                    rdap_org=(rdap or {}).get("org"),
                                    institution_kind=rec.get("kind") if rec else None)
                row = {
                    "prefix": p, "asn": info.get("asn"), "as_org": info.get("as_org"),
                    "as_domain": info.get("as_domain"), "rdns": host,
                    "rdns_verified": 1 if verified else 0,
                    "rdap_name": (rdap or {}).get("name"), "rdap_org": (rdap or {}).get("org"),
                    "net_class": net, "institution_id": inst_id, "match_via": via,
                    "checked_at": now_iso,
                }
                conn.execute(
                    "INSERT INTO networks (prefix, asn, as_org, as_domain, rdns, rdns_verified, "
                    "rdap_name, rdap_org, net_class, institution_id, match_via, checked_at) "
                    "VALUES (:prefix, :asn, :as_org, :as_domain, :rdns, :rdns_verified, :rdap_name, "
                    ":rdap_org, :net_class, :institution_id, :match_via, :checked_at) "
                    "ON CONFLICT(prefix) DO UPDATE SET asn=excluded.asn, as_org=excluded.as_org, "
                    "as_domain=excluded.as_domain, rdns=excluded.rdns, "
                    "rdns_verified=excluded.rdns_verified, rdap_name=excluded.rdap_name, "
                    "rdap_org=excluded.rdap_org, net_class=excluded.net_class, "
                    "institution_id=excluded.institution_id, match_via=excluded.match_via, "
                    "checked_at=excluded.checked_at", row)
            cached[p] = row
            if inst_id:
                summary["networks_identified"] += 1

        # Copy network facts onto the hits and drop the address. GPC and DNT
        # hits are updated separately, per hit rather than per address, so a
        # second browser on the same address keeps its own setting. They get
        # country and network class and nothing else: city and region are
        # cleared outright, not filled from Cloudflare's headers.
        days_touched = {r[0] for r in conn.execute(
            "SELECT DISTINCT day FROM hits WHERE resolved = 0")}
        with conn:
            for ip in pending:
                info = geo.lookup(ip) if is_public(ip) else {}
                net = cached.get(prefix_of(ip) or "", {}) if is_public(ip) else {}
                if is_public(ip) and not net:
                    net = _light_network(info, registry)
                net_class = net.get("net_class") or ("unknown" if is_public(ip) else "local")
                cur = conn.execute(
                    "UPDATE hits SET resolved = 1, ip = NULL, "
                    "country = COALESCE(?, cf_country), region = COALESCE(?, cf_region), "
                    "city = COALESCE(?, cf_city), asn = ?, as_org = ?, net_class = ?, "
                    "institution_id = ? WHERE ip = ? AND resolved = 0 AND gpc = 0",
                    (info.get("country"), info.get("region"), info.get("city"),
                     net.get("asn") or info.get("asn"), net.get("as_org") or info.get("as_org"),
                     net_class, net.get("institution_id"), ip))
                summary["hits_resolved"] += cur.rowcount
                cur = conn.execute(
                    "UPDATE hits SET resolved = 1, ip = NULL, country = COALESCE(?, cf_country), "
                    "region = NULL, city = NULL, cf_region = NULL, cf_city = NULL, asn = NULL, "
                    "as_org = NULL, net_class = ?, institution_id = NULL "
                    "WHERE ip = ? AND resolved = 0 AND gpc = 1",
                    (info.get("country"), net_class, ip))
                summary["hits_resolved"] += cur.rowcount
                summary["gpc_hits"] += cur.rowcount
            cur = conn.execute(
                "UPDATE hits SET resolved = 1, country = cf_country, "
                "region = CASE WHEN gpc = 1 THEN NULL ELSE cf_region END, "
                "city = CASE WHEN gpc = 1 THEN NULL ELSE cf_city END "
                "WHERE resolved = 0 AND ip IS NULL")
            summary["hits_resolved"] += cur.rowcount
            cur = conn.execute("UPDATE hits SET ip = NULL WHERE ip IS NOT NULL AND ts < ?",
                               (ip_cutoff,))
            summary["stale_addresses_purged"] = cur.rowcount

        recent = {(now.date() - _dt.timedelta(days=i)).isoformat() for i in range(REBUILD_DAYS)}
        with conn:
            summary["visitor_days"] = rebuild_days(conn, days_touched | recent)
            summary["responses_matched"] = match_responses(
                conn, registry=registry, lookups=lookups, corpus=corpus,
                now_iso=now_iso, org_cutoff=org_cutoff)
            summary["corpus_flags_changed"] = refresh_corpus_flags(conn, corpus)
            summary["removed_names_scrubbed"] = scrub_removed_names(conn)
            pruned_hits, pruned_events = prune_noise(conn, now)
            summary["pruned_hits"], summary["pruned_events"] = pruned_hits, pruned_events
            conn.execute("DELETE FROM networks WHERE checked_at < ?",
                         ((now - _dt.timedelta(days=NETWORK_TTL_DAYS * 3)).isoformat(),))
            conn.execute("DELETE FROM org_lookups WHERE checked_at < ?",
                         ((now - _dt.timedelta(days=ORG_TTL_DAYS * 2)).isoformat(),))
        # Fold the write-ahead log back into the file and empty it, so the
        # page versions that still held raw addresses don't linger in -wal.
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:  # noqa: BLE001 — a busy log is checkpointed next time
            pass

        summary["rdap_lookups"] = lookups.rdap_used
        summary["ror_lookups"] = lookups.ror_used
        result = {"ok": True, **dict(summary), "geo_sources": geo.sources,
                  "registry": {"ipeds": registry.ipeds_count, "hipo": registry.hipo_count}}
        with conn:
            conn.execute("UPDATE runs SET finished_at = ?, ok = 1, summary = ? WHERE id = ?",
                         (store.utc_now().isoformat(timespec="seconds"), json.dumps(result), run_id))
        log.warning("usage resolve finished: %s", json.dumps(result))
        return result
    except Exception as e:
        log.exception("usage resolve failed")
        try:
            with conn:
                conn.execute("UPDATE runs SET finished_at = ?, ok = 0, summary = ? WHERE id = ?",
                             (store.utc_now().isoformat(timespec="seconds"),
                              json.dumps({"error": str(e)}), run_id))
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()
        if own_geo:
            geo.close()
