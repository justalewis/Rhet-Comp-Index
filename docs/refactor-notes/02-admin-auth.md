# 02 — Admin token authentication (Prompt B1)

Audit trail for adding bearer-token authentication to mutating endpoints, currently `POST /fetch`.

## Files added / modified

- `auth.py` (new) — `require_admin_token` decorator, `admin_token_configured()` helper, `_client_ip()` and `_truncate()` private helpers.
- `app.py` — imports the decorator; applies `@require_admin_token` to `/fetch`; adds a startup `log.critical` when `PINAKES_ADMIN_TOKEN` is missing; `/health` now returns JSON with `admin_auth: "configured" | "missing"`.
- `scheduler.py` — module docstring updated to record that the scheduler invokes ingesters in-process and bypasses HTTP auth by design.
- `fly.toml` — comment block at top documents that `PINAKES_ADMIN_TOKEN` is a Fly *secret*, not an `[env]` value.
- `README.md` — new "Deployment secrets" section with the `flyctl secrets set` recipe, the `curl -X POST /fetch` example, and a pointer to `/health` for status verification.
- `tests/test_auth.py` (new) — 19 tests covering each branch of the decorator and its integration with `/fetch` and `/health`.
- `tests/test_routes_export.py` — existing `/fetch` and `/health` tests updated to match the new contract (auth header on `/fetch`, JSON on `/health`).

## Decision rationale

**Single-administrator shared secret over OAuth/JWT/sessions.**

Pinakes has one operator. Building user accounts, password hashing, sessions, or an OAuth integration would add far more code (and attack surface) than the threat justifies. The shared-secret model is:

- **One env var** (`PINAKES_ADMIN_TOKEN`), set as a Fly secret, rotated by re-running `flyctl secrets set`.
- **One decorator** (`require_admin_token`), applied with one line per route.
- **Zero new dependencies.** Only `hmac` (stdlib) and Flask primitives we already use.

If a second administrator ever joins the project, this should be revisited — not because shared secrets stop working at two people, but because rotating a shared secret across multiple human operators is the failure mode that makes them slide back to "let's just commit it to a private gist." A real auth provider belongs in the codebase before that conversation, not after.

## Threat model

**What this protects against:**

- **Random scanning.** A scanner that POSTs to `/fetch` blindly cannot trigger a fetch run.
- **Casual abuse.** Someone who reads the source on GitHub and tries to invoke `/fetch` directly cannot do so without the token.
- **Accidental exposure of `/fetch` in dev tooling.** Anything missing the bearer header is rejected.

**What this does NOT protect against:**

- **Compromise of the administrator's machine** (the token lives in shell history / env / fly secrets).
- **Token leakage via referrer headers, browser history, or server logs at the proxy layer.** Bearer tokens in `Authorization` headers are *less* exposed than query-string or cookie tokens, but a misconfigured proxy could still log them.
- **A trusted insider** with `flyctl secrets list` access. There is no separation of duties here by design.

**Token rotation cadence:** at any sign of leakage, immediately. Otherwise, on a rolling basis (e.g., quarterly). Rotation is one command and forces no client downtime since `/fetch` is only called by the operator.

## Constant-time comparison

`hmac.compare_digest` is used instead of `==`. This defeats timing side-channel attacks where an attacker could otherwise binary-search the token by measuring response latency. For a 32-byte URL-safe random token (`secrets.token_urlsafe(32)` → 43 chars of entropy ≈ 256 bits), a timing attack is computationally infeasible regardless — but the cost of using `compare_digest` is zero, so we use it.

## Logging

Every auth failure logs at `WARNING` with:

- Source IP (preferring `Fly-Client-IP` header so the Fly proxy doesn't appear as the client).
- Route path.
- The first four characters of the supplied token followed by `...`.

The full token is **never** logged. The "first 4 chars" is enough to correlate repeated attempts from the same attacker without leaking the secret if logs are exposed (e.g., in Sentry breadcrumbs or shipped to an aggregator).

## Decisions explicitly NOT made in this prompt

- **Rate limiting on `/fetch`.** Belongs to prompt B2.
- **A 429 handler.** Belongs to prompt B2.
- **Auth on read endpoints.** Out of scope; read endpoints stay anonymous.
- **A user-account system.** Out of scope and intentionally rejected (see decision rationale).
