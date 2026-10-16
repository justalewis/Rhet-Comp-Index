# Usage statistics

Pinakes records how the site is used so the project can say, with evidence,
which tools people use, how often, and from which institutions and countries.
The output is a dashboard at `/admin/usage` and a printable report for grant
applications and progress reports. This document covers what is recorded,
how institutions are identified, what the numbers mean, and how to run it.
Server steps for the Clearinghouse box are in
[WINDOWS-SERVER-RUNBOOK.md](WINDOWS-SERVER-RUNBOOK.md#usage-statistics).

## What is recorded

Recording is off until `PINAKES_USAGE_TRACKING=1` is set in the service
environment. When it is on, two things are written to `usage.db`, a SQLite
file kept apart from `articles.db` so recording can never block the fetch:

- **Each request** (`usage/capture.py`): time, page or tool, status,
  response time, the external referring site, the browser's preferred
  language, browser family, operating system and device type, whitelisted
  query parameters, the search text if there was one, and a daily visitor
  id. The raw IP address is held until the nightly job resolves it.
- **Browser events** (`static/usage.js`, posted to `/api/e`): that the page
  loaded with JavaScript running, tool switches inside Explore and
  Datastories, outbound clicks (DOI links counted separately), downloads and
  exports, and time on page. No IP is stored with events.

Never recorded: static files, health checks, admin pages, the usage
endpoints themselves, `/robots.txt`, 404s, requests the IP denylist refused,
OAuth and verification callbacks, and any URL carrying an alert or
name-removal token. Query parameters named `code`, `state`, `token`,
`email`, `password`, `key` or `secret` are dropped. The raw User-Agent
header is parsed and discarded.

## Privacy design

- **No cookies and no storage in the browser.** The visitor id is
  `HMAC(salt, ip | user-agent | host)`, cut to 16 hex characters. Each day
  gets a new random salt, and salts older than yesterday are deleted, after
  which nobody can recompute an id from an address or link one day's
  visitor to the next.
- **Raw IP addresses are deleted** by the nightly resolver once their
  network is looked up, and in any case after `USAGE_RAW_IP_HOURS` (default
  48). What stays is country, region, city, network owner, network class,
  and the institution id.
- **Global Privacy Control and Do Not Track** are honoured: those visits
  keep only country and network class, with no city, network owner or
  institution.
- **Reports are aggregate.** The public version names an institution,
  search term, referring site or article only when at least three
  visitor-days account for it, and summarises the rest as counts.
- **Network lookups are cached per /24 (IPv4) or /48 (IPv6)**, so the
  address registries and ROR see network prefixes, a few hundred a night at
  most, not a stream of visitor addresses.

The public notice is the "Usage statistics" section of `/about`, which
appears only while recording is on.

## The unit: visitor-days

Every count is a visitor-day: one browser on one network on one day.
Because ids rotate daily, someone who visits on three days counts three
times, and there is no "unique visitors over the semester" figure. That is
the cost of not being able to track anyone. Say "visitor-days" in a grant
narrative, not "users."

## People, bots and the rest

The resolver classes each visitor-day (`usage/resolve.py`, `classify_visitor`):

| Class | Rule |
|---|---|
| `feed-reader` | User-Agent is a feed reader (Feedly, Inoreader, ...) |
| `bot` | Declared crawler or script; non-browser User-Agent; over 40 requests in a minute or 400 in a day; or API calls without ever loading a page |
| `human` | The page's script ran (any browser event) |
| `datacenter` | Came from a hosting or cloud network and never ran the script |
| `likely-human` | A normal browser on a normal network that never ran the script |

"People" in the reports means `human` plus `likely-human` by default, or
`human` only with the "browser-confirmed only" setting. The other classes
are reported in their own section and never added in.

## How institutions are identified

For each network not seen in the last 30 days, the resolver tries, in order:

1. **Verified reverse DNS.** The address's hostname, accepted only if that
   hostname resolves back to the same address, then matched by domain
   suffix against IPEDS and the world-universities list
   (`wifi-12.cs.washington.edu` → washington.edu).
2. **The network owner's domain**, when an IPinfo database supplies one.
3. **The address registry (RDAP)**: email domains of the block's
   registrant, then the registrant's name. This is what catches colleges
   that own their address space but reach the internet through a state or
   regional network.
4. **The autonomous-system name** (University of Washington runs its own).
5. **ROR's affiliation matcher**, for registrant or network names that look
   institutional but are on neither list. US matches are joined back to
   IPEDS for Carnegie classification, sector, HBCU and tribal status.
6. **A bare registrant name** that looks institutional ("Example County
   School District") is kept under that name when nothing else matches.

An ambiguous match (a domain or name shared by two institutions) matches
nothing. Institutions whose ROR id or name also appears among the authors'
affiliations in `articles.db` are flagged as publishing in the index.

**This identifies on-campus connections only.** Faculty and students at
home, on a phone, or on a VPN show up as their internet provider. Treat the
institution count as a verified minimum. Referring sites help: course sites
and library guides usually carry the institution's name in the address, and
the report lists them separately.

## Data sources

| File in `usage-data/` | Source | Licence | Refresh |
|---|---|---|---|
| `dbip-city-lite.mmdb`, `dbip-asn-lite.mmdb` | [DB-IP Lite](https://db-ip.com/db/lite.php) | CC BY 4.0, attribution required | monthly |
| `universities.json` | [Hipo world universities and domains](https://github.com/Hipo/university-domains-list) | MIT | monthly |
| `ipeds_hd.csv` | [IPEDS institutional characteristics](https://nces.ed.gov/ipeds/) | public domain | yearly |
| (API) | [ROR](https://ror.org) affiliation matching | CC0 | cached 180 days |
| (API) | RDAP via [rdap.org](https://rdap.org) | registry terms | cached 30 days |

None needs an account. MaxMind GeoLite2 (`GeoLite2-City.mmdb`,
`GeoLite2-ASN.mmdb`) or IPinfo Lite (`ipinfo_lite.mmdb`) files placed in the
same directory are used instead when present; IPinfo adds the network
owner's domain. The dashboard and report print the attribution each source
requires. Set `ROR_CLIENT_ID` if ROR's anonymous rate limit becomes a
problem.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PINAKES_USAGE_TRACKING` | off | `1` turns recording on |
| `PINAKES_TRUSTED_IP_HEADER` | unset | Must be `CF-Connecting-IP` on the Clearinghouse box, or every visitor is the proxy |
| `USAGE_DB_PATH` | `usage.db` beside `articles.db` | |
| `USAGE_DATA_DIR` | `usage-data/` beside `articles.db` | IP databases and institution lists |
| `USAGE_RAW_IP_HOURS` | `48` | Hard limit on keeping a raw address |
| `ROR_CLIENT_ID` | unset | Optional ROR API client id |

## Jobs

| Job | Endpoint | CLI | Schedule (Windows) |
|---|---|---|---|
| Resolve, classify, delete raw IPs | `POST /api/admin/usage/resolve` | `python -m usage resolve` | daily 05:10 |
| Download stale reference data | `POST /api/admin/usage/refresh-data` | `python -m usage refresh-data` | Sundays 05:40 |
| Report | `GET /api/admin/usage/report` | `python -m usage report --from ... --to ...` | on demand |

Both jobs share one lock, so a resolve and a refresh never overlap. The
nightly backup copies `usage.db` alongside `articles.db`.

## Producing a grant report

1. Open `/admin/usage`, enter the admin token, and pick the period.
2. Tick **Public version** for anything leaving the project.
3. **Download report (HTML)** gives a standalone page that prints cleanly to
   PDF. **Download tables (CSV)** gives one CSV per table plus the full
   summary as JSON, for building your own charts.

From the server console:

```powershell
C:\Pinakes\venv\Scripts\python.exe -m usage report --from 2026-09-01 --to 2026-12-31 --public --out fall-2026.html
```

## Limits worth stating in a narrative

- Institutions are a minimum, for the on-campus reason above.
- Visitor-days overcount returning people and cannot be turned into people.
- Shared campus networks behind one address (some libraries, some
  dormitories) collapse many readers with the same browser into one
  visitor-day.
- City-level location from free databases is approximate; country is
  reliable, region mostly so.
- Recording starts the day it is switched on. GoatCounter has page counts
  from March 2026 onward for anything earlier.
