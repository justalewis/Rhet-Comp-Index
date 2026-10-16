"""usage/refresh.py — download the reference data the resolver reads.

    dbip-city-lite.mmdb   DB-IP City Lite, monthly, ~60 MB compressed
    dbip-asn-lite.mmdb    DB-IP ASN Lite, monthly, ~5 MB compressed
    universities.json     Hipo world universities and domains, ~2 MB
    ipeds_hd.csv          IPEDS institutional characteristics, ~1 MB zipped

None needs an account. Each file is refreshed only when older than its
interval, so a weekly scheduled run downloads something only when there is
something new. Files are written to a temporary name and swapped in, so a
failed download never leaves a half-written database behind.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import io
import json
import logging
import os
import shutil
import zipfile

from . import store
from .resolve import JOB_LOCK

log = logging.getLogger(__name__)

DBIP_URL = "https://download.db-ip.com/free/dbip-{kind}-lite-{month}.mmdb.gz"
HIPO_URL = ("https://raw.githubusercontent.com/Hipo/university-domains-list/"
            "master/world_universities_and_domains.json")
IPEDS_URL = "https://nces.ed.gov/ipeds/datacenter/data/HD{year}.zip"

MAX_AGE_DAYS = {"dbip-city-lite.mmdb": 25, "dbip-asn-lite.mmdb": 25,
                "universities.json": 25, "ipeds_hd.csv": 120}


def _age_days(path: str, now: _dt.datetime) -> float | None:
    if not os.path.exists(path):
        return None
    mtime = _dt.datetime.fromtimestamp(os.path.getmtime(path), _dt.timezone.utc).replace(tzinfo=None)
    return (now - mtime).total_seconds() / 86400


def _fetch(session, url: str, timeout: float = 120.0):
    resp = session.get(url, timeout=timeout, stream=True)
    if resp.status_code != 200:
        resp.close()
        return None
    return resp


def _swap_in(tmp: str, final: str) -> None:
    try:
        os.replace(tmp, final)
    except PermissionError:
        # Windows refuses to replace a file another process has open. The
        # resolver closes its readers after each run, so this only happens if
        # someone else has the file open; leave the new copy for next time.
        log.warning("%s is in use; new copy left at %s", final, tmp)
        raise


def _dbip(session, kind: str, now: _dt.datetime, target: str) -> str:
    months = [now.strftime("%Y-%m"), (now.replace(day=1) - _dt.timedelta(days=1)).strftime("%Y-%m")]
    for month in months:
        url = DBIP_URL.format(kind=kind, month=month)
        resp = _fetch(session, url, timeout=300)
        if resp is None:
            continue
        tmp = target + ".tmp"
        try:
            with gzip.GzipFile(fileobj=resp.raw) as gz, open(tmp, "wb") as out:
                shutil.copyfileobj(gz, out, length=1 << 20)
        finally:
            resp.close()
        _check_mmdb(tmp)
        _swap_in(tmp, target)
        return url
    raise RuntimeError(f"no DB-IP {kind} file for {months}")


def _check_mmdb(path: str) -> None:
    import maxminddb
    reader = maxminddb.open_database(path)
    try:
        reader.get("8.8.8.8")
    finally:
        reader.close()


def _hipo(session, target: str) -> str:
    resp = _fetch(session, HIPO_URL)
    if resp is None:
        raise RuntimeError("Hipo list unavailable")
    try:
        data = json.loads(resp.content.decode("utf-8"))
    finally:
        resp.close()
    if not isinstance(data, list) or len(data) < 1000:
        raise RuntimeError("Hipo list looks wrong; keeping the old copy")
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    _swap_in(tmp, target)
    return HIPO_URL


def _ipeds(session, now: _dt.datetime, target: str) -> str:
    for year in range(now.year - 1, now.year - 4, -1):
        url = IPEDS_URL.format(year=year)
        resp = _fetch(session, url)
        if resp is None:
            continue
        try:
            payload = resp.content
        finally:
            resp.close()
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            # A revised release (hd2023_rv.csv) supersedes the original.
            members.sort(key=lambda n: ("_rv" not in n.lower(), n))
            if not members:
                continue
            raw = zf.read(members[0])
        if b"UNITID" not in raw[:200].upper():
            continue
        tmp = target + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(raw)
        _swap_in(tmp, target)
        return url
    raise RuntimeError("no IPEDS HD file found for the last three years")


def refresh(*, force: bool = False, now: _dt.datetime | None = None, session=None,
            only: set[str] | None = None) -> dict:
    """Download whatever is missing or stale. Returns {file: outcome}."""
    if not JOB_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "a usage job is already running"}
    try:
        now = now or store.utc_now()
        d = store.data_dir()
        os.makedirs(d, exist_ok=True)
        if session is None:
            from .netinfo import http_session
            session = http_session()
        jobs = {
            "dbip-city-lite.mmdb": lambda t: _dbip(session, "city", now, t),
            "dbip-asn-lite.mmdb": lambda t: _dbip(session, "asn", now, t),
            "universities.json": lambda t: _hipo(session, t),
            "ipeds_hd.csv": lambda t: _ipeds(session, now, t),
        }
        results, ok = {}, True
        conn = store.connect()
        try:
            for name, job in jobs.items():
                if only and name not in only:
                    continue
                target = os.path.join(d, name)
                age = _age_days(target, now)
                if not force and age is not None and age < MAX_AGE_DAYS[name]:
                    results[name] = f"current ({age:.0f} days old)"
                    continue
                try:
                    url = job(target)
                    size = os.path.getsize(target)
                    results[name] = f"downloaded {size / 1e6:.1f} MB"
                    with conn:
                        store.set_meta(conn, f"data:{name}", json.dumps({
                            "fetched_at": now.isoformat(timespec="seconds"),
                            "url": url, "bytes": size}))
                except Exception as e:  # noqa: BLE001
                    ok = False
                    results[name] = f"failed: {e}"
                    log.warning("usage data refresh: %s failed: %s", name, e)
            with conn:
                conn.execute("INSERT INTO runs (job, started_at, finished_at, ok, summary) "
                             "VALUES ('refresh', ?, ?, ?, ?)",
                             (now.isoformat(timespec="seconds"),
                              store.utc_now().isoformat(timespec="seconds"),
                              1 if ok else 0, json.dumps(results)))
        finally:
            conn.close()
        log.warning("usage data refresh: %s", json.dumps(results))
        return {"ok": ok, "files": results}
    finally:
        JOB_LOCK.release()


def data_status() -> dict:
    """What is on disk, for the dashboard's status panel."""
    d = store.data_dir()
    out = {}
    now = store.utc_now()
    for name in MAX_AGE_DAYS:
        path = os.path.join(d, name)
        age = _age_days(path, now)
        out[name] = None if age is None else {
            "age_days": round(age, 1), "mb": round(os.path.getsize(path) / 1e6, 1)}
    for extra in ("GeoLite2-City.mmdb", "GeoLite2-ASN.mmdb", "ipinfo_lite.mmdb"):
        path = os.path.join(d, extra)
        if os.path.exists(path):
            out[extra] = {"age_days": round(_age_days(path, now), 1),
                          "mb": round(os.path.getsize(path) / 1e6, 1)}
    return out
