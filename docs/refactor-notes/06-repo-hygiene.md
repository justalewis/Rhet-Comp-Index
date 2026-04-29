# 06 — Repository hygiene batch (Prompt C1)

Audit trail for the six independent hygiene items in prompt C1. None of these items touch executable code paths; the only Python diff is updating two ingestion-script path constants for the JSON-seed move.

## Items completed

### 1. README rewrite

- `README.md` rewritten end-to-end. Old version described "14 major Rhet/Comp journals via the CrossRef API" and listed five Python files; the actual project covers 44+ journals across four data sources and ~ 30 Python modules.
- Added three badges (Python version, GPL-3.0, Tests CI). Tone preserved: factual, no marketing copy.
- New sections: Architecture overview (with ASCII diagram), Local development, Running tests, Deployment (Fly process groups + secrets + health endpoints), Project structure (full module inventory grouped by concern), Contributing (pointer to `CONTRIBUTING.md`), Citation (BibTeX placeholder for the JWA note).

### 2. Duplicate workflow resolved

- Deleted `.github/workflows/deploy.yml`.
- Kept `.github/workflows/fly-deploy.yml`. Added a comment block at the top documenting the `workflow_run`-on-Tests trigger, the `--strategy immediate` choice, and the concurrency lock.
- Verified: `.github/workflows/test.yml` still runs first; the deploy workflow is gated on `workflow_run.conclusion == 'success'`.

### 3. JSON seeds moved to `data/seeds/`

- `git mv ccdp_scraped.json data/seeds/ccdp_scraped.json` — preserves history.
- `git mv peer_review_references.json data/seeds/peer_review_references.json` — preserves history.
- New `data/seeds/README.md` documents producer / consumer / re-run instructions for each file.
- Path constants updated (NOT new code paths):
  - `ingest_peer_review_1_1.py:26` — `JSON_PATH` now `data/seeds/peer_review_references.json`
  - `scrape_ccdp.py:53` — `OUTPUT_FILE` now `data/seeds/ccdp_scraped.json`; `import os` added at top to support the `os.path.join`.

### 4. `error.html` template inheritance

- `templates/error.html` now extends `base-core.html` (NOT `base.html`). Sidebar intentionally omitted — a fail-state page should not retry the DB to render the journal sidebar; if `/health/ready` is failing, we'd be cascading the failure.
- Error-specific CSS moved into `{% block head_extra %}`; the per-code default-message dict moved into `{% block body %}`.
- New test `tests/test_routes_html.py::test_404_inherits_from_base_core` confirms the inheritance works (theme-toggle script and main stylesheet link present in 404 responses).
- Existing 404, 429-HTML, and 500-handler tests all still pass.

### 5. CSS audit (report only — no deletions)

- Report at [`docs/refactor-notes/05-css-audit.md`](05-css-audit.md). Findings:
  - `style.css` (3531 lines): 12 suspected-dead class selectors with file:line references.
  - `style-scandi.css` (1687 lines): 0 suspected dead. Pure override layer (`html.scandi .x`).
  - `style-terminal.css` (720 lines): 0 suspected dead. Pure override layer (`html.terminal .x`).
- New `static/README.md` documents the theme-switching mechanism (localStorage + class on `<html>`, all three stylesheets loaded simultaneously) and points at the dead-rules report.
- **Deferred for human decision**: actually deleting the 12 dead selectors. Some may be injected dynamically by D3 in `explore.js`; a manual review pass should confirm before they're removed.

### 6. Governance files

- `CONTRIBUTING.md` — scraping-ethics nine-point list (drawn from inline annotations in `scraper.py`), test requirements per change type, style notes, commit message convention, and the Claude-Code-prompt workflow used for structural changes.
- `CHANGELOG.md` — Keep-a-Changelog format. Single `[Unreleased]` entry covering the structural changes from prompts A1, B1–B3, and C1. No history reconstruction older than this window.
- `docs/architecture.md` — placeholder pointing at the four refactor-note files; will be replaced by prompt G1.
- `docs/methodology.md` — placeholder sketching scope, identifier strategy, citation graph construction, auto-tagging, and OA classification; will be expanded by prompt G1.
- `docs/refactor-notes/` — already populated; nothing added here that wasn't already in this directory.

## Files moved (git history preserved)

| From | To |
|---|---|
| `ccdp_scraped.json` | `data/seeds/ccdp_scraped.json` |
| `peer_review_references.json` | `data/seeds/peer_review_references.json` |

## Files deleted

| Path | Reason |
|---|---|
| `.github/workflows/deploy.yml` | Duplicate of `fly-deploy.yml`; both fired on every push, deduped only by `concurrency: deploy-group`. |

## Files added

```
CHANGELOG.md
CONTRIBUTING.md
data/seeds/README.md
docs/architecture.md
docs/methodology.md
docs/refactor-notes/05-css-audit.md
docs/refactor-notes/06-repo-hygiene.md
static/README.md
```

## Files modified beyond template / docs

- `ingest_peer_review_1_1.py` — `JSON_PATH` constant only.
- `scrape_ccdp.py` — `OUTPUT_FILE` constant; `import os` added.
- `templates/error.html` — converted to extend `base-core.html`.
- `tests/test_routes_html.py` — added `test_404_inherits_from_base_core`.
- `.github/workflows/fly-deploy.yml` — top-of-file comment block; logic unchanged.
- `README.md` — full rewrite (separate item).

## Items deferred for human decision

1. **Deletion of the 12 suspected-dead CSS selectors.** Listed in [05-css-audit.md](05-css-audit.md). Recommended only after confirming none are injected at runtime by D3.
2. **Bibliographic detail in `CHANGELOG.md`** for the structural release. Once the JWA note is published the `## [Unreleased]` block can be promoted to a versioned release with a date and a citation pointer.
3. **Filling in `docs/architecture.md` and `docs/methodology.md`.** Both are placeholders pending prompt G1.
