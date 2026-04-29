# Rhetoric & Composition — Current Scholarship

A local web app that aggregates newly published articles from 14 major Rhet/Comp journals via the CrossRef API.

## Setup

```bash
pip install -r requirements.txt
```

## First run — fetch articles

```bash
# Fetch all 14 journals (will take a few minutes on the first run)
python fetcher.py

# Or fetch a single journal by ISSN
python fetcher.py 0010-096X
```

## Run the web app

```bash
python app.py
# Open http://localhost:5000
```

## Keep it updated automatically

Run the scheduler in a separate terminal (or at login):

```bash
python scheduler.py
```

This runs an incremental fetch at startup and again every 24 hours.

You can also click **Refresh from CrossRef** in the sidebar to trigger a manual fetch anytime.

## Project structure

```
├── app.py          Flask web server
├── fetcher.py      CrossRef API integration
├── scheduler.py    APScheduler daily refresh
├── db.py           SQLite database layer
├── journals.py     Journal names and ISSNs
├── templates/
│   └── index.html  Main page template
├── static/
│   └── style.css   Stylesheet
├── articles.db     SQLite database (created on first run)
└── requirements.txt
```

## Running tests

```bash
pip install -r requirements-dev.txt
pytest                    # full suite (fast tests only)
pytest -m "not slow"      # explicit fast suite
pytest --cov=. --cov-report=term-missing  # with coverage
```

The harness uses an isolated SQLite file per test (no production data is touched) and stubs all HTTP via `responses` and `feedparser` mocks. CI runs the suite on every push and pull request, and the Fly deploy is gated on test passage.

## Notes

- The CrossRef API is free and requires no API key
- Edit the `User-Agent` string in `fetcher.py` to include your email (polite API practice)
- On a first full fetch, CrossRef rate limits may slow things down slightly; subsequent incremental fetches are fast
