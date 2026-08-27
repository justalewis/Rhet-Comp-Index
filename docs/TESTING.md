# Testing Pinakes

Three layers, each answering a question the one below it cannot.

| Layer | What it proves | Where it runs | Command |
|---|---|---|---|
| **Unit + route coverage** | The code is correct and every route answers | CI, on every push | `pytest` |
| **Smoke crawl** | A *deployed* host serves real data on every route | Scheduled / manual | `python -m tools.smoke_crawl` |
| **Panel check** | The tools actually **draw** in a browser | Manual / opt-in CI | `python -m tools.panel_check` |

All three read the same route and panel registries, generated from the code
itself. Nothing here is a hand-maintained list of things to test, because a
hand-maintained list is wrong the day someone adds a blueprint — and wrong
silently, which is worse.

---

## Why three layers

The instinct for "test the whole site" is to crawl it and check for 200s. On
this site that is close to useless, for three reasons.

**Most of the surface is JSON consumed by JavaScript.** Of ~136 reachable
routes, 91 are API endpoints. The HTML pages return 200 whether or not any tool
behind them works.

**The tools are hash-routed panels.** `/tools` links to `/explore#timeline`,
`/explore#citnet`, `/wac`, `/datastories`. A fragment never reaches the server,
so crawling `/explore` tells you exactly nothing about whether its eighteen
panels render. There are 68 panels across three pages.

**"200 OK" is the wrong assertion.** With an empty database every endpoint
returns 200 with `{"nodes": [], "links": []}`. That is the failure mode that
looks most like success, and a status-code crawler cannot see it at all.

So: the unit layer proves the code, the crawl proves the deployment serves
data, and the panel check proves a reader gets a chart.

---

## Layer 1 — `pytest`

```bash
pytest
```

Runs offline against a deterministic fixture database. No network, no
deployment. This is what gates the Fly deploy (`.github/workflows/test.yml`).

`tests/test_route_coverage.py` is the part worth knowing about. It enumerates
every route from `app.url_map` (via `tools/route_inventory.py`) and asserts,
for each:

- it responds without a 5xx;
- the Content-Type matches what the route serves;
- JSON parses;
- no error page is rendering behind a 200;
- API responses are not shared-cacheable;
- operator routes refuse anonymous callers.

**This is a ratchet.** Add a blueprint and its routes are tested immediately.
Before it existed, `tests/_route_schemas.py` named 26 API routes against the 56
the app registers, and the 30 `/api/wac/*` and 25 `/api/datastories/*`
endpoints had no route-level coverage at all — nothing failed when they broke,
because nothing looked.

What it does *not* do is assert any payload is *correct*. That is the floor,
not the ceiling; per-route semantics belong in the focused test modules next to
the code they cover.

### When it fails on a route you just added

Either the route is genuinely broken, or the inventory needs to know something
about it. Both live in `tools/route_inventory.py`:

- needs a query parameter to work → add it to `REQUIRED_QUERY`
- correctly answers something other than 200 → add it to `EXPECTED_STATUS`
  **with the reason**
- serves something unusual, or needs a credential → add an `_EXACT_RULES` entry
- legitimately returns an empty payload → add it to `ALLOWED_EMPTY`

Every skip carries a stated reason. An unexplained skip is how coverage quietly
erodes.

---

## Layer 2 — `tools.smoke_crawl`

```bash
python -m tools.smoke_crawl
```

Probes every route on a live host. Defaults to the test deployment; override
with `--base-url` or the `PINAKES_SITE_URL` environment variable.

```bash
python -m tools.smoke_crawl --base-url http://127.0.0.1:8080
```

With an admin token it also covers the 27 Datastories endpoints and the
operator routes — `auth_datastories.is_authenticated` falls through to the
admin token, so one secret unlocks both:

```bash
python -m tools.smoke_crawl --admin-token "$env:PINAKES_ADMIN_TOKEN" --include-admin
```

Three things make it more than curl-in-a-loop.

**It busts the cache.** Every request carries a unique `_cb` parameter. IIS/ARR
sits in front of the Windows deployment and caches API responses; a crawl that
does not bust it measures the proxy and would report 200 on every route with
the app face-down behind it. The crawler also asserts API responses are not
marked `Cache-Control: public`, which is the regression guard for exactly that.

**It discovers parameters.** `/article/<int:article_id>` is filled from an id
read off the live site, not a literal. A hardcoded `/article/1` passes by luck
on one database and 404s on the next; either way it is not evidence.

**It detects degenerate payloads.** An endpoint that answers 200 with no
content is reported. When the whole index is empty those drop to warnings,
because then it is one known cause rather than ninety separate faults.

Findings are split into per-route and site-level. One bad cache default
produced 46 identical failures on the first run, which buries everything that
is genuinely route-specific.

It paces itself under the app's rate limits (`rate_limit.LIMITS`), so a full
pass takes about five minutes, most of it waiting. Tripping the limiter and
then reporting our own 429s as site failures would be worse than slow.

> **Git Bash on Windows:** MSYS rewrites an argument starting with `/`, so
> `--only '^/api/...'` arrives as `^C:/Program Files/Git/api/...`. Anchor on a
> segment (`--only 'api/citations'`) or use PowerShell.

---

## Layer 3 — `tools.panel_check`

```bash
pip install -r requirements-browser.txt
python -m playwright install chromium
python -m tools.panel_check
```

Drives all 68 panels in a real Chromium and reports what rendered. This is the
only layer that answers *does the tool draw?*

```bash
python -m tools.panel_check --page explore --headed          # watch it
python -m tools.panel_check --screenshot-dir shots           # capture failures
python -m tools.panel_check --admin-token "$env:PINAKES_ADMIN_TOKEN"
```

The panel list is parsed out of the templates, so adding a panel gets it
checked:

| Page | Registry | Activation | Panels |
|---|---|---|---|
| `/explore` | `.explore-tab[data-hash]` | set `location.hash` | 18 |
| `/wac` | `.wac-card[data-viz]` | scroll into view (IntersectionObserver) | 24 |
| `/datastories` | `.explore-tab[data-hash]` | set `location.hash` | 26 |

The hash and the panel's DOM id are **not** the same string —
`data-hash="sleeping-beauties"` activates `#tab-sleepers`. The mapping is
parsed from the `showTab('...', this)` calls rather than assumed; measuring the
wrong element is how all eighteen panels read as blank.

Each panel lands in one of four outcomes, because "no chart" has two very
different causes:

| Outcome | Meaning |
|---|---|
| `DREW` | a chart rendered with real marks |
| `EMPTY` | the panel rendered an explicit "No data." state |
| `BLANK` | nothing rendered **and nothing explained** — the broken case |
| `ERROR` | a console error or failed fetch while the panel loaded |

On an empty index only `ERROR` fails the run: a console error is a fault
whatever the data says, but a panel with nothing to draw is not evidence of
breakage. On a populated index, `EMPTY` and `BLANK` fail too — a reader who
arrives at a tool and gets nothing is a bug regardless of the cause.

Page-level problems (a Content-Security-Policy violation, say) are reported
once as site-level findings rather than charged to whichever panel happened to
be active. Attributing a CSP header to a panel would fail all 26 Datastories
panels for one line in `base-core.html`.

---

## In CI

| Workflow | Trigger | What |
|---|---|---|
| `test.yml` | every push / PR | `pytest`. Gates the Fly deploy. |
| `site-check.yml` | 07:40 UTC daily, or manual | Smoke crawl against `PINAKES_SITE_URL`. Panel check is opt-in via workflow dispatch. |

`site-check.yml` is deliberately not a deploy gate: it talks to a real server,
so a red run means the deployment is wrong, not the code. Point it at a
different host with the `base_url` dispatch input, and set the
`PINAKES_SITE_URL` repo variable to change its default.

Reports upload as artifacts (`--json-report`), with screenshots for every
`BLANK` or `ERROR` panel.

---

## Reading a result on an empty index

A freshly seeded deployment has no articles, and it is worth knowing what that
looks like so it is not mistaken for breakage:

- **smoke crawl** — every content endpoint warns "empty payload (the index
  itself is empty)". Expected. The note at the end of the run says so.
- **panel check** — panels split between `EMPTY` (said "No data") and `BLANK`
  (said nothing). Only `ERROR` fails.

Re-run both after a fetch completes. That is when the warnings become
meaningful, and when `BLANK` starts telling you something about the tool rather
than about the database.
