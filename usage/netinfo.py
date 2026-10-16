"""usage/netinfo.py — what a network is: reverse DNS, the address registry's
record of who holds the block (RDAP), a ROR lookup for organisation names,
and the coarse class reports use (education, ISP, mobile, hosting, ...).

Everything here talks to the network, so everything has a timeout, failures
return None rather than raising, and resolve.py caches the answers per /24 so
a campus is looked up once a month, not once per visitor.
"""

from __future__ import annotations

import concurrent.futures as cf
import ipaddress
import logging
import re
import socket
import time

log = logging.getLogger(__name__)

USER_AGENT = "Pinakes usage resolver (+https://pinakes.wacclearinghouse.org/about)"
RDAP_URL = "https://rdap.org/ip/{ip}"
ROR_URL = "https://api.ror.org/v2/organizations"


def prefix_of(ip: str) -> str | None:
    """The /24 (IPv4) or /48 (IPv6) an address sits in."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    bits = 24 if addr.version == 4 else 48
    return str(ipaddress.ip_network(f"{addr}/{bits}", strict=False))


def network_address(prefix: str) -> str:
    """'128.95.10.0/24' → '128.95.10.0'. What RDAP is asked about, so the
    registry learns which network was looked up but never a visitor's own
    address; the record that comes back is the same block either way."""
    return str(ipaddress.ip_network(prefix, strict=False).network_address)


def is_public(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


# ── Reverse DNS ──────────────────────────────────────────────────────────────

def _ptr(ip: str) -> tuple[str | None, bool]:
    try:
        host = socket.gethostbyaddr(ip)[0].rstrip(".").lower()
    except (OSError, UnicodeError):
        return None, False
    # Forward-confirm: anyone can publish a PTR record claiming to be
    # somewhere.harvard.edu, but only the domain's owner can make that name
    # resolve back to the address.
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
    except (OSError, UnicodeError):
        return host, False
    return host, ip in addrs


def reverse_dns(ips: list[str], timeout: float = 6.0, workers: int = 16) -> dict:
    """{ip: (hostname or None, forward_confirmed)} for each address."""
    out = {ip: (None, False) for ip in ips}
    if not ips:
        return out
    pool = cf.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="usage-ptr")
    futures = {pool.submit(_ptr, ip): ip for ip in ips}
    try:
        for fut in cf.as_completed(futures, timeout=timeout + len(ips) * 0.05):
            try:
                out[futures[fut]] = fut.result()
            except Exception:  # noqa: BLE001
                pass
    except cf.TimeoutError:
        log.info("reverse DNS: %d lookups timed out", sum(1 for f in futures if not f.done()))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return out


# ── RDAP ─────────────────────────────────────────────────────────────────────

def _vcard(entity: dict) -> dict:
    out = {"fn": None, "org": None, "emails": []}
    arr = entity.get("vcardArray")
    if not (isinstance(arr, list) and len(arr) == 2 and isinstance(arr[1], list)):
        return out
    for prop in arr[1]:
        if not (isinstance(prop, list) and len(prop) >= 4):
            continue
        name, value = prop[0], prop[3]
        if name == "fn" and isinstance(value, str):
            out["fn"] = value.strip()
        elif name == "org":
            out["org"] = (value if isinstance(value, str) else " ".join(map(str, value))).strip()
        elif name == "email" and isinstance(value, str):
            out["emails"].append(value.strip().lower())
    return out


def _walk_entities(entities, depth=0):
    for ent in entities or []:
        if not isinstance(ent, dict):
            continue
        yield ent
        if depth < 3:
            yield from _walk_entities(ent.get("entities"), depth + 1)


def parse_rdap(data: dict) -> dict:
    """The parts of an RDAP IP-network response that say who holds it."""
    out = {"name": data.get("name"), "org": None, "domains": [], "country": data.get("country")}
    registrant = None
    for ent in _walk_entities(data.get("entities")):
        roles = ent.get("roles") or []
        if "registrant" in roles:
            registrant = ent
            break
    if registrant is None:
        for ent in _walk_entities(data.get("entities")):
            if "administrative" in (ent.get("roles") or []):
                registrant = ent
                break
    if registrant is not None:
        card = _vcard(registrant)
        out["org"] = card["org"] or card["fn"]
        out["domains"] = sorted({e.split("@", 1)[1] for e in card["emails"] if "@" in e})
    return out


def rdap_lookup(ip: str, session, timeout: float = 12.0) -> dict | None:
    """Who holds the block containing `ip`, via rdap.org's redirect to the
    right regional registry. None on any failure; raises RateLimited on 429
    so the caller can stop for the night."""
    try:
        resp = session.get(RDAP_URL.format(ip=ip), timeout=timeout,
                           headers={"Accept": "application/rdap+json, application/json"})
    except Exception as e:  # noqa: BLE001
        log.info("RDAP %s failed: %s", ip, e)
        return None
    if resp.status_code == 429:
        raise RateLimited("rdap")
    if resp.status_code != 200:
        return None
    try:
        return parse_rdap(resp.json())
    except ValueError:
        return None


class RateLimited(Exception):
    pass


# ── ROR ──────────────────────────────────────────────────────────────────────

def parse_ror_org(org: dict) -> dict:
    names = org.get("names") or []
    display = next((n.get("value") for n in names if "ror_display" in (n.get("types") or [])), None)
    display = display or (names[0].get("value") if names else None)
    loc = (org.get("locations") or [{}])[0].get("geonames_details") or {}
    website = next((link.get("value") for link in (org.get("links") or [])
                    if link.get("type") == "website"), None)
    domains = org.get("domains") or []
    return {
        "ror_id": org.get("id"), "name": display, "types": org.get("types") or [],
        "country": loc.get("country_code"),
        "region": loc.get("country_subdivision_code") or loc.get("country_subdivision_name"),
        "city": loc.get("name"), "website": website, "domains": domains,
    }


def ror_match(name: str, session, client_id: str | None = None,
              timeout: float = 12.0) -> dict | None:
    """ROR's affiliation matcher. Returns the organisation ROR itself marks as
    the chosen match, or None. Raises RateLimited on 429."""
    headers = {"Accept": "application/json"}
    if client_id:
        headers["Client-Id"] = client_id
    try:
        resp = session.get(ROR_URL, params={"affiliation": name}, timeout=timeout, headers=headers)
    except Exception as e:  # noqa: BLE001
        log.info("ROR lookup for %r failed: %s", name, e)
        return None
    if resp.status_code == 429:
        raise RateLimited("ror")
    if resp.status_code != 200:
        return None
    try:
        items = resp.json().get("items") or []
    except ValueError:
        return None
    for item in items:
        if item.get("chosen") and isinstance(item.get("organization"), dict):
            return parse_ror_org(item["organization"])
    return None


def ror_kind(types: list[str], name: str | None) -> str:
    from .institutions import kind_from_name
    if "education" in types:
        return kind_from_name(name) if kind_from_name(name) in (
            "university", "college", "community-college", "k12") else "university"
    for t, kind in (("government", "government"), ("healthcare", "healthcare"),
                    ("company", "company"), ("archive", "library"),
                    ("facility", "research"), ("funder", "nonprofit"),
                    ("nonprofit", "nonprofit")):
        if t in types:
            return kind
    return kind_from_name(name) or "other"


# ── Classes ──────────────────────────────────────────────────────────────────

_HOSTING = re.compile(
    r"amazon|aws|google cloud|google llc|microsoft|azure|digitalocean|linode|akamai|"
    r"ovh|hetzner|alibaba|aliyun|tencent|oracle|contabo|vultr|choopa|scaleway|leaseweb|"
    r"m247|datacamp|cdn77|hostinger|hostwinds|ionos|godaddy|rackspace|softlayer|ibm cloud|"
    r"cloudflare|fastly|psychz|colocrossing|quadranet|zenlayer|gcore|g-core|servers\.com|"
    r"hosting|data ?cent(?:er|re)|server|vps|cloud",
    re.I,
)
# Only names that say so outright. Most large carriers (Claro, Movistar, Vivo)
# run fixed broadband on the same networks, so guessing from the brand would
# mislabel home connections.
_MOBILE = re.compile(r"mobile|mobility|wireless|cellular|t-mobile", re.I)
_EDU_HOST = re.compile(r"(\.edu|\.ac\.[a-z]{2}|\.edu\.[a-z]{2}|\.k12\.[a-z]{2}\.us)$", re.I)
_GOV_HOST = re.compile(r"(\.gov|\.mil|\.gov\.[a-z]{2}|\.gob\.[a-z]{2}|\.gouv\.[a-z]{2})$", re.I)


def network_class(*, as_org: str | None, rdns: str | None, rdns_verified: bool,
                  rdap_org: str | None, institution_kind: str | None) -> str:
    """education | government | organization | hosting | mobile | isp | unknown."""
    from .institutions import EDUCATION_KINDS, kind_from_name
    if institution_kind in EDUCATION_KINDS:
        return "education"
    host = rdns if rdns_verified else None
    if host and _EDU_HOST.search(host):
        return "education"
    if host and _GOV_HOST.search(host):
        return "government"
    if institution_kind == "government" or kind_from_name(rdap_org) == "government":
        return "government"
    if institution_kind in ("company", "nonprofit", "healthcare", "research", "library"):
        return "organization"
    if as_org and _HOSTING.search(as_org):
        return "hosting"
    if as_org and _MOBILE.search(as_org):
        return "mobile"
    if as_org or rdap_org:
        return "isp"
    return "unknown"


def http_session():
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def pause(seconds: float) -> None:
    time.sleep(seconds)
