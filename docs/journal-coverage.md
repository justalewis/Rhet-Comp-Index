# Journal coverage

Every venue indexed by Pinakes, listed in the order it appears in journals.py. Counts are not included here; the live counts at https://pinakes.wacclearinghouse.org/coverage are authoritative and update with the index.

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
| Open Words: Access and English Studies | 2690-392X | ✓ | WAC Clearinghouse — 136 DOIs, 2006–2023; references backfilled via CrossRef 2026-06 |
| Revista Latinoamericana de Estudios de la Escritura | 2998-7016 | ✓ | No ISSN in its CrossRef deposits; selected by DOI prefix 10.37514/rle-j. and container title. One mis-registered duplicate DOI (2025.4.2.02) is excluded. |
| Journal of Basic Writing | 0147-1635 | ✓ | WAC Clearinghouse. Whole run (1975 to present) has DOIs; no reference lists deposited. |
| Language and Learning Across the Disciplines | 1091-7098 | ✓ | WAC Clearinghouse. Historical (1994 to 2003); predecessor of Across the Disciplines. |
| Academic.Writing | 1552-325X | ✓ | WAC Clearinghouse. Historical (2000 to 2003); predecessor of Across the Disciplines. |
| RhetNet | 2768-251X | ✓ | WAC Clearinghouse. Historical (1994 to 1997). |
| Academic Labor: Research and Artistry | — (no ISSN) | ✓ | No ISSN in its CrossRef deposits; selected by DOI stem 10.37514/alr-j. and container title. |
| Journal of Second Language Writing | 1060-3743 |  | Elsevier. No abstracts in CrossRef. |
| Quarterly Journal of Speech | 0033-5630 |  | Taylor & Francis. Print ISSN returns 6,580 records, online ISSN 6,567. Runs from 1915. |
| Argumentation and Advocacy | 1051-1431 |  | Taylor & Francis. Print ISSN returns 1,256 records, online ISSN 811. |
| Discourse and Writing / Rédactologie | 2563-7320 | ✓ | Canadian Journal for Studies in Discourse and Writing. Open access. |
| Journal of Writing Assessment | 2169-9232 | ✓ | eScholarship. DOIs from 2021 only. Open access. |
| Women's Studies in Communication | 1930-6458 |  | Taylor & Francis. Online ISSN used; print ISSN returns fewer records. |
| Written Language & Literacy | 1387-6732 |  | John Benjamins. |
| Advances in the History of Rhetoric | 1936-0835 |  |  |
| Assessing Writing | 1075-2935 |  |  |
| Rhetoric of Health and Medicine | 2573-5063 | ✓ |  |
| Business and Professional Communication Quarterly | 2329-4922 |  |  |
| IEEE Transactions on Professional Communication | 0361-1434 |  |  |
| Reflections: A Journal of Community-Engaged Writing and Rhetoric | 1541-2075 | ✓ | WordPress. Archive page at /archive/ lists all articles with pipe-separated title/author format. ... |
| Rhetorica | 1533-8541 |  |  |
| Argumentation | 0920-427X |  |  |
| Res Rhetorica | 2392-3113 |  |  |
| Rhetoric, Politics & Culture | 2693-7549 |  |  |
| Journal of Writing Research | 2030-1006 |  |  |
| Writing and Pedagogy | 1756-5839 |  |  |
| Journal of Academic Writing | 2225-8973 |  |  |

## RSS / OAI-PMH / WordPress journals

| Journal | ISSN | Gold OA | Notes |
|---|---|:-:|---|
| Enculturation | — | ✓ |  |
| Present Tense: A Journal of Rhetoric in Society | — | ✓ |  |
| KB Journal: The Journal of the Kenneth Burke Society | — | ✓ |  |
| Literacy in Composition Studies | — | ✓ |  |
| Journal of Response to Writing | — | ✓ |  |

## Custom-scraped journals

| Journal | ISSN | Gold OA | Notes |
|---|---|:-:|---|
| Kairos: A Journal of Rhetoric, Technology, and Pedagogy | — | ✓ | Custom static HTML. Issues at /{vol}.{issue}/index.html. Vol 1 = 1996; Vol 30.2 = Spring 2026. |
| Praxis: A Writing Center Journal | — | ✓ | Squarespace. No RSS. Issues listed at /issues-archive. |
| Journal of Multimodal Rhetorics | — | ✓ | Custom Ruby/Rack app. Issue TOC at /{vol}-{issue}-issue. Vol 1 = 2017. |
| Basic Writing e-Journal | — | ✓ | Static HTML (CUNY). Archive index at /Archives.html. Largely dormant since Vol 16.1 (2020). |
| Composition Studies | — | ✓ | WordPress.com. Not in CrossRef. Archive at /archive/; issues 2017–present have individual article PDF links. Pre-2016 issues are full-PDF only (not scraped). |
| Writing on the Edge | — |  | Drupal 10 (UC Davis). RSS blocked (403). Subscription journal — metadata scrapeable, full text paywalled. |
| Writing Lab Newsletter | — | ✓ | Print newsletter (1975–2015). Archive at /resources.html as full-issue PDFs. No individual article pages; entries are at the issue level. |
| Writing Center Journal | — | ✓ | Purdue Digital Commons (Open Access). Vol/iss TOC pages list articles with titles and authors. Vol 1 ≈ 1980. |
| The Peer Review | — | ✓ | IWCA WordPress site. Issues listed at /issues/. Article slugs at root level. |
| WPA: Writing Program Administration | — |  | CWPA association CMS (TCS Software). Not in CrossRef (0 DOIs; ISSN 0196-4682). Entire run (vol 1, 1977 – present) on one archive page; ~970 articles with titles + authors. One-year moving wall: newest two issues members-only, older PDFs free (bronze OA). DOI/reference-registration outreach candidate. |
| Composition Forum | — | ✓ | Two-era site: old PHP (vols 14.2–54) and new WordPress (55+). Server blocks bot UAs; scraper uses browser-like headers. Full archive: ~500 articles with titles, authors, abstracts. |

## Digital press scrapers (book publishers)

| Journal | ISSN | Gold OA | Notes |
|---|---|:-:|---|
| Computers and Composition Digital Press | — |  | Open-access digital books (monographs + edited collections). 27 books, ~280 chapters. Scraped via scrape_ccdp.py. No Dublin Core metadata; parsed from page content. All CC-licensed (gold OA). |

## Manually-indexed journals

| Journal | ISSN | Gold OA | Notes |
|---|---|:-:|---|
| Pre/Text | — |  | Analog-only journal; never digitized or assigned DOIs. 234 articles indexed from a hand-compiled record across 22 volumes. Ingested via ingest_pretext.py. |
| JAC: A Journal of Rhetoric, Culture, and Politics | 0731-6755 |  | 1,221 articles indexed from CompPile records across vols 1–34. No DOIs (subscription journal that ceased active publication c. 2014; never deposited in CrossRef). Three CSV variants of the title ('JAC', 'JAC: Journal of Advanced Composition', 'JAC: A Journal of Rhetoric, Culture, and Politics') all resolve to the canonical post-2013 name. Ingested via ingest_jac.py. |

## Summary

**70 venues** in total, 51 via CrossRef, 5 via RSS/OAI/WP, 11 via custom scraper, 1 via book-publisher scraper, 2 manually indexed. 35 are gold OA.

Journals appear in the sidebar and the Atlas in 8 groups: Composition & Writing Studies, Writing Research & Assessment, Rhetoric, Technical Communication, Writing Centers, WAC / Writing Across the Curriculum, Digital & Multimodal, Community Literacy.

Live article counts per journal: see https://pinakes.wacclearinghouse.org/coverage. For the SQL behind those numbers, see [`coverage_report.py`](../coverage_report.py).
