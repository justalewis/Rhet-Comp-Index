# Journal coverage

Every venue indexed by Pinakes, listed in the order it appears in journals.py. Counts are not included here; the live counts at https://pinakes.xyz/coverage are authoritative and update with every fetch cycle.

A journal's **path** is the ingestion strategy used to populate it: CrossRef (by ISSN, the bulk of the corpus), RSS / OAI / WordPress feeds (open-access journals without CrossRef coverage), custom HTML scrapers (open-access journals without machine-readable feeds), or manually-curated entries (analog-only journals whose record was hand-compiled and ingested once).

A journal's **gold OA** mark indicates that every article from this journal is open access without paywalls; tagged at ingestion time without requiring per-article OA verification via OpenAlex.

## CrossRef-indexed journals

| Journal | ISSN | Gold OA | Notes |
|---|---|:-:|---|
| College Composition and Communication | 0010-096X |  |  |
| College English | 0010-0994 |  |  |
| Written Communication | 0741-0883 |  |  |
| Rhetoric Society Quarterly | 0277-3945 |  |  |
| Rhetoric Review | 0735-0198 |  |  |
| Technical Communication Quarterly | 1057-2252 |  |  |
| Research in the Teaching of English | 0034-527X |  |  |
| Journal of Business and Technical Communication | 1050-6519 |  |  |
| Journal of Technical Writing and Communication | 0047-2816 |  |  |
| Philosophy & Rhetoric | 0031-8213 |  |  |
| Rhetoric & Public Affairs | 1094-8392 |  |  |
| Teaching English in the Two-Year College | 0098-6291 |  |  |
| Pedagogy | 1531-4200 |  |  |
| Community Literacy Journal | 1555-9734 | ✓ |  |
| Poroi | 2151-2957 | ✓ |  |
| Computers and Composition | 8755-4615 |  |  |
| Communication Design Quarterly | 2166-1642 | ✓ |  |
| Communication Design Quarterly Review | 2166-1200 | ✓ |  |
| Across the Disciplines | 1554-8244 | ✓ |  |
| The WAC Journal | 1544-4929 | ✓ |  |
| Journal of Writing Analytics | 2474-7491 | ✓ |  |
| Prompt: A Journal of Academic Writing Assignments | 2476-0943 | ✓ |  |
| Peitho | 2169-0774 | ✓ |  |
| Double Helix | 2372-7497 | ✓ |  |
| Advances in the History of Rhetoric | 1936-0835 |  |  |
| Assessing Writing | 1075-2935 |  |  |
| Rhetoric of Health and Medicine | 2573-5063 | ✓ |  |
| Business and Professional Communication Quarterly | 2329-4922 |  |  |

## RSS / OAI-PMH / WordPress journals

| Journal | ISSN | Gold OA | Notes |
|---|---|:-:|---|
| Enculturation | — | ✓ |  |
| Present Tense: A Journal of Rhetoric in Society | — | ✓ |  |
| KB Journal: The Journal of the Kenneth Burke Society | — | ✓ |  |
| Literacy in Composition Studies | — | ✓ |  |

## Custom-scraped journals

| Journal | ISSN | Gold OA | Notes |
|---|---|:-:|---|
| Kairos: A Journal of Rhetoric, Technology, and Pedagogy | — | ✓ | Custom static HTML. Issues at /{vol}.{issue}/index.html. Vol 1 = 1996; Vol 30.2 = Spring 2026. |
| Praxis: A Writing Center Journal | — | ✓ | Squarespace. No RSS. Issues listed at /issues-archive. |
| Journal of Multimodal Rhetorics | — | ✓ | Custom Ruby/Rack app. Issue TOC at /{vol}-{issue}-issue. Vol 1 = 2017. |
| Basic Writing e-Journal | — | ✓ | Static HTML (CUNY). Archive index at /Archives.html. Largely dormant since Vol 16.1 (2020). |
| Composition Studies | — | ✓ | WordPress.com. Not in CrossRef. Archive at /archive/; issues 2017–present have individual article... |
| Writing on the Edge | — |  | Drupal 10 (UC Davis). RSS blocked (403). Subscription journal — metadata scrapeable, full text pa... |
| Writing Lab Newsletter | — | ✓ | Print newsletter (1975–2015). Archive at /resources.html as full-issue PDFs. No individual articl... |
| Writing Center Journal | — | ✓ | Purdue Digital Commons (Open Access). Vol/iss TOC pages list articles with titles and authors. Vo... |
| The Peer Review | — | ✓ | IWCA WordPress site. Issues listed at /issues/. Article slugs at root level. |
| Reflections: A Journal of Community-Engaged Writing and Rhetoric | — | ✓ | WordPress. Archive page at /archive/ lists all articles with pipe-separated title/author format. ... |
| Composition Forum | — | ✓ | Two-era site: old PHP (vols 14.2–54) and new WordPress (55+). Server blocks bot UAs; scraper uses... |

## Digital press scrapers (book publishers)

| Journal | ISSN | Gold OA | Notes |
|---|---|:-:|---|
| Computers and Composition Digital Press | — |  | Open-access digital books (monographs + edited collections). 27 books, ~280 chapters. Scraped via... |

## Manually-indexed journals

| Journal | ISSN | Gold OA | Notes |
|---|---|:-:|---|
| Pre/Text | — |  | Analog-only journal; never digitized or assigned DOIs. 234 articles indexed from a hand-compiled ... |

## Summary

**45 venues** in total — 28 via CrossRef, 4 via RSS/OAI/WP, 11 via custom scraper, 1 via book-publisher scraper, 1 manually indexed. 25 are gold OA.

Live article counts per journal: see https://pinakes.xyz/coverage. For the SQL behind those numbers, see [`coverage_report.py`](../coverage_report.py).
