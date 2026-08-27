"""Every route the application registers is exercised, offline, against the
seeded fixture database.

This is the ratchet. The rest of the suite tests routes someone remembered to
write a test for; `tests/_route_schemas.py` names 26 API routes against the 56
the app actually registers, and until this file existed the 30 `/api/wac/*` and
25 `/api/datastories/*` endpoints had no route-level coverage at all. Nothing
failed when they broke, because nothing looked.

The route list comes from `app.url_map` via tools/route_inventory.py, so adding
a blueprint adds tests. A new endpoint that 500s, returns malformed JSON, or
renders an error page arrives here red without anyone remembering to wire it up.

What this does NOT do: assert that any particular payload is *correct*. It
asserts that every route answers, answers in the right shape, and does not blow
up — the floor, not the ceiling. Per-route semantics belong in the focused test
modules alongside the code they cover.

The live equivalent, run against a deployed host rather than a fixture
database, is `python -m tools.smoke_crawl`. Both read the same inventory, so
what one skips the other skips, for the same stated reason.
"""

from __future__ import annotations

import json

import pytest

from tools.route_inventory import (
    EXPORT, FEED, HTML, JSON,
    NEEDS_ADMIN, NEEDS_BASIC, NEEDS_DATASTORIES,
    probeable_routes,
)

ADMIN_TOKEN = "route-coverage-admin-token"
DATASTORIES_PASSWORD = "route-coverage-datastories-password"
JWA_USER, JWA_PASSWORD = "editor", "route-coverage-jwa-password"

# Ids and names the deterministic seed creates (tests/_seed.py).
SEEDED = {
    "article_id":     "1",
    "author_name":    "Jane Smith",
    "book_id":        "1",
    "institution_id": "1",
}


# ── Module-scoped seeded app ────────────────────────────────────────────────
# Module-scoped rather than per-test: re-seeding for each of ~130 parametrised
# routes would dominate the suite's runtime for no isolation benefit, since
# every route here is a read.

@pytest.fixture(scope="module")
def coverage_env():
    mp = pytest.MonkeyPatch()
    mp.setenv("PINAKES_ADMIN_TOKEN", ADMIN_TOKEN)
    mp.setenv("PINAKES_DATASTORIES_PASSWORD", DATASTORIES_PASSWORD)
    mp.setenv("PINAKES_JWA_USER", JWA_USER)
    mp.setenv("PINAKES_JWA_PASSWORD", JWA_PASSWORD)
    # The alerts feature 404s its signup form unless explicitly enabled, and the
    # inventory records that as expected. Turning it on here covers the routes.
    mp.setenv("PINAKES_ALERTS_ENABLED", "1")
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def coverage_client(tmp_path_factory, coverage_env):
    import app as _app_module
    import db as _db
    import health as _health

    db_file = tmp_path_factory.mktemp("route-coverage") / "coverage.db"
    original = _db.DB_PATH
    _db.DB_PATH = str(db_file)
    coverage_env.setenv("DB_PATH", str(db_file))
    _db.init_db()

    from tests._seed import seed_database
    seed_database(db_file)

    _app_module._sidebar_cache = None
    _app_module._sidebar_ts = 0.0
    _db._DETAILED_COVERAGE_CACHE.clear()
    _health.clear_integrity_cache()

    _app_module.app.config["TESTING"] = True
    yield _app_module.app.test_client()

    _db.DB_PATH = original


@pytest.fixture(scope="module")
def discovered(coverage_client):
    """Parameter values resolved against the seeded database.

    Mirrors tools/smoke_crawl.Crawler.discover. Feed slugs and WAC DOIs are read
    off the running app rather than hardcoded, because they derive from the
    journal configuration and would otherwise silently rot when it changes.
    """
    import re

    values = dict(SEEDED)

    opml = coverage_client.get("/feed.opml")
    if opml.status_code == 200:
        body = opml.get_data(as_text=True)
        slugs = re.findall(r"/feed/([A-Za-z0-9\-_]+)\.xml", body)
        groups = re.findall(r"/feed/group/([A-Za-z0-9\-_]+)\.xml", body)
        if slugs:
            values["feed_slug"] = slugs[0]
        if groups:
            values["feed_group_slug"] = groups[0]

    collections = coverage_client.get("/api/wac/collections")
    if collections.status_code == 200:
        try:
            payload = collections.get_json()
            rows = payload if isinstance(payload, list) else (payload or {}).get("collections") or []
            for row in rows[:1]:
                if isinstance(row, dict) and row.get("doi"):
                    values["wac_doi"] = str(row["doi"])
        except Exception:
            pass

    # No published list of build-your-own selection codes; a bogus one proves
    # the route rejects cleanly rather than 500ing.
    values.setdefault("feed_select_code", "aaaaaa")
    return values


def _auth_headers(spec):
    if spec.requires in (NEEDS_ADMIN, NEEDS_DATASTORIES):
        return {"Authorization": f"Bearer {ADMIN_TOKEN}"}
    if spec.requires == NEEDS_BASIC:
        import base64
        raw = base64.b64encode(f"{JWA_USER}:{JWA_PASSWORD}".encode()).decode()
        return {"Authorization": f"Basic {raw}"}
    return {}


ROUTES = probeable_routes()
# Split on the credential, not on what the route serves: an operator route may
# serve HTML or JSON, and what makes it different is that it must refuse
# anonymous callers.
GET_ROUTES = [s for s in ROUTES if not s.is_operator]
ADMIN_GET_ROUTES = [s for s in ROUTES if s.is_operator]


def _ids(specs):
    return [s.rule for s in specs]


# ── The inventory itself ────────────────────────────────────────────────────

def test_inventory_is_not_empty():
    """A silently empty inventory would make every test below vacuously pass."""
    assert len(ROUTES) > 100, f"expected the full route surface, found {len(ROUTES)}"


def test_every_route_has_a_known_category():
    known = {HTML, JSON, FEED, EXPORT}
    unknown = [s for s in ROUTES if s.category not in known]
    assert not unknown, f"unclassified routes: {[s.rule for s in unknown]}"


def _rule_to_regex(rule: str):
    """Werkzeug rule -> a regex matching the concrete paths it accepts.

    A `path:` converter matches across slashes; every other converter stops at
    one. Author names arrive as `<path:name>` precisely because they contain
    characters the default converter would split on.
    """
    import re as _re

    parts, cursor = [], 0
    for match in _re.finditer(r"<(?:([a-zA-Z_]+):)?([a-zA-Z_]+)>", rule):
        parts.append(_re.escape(rule[cursor:match.start()]))
        parts.append(".+" if match.group(1) == "path" else "[^/]+")
        cursor = match.end()
    parts.append(_re.escape(rule[cursor:]))
    return _re.compile("^" + "".join(parts) + "$")


def test_json_schema_entries_reference_real_routes():
    """Guard the other direction: a schema entry naming a route that no longer
    exists is dead weight that reads as coverage."""
    from tests._route_schemas import JSON_ROUTE_SCHEMAS

    matchers = [_rule_to_regex(s.rule) for s in ROUTES]
    stale = [
        path for path in JSON_ROUTE_SCHEMAS
        if not any(m.match(path.split("?")[0]) for m in matchers)
    ]
    assert not stale, f"schema entries for routes that no longer exist: {stale}"


# ── Every route answers ─────────────────────────────────────────────────────

@pytest.mark.parametrize("spec", GET_ROUTES, ids=_ids(GET_ROUTES))
def test_route_responds(spec, coverage_client, discovered):
    path = spec.concrete_path(discovered)
    if path is None:
        missing = sorted(spec.needs_discovery - set(discovered))
        pytest.skip(f"could not resolve {missing} from the seeded database")

    response = coverage_client.get(path, headers=_auth_headers(spec))

    assert response.status_code < 500, (
        f"{path} returned {response.status_code}\n"
        f"{response.get_data(as_text=True)[:600]}"
    )

    expected = spec.expected_status
    if expected and response.status_code in expected[0]:
        pytest.skip(f"HTTP {response.status_code} as expected - {expected[1]}")

    if spec.unresolvable_args:
        assert response.status_code != 200, (
            f"{path} accepted a deliberately invalid parameter with HTTP 200"
        )
        return

    if 300 <= response.status_code < 400:
        return

    assert response.status_code == 200, (
        f"{path} returned {response.status_code}: "
        f"{response.get_data(as_text=True)[:300]}"
    )


@pytest.mark.parametrize("spec", GET_ROUTES, ids=_ids(GET_ROUTES))
def test_route_content_type_and_shape(spec, coverage_client, discovered):
    path = spec.concrete_path(discovered)
    if path is None:
        pytest.skip("parameters unresolvable")

    response = coverage_client.get(path, headers=_auth_headers(spec))
    if response.status_code != 200 or spec.unresolvable_args:
        pytest.skip(f"HTTP {response.status_code}; shape assertions need a 200")

    ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip()
    body = response.get_data(as_text=True)

    if spec.category == HTML:
        assert ctype.startswith("text/html"), f"{path}: Content-Type {ctype}"
        for marker in ("Traceback (most recent call last)", "jinja2.exceptions"):
            assert marker not in body, f"{path}: error page rendered with HTTP 200"
        return

    if spec.category == FEED:
        # OPML is XML but is served as text/x-opml, which does not contain the
        # substring "xml" — hence the explicit allowance rather than a match.
        assert ("xml" in ctype or "opml" in ctype or ctype == "text/plain"), (
            f"{path}: Content-Type {ctype}"
        )
        assert body.lstrip().startswith(("<?xml", "<")), f"{path}: body is not XML"
        return

    if spec.category == EXPORT:
        assert not ctype.startswith("text/html"), (
            f"{path}: export returned HTML instead of a download"
        )
        return

    assert ctype.startswith("application/json"), f"{path}: Content-Type {ctype}"
    try:
        json.loads(body)
    except ValueError as exc:
        pytest.fail(f"{path}: invalid JSON — {exc}\n{body[:300]}")


@pytest.mark.parametrize("spec", GET_ROUTES, ids=_ids(GET_ROUTES))
def test_api_responses_are_not_shared_cacheable(spec, coverage_client, discovered):
    """API payloads change the moment a fetch lands, and a shared cache in front
    of the origin cannot be told. `public` there let IIS/ARR pin hour-stale
    copies of every endpoint on the Windows deployment. Feeds are the deliberate
    exception and set their own header.
    """
    if spec.category != JSON:
        pytest.skip("only API routes carry this constraint")

    path = spec.concrete_path(discovered)
    if path is None:
        pytest.skip("parameters unresolvable")

    response = coverage_client.get(path, headers=_auth_headers(spec))
    cache = (response.headers.get("Cache-Control") or "").lower()
    if not cache:
        pytest.skip("route sets no Cache-Control")
    assert "public" not in cache, (
        f"{path} is shared-cacheable (Cache-Control: {cache}); expected 'private'"
    )


# ── Admin routes ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("spec", ADMIN_GET_ROUTES, ids=_ids(ADMIN_GET_ROUTES))
def test_admin_route_requires_a_token(spec, coverage_client, discovered):
    """Every operator route rejects an unauthenticated caller. This is the one
    place the suite checks the negative rather than the happy path — an admin
    endpoint that quietly went public would otherwise pass everything above.
    """
    path = spec.concrete_path(discovered)
    if path is None:
        pytest.skip("parameters unresolvable")

    response = coverage_client.get(path)
    assert response.status_code in (401, 403, 503), (
        f"{path} answered {response.status_code} with no admin token; "
        "operator routes must refuse anonymous callers"
    )


@pytest.mark.parametrize("spec", ADMIN_GET_ROUTES, ids=_ids(ADMIN_GET_ROUTES))
def test_admin_route_accepts_a_token(spec, coverage_client, discovered):
    path = spec.concrete_path(discovered)
    if path is None:
        pytest.skip("parameters unresolvable")

    response = coverage_client.get(path, headers=_auth_headers(spec))
    assert response.status_code < 500, (
        f"{path} returned {response.status_code} with a valid token\n"
        f"{response.get_data(as_text=True)[:400]}"
    )
    assert response.status_code not in (401, 403), (
        f"{path} rejected a valid admin token"
    )
