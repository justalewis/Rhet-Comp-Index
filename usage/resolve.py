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
from .netinfo import (RateLimited, is_public, network_class, pause, prefix_of,
                      rdap_lookup, reverse_dns, ror_kind, ror_match)

log = logging.getLogger(__name__)

JOB_LOCK = threading.Lock()          # one resolve or refresh at a time
NETWORK_TTL_DAYS = 30
ORG_TTL_DAYS = 180
REBUILD_DAYS = 3


def raw_ip_hours() -> int:
    try:
        return max(1, int(os.environ.get("USAGE_RAW_IP_HOURS", "48")))
    except ValueError:
        return 48


# ── Visitor classification ───────────────────────────────────────────────────

def classify_visitor(v: dict) -> str:
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
    return "likely-human"


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
                    "bot": None, "bot_kind": None, "family": None, "device": None,
                    "lang": None, "ref_host": None, "beacon": 0, "engaged_ms": 0,
                    "country": collections.Counter(), "region": collections.Counter(),
                    "city": collections.Counter(), "net_class": collections.Counter(),
                    "institution_id": collections.Counter(),
                }
            return agg[visitor]

        for r in conn.execute(
                "SELECT visitor, ts, is_api, tool, bot, bot_kind, ua_family, ua_device, "
                "lang, ref_host, country, cf_country, region, city, net_class, institution_id "
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
            }
            # A visitor seen only through browser events has a real browser.
            if v["hits"] == 0:
                flat["family"] = "browser"
            cls = classify_visitor(flat)
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
    for name in (rdap_org, geo_info.get("as_org")):
        if name and looks_institutional(name):
            ror = _ror_cached(conn, lookups, name, now_iso, org_cutoff)
            if ror and ror.get("name"):
                domain = domain_of(ror.get("website")) or next(iter(ror.get("domains") or []), None)
                ipeds = None
                if ror.get("country") == "US":
                    ipeds = registry.by_domain(domain) or registry.by_name(ror["name"], "US")
                if ipeds:
                    return dict(ipeds, ror_id=ror["ror_id"]), "ror"
                return {
                    "key": "ror:" + ror["ror_id"].rstrip("/").split("/")[-1],
                    "name": ror["name"], "kind": ror_kind(ror.get("types") or [], ror["name"]),
                    "country": ror.get("country"), "region": ror.get("region"),
                    "city": ror.get("city"), "domain": domain, "ror_id": ror["ror_id"],
                    "ipeds_unitid": None, "carnegie": None, "sector": None,
                    "hbcu": None, "tribal": None, "source": "ror",
                }, "ror"
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

        pending: dict[str, int] = {}
        hits_per_ip: collections.Counter = collections.Counter()
        for r in conn.execute("SELECT ip, MAX(gpc) AS gpc, COUNT(*) AS n FROM hits "
                              "WHERE resolved = 0 AND ip IS NOT NULL GROUP BY ip"):
            pending[r["ip"]] = r["gpc"]
            hits_per_ip[r["ip"]] = r["n"]
        summary["addresses"] = len(pending)

        # One representative address per uncached public network, busiest first.
        by_prefix: dict[str, list[str]] = collections.defaultdict(list)
        for ip in pending:
            if is_public(ip):
                p = prefix_of(ip)
                if p:
                    by_prefix[p].append(ip)
        cached = {}
        for p in by_prefix:
            row = conn.execute("SELECT * FROM networks WHERE prefix = ? AND checked_at >= ?",
                               (p, net_cutoff)).fetchone()
            if row:
                cached[p] = dict(row)
        todo = sorted((p for p in by_prefix if p not in cached),
                      key=lambda p: -sum(hits_per_ip[ip] for ip in by_prefix[p]))
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
                rdap = lookups.rdap(ip)
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

        # Copy network facts onto the hits and drop the address.
        days_touched = set()
        with conn:
            for ip, gpc in pending.items():
                info = geo.lookup(ip) if is_public(ip) else {}
                net = cached.get(prefix_of(ip) or "", {}) if is_public(ip) else {}
                if gpc:
                    vals = (info.get("country"), None, None, None, None,
                            net.get("net_class") or "unknown", None)
                    summary["gpc_addresses"] += 1
                else:
                    vals = (info.get("country"), info.get("region"), info.get("city"),
                            net.get("asn") or info.get("asn"), net.get("as_org") or info.get("as_org"),
                            net.get("net_class") or ("unknown" if is_public(ip) else "local"),
                            net.get("institution_id"))
                for (d,) in conn.execute("SELECT DISTINCT day FROM hits WHERE ip = ? AND resolved = 0",
                                         (ip,)).fetchall():
                    days_touched.add(d)
                cur = conn.execute(
                    "UPDATE hits SET resolved = 1, ip = NULL, "
                    "country = COALESCE(?, cf_country), region = COALESCE(?, cf_region), "
                    "city = COALESCE(?, cf_city), asn = ?, as_org = ?, net_class = ?, "
                    "institution_id = ? WHERE ip = ? AND resolved = 0",
                    (*vals, ip))
                summary["hits_resolved"] += cur.rowcount
            cur = conn.execute("UPDATE hits SET resolved = 1, country = cf_country, "
                               "region = cf_region, city = cf_city "
                               "WHERE resolved = 0 AND ip IS NULL")
            summary["hits_resolved"] += cur.rowcount
            cur = conn.execute("UPDATE hits SET ip = NULL WHERE ip IS NOT NULL AND ts < ?",
                               (ip_cutoff,))
            summary["stale_addresses_purged"] = cur.rowcount

        recent = {(now.date() - _dt.timedelta(days=i)).isoformat() for i in range(REBUILD_DAYS)}
        with conn:
            summary["visitor_days"] = rebuild_days(conn, days_touched | recent)
            summary["corpus_flags_changed"] = refresh_corpus_flags(conn, corpus)
            conn.execute("DELETE FROM networks WHERE checked_at < ?",
                         ((now - _dt.timedelta(days=NETWORK_TTL_DAYS * 3)).isoformat(),))
            conn.execute("DELETE FROM org_lookups WHERE checked_at < ?",
                         ((now - _dt.timedelta(days=ORG_TTL_DAYS * 2)).isoformat(),))

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
