"""usage/geo.py — IP address → country, region, city and network owner, read
from local MaxMind-format (.mmdb) files. No network calls.

Three free sources share the format, and whichever files are present in the
data directory are used:

    dbip-city-lite.mmdb, dbip-asn-lite.mmdb   DB-IP Lite (CC BY 4.0). No
                                              account needed; refresh.py
                                              downloads these by default.
    GeoLite2-City.mmdb, GeoLite2-ASN.mmdb     MaxMind GeoLite2 (free account
                                              and licence key).
    ipinfo_lite.mmdb                          IPinfo Lite (free token). Adds
                                              the network owner's domain.

DB-IP's licence requires attribution wherever its data is shown; the
dashboard and the report carry it (ATTRIBUTION below).
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

CITY_FILES = ("GeoLite2-City.mmdb", "dbip-city-lite.mmdb")
ASN_FILES = ("ipinfo_lite.mmdb", "GeoLite2-ASN.mmdb", "dbip-asn-lite.mmdb")

ATTRIBUTION = {
    "dbip": ('IP geolocation by <a href="https://db-ip.com">DB-IP</a>',
             "IP geolocation by DB-IP (db-ip.com), CC BY 4.0"),
    "maxmind": ('Includes GeoLite2 data created by MaxMind, available from '
                '<a href="https://www.maxmind.com">maxmind.com</a>',
                "Includes GeoLite2 data created by MaxMind (maxmind.com)"),
    "ipinfo": ('IP address data powered by <a href="https://ipinfo.io">IPinfo</a>',
               "IP address data powered by IPinfo (ipinfo.io)"),
}


def _source_of(filename: str) -> str:
    if filename.startswith("dbip"):
        return "dbip"
    if filename.startswith("GeoLite2"):
        return "maxmind"
    return "ipinfo"


def _name(node) -> str | None:
    if not isinstance(node, dict):
        return None
    names = node.get("names")
    if isinstance(names, dict):
        return names.get("en") or next(iter(names.values()), None)
    return None


class Geo:
    """Open readers for whatever databases exist. Use as a context manager so
    the files are released afterwards: on Windows an open .mmdb cannot be
    replaced by the monthly refresh."""

    def __init__(self, data_dir: str):
        self.city = None
        self.asn = None
        self.sources: list[str] = []
        try:
            import maxminddb
        except ImportError:
            log.warning("maxminddb is not installed; IP geolocation is off")
            return
        for attr, files in (("city", CITY_FILES), ("asn", ASN_FILES)):
            for fn in files:
                path = os.path.join(data_dir, fn)
                if os.path.exists(path):
                    try:
                        setattr(self, attr, maxminddb.open_database(path))
                        self.sources.append(_source_of(fn))
                        break
                    except Exception as e:  # noqa: BLE001
                        log.warning("could not open %s: %s", path, e)

    @property
    def available(self) -> bool:
        return self.city is not None or self.asn is not None

    def lookup(self, ip: str) -> dict:
        out = {"country": None, "region": None, "city": None,
               "asn": None, "as_org": None, "as_domain": None}
        rec = self._get(self.city, ip)
        if rec:
            country = rec.get("country") or rec.get("registered_country") or {}
            out["country"] = country.get("iso_code") if isinstance(country, dict) else None
            subs = rec.get("subdivisions")
            if isinstance(subs, list) and subs:
                out["region"] = _name(subs[0])
            out["city"] = _name(rec.get("city"))
        rec = self._get(self.asn, ip)
        if rec:
            # MaxMind / DB-IP field names.
            if rec.get("autonomous_system_number") is not None:
                out["asn"] = int(rec["autonomous_system_number"])
                out["as_org"] = rec.get("autonomous_system_organization")
            # IPinfo Lite field names.
            elif rec.get("asn"):
                try:
                    out["asn"] = int(str(rec["asn"]).upper().lstrip("AS"))
                except ValueError:
                    pass
                out["as_org"] = rec.get("as_name")
                out["as_domain"] = rec.get("as_domain")
                out["country"] = out["country"] or rec.get("country_code")
        return out

    @staticmethod
    def _get(reader, ip):
        if reader is None:
            return None
        try:
            rec = reader.get(ip)
        except (ValueError, TypeError):
            return None
        return rec if isinstance(rec, dict) else None

    def close(self) -> None:
        for r in (self.city, self.asn):
            if r is not None:
                try:
                    r.close()
                except Exception:  # noqa: BLE001
                    pass
        self.city = self.asn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
