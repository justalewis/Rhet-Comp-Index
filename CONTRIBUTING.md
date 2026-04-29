# Contributing to Pinakes

Pinakes is a small project maintained by one person ([Justin Lewis](https://github.com/justalewis)). External contributions are welcome and likely to be reviewed quickly, but please read this document before opening a pull request — there are a few non-obvious requirements.

## Scraping ethics

The most important rule. Pinakes ingests data from publisher sites, journal feeds, and open APIs. Every ingester is bound by these principles:

1. **Identify yourself.** Every HTTP request must carry a descriptive `User-Agent` containing a contact email and a one-line description (see [`scraper.py`](scraper.py) and [`fetcher.py`](fetcher.py) for the canonical strings).
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

- **Any new database query function** in [`db.py`](db.py): add a unit test in `tests/test_db_*.py` covering one happy path and one edge case (empty input, missing DB, etc.).
- **Any new route in [`app.py`](app.py)**: add a smoke test in `tests/test_routes_*.py` that exercises a 200 response and asserts a content marker.
- **Any new ingester or parser**: add a test that uses a captured fixture in `tests/fixtures/` and stubs the HTTP layer with `responses` or `feedparser` mocks. Never make a network call from the test suite.

Run the suite locally before pushing:

```bash
pip install -r requirements-dev.txt
pytest                                       # full suite
pytest --cov=. --cov-report=term-missing     # with coverage
```

CI runs the same suite on every push and blocks the Fly deploy on test failures.

## Style

Python code is checked informally — no required formatter today, but follow the prevailing style:

- Imperative function names; type hints on new functions where they aid clarity.
- Comments only where the *why* isn't obvious; never narrate the code.
- Match the existing two-blank-line / module-docstring conventions in [`db.py`](db.py) and [`app.py`](app.py).

For HTML / Jinja: extend `templates/base.html` (full layout) or `templates/base-core.html` (minimal — used for fail states like `error.html`); don't duplicate the `<head>`.

For CSS: there are three theme files in `static/`. See [`static/style.css`](static/style.css) for the default and [`docs/refactor-notes/05-css-audit.md`](docs/refactor-notes/05-css-audit.md) for the theme-switching mechanism.

## Commit messages

- Subject line in the imperative mood, sentence case, ≤ 72 chars (e.g. "Add bibcoupling network endpoint", not "Added a new endpoint for bibcoupling networks.").
- Body wrapped at 72 chars; explain the *why* if it isn't obvious from the diff.
- Reference an issue with `#123` if one exists; not required.

## Large structural changes

Pinakes uses a "Claude Code prompt" workflow for non-trivial refactors. Each prompt is a self-contained spec: scope, constraints, deliverables, definition of done, and explicit "what you must NOT do." Audit-trail notes for completed prompts live in [`docs/refactor-notes/`](docs/refactor-notes/).

If you want to propose a structural change, drafting it as a prompt first (and opening a discussion before any code) tends to get faster review. The maintainer is generally available via the contact email in `fetcher.py`.

## License

By contributing you agree your work will be released under the project's [GPL-3.0 license](LICENSE).
