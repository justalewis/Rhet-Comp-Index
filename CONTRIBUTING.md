# Contributing to Pinakes

Pinakes is a small project maintained by one person ([Justin Lewis](https://github.com/justalewis)). External contributions are welcome and likely to be reviewed quickly, but please read this document before opening a pull request — there are a few non-obvious requirements.

## Scraping ethics

The most important rule. Pinakes ingests data from publisher sites, journal feeds, and open APIs. Every ingester is bound by these principles:

1. **Identify yourself.** Every HTTP request must carry a descriptive `User-Agent` that names the project and gives a way to reach it. Follow [`fetcher.py`](fetcher.py): it names the site (from `PINAKES_SITE_URL`) and adds a `mailto:` from the `CROSSREF_MAILTO` environment variable when that is set, which keeps the address out of the repository. Not every ingester does this yet: `HEADERS` in [`scraper.py`](scraper.py) and `USER_AGENT` in [`rss_fetcher.py`](rss_fetcher.py) still carry the placeholder `your-email@example.com`, and the Kairos header in `scraper.py` names the site but gives no address. Don't copy those into a new ingester.
2. **Respect `robots.txt`.** Inline comments in `scraper.py` record the date each site's robots was checked and which paths are allowed; a new scraper must include the same kind of annotation before it lands.
3. **Rate-limit conservatively.** Default 1 request/second; lower (e.g., 5–10 s) for hand-built sites, journal archives, or anywhere `Crawl-delay` is declared. The existing scrapers use `time.sleep` with explicit constants.
4. **Don't fetch full text.** Pinakes is a metadata index. Scrapers extract titles, authors, dates, abstracts, and DOIs only. Article body / PDF content is out of scope.
5. **Cache what you fetch.** Re-running an ingester should re-use already-stored records (`upsert_article` is idempotent on URL); it should not re-hit the source for unchanged data.
6. **Surface errors.** Failed fetches log at `WARNING` with the URL and reason. Silent failures hide real problems.
7. **Prefer open APIs over scraping.** CrossRef → OpenAlex → publisher RSS → custom scraper, in that order. Add a scraper only when the upstream paths don't have the data.
8. **Never scrape paywalled content.** If `robots.txt` permits a path but the page is gated, treat it as a "do not fetch" signal regardless.
9. **Document the journal-specific quirks.** Each scraper function in `scraper.py` should explain what era of the site it handles and why a generic approach wouldn't work.

A new scraper is reviewed against this list before it merges. If it fails any of the nine, the PR will be asked to fix the issue or to remove the scraper.

## Tests are required

The harness lives in `tests/`; conventions are documented in [`docs/refactor-notes/01-test-harness.md`](docs/refactor-notes/01-test-harness.md). For most changes:

- **Any new database query function** in the [database layer](db/) (organized by concern under `db/`: `articles.py`, `authors.py`, `citations.py`, `books.py`, `institutions.py`, `coverage.py`, `datastories.py`, `user_tags.py`, `wac.py`, `fetch_log.py`, and `core.py` for the connection, schema and migrations): add a unit test in `tests/test_db_*.py` covering one happy path and one edge case (empty input, missing DB, etc.).
- **Any new route** (routes live in the blueprints under [`blueprints/`](blueprints/); [`app.py`](app.py) only registers them): add a smoke test in `tests/test_routes_*.py` that exercises a 200 response and asserts a content marker. `test_route_count_matches_expected` in [`tests/test_routes_html.py`](tests/test_routes_html.py) asserts the total number of routes, so raise that count and add a line to the comment above it saying what you added.
- **Any new ingester or parser**: add a test that uses a captured fixture in `tests/fixtures/` and stubs the HTTP layer with `responses` or `feedparser` mocks. Never make a network call from the test suite.

Run the suite locally before pushing:

```bash
pip install -r requirements-dev.txt
pytest                                       # full suite
pytest --cov=. --cov-report=term-missing     # with coverage
```

CI ([`test.yml`](.github/workflows/test.yml)) runs the same suite on pushes and pull requests to `main`. A red run blocks the deploy of the former Fly.io app (`fly-deploy.yml`), which is no longer production; the production server is updated by hand with `deploy/windows/Update-Pinakes.ps1` and does not check CI, so run the tests yourself before anything goes to `main`.

## Style

Python code is checked informally — no required formatter today, but follow the prevailing style:

- Imperative function names; type hints on new functions where they aid clarity.
- Comments only where the *why* isn't obvious; never narrate the code.
- Match the existing two-blank-line / module-docstring conventions in the [database layer](db/) and [`app.py`](app.py).

For HTML / Jinja: extend `templates/base-alexandrian.html` (full layout, with the sidebar) or `templates/base-core.html` (minimal — used for fail states like `error.html`); don't duplicate the `<head>`.

For CSS: style components in [`static/pinakes.css`](static/pinakes.css), against the tokens, never with literal colours. `static/tokens.css` and `static/fonts.css` are copies synced from Eunomia's Labors; edit them upstream, not here, or the next sync reverts you. [`static/README.md`](static/README.md) has the rules, and [`docs/refactor-notes/17-alexandrian-rebuild.md`](docs/refactor-notes/17-alexandrian-rebuild.md) the history (the old three-theme setup is gone).

## Commit messages

- [Conventional Commits](https://www.conventionalcommits.org/) subject: `type(scope): summary`, with the summary lowercase (proper nouns aside), imperative mood, no trailing period, ≤ 72 chars (e.g. `feat(citations): add bibcoupling network endpoint`, not "Added a new endpoint for bibcoupling networks."). Recent commits use `feat`, `fix`, `docs`, and `chore`.
- Body wrapped at 72 chars; explain the *why* if it isn't obvious from the diff.
- Reference an issue with `#123` if one exists; not required.

## Large structural changes

Pinakes uses a "Claude Code prompt" workflow for non-trivial refactors. Each prompt is a self-contained spec: scope, constraints, deliverables, definition of done, and explicit "what you must NOT do." Audit-trail notes for completed prompts live in [`docs/refactor-notes/`](docs/refactor-notes/).

If you want to propose a structural change, drafting it as a prompt first (and opening a discussion before any code) tends to get faster review. To reach the maintainer, open an issue on this repository. (`fetcher.py` no longer carries a contact address; its `mailto:` comes from the `CROSSREF_MAILTO` environment variable.)

## License

By contributing you agree your work will be released under the project's [GPL-3.0 license](LICENSE).
