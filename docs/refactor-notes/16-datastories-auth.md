# 16 — Datastories: public landing + password-gated tools

The Datastories surface used to be hidden in production via the
`datastories_enabled()` flag — the whole blueprint was conditionally
registered, so `/datastories` returned 404 on Fly. That worked while the
project was a private development branch but meant there was nothing to
share with collaborators or editors short of "merge this branch and fly
it yourself."

This refactor flips the model: the blueprint always registers, the
landing page at `/datastories` is publicly reachable, and the actual
tool surface (now at `/datastories/tools`) plus every `/api/datastories/*`
endpoint sits behind a single shared password.

## What's at each route now

```
/datastories         landing page; describes the project. Public.
/datastories/login   POST password, set 14-day cookie. Rate-limited.
/datastories/logout  POST clears cookie.
/datastories/tools   the existing accordion-of-chapters tool surface.
                     Auth-gated: cookie or admin-token.
/api/datastories/*   the 26 JSON endpoints. Auth-gated; 401 on miss.
```

## Auth model

Two ways to pass the gate, both implemented in `auth_datastories.py`:

1. **Signed session cookie**, set by `/datastories/login` after the user
   submits the correct password. Signed with the app's `SECRET_KEY` via
   `itsdangerous.URLSafeTimedSerializer`, stored under cookie name
   `pinakes_ds`, max-age 14 days, `httponly`, `samesite=Lax`, `secure`
   when the request is HTTPS.
2. **Admin bearer token**, the same `PINAKES_ADMIN_TOKEN` that gates
   the existing `/api/admin/*` mutation endpoints. Reusing this saves
   the operator from juggling a second secret for scripted/CI access
   to the Datastories API.

`is_authenticated()` in `auth_datastories.py` checks the cookie first,
falls through to admin-token. Either path through is sufficient.

## Why 14 days

Pulled out of the audit's tradeoff space. 30 days is the comfortable
"researcher logging in once a week doesn't have to re-enter" threshold;
7 days tightens the leak window but means active collaborators re-enter
weekly. 14 days lands between the two — about an academic two-week
sprint cadence — and matches how I expect collaborators to use the
tools (visit, look at one chapter's analysis, leave for a week or two,
come back).

If you decide a different TTL is right after a few weeks of use, change
`COOKIE_TTL_SECONDS` in `auth_datastories.py`. Existing cookies stay
valid until their original 14-day expiry.

## Required environment variables

```
PINAKES_DATASTORIES_PASSWORD   the shared password
PINAKES_SECRET_KEY             cookie signing key; required in prod
                               (per-process random key in local dev)
```

Both are loaded via `python-dotenv` from a local `.env` (gitignored)
when running `python app.py`. On Fly:

```
fly secrets set PINAKES_DATASTORIES_PASSWORD=<value>
fly secrets set PINAKES_SECRET_KEY=<value>     # generate with secrets.token_urlsafe(48)
```

If `PINAKES_SECRET_KEY` is unset on Fly the app raises `RuntimeError`
on startup — the deploy will fail rather than ship with a per-process
key that would log everyone out on every worker restart. This is
deliberate fail-fast behaviour.

If `PINAKES_DATASTORIES_PASSWORD` is unset, the app starts but every
gated route returns 503 with a "password not configured" error. The
landing page still renders.

## Behavioural changes for existing users

- The Datastories link in the top nav is now **always** visible. It
  used to be hidden on Fly entirely.
- Direct links to old `/datastories#chN-tool-name` URLs now hit the
  public landing page, not the tools. Bookmarks must be updated to
  `/datastories/tools#chN-tool-name`. The nav-menu entries were
  updated in this same change to point at the new URLs.
- The `datastories_enabled()` function in `app.py` always returns
  True now. It's kept as a function (rather than removed) for
  backward compatibility with `inject_globals()` and any template
  that still checks `{% if datastories_enabled %}`. It will likely
  be removed entirely in a future cleanup.

## Files touched

  auth_datastories.py            new — auth helpers + decorators
  templates/datastories_landing.html  new — landing page with login form
  app.py                         dotenv loading; SECRET_KEY config;
                                 always-register blueprint;
                                 datastories_enabled simplified
  blueprints/datastories.py      route restructure; 26 API decorators
  templates/_feature_nav.html    drop the {% if datastories_enabled %}
                                 wrapper; update tool links to /tools
  requirements.txt               add python-dotenv

## Deploy checklist

Before merging this branch to main (which auto-deploys to Fly):

```
fly secrets set PINAKES_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
fly secrets set PINAKES_DATASTORIES_PASSWORD=<your-chosen-password>
```

Then merge. The deploy will register the Datastories blueprint, the
landing page will be public, and tools will accept the password.

## Verified

- `/datastories` returns 200, renders the landing copy with login form.
- `/datastories/tools` without cookie → 302 to `/datastories`.
- `/api/datastories/*` without cookie → 401 JSON error.
- Wrong password → 401, landing page re-renders with inline error.
- Right password → 302 → `/datastories/tools` with cookie set.
- With cookie, `/datastories/tools` → 200, full tool surface renders.
- With cookie, `/api/datastories/*` → 200, real JSON.
- `/datastories/logout` clears the cookie and redirects to landing.
