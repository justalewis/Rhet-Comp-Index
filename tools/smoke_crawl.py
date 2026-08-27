"""smoke_crawl.py — probe every route on a running Pinakes deployment.

    python -m tools.smoke_crawl                                  # the test site
    python -m tools.smoke_crawl --base-url http://127.0.0.1:8080
    python -m tools.smoke_crawl --admin-token "$env:PINAKES_ADMIN_TOKEN"

Walks the route inventory (tools/route_inventory.py, derived from app.url_map)
against a live host and reports PASS / WARN / FAIL per route.

Three things make this more than "curl every URL and check for 200".

**Cache-busting.** Every request carries a unique `_cb` parameter. The API sends
`Cache-Control: max-age=3600`, and a shared cache in front of the origin — IIS
with ARR, in front of testpinakes — will happily answer for the application. A
crawl that does not bust the cache measures the proxy: it would report 200 on
every route with the app face-down behind it. The crawler also asserts that API
responses are not marked `public`, which is the regression guard for exactly
that failure.

**Discovered parameters.** Routes like `/article/<int:article_id>` are filled
from ids the crawler reads off the live site first, not from literals. A
hardcoded `/article/1` passes by luck on one database and 404s on the next,
and either way it is not evidence.

**Degenerate-payload detection.** With an empty database every endpoint returns
200 with `{"nodes": [], "links": []}` — the failure mode that looks most like
success, and the one a status-code crawler cannot see. Payloads that carry no
content are reported. When the index as a whole is empty those reports drop to
warnings, because then it is one known cause rather than 90 separate faults.

Rate limits are respected per tier (see rate_limit.LIMITS): the crawler paces
itself rather than tripping the limiter and then reporting its own 429s as site
failures.

Exit status: 0 when nothing FAILed, 1 otherwise. `--json-report` writes a
machine-readable summary for CI.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import string
import sys
import time
from dataclasses import dataclass, field, asdict

import requests

from tools.route_inventory import (
    ALLOWED_EMPTY, EXPORT, FEED, HTML, JSON,
    NEEDS_ADMIN, NEEDS_BASIC, NEEDS_DATASTORIES,
    RouteSpec, probeable_routes,
)

DEFAULT_BASE_URL = "https://testpinakes.wacclearinghouse.org"

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"


# ── Rate limiting ───────────────────────────────────────────────────────────
# Mirrors rate_limit.LIMITS. Tripping the limiter would make the crawler report
# its own 429s as site failures, so it paces instead.

_TIER_PER_MINUTE: list[tuple[str, int]] = [
    ("/api/articles/search", 120),
    ("/api/citations/",       20),
    ("/api/stats/",           20),
    ("/api/datastories/",     12),   # the tightest datastories route
    ("",                      60),   # default
]


class Pacer:
    """Per-tier minimum interval between requests."""

    def __init__(self, enabled: bool = True, multiplier: float = 1.0):
        self.enabled = enabled
        self.multiplier = multiplier
        self._last: dict[str, float] = {}
        self.slept = 0.0

    @staticmethod
    def tier(path: str) -> tuple[str, int]:
        for prefix, per_minute in _TIER_PER_MINUTE:
            if path.startswith(prefix):
                return prefix or "default", per_minute
        return "default", 60

    def wait(self, path: str) -> None:
        if not self.enabled:
            return
        name, per_minute = self.tier(path)
        # A little headroom: the limiter counts in fixed windows, so pacing at
        # exactly the limit can still collide at a window boundary.
        interval = (60.0 / per_minute) * 1.08 * self.multiplier
        now = time.monotonic()
        previous = self._last.get(name)
        if previous is not None:
            delay = interval - (now - previous)
            if delay > 0:
                time.sleep(delay)
                self.slept += delay
        self._last[name] = time.monotonic()


# ── Payload inspection ──────────────────────────────────────────────────────

# Keys that carry the actual content of a response. If every one present is
# empty, the endpoint answered without saying anything.
CONTENT_KEYS = {
    "articles", "authors", "books", "collections", "communities", "data",
    "edges", "institutions", "items", "journals", "labels", "links", "nodes",
    "pairs", "path", "results", "rows", "series", "topics", "values", "years",
}

# Markers of a Flask error page leaking through with a 200.
_ERROR_MARKERS = (
    "Traceback (most recent call last)",
    "werkzeug.exceptions",
    "Internal Server Error",
    "jinja2.exceptions",
)


def is_degenerate(payload) -> bool:
    """True when a JSON payload carries no content.

    A dict with no recognisable content keys is left alone — plenty of
    endpoints legitimately return scalars or config-shaped objects, and
    guessing at those would produce noise, not findings.
    """
    if payload is None:
        return True
    if isinstance(payload, list):
        return len(payload) == 0
    if isinstance(payload, dict):
        present = {k: v for k, v in payload.items() if k in CONTENT_KEYS}
        if not present:
            return False
        return all(not v for v in present.values())
    return False


def _load_expectations() -> tuple[dict, dict]:
    """Reuse the contracts the offline suite already states, rather than
    restating them here and letting the two drift."""
    try:
        from tests._route_schemas import JSON_ROUTE_SCHEMAS, HTML_ROUTE_HEADINGS
        return dict(JSON_ROUTE_SCHEMAS), dict(HTML_ROUTE_HEADINGS)
    except Exception:
        return {}, {}


JSON_SCHEMAS, HTML_HEADINGS = _load_expectations()


# ── Results ─────────────────────────────────────────────────────────────────

@dataclass
class Result:
    rule: str
    url: str
    category: str
    status: str = PASS
    http_status: int | None = None
    elapsed_ms: int = 0
    size: int = 0
    notes: list[str] = field(default_factory=list)

    def demote(self, level: str, note: str) -> None:
        """Record a problem, keeping the worst level seen."""
        self.notes.append(note)
        order = {PASS: 0, WARN: 1, FAIL: 2, SKIP: 0}
        if order[level] > order[self.status]:
            self.status = level


# ── The crawler ─────────────────────────────────────────────────────────────

class Crawler:

    def __init__(self, args):
        self.base = args.base_url.rstrip("/")
        self.timeout = args.timeout
        self.cache_bust = not args.no_cache_bust
        self.admin_token = args.admin_token
        self.basic_auth = None
        if args.jwa_user and args.jwa_password:
            self.basic_auth = (args.jwa_user, args.jwa_password)
        self.pacer = Pacer(enabled=not args.no_pace, multiplier=args.pace_multiplier)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = (
            "pinakes-smoke-crawl/1.0 (+https://github.com/justalewis/Rhet-Comp-Index)"
        )
        self.discovered: dict[str, str] = {}
        self.index_is_empty = False
        self.results: list[Result] = []
        # Findings that are properties of the deployment rather than of one
        # route. A misconfigured cache header is one fact about the server, not
        # 86 separate faults, and reporting it per-route buries every finding
        # that really is route-specific.
        self.site_findings: dict[str, dict] = {}

    def note_site_finding(self, key: str, level: str, message: str, example: str) -> None:
        finding = self.site_findings.setdefault(
            key, {"level": level, "message": message, "count": 0, "examples": []})
        finding["count"] += 1
        if len(finding["examples"]) < 3:
            finding["examples"].append(example)

    # -- HTTP ---------------------------------------------------------------

    def get(self, path: str, *, admin: bool = False, basic: bool = False):
        params = {}
        if self.cache_bust:
            params["_cb"] = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        headers = {}
        if admin and self.admin_token:
            headers["Authorization"] = f"Bearer {self.admin_token}"

        self.pacer.wait(path)
        started = time.monotonic()
        response = self.session.get(
            self.base + path,
            params=params or None,
            headers=headers or None,
            auth=self.basic_auth if basic else None,
            timeout=self.timeout,
            allow_redirects=False,
        )
        elapsed = int((time.monotonic() - started) * 1000)

        # One retry on 429: pacing should prevent it, but a fixed-window
        # boundary or concurrent real traffic can still produce one.
        if response.status_code == 429:
            wait = float(response.headers.get("Retry-After", "5") or 5)
            time.sleep(min(wait, 65))
            started = time.monotonic()
            response = self.session.get(
                self.base + path, params=params or None, headers=headers or None,
                auth=self.basic_auth if basic else None,
                timeout=self.timeout, allow_redirects=False,
            )
            elapsed = int((time.monotonic() - started) * 1000)
        return response, elapsed

    # -- Phase 1: discovery -------------------------------------------------

    def discover(self) -> None:
        """Read real ids off the live site so parameterised routes get real values."""

        def probe_json(path):
            try:
                response, _ = self.get(path)
                if response.status_code != 200:
                    return None
                return response.json()
            except Exception:
                return None

        articles = probe_json("/api/articles?limit=5")
        if isinstance(articles, dict):
            total = articles.get("total", 0)
            rows = articles.get("articles") or []
            self.index_is_empty = not rows and not total
            for row in rows:
                if "id" in row and "article_id" not in self.discovered:
                    self.discovered["article_id"] = str(row["id"])
                for author in (row.get("authors") or []):
                    name = author if isinstance(author, str) else author.get("name")
                    if name:
                        self.discovered.setdefault("author_name", name)
                        break
                if "article_id" in self.discovered and "author_name" in self.discovered:
                    break

        if "author_name" not in self.discovered:
            authors = probe_json("/api/stats/author-network?top_n=5")
            if isinstance(authors, dict):
                for node in (authors.get("nodes") or [])[:1]:
                    name = node.get("name") or node.get("id")
                    if name:
                        self.discovered["author_name"] = str(name)

        institutions = probe_json("/api/stats/institutions")
        rows = institutions if isinstance(institutions, list) else (
            (institutions or {}).get("institutions") or [])
        for row in rows[:1]:
            if isinstance(row, dict) and row.get("id") is not None:
                self.discovered["institution_id"] = str(row["id"])

        collections = probe_json("/api/wac/collections")
        rows = collections if isinstance(collections, list) else (
            (collections or {}).get("collections") or [])
        for row in rows[:1]:
            if isinstance(row, dict) and row.get("doi"):
                self.discovered["wac_doi"] = str(row["doi"])

        # Feed slugs come out of the OPML index and the /feeds page, which is
        # where the real ones are published.
        try:
            response, _ = self.get("/feed.opml")
            if response.status_code == 200:
                slugs = re.findall(r"/feed/([A-Za-z0-9\-_]+)\.xml", response.text)
                group = re.findall(r"/feed/group/([A-Za-z0-9\-_]+)\.xml", response.text)
                if slugs:
                    self.discovered["feed_slug"] = slugs[0]
                if group:
                    self.discovered["feed_group_slug"] = group[0]
        except Exception:
            pass

        if "feed_slug" not in self.discovered or "feed_group_slug" not in self.discovered:
            try:
                response, _ = self.get("/feeds")
                if response.status_code == 200:
                    slugs = re.findall(r'href="/feed/([A-Za-z0-9\-_]+)(?:\.xml)?"', response.text)
                    group = re.findall(r'href="/feed/group/([A-Za-z0-9\-_]+)', response.text)
                    if slugs:
                        self.discovered.setdefault("feed_slug", slugs[0])
                    if group:
                        self.discovered.setdefault("feed_group_slug", group[0])
            except Exception:
                pass

        # There is no published list of build-your-own selection codes — they
        # are minted per selection — so a bogus one is used to prove the route
        # rejects it cleanly rather than 500ing.
        self.discovered.setdefault("feed_select_code", "aaaaaa")

        # Books have no JSON index; scrape one id off the HTML listing.
        try:
            response, _ = self.get("/books")
            if response.status_code == 200:
                ids = re.findall(r'href="/book/(\d+)"', response.text)
                if ids:
                    self.discovered["book_id"] = ids[0]
        except Exception:
            pass

    # -- Phase 2: probe -----------------------------------------------------

    def check(self, spec: RouteSpec, path: str) -> Result:
        result = Result(rule=spec.rule, url=path, category=spec.category)

        wants_admin = spec.requires in (NEEDS_ADMIN, NEEDS_DATASTORIES)
        wants_basic = spec.requires == NEEDS_BASIC

        try:
            response, elapsed = self.get(
                path, admin=wants_admin, basic=wants_basic and bool(self.basic_auth))
        except requests.RequestException as exc:
            result.status = FAIL
            result.notes.append(f"request failed: {exc}")
            return result

        result.http_status = response.status_code
        result.elapsed_ms = elapsed
        result.size = len(response.content or b"")
        body = response.text or ""
        ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip()

        self._check_status(spec, result, response, body)
        if result.status == FAIL:
            return result
        if response.status_code >= 300:
            return result       # a legitimate redirect or gate; nothing more to assert

        self._check_headers(spec, result, response)
        self._check_body(spec, result, response, body, ctype)
        return result

    def _check_status(self, spec, result, response, body):
        code = response.status_code

        # Gating is evaluated FIRST, ahead of the generic 5xx rule. /jwa is
        # deliberately fail-closed: with no credentials configured it answers
        # 503, which is the designed behaviour (blueprints/jwa.py), not an
        # outage. Checking 5xx first would report that as a server error on
        # every crawl.
        has_credential = (
            (spec.requires in (NEEDS_ADMIN, NEEDS_DATASTORIES) and self.admin_token)
            or (spec.requires == NEEDS_BASIC and self.basic_auth)
        )
        if spec.requires and not has_credential:
            if code in (401, 403, 503) or code in (301, 302, 307, 308):
                result.status = SKIP
                result.notes.append(f"gated ({spec.requires}); HTTP {code} without a credential")
            else:
                result.demote(FAIL, f"gated route answered HTTP {code} with no credential")
            return

        # Routes whose correct answer is documented as something other than 200.
        expected = spec.expected_status
        if expected and code in expected[0]:
            result.status = SKIP
            result.notes.append(f"HTTP {code} as expected - {expected[1]}")
            return

        if code >= 500:
            result.demote(FAIL, f"HTTP {code} - server error")
            snippet = re.sub(r"<[^>]+>", " ", body)[:160].strip()
            if snippet:
                result.notes.append(f"body: {snippet}")
            return

        # Routes probed with a deliberately invalid parameter.
        if spec.unresolvable_args:
            if code in (400, 401, 403, 404, 410) or 300 <= code < 400:
                result.notes.append(f"bogus parameter rejected cleanly (HTTP {code})")
            elif code == 200:
                result.demote(WARN, "bogus parameter accepted with HTTP 200")
            return

        if code == 429:
            result.demote(WARN, "rate limited even after backoff — crawl paced too fast")
            return
        if 300 <= code < 400:
            result.notes.append(f"redirect to {response.headers.get('Location', '?')}")
            return
        if code != 200:
            result.demote(FAIL, f"HTTP {code}")

    def _check_headers(self, spec, result, response):
        cache = (response.headers.get("Cache-Control") or "").lower()
        # The regression guard. A shared cache in front of the origin cannot be
        # invalidated when a fetch lands, so API payloads must not be `public`.
        # Recorded site-wide: one bad default produces this on every API route.
        if spec.category == JSON and "public" in cache:
            self.note_site_finding(
                "shared-cacheable-api",
                FAIL,
                f"API responses are shared-cacheable (Cache-Control: {cache}). "
                "A proxy such as IIS/ARR will store them and serve stale payloads "
                "that no fetch can invalidate. Expected 'private'.",
                spec.rule,
            )

    def _check_body(self, spec, result, response, body, ctype):
        if spec.category == HTML:
            if not ctype.startswith("text/html"):
                result.demote(FAIL, f"expected HTML, got Content-Type: {ctype or '(none)'}")
                return
            for marker in _ERROR_MARKERS:
                if marker in body:
                    result.demote(FAIL, f"error page rendered with HTTP 200 ({marker!r})")
                    return
            if len(body) < 500:
                result.demote(WARN, f"suspiciously small HTML response ({len(body)} bytes)")
            heading = HTML_HEADINGS.get(spec.rule)
            if heading and heading not in body:
                result.demote(FAIL, f"expected heading {heading!r} missing from page")
            return

        if spec.category == FEED:
            if not (ctype.endswith("xml") or "xml" in ctype or ctype == "text/plain"):
                result.demote(WARN, f"feed Content-Type is {ctype or '(none)'}")
            if body.strip() and not body.lstrip().startswith(("<?xml", "<")):
                result.demote(FAIL, "feed body is not XML")
            return

        if spec.category == EXPORT:
            # A download, not a page. The one thing that would be wrong is
            # serving HTML here, which would mean an error page in place of the
            # file. Emptiness is legitimate when the index is empty.
            if ctype.startswith("text/html"):
                result.demote(FAIL, f"export returned HTML instead of a download ({ctype})")
            elif not body.strip() and not self.index_is_empty:
                result.demote(WARN, "export produced an empty file on a populated index")
            return

        # JSON-ish categories.
        if not ctype.startswith("application/json"):
            # /export serves CSV; anything else non-JSON under /api is wrong.
            if spec.rule.startswith("/api/"):
                result.demote(FAIL, f"expected JSON, got Content-Type: {ctype or '(none)'}")
            return

        try:
            payload = response.json()
        except ValueError as exc:
            result.demote(FAIL, f"invalid JSON: {exc}")
            return

        required = JSON_SCHEMAS.get(spec.rule)
        if required:
            if not isinstance(payload, dict):
                result.demote(FAIL, f"expected a JSON object with keys {sorted(required)}")
            else:
                missing = set(required) - set(payload)
                if missing:
                    result.demote(FAIL, f"missing top-level key(s): {sorted(missing)}")

        if spec.rule in ALLOWED_EMPTY:
            return
        if is_degenerate(payload):
            if self.index_is_empty:
                result.demote(WARN, "empty payload (the index itself is empty)")
            else:
                result.demote(FAIL, "empty payload on a populated index - the tool has no data to draw")

    # -- Orchestration ------------------------------------------------------

    def run(self, only: str | None, include_admin: bool) -> list[Result]:
        specs = probeable_routes()

        if only:
            pattern = re.compile(only)
            specs = [s for s in specs if pattern.search(s.rule)]
        if not include_admin:
            specs = [s for s in specs if not s.is_operator]

        for spec in specs:
            path = spec.concrete_path(self.discovered)
            if path is None:
                missing = sorted(spec.needs_discovery - set(self.discovered))
                result = Result(rule=spec.rule, url=spec.rule, category=spec.category,
                                status=SKIP)
                result.notes.append(f"could not resolve {', '.join(missing)} from the live site")
                self.results.append(result)
                self._emit(result)
                continue
            result = self.check(spec, path)
            self.results.append(result)
            self._emit(result)

        return self.results

    def _emit(self, result: Result) -> None:
        colour = {PASS: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m", SKIP: "\033[90m"}
        reset = "\033[0m"
        tint = colour.get(result.status, "") if sys.stdout.isatty() else ""
        off = reset if tint else ""
        status = f"{result.http_status or '---':>3}"
        line = f"  {tint}{result.status:<4}{off} {status}  {result.elapsed_ms:>5}ms  {result.url}"
        print(line)
        for note in result.notes:
            print(f"          {note}")


# ── Reporting ───────────────────────────────────────────────────────────────

def report(crawler: Crawler, results: list[Result], started: float) -> int:
    counts = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0}
    for r in results:
        counts[r.status] += 1

    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)

    if crawler.site_findings:
        print("\n  SITE-LEVEL FINDINGS (properties of the deployment, not of one route):")
        for finding in crawler.site_findings.values():
            print(f"\n    [{finding['level']}] {finding['message']}")
            print(f"           affects {finding['count']} route(s), "
                  f"e.g. {', '.join(finding['examples'])}")

    by_category: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_category.setdefault(r.category, {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0})
        bucket[r.status] += 1
    print(f"\n  {'category':<12} {'pass':>6} {'warn':>6} {'fail':>6} {'skip':>6}")
    for category in sorted(by_category):
        b = by_category[category]
        print(f"  {category:<12} {b[PASS]:>6} {b[WARN]:>6} {b[FAIL]:>6} {b[SKIP]:>6}")
    print(f"  {'TOTAL':<12} {counts[PASS]:>6} {counts[WARN]:>6} {counts[FAIL]:>6} {counts[SKIP]:>6}")

    slowest = sorted((r for r in results if r.http_status == 200),
                     key=lambda r: r.elapsed_ms, reverse=True)[:5]
    if slowest:
        print("\n  Slowest responses:")
        for r in slowest:
            print(f"    {r.elapsed_ms:>6}ms  {r.url}")

    if counts[FAIL]:
        print(f"\n  FAILURES ({counts[FAIL]}):")
        for r in results:
            if r.status == FAIL:
                print(f"    {r.url}")
                for note in r.notes:
                    print(f"        {note}")

    if counts[WARN]:
        print(f"\n  WARNINGS ({counts[WARN]}):")
        for r in results:
            if r.status == WARN:
                print(f"    {r.url}  ->  {'; '.join(r.notes)}")

    elapsed = time.monotonic() - started
    print(f"\n  {len(results)} routes in {elapsed:.0f}s "
          f"({crawler.pacer.slept:.0f}s of that pacing to stay under the rate limits)")

    if crawler.index_is_empty:
        print("\n  NOTE: the index reports 0 articles, so empty-payload findings are")
        print("        warnings rather than failures. Re-run after a fetch completes.")
    if not crawler.admin_token:
        print("\n  NOTE: no admin token supplied, so Datastories and admin routes were")
        print("        only checked for a correct refusal. Pass --admin-token to probe them.")

    site_failed = any(f["level"] == FAIL for f in crawler.site_findings.values())
    return 1 if (counts[FAIL] or site_failed) else 0


def main(argv=None) -> int:
    # Windows consoles default to a legacy codepage, which turns any non-ASCII
    # character in the output into a replacement glyph. This runs on Windows
    # servers by design, so make the stream UTF-8 rather than hoping.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        description="Probe every route on a running Pinakes deployment.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Three things")[0].strip(),
    )
    parser.add_argument("--base-url", default=os.environ.get("PINAKES_SITE_URL", DEFAULT_BASE_URL),
                        help=f"target deployment (default: {DEFAULT_BASE_URL})")
    parser.add_argument("--admin-token", default=os.environ.get("PINAKES_ADMIN_TOKEN"),
                        help="bearer token; unlocks Datastories and admin routes")
    parser.add_argument("--jwa-user", default=os.environ.get("PINAKES_JWA_USER"))
    parser.add_argument("--jwa-password", default=os.environ.get("PINAKES_JWA_PASSWORD"))
    parser.add_argument("--include-admin", action="store_true",
                        help="also probe admin GET routes (needs --admin-token)")
    parser.add_argument("--only", metavar="REGEX",
                        help="probe only routes whose rule matches this pattern. NOTE: in "
                             "Git Bash on Windows, a pattern starting with '/' is rewritten "
                             "by MSYS path conversion - anchor on the segment instead "
                             "(e.g. 'api/citations') or use PowerShell.")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--no-cache-bust", action="store_true",
                        help="do NOT append a cache-buster. Only useful for deliberately "
                             "testing what an intermediate cache is serving.")
    parser.add_argument("--no-pace", action="store_true",
                        help="disable rate-limit pacing. Will trip the limiter on a real host.")
    parser.add_argument("--pace-multiplier", type=float, default=1.0,
                        help="scale the pacing interval (>1 is gentler)")
    parser.add_argument("--json-report", metavar="PATH",
                        help="write machine-readable results for CI")
    args = parser.parse_args(argv)

    started = time.monotonic()
    crawler = Crawler(args)

    print(f"Pinakes smoke crawl")
    print(f"Target:      {crawler.base}")
    print(f"Cache-bust:  {'on' if crawler.cache_bust else 'OFF'}")
    print(f"Pacing:      {'on' if crawler.pacer.enabled else 'OFF'}")
    print(f"Admin token: {'supplied' if crawler.admin_token else 'not supplied'}")

    print("\nDiscovering live parameter values ...")
    crawler.discover()
    if crawler.discovered:
        for key in sorted(crawler.discovered):
            print(f"  {key:<18} {crawler.discovered[key]}")
    else:
        print("  (nothing discovered — the site may be empty or unreachable)")

    print("\nProbing routes ...")
    results = crawler.run(only=args.only, include_admin=args.include_admin)
    code = report(crawler, results, started)

    if args.json_report:
        payload = {
            "base_url": crawler.base,
            "index_is_empty": crawler.index_is_empty,
            "discovered": crawler.discovered,
            "site_findings": crawler.site_findings,
            "results": [asdict(r) for r in results],
        }
        with open(args.json_report, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"\n  JSON report: {args.json_report}")

    return code


if __name__ == "__main__":
    sys.exit(main())
