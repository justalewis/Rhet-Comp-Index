# 03 — Rate limiting (Prompt B2)

Audit trail for adding tiered rate limiting to public API endpoints.

## Files added / modified

- `requirements.txt` — adds `flask-limiter>=3.5` (resolved to 4.1.1 locally).
- `rate_limit.py` (new) — `limiter` instance, `client_ip_key()`, `LIMITS` dict, and `fetch_auth_failing()` exempt_when callback.
- `auth.py` — refactored to expose `_check_token()` and `token_check_passes()`. The decorator now dispatches off the same predicate, so the limiter and the auth check can share logic without duplicating it.
- `app.py` — `limiter.init_app(app)`; `@limiter.limit(...)` per-tier decorators on every targeted route; `@limiter.exempt` on `/health`; new `@app.errorhandler(429)`; `/fetch`'s inner `_run` extracted to module-level `_run_background_fetch` so tests can patch it without intercepting `threading.Thread` (Flask-Limiter's memory storage uses `threading.Timer` internally — patching Thread globally broke the limiter).
- `templates/error.html` — adds per-code default messages including 429 while leaving the 404/500 cases working.
- `fly.toml` — comment block on the `[[http_service.checks]]` section explaining `/health`'s exemption.
- `conftest.py` — autouse `_reset_rate_limiter` fixture clears in-memory storage before and after each test, so accumulated counts never leak between tests.
- `tests/test_rate_limit.py` (new) — 18 tests covering the IP key function, every limit tier, `/health` exemption, the 429 response shape (Retry-After, JSON for /api, HTML for browsers), `/static/` exemption, and the `/fetch` auth/limit interaction.
- `tests/test_routes_export.py`, `tests/test_auth.py` — three /fetch-related tests updated to patch `app._run_background_fetch` instead of `app.threading.Thread`.

## Limit values — the math

| Tier | Limit | Reasoning |
|---|---|---|
| **default** | 60/min | 1/sec. Comfortably above any human reading the homepage (~10/min for an active reader paginating). Catches loops without hitting normal use. |
| **citations** | 20/min | Each call runs a NetworkX computation that takes 0.5–3s on the production DB. 20/min = one every 3s, which is the wall-clock floor anyway; tighter than this and a legitimate operator clicking through visualisations would 429 themselves. |
| **stats** | 20/min | Same shape as citations — DB aggregations across hundreds of MB of articles. |
| **search** | 120/min | Typeahead. A user typing "first-year composition pedagogy" with debouncing fires ~10–20 requests per second of typing; conservative budget allows for two such bursts per minute. |
| **fetch** | 6/hour | The operator triggers fetches manually. Six in an hour catches a runaway script. Combined with `exempt_when=fetch_auth_failing`, unauthenticated probes never consume from this budget. |

Static assets (`/static/...`) are exempted from the default tier via `default_limits_exempt_when` so 404s on legitimate page loads (which fetch CSS/JS) do not eat the user's budget on first visit.

## Why in-memory, not Redis

- Pinakes runs **one Fly.io machine, one gunicorn worker**. Limit counts in-process are accurate.
- The Flask-Limiter docs warn that in-memory storage is per-worker. With one worker, "per-worker" = "global."
- A Redis dependency adds operational surface (a second container, network round-trips, retry logic) for zero benefit at this scale.

**Upgrade path** — if Pinakes ever scales to multiple workers or multiple machines:
1. Add the Fly Redis add-on: `flyctl redis create`.
2. Set `REDIS_URL` as a Fly secret.
3. Change `storage_uri="memory://"` to `storage_uri=os.environ["REDIS_URL"]` in `rate_limit.py`.
4. No other code changes; the Limiter API is identical.

## Why `/fetch` keeps both decorators with `exempt_when`

The constraint from the prompt: "auth checked first, unauthenticated requests should not count against the rate budget."

Flask-Limiter's `@limiter.limit(...)` is enforced via a `before_request` hook that runs **before** any view-level decorator. So the naive `@limiter.limit(...)` + `@require_admin_token` stack would charge the budget on every probe before auth could 401 it.

Solution: `@limiter.limit(LIMITS["fetch"], exempt_when=fetch_auth_failing)`. The `exempt_when` callback runs the same `_check_token()` predicate the auth decorator uses. If auth would fail, the limit is skipped entirely and the request flows through to the view, where `require_admin_token` returns the proper 401/403/503. If auth passes, the limit is checked normally.

Cost: one extra `_check_token()` call per `/fetch` request. The token comparison is one `hmac.compare_digest` over <50 bytes; negligible.

## Logging

Per the constraint, rate-limit hits log at `DEBUG` only — not INFO or above. The `app.errorhandler(429)` writes:

```
DEBUG  rate limit hit: ip=<source> path=<route> description=<limit string>
```

This means you won't see hits in production logs by default (Fly is INFO+). Increase the log level when investigating, or wait for prompt D1 (Sentry) to capture them as breadcrumbs.

## Monitoring

When prompt D1 lands and Sentry is wired in:
- 429 responses won't be sent to Sentry as errors (they're expected).
- Add a custom Sentry breadcrumb in the 429 handler if hit-rate visibility is wanted.
- Track via `sentry_sdk.metrics.incr("rate_limit.hit", tags={"path": request.path, "tier": ...})` — but only if you've enabled Sentry metrics, which we haven't decided on yet.

For now, the cheap monitoring path is: tail Fly logs at DEBUG when investigating an outage, and check `/health` for liveness. The `Retry-After` header on 429 responses tells legitimate clients exactly how long to back off, which is the more important piece.

## Decisions explicitly NOT made in this prompt

- **Per-user / per-API-key limits.** Pinakes has no users beyond the operator. Single-key bucket is fine.
- **Sliding-window vs fixed-window strategy.** Flask-Limiter defaults to fixed-window. Sliding window is more accurate but uses more storage. Fixed is fine for our scale.
- **Burst allowances** (e.g., "20/min with a burst of 30"). Flask-Limiter supports this but the simple flat caps are easier to reason about. Revisit if the operator complains.
- **Redis backend.** See "Upgrade path" above.
- **Custom 503 from the limiter.** Storage failures swallow errors (`swallow_errors=True`) and let the request through. Better to over-serve than to 5xx because the in-memory dict had a hiccup.
