"""route_inventory.py — the authoritative list of what this site exposes.

Derived from `app.url_map` at import time, never from a hand-maintained list.
That is the whole point: a hand-kept list of routes to test is wrong the day
someone adds a blueprint, and the failure is silent — the suite still passes
while the new endpoint is untested. Here, a new route shows up in the inventory
automatically, and `tests/test_route_coverage.py` fails until it is classified
and covered.

Two consumers:

    tests/test_route_coverage.py   offline: every route is accounted for
    tools/smoke_crawl.py           live: probe a running deployment

Both work from the same classification, so "what the crawler skipped" and "what
the coverage test excused" can never drift apart.

Parameterised routes carry a *source* rather than a literal id. Literals rot:
`/article/1` is meaningless against a freshly seeded database where ids start
elsewhere, and hardcoding one makes the crawler pass by accident. The crawler
resolves each source against the live site before probing (see PARAM_SOURCES).
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Iterable


# ── Categories ──────────────────────────────────────────────────────────────

# Category describes WHAT A ROUTE SERVES. It is deliberately independent of
# whether the route needs a credential — conflating the two was wrong: an
# operator page and a public page both serve HTML and should be checked the
# same way, while `/api/admin/*` and `/api/wac/*` are both JSON.
HTML = "html"            # Jinja-rendered page
JSON = "json"            # JSON API
FEED = "feed"            # Atom/RSS/OPML — XML
EXPORT = "export"        # data download (BibTeX/CSV), not a page
STATIC = "static"        # Flask's static file server
MUTATING = "mutating"    # POST/PUT/DELETE — never probed blind

# What credential a route needs, if any. Orthogonal to category.
NEEDS_ADMIN = "admin"
NEEDS_DATASTORIES = "datastories"   # the admin token also satisfies this
NEEDS_BASIC = "basic"               # /jwa, HTTP Basic


# ── Parameter sources ───────────────────────────────────────────────────────
# Maps a Werkzeug rule argument to a key the crawler discovers from the live
# site. `None` means the value cannot be synthesised and the route is probed
# with a deliberately bogus value as a negative test instead (see BOGUS).

PARAM_SOURCES: dict[str, str | None] = {
    "article_id":     "article_id",
    "book_id":        "book_id",
    "institution_id": "institution_id",
    "name":           "author_name",
    "doi":            "wac_doi",
    "slug":           "feed_slug",
    "gslug":          "feed_group_slug",
    # Signed, secret, or operator-scoped. A crawler cannot mint these, and it
    # should not: they gate unsubscribes and redaction decisions.
    "token":          None,
    "rid":            None,
    "user_tag_id":    None,
    "filename":       None,
    # Build-your-own feed selection codes are minted per selection and never
    # listed anywhere, so there is nothing to discover. Probed with a bogus
    # code, where the assertion is that it is rejected cleanly.
    "code":           None,
}

# Values used when a parameter has no source. The assertion for these routes is
# "degrades gracefully" — a 4xx or a redirect, never a 500.
BOGUS = {
    "token":       "not-a-real-token",
    "rid":         "999999",
    "user_tag_id": "999999",
    "filename":    "does-not-exist.html",
    "code":        "aaaaaa",
}


# ── Classification ──────────────────────────────────────────────────────────
# Ordered longest-prefix-first; the first match wins.

# Exact-rule overrides, consulted before the prefix table. These are the cases
# where the prefix would get it wrong.
_EXACT_RULES: dict[str, tuple[str, str | None]] = {
    # Operator console *shells*. Public HTML by design: they embed no data
    # server-side and load nothing until the operator pastes a token into
    # sessionStorage, which is then sent as Bearer on every API call. The data
    # is protected at /api/admin/*, not here. Requiring a credential for the
    # page would gain nothing and break the paste-your-token flow.
    "/admin/curate":     (HTML, None),
    "/admin/redactions": (HTML, None),
    "/admin/user-tags":  (HTML, None),

    # /feeds and /feeds/select are pages about feeds, not feeds. The /feed
    # prefix would otherwise capture them and demand XML.
    "/feeds":            (HTML, None),
    "/feeds/select":     (HTML, None),

    # The Datastories tool page is HTML behind the Datastories credential;
    # only /api/datastories/* is JSON.
    "/datastories/tools": (HTML, NEEDS_DATASTORIES),

    # /health/deep is the one health route that needs the admin token.
    "/health/deep":      (JSON, NEEDS_ADMIN),
}

_PREFIX_RULES: list[tuple[str, str, str | None]] = [
    ("/static/",              STATIC, None),
    ("/api/admin/",           JSON,   NEEDS_ADMIN),
    ("/fetch",                JSON,   NEEDS_ADMIN),
    ("/api/datastories/",     JSON,   NEEDS_DATASTORIES),
    ("/jwa",                  HTML,   NEEDS_BASIC),
    ("/health",               JSON,   None),
    ("/export",               EXPORT, None),
    ("/api/",                 JSON,   None),
]


def _feed_category(rule_str: str) -> str | None:
    """Routes under /feed serve XML only when they carry an XML suffix.

    `/feed/<slug>.xml` is the feed; `/feed/<slug>` is the human landing page
    for it. Treating both as XML is how a perfectly good HTML page gets
    reported as a malformed feed.
    """
    if not rule_str.startswith("/feed"):
        return None
    if rule_str.endswith((".xml", ".opml")):
        return FEED
    return HTML

# Routes whose correct answer is not 200. Each entry says which codes are
# acceptable and why, so "it 404s" is a documented decision rather than a
# finding someone has to re-investigate every time the crawl runs.
EXPECTED_STATUS: dict[str, tuple[set[int], str]] = {
    "/alerts/new": (
        {404},
        "404 unless PINAKES_ALERTS_ENABLED=1 - the feature ships dark on purpose "
        "so the code can deploy before the sending domain is verified.",
    ),
    "/redaction-request/orcid/callback": (
        {400},
        "An OAuth callback needs the provider's query parameters; 400 on a blind "
        "GET is the correct rejection.",
    ),
}

# Routes that need a query parameter to do anything. Values are discovery keys
# (see PARAM_SOURCES): the crawler fills them from the live site, and skips the
# route when it cannot, rather than reporting the resulting 400 as a fault.
REQUIRED_QUERY: dict[str, dict[str, str]] = {
    "/api/citations/ego":          {"article": "article_id"},
    "/api/citations/reading-path": {"article": "article_id"},
}

# Routes that answer correctly but whose response is not worth asserting on, or
# whose probe would be actively unhelpful. Each needs a reason — an unexplained
# skip is how coverage quietly erodes.
EXPLICIT_SKIPS: dict[str, str] = {
    "/static/<path:filename>":
        "Flask's static server; asset integrity is not what this suite is for.",
    "/robots.txt":
        "Static text, no application logic behind it.",
    "/datastories/login":
        "POST-only credential check; exercised by tests/test_auth.py, not by a blind probe.",
    "/datastories/logout":
        "POST-only session teardown.",
}

# Routes whose payload is legitimately allowed to be empty even on a populated
# index, so the crawler must not treat emptiness as a failure.
ALLOWED_EMPTY: set[str] = {
    "/new",                       # nothing published in the last seven days is normal
    "/api/admin/suppressed",      # an empty suppression list is the healthy state
    "/api/admin/redaction-requests",
    "/api/admin/user-tags",
}


@dataclass
class RouteSpec:
    """One URL rule, classified."""
    rule: str
    endpoint: str
    methods: frozenset
    category: str
    requires: str | None = None
    arguments: frozenset = field(default_factory=frozenset)
    skip_reason: str | None = None

    @property
    def is_get(self) -> bool:
        return "GET" in self.methods

    @property
    def is_operator(self) -> bool:
        """Needs the admin token. Used for filtering, in place of the old
        catch-all ADMIN category — an operator route can serve JSON or HTML,
        and the credential is the fact worth filtering on."""
        return self.requires == NEEDS_ADMIN

    @property
    def is_probeable(self) -> bool:
        """Can the crawler issue a blind GET at this and assert on the result?"""
        return (
            self.is_get
            and self.skip_reason is None
            and self.category not in (STATIC, MUTATING)
        )

    @property
    def unresolvable_args(self) -> set[str]:
        """Arguments with no discoverable source — probed with BOGUS values.

        Only counts arguments that actually appear as a placeholder in the
        rule. Werkzeug registers a defaulted argument as a separate rule with
        the value baked in: `/jwa/` carries `filename` in `rule.arguments` but
        has no `<filename>` in its path, because it defaults to index.html.
        Such a route is fully resolvable, and treating it as a bogus-parameter
        probe would assert that a working page must not return 200.
        """
        return {
            a for a in self.arguments
            if PARAM_SOURCES.get(a) is None
            and re.search(r"<(?:[a-zA-Z_]+:)?" + re.escape(a) + r">", self.rule)
        }

    @property
    def needs_discovery(self) -> set[str]:
        """Discovery keys this route needs before it can be probed — from both
        path arguments and required query parameters."""
        keys = {
            PARAM_SOURCES[a] for a in self.arguments
            if PARAM_SOURCES.get(a) is not None
        }
        keys |= set(REQUIRED_QUERY.get(self.rule, {}).values())
        return keys

    @property
    def expected_status(self) -> tuple[set[int], str] | None:
        """Acceptable non-200 status codes for this route, with the reason."""
        return EXPECTED_STATUS.get(self.rule)

    def concrete_path(self, values: dict[str, str]) -> str | None:
        """Substitute `values` (keyed by discovery key) into the rule.

        Returns None when a needed value was not discovered, which the crawler
        reports as a skip rather than guessing.
        """
        from urllib.parse import urlencode

        path = self.rule
        for arg in self.arguments:
            source = PARAM_SOURCES.get(arg)
            if source is None:
                replacement = BOGUS.get(arg, "0")
            else:
                if source not in values or values[source] in (None, ""):
                    return None
                replacement = str(values[source])
            path = re.sub(
                r"<(?:[a-zA-Z_]+:)?" + re.escape(arg) + r">",
                _quote(replacement),
                path,
            )

        query = {}
        for param, source in REQUIRED_QUERY.get(self.rule, {}).items():
            if source not in values or values[source] in (None, ""):
                return None
            query[param] = values[source]
        if query:
            path = f"{path}?{urlencode(query)}"
        return path


def _quote(value: str) -> str:
    """Percent-encode a path segment, leaving characters Werkzeug's `path`
    converter accepts. Author names carry spaces, periods and hyphens."""
    from urllib.parse import quote
    return quote(str(value), safe="-._~")


def classify(rule_str: str, methods: Iterable[str]) -> tuple[str, str | None]:
    """(category, required credential) for a rule.

    Category is what the route serves; the credential is a separate fact. A
    route with no GET method is MUTATING regardless of what it would serve,
    because the crawler must never probe one blind — but it keeps its
    credential so the coverage suite can still assert the route refuses
    anonymous callers.
    """
    real = set(methods) - {"HEAD", "OPTIONS"}

    if rule_str in _EXACT_RULES:
        category, requires = _EXACT_RULES[rule_str]
        return (MUTATING if "GET" not in real else category), requires

    feed = _feed_category(rule_str)
    if feed is not None:
        return (MUTATING if "GET" not in real else feed), None

    for prefix, category, requires in _PREFIX_RULES:
        if rule_str.startswith(prefix):
            return (MUTATING if "GET" not in real else category), requires

    if "GET" not in real:
        return MUTATING, None
    return HTML, None


def _ensure_importable() -> None:
    """Point DB_PATH at a scratch file before importing the app.

    Importing app.py runs init_db(), migrations and a cache pre-warm at module
    scope. Without this, merely listing the routes would touch — and migrate —
    whatever database the developer happens to have. conftest.py does the same
    thing for the test suite; this covers the standalone crawler.
    """
    os.environ.setdefault(
        "DB_PATH",
        os.path.join(tempfile.gettempdir(), "pinakes-route-inventory.db"),
    )
    os.environ.setdefault("FLASK_ENV", "testing")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)


def load_routes() -> list[RouteSpec]:
    """Every rule the application registers, classified. Sorted for stable output."""
    _ensure_importable()
    import app as _app

    specs: list[RouteSpec] = []
    for rule in _app.app.url_map.iter_rules():
        rule_str = str(rule)
        category, requires = classify(rule_str, rule.methods)
        specs.append(RouteSpec(
            rule=rule_str,
            endpoint=rule.endpoint,
            methods=frozenset(rule.methods),
            category=category,
            requires=requires,
            arguments=frozenset(rule.arguments),
            skip_reason=EXPLICIT_SKIPS.get(rule_str),
        ))
    return sorted(specs, key=lambda s: (s.category, s.rule))


def probeable_routes() -> list[RouteSpec]:
    return [s for s in load_routes() if s.is_probeable]


def summarise(specs: list[RouteSpec]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in specs:
        counts[s.category] = counts.get(s.category, 0) + 1
    return dict(sorted(counts.items()))


if __name__ == "__main__":
    routes = load_routes()
    print(f"{len(routes)} rules registered\n")
    for category, count in summarise(routes).items():
        print(f"  {category:<10} {count:>4}")

    creds: dict[str, int] = {}
    for s in routes:
        if s.requires:
            creds[s.requires] = creds.get(s.requires, 0) + 1
    if creds:
        print("\n  credential required:")
        for name, count in sorted(creds.items()):
            print(f"    {name:<14} {count:>4}")

    probeable = probeable_routes()
    print(f"\n  {'probeable':<10} {len(probeable):>4}")
    needs = sorted({k for s in probeable for k in s.needs_discovery})
    print(f"\nDiscovery keys required: {', '.join(needs)}")

    unresolvable = [s for s in probeable if s.unresolvable_args]
    if unresolvable:
        print("\nProbed with bogus values (expect 4xx / redirect, never 5xx):")
        for s in unresolvable:
            print(f"  {s.rule}")
