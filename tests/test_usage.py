"""Usage statistics: request capture, browser events, the nightly resolver,
the reports, and the admin endpoints. Network lookups are replaced with
fakes; nothing here touches the internet."""

import csv
import datetime as dt
import io
import json
import sqlite3
import zipfile

import pytest

from usage import beacon, capture, institutions, netinfo, report, resolve, store, tools, ua
from usage.geo import Geo

TOKEN = "real-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CHROME = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


@pytest.fixture
def tracking(monkeypatch, client):
    """Recording on, written synchronously, the visitor IP read from
    CF-Connecting-IP as on the Clearinghouse box."""
    monkeypatch.setenv("PINAKES_USAGE_TRACKING", "1")
    monkeypatch.setenv("PINAKES_USAGE_SYNC", "1")
    monkeypatch.setenv("PINAKES_TRUSTED_IP_HEADER", "CF-Connecting-IP")
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", TOKEN)
    return client


def visit(client, path, ip="81.2.69.160", ua_string=CHROME, **headers):
    h = {"CF-Connecting-IP": ip, "User-Agent": ua_string, **headers}
    return client.get(path, headers=h)


def rows(sql, *args):
    conn = store.connect()
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


# ── Capture ──────────────────────────────────────────────────────────────────

def test_nothing_recorded_when_tracking_is_off(client, monkeypatch):
    monkeypatch.delenv("PINAKES_USAGE_TRACKING", raising=False)
    monkeypatch.setenv("PINAKES_USAGE_SYNC", "1")
    client.get("/about")
    assert rows("SELECT * FROM hits") == []


def test_article_page_is_recorded(tracking):
    assert visit(tracking, "/article/1").status_code == 200
    (hit,) = rows("SELECT * FROM hits")
    assert hit["endpoint"] == "articles.article_detail"
    assert hit["area"] == "record" and hit["object"] == "1" and hit["is_api"] == 0
    assert hit["ip"] == "81.2.69.160"
    assert len(hit["visitor"]) == 16
    assert (hit["ua_family"], hit["ua_os"], hit["ua_device"]) == ("Chrome", "Windows", "desktop")
    assert hit["bot"] is None and hit["resolved"] == 0


def test_tool_api_call_is_recorded_as_that_tool(tracking):
    visit(tracking, "/api/citations/main-path")
    (hit,) = rows("SELECT tool, area, is_api FROM hits")
    assert hit == {"tool": "main-path", "area": "explore", "is_api": 1}


def test_static_health_and_admin_are_not_recorded(tracking):
    for path in ("/static/style.css", "/health", "/robots.txt", "/admin/usage",
                 "/api/admin/client-ip", "/api/admin/user-tags"):
        visit(tracking, path, Authorization=AUTH["Authorization"]).close()
    assert rows("SELECT endpoint FROM hits") == []


def test_tokens_and_contact_details_are_never_stored(tracking):
    visit(tracking, "/?q=writing+centers&journal=CCC&email=a@b.c&code=xyz&_cb=1")
    visit(tracking, "/alerts/unsubscribe/secret-token-value")
    hits = rows("SELECT endpoint, area, params, query FROM hits")
    assert len(hits) == 1
    assert hits[0]["area"] == "search" and hits[0]["query"] == "writing centers"
    assert json.loads(hits[0]["params"]) == {"journal": "CCC"}
    conn = sqlite3.connect(store.usage_db_path())
    try:
        dump = "\n".join(conn.iterdump())
    finally:
        conn.close()
    assert "secret-token-value" not in dump and "a@b.c" not in dump and "xyz" not in dump


def test_external_and_internal_referrers(tracking):
    visit(tracking, "/about", Referer="https://olympic.instructure.com/courses/1")
    visit(tracking, "/glossary", Referer="https://pinakes.wacclearinghouse.org/about?x=1")
    first, second = rows("SELECT ref_host, ref_path FROM hits ORDER BY id")
    assert first == {"ref_host": "olympic.instructure.com", "ref_path": None}
    assert second == {"ref_host": None, "ref_path": "/about"}


def test_gpc_is_flagged(tracking):
    visit(tracking, "/about", **{"Sec-GPC": "1"})
    assert rows("SELECT gpc FROM hits") == [{"gpc": 1}]


def test_visitor_id_is_stable_within_a_day_and_changes_across_days(app, monkeypatch, tracking):
    days = iter([dt.datetime(2026, 10, 1, 9), dt.datetime(2026, 10, 1, 17), dt.datetime(2026, 10, 2, 9)])
    monkeypatch.setattr(store, "utc_now", lambda: next(days))
    ids = []
    for _ in range(3):
        with app.test_request_context(headers={"User-Agent": CHROME}):
            ids.append(capture.client_context()["visitor"])
    assert ids[0] == ids[1] != ids[2]


def test_old_salts_are_dropped(tracking):
    store.salt_for("2026-01-01")
    store.salt_for("2026-01-05")
    conn = store.connect()
    try:
        store.drop_old_salts(conn, "2026-01-05")
        conn.commit()
        left = [r[0] for r in conn.execute("SELECT day FROM salts")]
    finally:
        conn.close()
    assert left == ["2026-01-05"]


# ── Browser events ───────────────────────────────────────────────────────────

def post_event(client, body, ip="81.2.69.160"):
    return client.post("/api/e", data=json.dumps(body), content_type="application/json",
                       headers={"CF-Connecting-IP": ip, "User-Agent": CHROME})


def test_events_are_validated_and_mapped(tracking):
    assert post_event(tracking, {"t": "view", "p": "/explore", "pv": "abc123"}).status_code == 204
    post_event(tracking, {"t": "nav", "p": "/explore", "h": "main-path", "pv": "abc123"})
    post_event(tracking, {"t": "nav", "p": "/explore", "h": "citnet"})
    post_event(tracking, {"t": "out", "p": "/article/1", "x": "doi.org/10.1234/ABC.5"})
    post_event(tracking, {"t": "dl", "p": "/explore", "x": "CSV"})
    post_event(tracking, {"t": "leave", "p": "/explore", "v": 4200})
    post_event(tracking, {"t": "nav", "p": "/datastories/tools", "h": "ch5-long-tail"})
    # Rejected: unknown type, a path that isn't Pinakes, an oversized body.
    post_event(tracking, {"t": "evil", "p": "/explore"})
    post_event(tracking, {"t": "view", "p": "https://elsewhere.example/"})
    assert tracking.post("/api/e", data="x" * 5000).status_code == 204
    events = rows("SELECT type, area, tool, target, value, pv FROM events ORDER BY id")
    assert [e["type"] for e in events] == ["view", "nav", "nav", "out", "dl", "leave", "nav"]
    assert events[1]["tool"] == "main-path" and events[1]["pv"] == "abc123"
    assert events[2]["tool"] == "citation-network"
    assert events[3]["target"] == "doi:10.1234/abc.5"
    assert events[4]["target"] == "csv"
    assert events[5]["value"] == 4200
    assert events[6]["area"] == "datastories" and events[6]["tool"] == "ch5-long-tail"


def test_event_shares_the_page_requests_visitor_id(tracking):
    visit(tracking, "/explore")
    post_event(tracking, {"t": "view", "p": "/explore"})
    (hit,) = rows("SELECT visitor FROM hits")
    (event,) = rows("SELECT visitor FROM events")
    assert hit["visitor"] == event["visitor"]


# ── Parsing and mapping ──────────────────────────────────────────────────────

@pytest.mark.parametrize("ua_string,family,device,bot,kind,subs", [
    (CHROME, "Chrome", "desktop", None, None, None),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", "Safari", "mobile", None, None, None),
    ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "Other", "desktop", "Google", "search", None),
    ("Feedly/1.0 (+http://www.feedly.com/fetcher.html; 16 subscribers; )", "Other", "desktop", "Feedly", "feed", 16),
    ("curl/8.4.0", "Other", "desktop", "curl", "script", None),
    ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)",
     "Other", "desktop", "ClaudeBot", "ai", None),
    ("", "Other", "desktop", "No user agent", "script", None),
])
def test_user_agent_parsing(ua_string, family, device, bot, kind, subs):
    p = ua.parse(ua_string)
    assert (p["family"], p["device"], p["bot"], p["bot_kind"], p["feed_subs"]) == (family, device, bot, kind, subs)


def test_every_public_route_has_an_area(app):
    unmapped = []
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith(("/admin", "/api/admin")) or tools.should_skip(rule.endpoint):
            continue
        if tools.classify(rule.endpoint, rule.rule).area == "other":
            unmapped.append(rule.endpoint)
    assert unmapped == [], f"add these endpoints to usage/tools.py: {unmapped}"


def test_tool_labels():
    assert tools.label_for("main-path") == "Main Path"
    assert tools.label_for("ch5-reach-of-citation") == "Reach of Citation (ch. 5)"
    assert tools.label_for("wac-editor-network") == "WAC: Editor Network"
    assert tools.is_auto("author-network") and not tools.is_auto("half-life")


# ── Institutions and network facts ───────────────────────────────────────────

IPEDS_ROWS = [
    {"UNITID": "236948", "INSTNM": "University of Washington-Seattle Campus", "IALIAS": "UW",
     "CITY": "Seattle", "STABBR": "WA", "WEBADDR": "www.washington.edu/", "SECTOR": "1",
     "HBCU": "2", "TRIBAL": "2", "C21BASIC": "15"},
    {"UNITID": "236382", "INSTNM": "Olympic College", "IALIAS": "", "CITY": "Bremerton",
     "STABBR": "WA", "WEBADDR": "www.olympic.edu/", "SECTOR": "1", "HBCU": "2",
     "TRIBAL": "2", "C21BASIC": "14"},
    {"UNITID": "100001", "INSTNM": "Columbia College", "IALIAS": "", "CITY": "Columbia",
     "STABBR": "MO", "WEBADDR": "www.ccis.edu", "SECTOR": "2", "HBCU": "2", "TRIBAL": "2", "C21BASIC": "19"},
    {"UNITID": "100002", "INSTNM": "Columbia College", "IALIAS": "", "CITY": "Chicago",
     "STABBR": "IL", "WEBADDR": "www.colum.edu", "SECTOR": "2", "HBCU": "2", "TRIBAL": "2", "C21BASIC": "18"},
]
HIPO = [{"name": "Universidad Nacional de Colombia", "domains": ["unal.edu.co"],
         "web_pages": ["https://unal.edu.co/"], "country": "Colombia", "alpha_two_code": "CO",
         "state-province": None}]


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "usage-data"
    d.mkdir()
    with open(d / institutions.IPEDS_FILE, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(IPEDS_ROWS[0]))
        w.writeheader()
        w.writerows(IPEDS_ROWS)
    (d / institutions.HIPO_FILE).write_text(json.dumps(HIPO), encoding="utf-8")
    monkeypatch.setenv("USAGE_DATA_DIR", str(d))
    return d


def test_registry_matching(data_dir):
    reg = institutions.Registry(str(data_dir))
    uw = reg.by_domain("wifi-12.cs.washington.edu")
    assert uw["name"].startswith("University of Washington")
    assert uw["carnegie"] == "Doctoral: very high research (R1)" and uw["kind"] == "university"
    oc = reg.by_name("OLYMPIC COLLEGE")
    assert oc["kind"] == "community-college" and oc["carnegie"] == "Associate's / two-year"
    assert reg.by_name("Columbia College") is None              # ambiguous
    assert reg.by_name("Columbia College", "US", "IL")["region"] == "IL"
    assert reg.by_domain("host.unal.edu.co")["country"] == "CO"
    assert reg.by_domain("comcast.net") is None


def test_carnegie_buckets_and_names():
    assert institutions.carnegie_bucket(16) == "Doctoral: high research (R2)"
    assert institutions.carnegie_bucket("33") == "Tribal college"
    assert institutions.carnegie_bucket(-2) is None
    assert institutions.norm_name("The Université de Montréal & Co.") == "universite de montreal and co"


def test_parse_rdap_registrant():
    data = {"name": "OLYMPIC-COLLEGE-NET", "country": "US", "entities": [
        {"roles": ["abuse"], "vcardArray": ["vcard", [["fn", {}, "text", "K20 NOC"]]]},
        {"roles": ["registrant"], "vcardArray": ["vcard", [
            ["fn", {}, "text", "Olympic College"],
            ["email", {}, "text", "noc@olympic.edu"]]]},
    ]}
    assert netinfo.parse_rdap(data) == {"name": "OLYMPIC-COLLEGE-NET", "org": "Olympic College",
                                        "domains": ["olympic.edu"], "country": "US"}


def test_parse_ror_org():
    org = {"id": "https://ror.org/05pbb8y14", "types": ["education"],
           "names": [{"value": "Olympic College", "types": ["ror_display", "label"]}],
           "links": [{"type": "website", "value": "http://www.olympic.edu/"}],
           "locations": [{"geonames_details": {"country_code": "US", "country_subdivision_code": "WA",
                                               "name": "Bremerton"}}]}
    p = netinfo.parse_ror_org(org)
    assert (p["name"], p["country"], p["region"], p["city"]) == ("Olympic College", "US", "WA", "Bremerton")


def test_network_classes():
    nc = netinfo.network_class
    assert nc(as_org="Comcast Cable", rdns="c-73.hsd1.wa.comcast.net", rdns_verified=True,
              rdap_org=None, institution_kind=None) == "isp"
    assert nc(as_org="Amazon.com, Inc.", rdns=None, rdns_verified=False,
              rdap_org=None, institution_kind=None) == "hosting"
    assert nc(as_org="Some ISP", rdns="lab.cs.example.edu", rdns_verified=True,
              rdap_org=None, institution_kind=None) == "education"
    # An unverified PTR claiming .edu earns nothing.
    assert nc(as_org="Some ISP", rdns="fake.harvard.edu", rdns_verified=False,
              rdap_org=None, institution_kind=None) == "isp"
    assert nc(as_org="T-Mobile USA", rdns=None, rdns_verified=False,
              rdap_org=None, institution_kind=None) == "mobile"


def test_rdap_is_asked_about_the_network_not_the_visitor(tracking, data_dir):
    visit(tracking, "/about", ip="81.2.70.10")
    lookups = FakeLookups()
    resolve.run(now=store.utc_now(), lookups=lookups, geo=FakeGeo(),
                registry=institutions.Registry(store.data_dir()))
    assert lookups.rdap_calls == ["81.2.70.0"]


def test_prefixes():
    assert netinfo.prefix_of("81.2.69.160") == "81.2.69.0/24"
    assert netinfo.prefix_of("2600:1700:abcd:1::5") == "2600:1700:abcd::/48"
    assert netinfo.prefix_of("nonsense") is None


class FakeReader:
    def __init__(self, table):
        self.table = table

    def get(self, ip):
        return self.table.get(ip)

    def close(self):
        pass


def test_geo_reads_maxmind_and_ipinfo_layouts(tmp_path):
    g = Geo(str(tmp_path))
    g.city = FakeReader({"81.2.69.160": {"country": {"iso_code": "US"},
                                         "subdivisions": [{"names": {"en": "Washington"}}],
                                         "city": {"names": {"en": "Seattle"}}}})
    g.asn = FakeReader({"81.2.69.160": {"autonomous_system_number": 73,
                                        "autonomous_system_organization": "University of Washington"},
                        "9.9.9.9": {"asn": "AS19281", "as_name": "Quad9", "as_domain": "quad9.net",
                                    "country_code": "CH"}})
    a = g.lookup("81.2.69.160")
    assert (a["country"], a["region"], a["city"], a["asn"], a["as_org"]) == (
        "US", "Washington", "Seattle", 73, "University of Washington")
    b = g.lookup("9.9.9.9")
    assert (b["asn"], b["as_org"], b["as_domain"], b["country"]) == (19281, "Quad9", "quad9.net", "CH")


# ── The resolver ─────────────────────────────────────────────────────────────

class FakeGeo:
    sources = ["dbip"]
    TABLE = {
        "81.2.69.160": {"country": "US", "region": "Washington", "city": "Seattle",
                        "asn": 73, "as_org": "University of Washington", "as_domain": None},
        "81.2.70.10": {"country": "US", "region": "Washington", "city": "Bremerton",
                       "asn": 101, "as_org": "K20 Education Network", "as_domain": None},
        "73.10.10.10": {"country": "US", "region": "Oregon", "city": "Portland",
                        "asn": 7922, "as_org": "Comcast Cable Communications", "as_domain": None},
        "52.1.1.1": {"country": "US", "region": "Virginia", "city": "Ashburn",
                     "asn": 14618, "as_org": "Amazon.com, Inc.", "as_domain": None},
    }

    def lookup(self, ip):
        return dict(self.TABLE.get(ip, {"country": None, "region": None, "city": None,
                                         "asn": None, "as_org": None, "as_domain": None}))

    def close(self):
        pass


class FakeLookups(resolve.Lookups):
    def __init__(self):
        super().__init__(enabled=True)
        self.rdap_calls = []

    def rdns(self, ips):
        return {ip: (("wifi-1.cs.washington.edu", True) if ip == "81.2.69.160" else (None, False))
                for ip in ips}

    def rdap(self, ip):
        self.rdap_calls.append(ip)
        if ip == "81.2.70.0":
            return {"name": "OC-NET", "org": "Olympic College", "domains": [], "country": "US"}
        return {"name": "COMCAST", "org": "Comcast Cable Communications, LLC", "domains": [], "country": "US"}

    def ror(self, name):
        return None, False


def run_resolver(now):
    return resolve.run(now=now, lookups=FakeLookups(), geo=FakeGeo(),
                       registry=institutions.Registry(store.data_dir()))


def test_resolver_identifies_classifies_and_forgets(tracking, data_dir, monkeypatch):
    # A UW visitor who uses Explore with JavaScript running.
    visit(tracking, "/explore", ip="81.2.69.160")
    visit(tracking, "/api/citations/half-life", ip="81.2.69.160")
    post_event(tracking, {"t": "view", "p": "/explore"}, ip="81.2.69.160")
    # Olympic College, found through the address registry; no script ran.
    visit(tracking, "/article/2", ip="81.2.70.10")
    # A home connection with Global Privacy Control.
    visit(tracking, "/about", ip="73.10.10.10", **{"Sec-GPC": "1"})
    # A scraper on AWS and a declared crawler.
    for _ in range(3):
        visit(tracking, "/api/stats/timeline", ip="52.1.1.1", ua_string="python-requests/2.31")
    visit(tracking, "/about", ip="73.10.10.10",
          ua_string="Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)")

    now = store.utc_now() + dt.timedelta(minutes=5)
    result = run_resolver(now)
    assert result["ok"], result
    assert rows("SELECT COUNT(*) AS n FROM hits WHERE ip IS NOT NULL OR resolved = 0") == [{"n": 0}]

    inst = {r["name"]: r for r in rows("SELECT * FROM institutions")}
    assert "University of Washington-Seattle Campus" in inst
    assert inst["Olympic College"]["kind"] == "community-college"

    by_ip_city = {r["city"]: r for r in rows("SELECT city, net_class, institution_id, as_org FROM hits")}
    assert by_ip_city["Seattle"]["net_class"] == "education"
    assert by_ip_city["Bremerton"]["institution_id"] == inst["Olympic College"]["id"]

    gpc = rows("SELECT city, as_org, institution_id, country FROM hits WHERE gpc = 1")
    assert gpc == [{"city": None, "as_org": None, "institution_id": None, "country": "US"}]

    classes = sorted(r["class"] for r in rows("SELECT class FROM visitor_days"))
    assert classes == ["bot", "bot", "human", "likely-human", "likely-human"]
    uw = rows("SELECT * FROM visitor_days WHERE class = 'human'")[0]
    assert uw["institution_id"] == inst["University of Washington-Seattle Campus"]["id"]
    assert uw["tools"] >= 1 and uw["tool_runs"] == 1

    # Networks are cached: a second run does no new lookups.
    visit(tracking, "/glossary", ip="81.2.70.11")
    lookups = FakeLookups()
    resolve.run(now=now, lookups=lookups, geo=FakeGeo(), registry=institutions.Registry(store.data_dir()))
    assert lookups.rdap_calls == []


def test_classify_visitor_rules():
    c = resolve.classify_visitor
    assert c({"bot": "Feedly", "bot_kind": "feed"}) == "feed-reader"
    assert c({"bot": "Google", "bot_kind": "search", "beacon": 1}) == "bot"
    assert c({"beacon": 1, "net_class": "hosting", "family": "Chrome"}) == "human"
    assert c({"net_class": "hosting", "family": "Chrome", "pages": 2}) == "datacenter"
    assert c({"family": "Chrome", "pages": 3, "hits": 5, "peak_per_min": 2}) == "likely-human"
    assert c({"family": "Chrome", "pages": 0, "hits": 5}) == "bot"
    assert c({"family": "Chrome", "pages": 300, "hits": 500, "peak_per_min": 90}) == "bot"


def test_raw_addresses_older_than_the_limit_are_purged(tracking, data_dir):
    visit(tracking, "/about", ip="81.2.69.160")
    conn = store.connect()
    try:
        with conn:
            conn.execute("UPDATE hits SET ts = '2020-01-01T00:00:00', resolved = 1")
    finally:
        conn.close()
    run_resolver(store.utc_now())
    assert rows("SELECT ip FROM hits") == [{"ip": None}]


# ── Reports ──────────────────────────────────────────────────────────────────

def seeded_usage(tracking):
    visit(tracking, "/explore", ip="81.2.69.160")
    visit(tracking, "/api/citations/half-life", ip="81.2.69.160")
    visit(tracking, "/article/1", ip="81.2.69.160")
    visit(tracking, "/?q=transfer", ip="81.2.69.160")
    post_event(tracking, {"t": "view", "p": "/explore"}, ip="81.2.69.160")
    post_event(tracking, {"t": "out", "p": "/article/1", "x": "doi.org/10.1234/x"}, ip="81.2.69.160")
    visit(tracking, "/article/2", ip="81.2.70.10")
    for _ in range(2):
        visit(tracking, "/api/stats/timeline", ip="52.1.1.1", ua_string="python-requests/2.31")
    run_resolver(store.utc_now() + dt.timedelta(minutes=5))


def test_summary_counts_people_and_keeps_bots_apart(tracking, data_dir):
    seeded_usage(tracking)
    start, end = report.default_range(7)
    s = report.build_summary(start, end)
    t = s["totals"]
    assert t["people_visitor_days"] == 2
    assert t["by_class"]["bot"] == 1
    assert t["institutions"] == 2
    assert {x["tool"] for x in s["tools"]} >= {"half-life"}
    assert t["doi_clicks"] == 1 and t["article_views"] == 2
    assert s["articles"][0]["title"]                       # titles come from articles.db
    assert any(c["country"] == "US" for c in s["countries"])
    assert s["data"]["attribution_text"] == ["IP geolocation by DB-IP (db-ip.com), CC BY 4.0"]

    confirmed_only = report.build_summary(start, end, classes=("human",))
    assert confirmed_only["totals"]["people_visitor_days"] == 1

    public = report.build_summary(start, end, public=True, min_count=3)
    assert public["institutions"] == [] and public["institutions_hidden"] == 2
    assert public["searches"] == [] and public["articles"] == []


def test_report_renders_and_zips(tracking, data_dir):
    seeded_usage(tracking)
    start, end = report.default_range(7)
    s = report.build_summary(start, end)
    page = report.render_html(s)
    assert "Pinakes usage" in page and "University of Washington-Seattle Campus" in page
    assert "<svg" in page and "DB-IP" in page
    with zipfile.ZipFile(io.BytesIO(report.to_zip(s))) as zf:
        assert {"tools.csv", "institutions.csv", "summary.json"} <= set(zf.namelist())
        inst = list(csv.DictReader(io.StringIO(zf.read("institutions.csv").decode())))
    assert {r["name"] for r in inst} >= {"Olympic College"}


# ── Admin endpoints ──────────────────────────────────────────────────────────

def test_admin_endpoints_need_the_token(tracking):
    for path in ("/api/admin/usage/summary", "/api/admin/usage/report", "/api/admin/usage/status"):
        assert tracking.get(path).status_code == 401
    assert tracking.post("/api/admin/usage/resolve").status_code == 401


def test_admin_summary_report_and_status(tracking, data_dir):
    seeded_usage(tracking)
    r = tracking.get("/api/admin/usage/summary?public=0", headers=AUTH)
    assert r.status_code == 200 and r.get_json()["totals"]["people_visitor_days"] == 2
    assert tracking.get("/api/admin/usage/summary?from=bad", headers=AUTH).status_code == 400
    r = tracking.get("/api/admin/usage/report?format=html&public=1", headers=AUTH)
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]
    r = tracking.get("/api/admin/usage/report?format=zip", headers=AUTH)
    assert r.mimetype == "application/zip"
    st = tracking.get("/api/admin/usage/status", headers=AUTH).get_json()
    assert st["tracking_enabled"] and st["trusted_ip_header"] == "CF-Connecting-IP"
    assert st["raw_addresses_held"] == 0 and st["hits"] > 0
    assert tracking.get("/admin/usage").status_code == 200


def test_admin_job_endpoints_start_background_work(tracking, monkeypatch):
    started = []
    monkeypatch.setattr(resolve, "run", lambda **kw: started.append("resolve"))
    import usage.refresh as refresh_mod
    monkeypatch.setattr(refresh_mod, "refresh", lambda **kw: started.append(("refresh", kw)))
    assert tracking.post("/api/admin/usage/resolve", headers=AUTH).status_code == 202
    assert tracking.post("/api/admin/usage/refresh-data", headers=AUTH,
                         json={"force": True}).status_code == 202
    import time
    for _ in range(50):
        if len(started) == 2:
            break
        time.sleep(0.02)
    assert "resolve" in started and ("refresh", {"force": True}) in started


def test_script_tag_only_when_tracking(client, monkeypatch):
    monkeypatch.delenv("PINAKES_USAGE_TRACKING", raising=False)
    assert b"/static/usage.js" not in client.get("/about").data
    monkeypatch.setenv("PINAKES_USAGE_TRACKING", "1")
    monkeypatch.setenv("PINAKES_USAGE_SYNC", "1")
    assert b"/static/usage.js" in client.get("/about").data


# ── Reference-data refresh ───────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content
        self.raw = io.BytesIO(content)

    def close(self):
        pass


class FakeSession:
    def __init__(self, routes):
        self.routes = routes
        self.asked = []

    def get(self, url, **kw):
        self.asked.append(url)
        return self.routes.get(url, FakeResponse(404))


def test_refresh_downloads_hipo_and_the_newest_ipeds(tmp_path, monkeypatch, fixture_db):
    from usage import refresh as refresh_mod
    monkeypatch.setenv("USAGE_DATA_DIR", str(tmp_path / "d"))
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("hd2024.csv", "UNITID,INSTNM\n1,Test U\n")
    hipo = json.dumps([{"name": f"U{i}", "domains": [f"u{i}.edu"]} for i in range(1200)]).encode()
    now = dt.datetime(2026, 10, 15)
    session = FakeSession({
        refresh_mod.HIPO_URL: FakeResponse(200, hipo),
        refresh_mod.IPEDS_URL.format(year=2024): FakeResponse(200, zbuf.getvalue()),
    })
    result = refresh_mod.refresh(now=now, session=session, only={"universities.json", "ipeds_hd.csv"})
    assert result["ok"], result
    assert (tmp_path / "d" / "ipeds_hd.csv").read_text().startswith("UNITID")
    assert refresh_mod.IPEDS_URL.format(year=2025) in session.asked     # tried newest first
    # Fresh files are left alone on the next run.
    again = refresh_mod.refresh(now=now, session=FakeSession({}), only={"universities.json"})
    assert again["files"]["universities.json"].startswith("current")


# ── The optional form at /tell-us ────────────────────────────────────────────

@pytest.fixture
def survey_on(monkeypatch, client):
    monkeypatch.setenv("PINAKES_USAGE_SURVEY", "1")
    monkeypatch.setenv("PINAKES_ADMIN_TOKEN", TOKEN)
    return client


def test_form_is_404_until_switched_on(client, monkeypatch):
    monkeypatch.delenv("PINAKES_USAGE_SURVEY", raising=False)
    assert client.get("/tell-us").status_code == 404
    assert b"/tell-us" not in client.get("/about").data


def test_form_renders_and_is_linked(survey_on):
    page = survey_on.get("/tell-us").get_data(as_text=True)
    assert "How you use Pinakes" in page and 'name="institution"' in page
    assert 'href="/tell-us"' in survey_on.get("/about").get_data(as_text=True)


def test_form_saves_a_cleaned_response(survey_on):
    r = survey_on.post("/tell-us", data={
        "institution": "  Olympic   College ", "role": "faculty", "uses": ["teaching", "bogus", "research"],
        "course": "ENGL 235", "note": "Used Main Path with my class.", "quote_ok": "1",
        "email": "Someone@Olympic.EDU", "came_from": "about"})
    assert r.status_code == 200 and "Thank you" in r.get_data(as_text=True)
    (row,) = rows("SELECT * FROM responses")
    assert row["institution_text"] == "Olympic College"
    assert json.loads(row["uses"]) == ["teaching", "research"]
    assert row["quote_ok"] == 1 and row["contact_email"] == "someone@olympic.edu"
    assert row["came_from"] == "about"


def test_quote_permission_needs_a_note(survey_on):
    survey_on.post("/tell-us", data={"role": "grad", "quote_ok": "1"})
    assert rows("SELECT quote_ok FROM responses") == [{"quote_ok": 0}]


def test_form_rejects_empty_and_bad_email(survey_on):
    r = survey_on.post("/tell-us", data={})
    assert r.status_code == 400 and "nothing to send" in r.get_data(as_text=True)
    r = survey_on.post("/tell-us", data={"note": "hi", "email": "not-an-address"})
    assert r.status_code == 400 and "hi</textarea>" in r.get_data(as_text=True)   # input kept
    assert rows("SELECT * FROM responses") == []


def test_honeypot_stores_nothing(survey_on):
    r = survey_on.post("/tell-us", data={"note": "buy pills", "website": "spam.example"})
    assert r.status_code == 200 and "Thank you" in r.get_data(as_text=True)
    assert rows("SELECT * FROM responses") == []


def test_form_posts_are_rate_limited(survey_on):
    codes = [survey_on.post("/tell-us", data={"note": f"n{i}"}).status_code for i in range(6)]
    assert codes == [200] * 5 + [429]
    assert survey_on.get("/tell-us").status_code == 200        # reading the form is not limited


def test_form_traffic_is_not_recorded_as_a_visit(survey_on, monkeypatch):
    monkeypatch.setenv("PINAKES_USAGE_TRACKING", "1")
    monkeypatch.setenv("PINAKES_USAGE_SYNC", "1")
    survey_on.get("/tell-us")
    survey_on.post("/tell-us", data={"note": "x"})
    assert rows("SELECT endpoint FROM hits") == []


def test_resolver_matches_named_institutions(survey_on, data_dir):
    for inst in ("Olympic College", "washington.edu", "Somewhere Unlisted"):
        survey_on.post("/tell-us", data={"institution": inst, "role": "faculty"})
    run_resolver(store.utc_now())
    got = {r["institution_text"]: r for r in rows(
        "SELECT r.institution_text, r.matched_at, i.name FROM responses r "
        "LEFT JOIN institutions i ON i.id = r.institution_id")}
    assert got["Olympic College"]["name"] == "Olympic College"
    assert got["washington.edu"]["name"].startswith("University of Washington")
    assert got["Somewhere Unlisted"]["name"] is None and got["Somewhere Unlisted"]["matched_at"]


def test_summary_quotes_only_with_permission_and_never_emails(survey_on, data_dir):
    survey_on.post("/tell-us", data={"institution": "Olympic College", "role": "faculty",
                                     "uses": ["teaching"], "note": "Quote me.", "quote_ok": "1",
                                     "email": "a@olympic.edu"})
    survey_on.post("/tell-us", data={"institution": "Olympic College", "role": "grad",
                                     "uses": ["research"], "note": "Private note."})
    run_resolver(store.utc_now())
    start, end = report.default_range(7)
    s = report.build_summary(start, end, public=True)
    r = s["responses"]
    assert r["count"] == 2 and r["contacts"] == 1
    assert r["by_role"] == {"Faculty or instructor": 1, "Graduate student": 1}
    assert [q["note"] for q in r["quotes"]] == ["Quote me."]
    assert r["institutions"][0]["name"] == "Olympic College" and r["institutions"][0]["responses"] == 2
    dumped = json.dumps(s)
    assert "a@olympic.edu" not in dumped and "Private note." not in dumped
    page = report.render_html(s)
    assert "In readers' words" in page and "Quote me." in page and "Private note." not in page


def test_admin_can_list_and_delete_responses(survey_on):
    survey_on.post("/tell-us", data={"note": "delete me", "email": "x@example.edu"})
    assert survey_on.get("/api/admin/usage/responses").status_code == 401
    listed = survey_on.get("/api/admin/usage/responses", headers=AUTH).get_json()["responses"]
    assert listed[0]["contact_email"] == "x@example.edu"
    rid = listed[0]["id"]
    assert survey_on.post(f"/api/admin/usage/responses/{rid}/delete", headers=AUTH).status_code == 200
    assert survey_on.post(f"/api/admin/usage/responses/{rid}/delete", headers=AUTH).status_code == 404
    assert rows("SELECT * FROM responses") == []


# ── Privacy fixes from the 2026-10-16 audit ──────────────────────────────────

JANE = "Jane Smith"          # a seeded author, as in tests/test_redaction.py


def usage_db_text():
    conn = sqlite3.connect(store.usage_db_path())
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def test_a_removed_authors_name_is_never_stored(tracking):
    from redaction import redact_author
    token = redact_author(JANE)["token"]
    assert visit(tracking, f"/author/{JANE}").status_code == 404     # the old real-name URL
    visit(tracking, f"/api/author/{JANE}/timeline")
    visit(tracking, f"/?q={JANE} writing centers")
    visit(tracking, "/about", Referer=f"https://pinakes.wacclearinghouse.org/author/{JANE}")
    post_event(tracking, {"t": "view", "p": f"/author/{JANE}"})
    dump = usage_db_text()
    assert JANE not in dump and JANE.casefold() not in dump.casefold()
    hits = rows("SELECT endpoint, object, query, ref_path FROM hits ORDER BY id")
    assert hits[0]["object"] is None                                   # 404: no object
    assert hits[1]["object"] in (None, token)
    assert hits[2]["query"] is None
    assert hits[3]["ref_path"] == "/author/<path:name>"
    assert rows("SELECT page FROM events") == [{"page": "/author/<path:name>"}]


def test_names_recorded_before_a_removal_are_scrubbed_nightly(tracking, data_dir):
    visit(tracking, f"/author/{JANE}")
    visit(tracking, f"/?q={JANE}")
    assert JANE in usage_db_text()
    from redaction import redact_author
    token = redact_author(JANE)["token"]
    run_resolver(store.utc_now())
    assert JANE not in usage_db_text()
    assert rows("SELECT object FROM hits WHERE endpoint = 'authors.author_detail'") == [{"object": token}]


def test_gpc_visits_keep_no_city_or_region_from_any_source(tracking, data_dir):
    cf = {"cf-ipcity": "Seattle", "cf-region": "Washington", "CF-IPCountry": "US"}
    visit(tracking, "/about", ip="81.2.69.160", **{"Sec-GPC": "1"}, **cf)
    firefox = "Mozilla/5.0 (Windows NT 10.0; rv:131.0) Gecko/20100101 Firefox/131.0"
    visit(tracking, "/glossary", ip="81.2.69.160", ua_string=firefox, **cf)   # another browser, same address
    raw = rows("SELECT gpc, cf_city, cf_region FROM hits ORDER BY id")
    assert raw == [{"gpc": 1, "cf_city": None, "cf_region": None},
                   {"gpc": 0, "cf_city": "Seattle", "cf_region": "Washington"}]
    run_resolver(store.utc_now())
    gpc, plain = rows("SELECT gpc, country, region, city, as_org, institution_id FROM hits ORDER BY id")
    assert (gpc["country"], gpc["region"], gpc["city"], gpc["as_org"], gpc["institution_id"]) == (
        "US", None, None, None, None)
    assert plain["city"] == "Seattle" and plain["institution_id"] is not None
    gpc_day = rows("SELECT region, city FROM visitor_days WHERE visitor = "
                   "(SELECT visitor FROM hits WHERE gpc = 1)")
    assert gpc_day == [{"region": None, "city": None}]
    # The same browser sending GPC on one request and not another: the whole
    # visitor-day is treated as GPC.
    visit(tracking, "/about", ip="81.2.69.170", **{"Sec-GPC": "1"}, **cf)
    visit(tracking, "/glossary", ip="81.2.69.170", **cf)
    run_resolver(store.utc_now() + dt.timedelta(minutes=1))
    mixed = rows("SELECT region, city, institution_id FROM visitor_days WHERE visitor IN "
                 "(SELECT visitor FROM hits WHERE ip IS NULL AND gpc = 0 AND city IS NOT NULL "
                 "AND visitor IN (SELECT visitor FROM hits WHERE gpc = 1))")
    assert mixed == [{"region": None, "city": None, "institution_id": None}]


def test_usage_db_overwrites_deleted_content(tracking):
    conn = store.connect()
    try:
        assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
    finally:
        conn.close()


def test_backup_copies_lose_raw_addresses_and_salts(tracking, tmp_path):
    import importlib.util
    import pathlib
    visit(tracking, "/about", ip="81.2.69.160")
    store.salt_for(store.utc_now().date().isoformat())
    copy = tmp_path / "usage-backup.db"
    src = sqlite3.connect(store.usage_db_path())
    dst = sqlite3.connect(copy)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    script = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "windows" / "scrub_usage_backup.py"
    spec = importlib.util.spec_from_file_location("scrub_usage_backup", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["scrub", str(copy)]) == 0
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("SELECT COUNT(*) FROM hits WHERE ip IS NOT NULL").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM salts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM hits").fetchone()[0] == 1
    finally:
        conn.close()
    assert b"81.2.69.160" not in copy.read_bytes()
    # The live database still has its address until the resolver runs.
    assert rows("SELECT ip FROM hits") == [{"ip": "81.2.69.160"}]
