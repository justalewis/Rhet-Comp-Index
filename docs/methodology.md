# Methodology

> **Status: placeholder.** A full methodology write-up will land with prompt G1 alongside the *Journal of Writing Analytics* research note. Until then, this page sketches the methodological choices that the eventual document will defend in detail.

## Scope of the index

Pinakes covers Rhetoric & Composition as a discipline. Inclusion is driven by venue (the journal or press), not by topic or author. The journal list is maintained in [`journals.py`](../journals.py) and is split across four ingestion strategies:

- **CrossRef** (≈ 28 journals) — every journal that deposits DOIs with CrossRef. Bulk of the corpus.
- **RSS / OAI-PMH / WordPress** (4 journals) — open-access journals without CrossRef coverage that expose machine-readable feeds.
- **Custom scrapers** (12 journals) — open-access journals with no machine-readable feed; metadata is parsed from HTML according to journal-specific rules.
- **Manual** (1 journal) — *Pre/Text*, an analog-only journal whose record was hand-compiled and ingested once.

Two open-access digital book publishers ([CCDP](https://ccdigitalpress.org/) so far) are also covered via dedicated scrapers.

## Why these journals

The list is intended to be exhaustive of major peer-reviewed journals in Rhetoric & Composition with continuous publication. It is not aspirational — every journal listed is actually being ingested. The boundary cases (journals that publish in adjacent fields like Technical Communication, Writing Studies, or Communication) are decided pragmatically: included if the editorial board self-identifies with R/C and a substantial portion of the articles are recognisable as R/C scholarship.

A list of journals considered and **excluded** (along with the reason — out of scope, indexing infeasible, ceased publication, etc.) will appear in the G1 document.

## Identifier strategy

The unique key for an article is its URL (the canonical `https://doi.org/<DOI>` for CrossRef articles, the article-page URL otherwise). DOIs are stored separately when available and used to resolve citation edges. This means:

- An article never has two rows even if it later acquires a DOI.
- Citation edges from CrossRef references resolve to the in-index target only when the DOI matches.
- Articles without DOIs (most scraped sources, manual entries) participate in the network only as targets via author / title / year fuzzy match — not implemented yet; on the G1 roadmap.

## Citation graph construction

Citation edges come from CrossRef's `references` field, populated via [`cite_fetcher.py`](../cite_fetcher.py). For each in-index article that CrossRef has reference data for, we store one row per cited reference in the `citations` table. The `target_article_id` is set when the cited DOI matches an in-index article; otherwise the target is recorded as a DOI-only edge so it counts toward outbound coverage but is not a graph node.

This means the visible citation network represents intra-disciplinary citations only — articles in the index citing other articles in the index. External citations (R/C articles citing scholarship outside the index) appear in counts but not as graph edges. This is a deliberate scoping choice; lifting it would require resolving every cited DOI against OpenAlex, which is feasible but has not been done.

## Auto-tagging

[`tagger.py`](../tagger.py) applies a controlled vocabulary of ~ 60 terms to article titles and abstracts via case-insensitive regex matching. Single-word triggers use word boundaries (so "grammar" doesn't fire on "programmatic"); multi-word phrases are literal substring matches.

The vocabulary is hand-curated. It is not a substitute for a learned topic model — its purpose is to support faceted browsing on the site, not to make claims about disciplinary structure. The G1 document will compare auto-tag coverage against a held-out manually-labelled subset.

## Open-access status

OA status comes from two sources:

1. **Known gold-OA journals** (in [`journals.py`](../journals.py)'s `GOLD_OA_JOURNALS` set) — every article from these journals is tagged `oa_status: 'gold'` at ingest time, with the article URL itself recorded as the `oa_url`.
2. **OpenAlex enrichment** — for articles in mixed / paywalled journals, [`enrich_openalex.py`](../enrich_openalex.py) queries OpenAlex's `oa_status` and `best_oa_location` fields and stores both.

This means our OA coverage figures depend on OpenAlex's classification for the majority of records. Limitations of that classification — particularly its tendency to over-count "bronze" OA — will be discussed in the G1 document.

## What this page will become

Prompt G1 will expand each of these sections into a defensible methodology, add a journal-by-journal coverage matrix, and document the limitations of the index in detail. This page is a placeholder so that the structure is in place and the README's link target is real.
