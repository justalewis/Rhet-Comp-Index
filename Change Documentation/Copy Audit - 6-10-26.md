# Pinakes copy audit — June 10, 2026

Full-site audit and rewrite of rendered prose. Voice treatment: justin-article-style-v2
for essay-like pages (About, Coverage, Datastories framing, methodology sections),
justin-tool-copy register for interface prose (ledes, microcopy, empty states, errors),
no-ai-trace diagnostic over everything. Scope per Justin: every rendered string,
including microcopy; applied and deployed in one pass.

Marker codes below follow the no-ai-trace diagnostic (A=tone, B=vocabulary,
C=syntax, D=paragraph shape, F=punctuation) and the v2 checklist numbers.

---

## about.html — 13 edits

| Location | Marker | Before | After |
|---|---|---|---|
| ¶2 | C1 negative parallelism | "was not simply a finding aid. It was an argument about…" | "A finding aid first, the Pinakes also made an argument about…: it recorded which conversations mattered…" |
| ¶3 | v2 no-collective-our (×3); "web of knowledge" metaphor | "our disciplinary web of knowledge… contours of our discipline… built with our questions in mind" | "the discipline's scholarship… contours of the discipline… built with the field's questions in mind" |
| ¶4 | C1 "not by X but by Y" | "organized not by shelf location but by the patterns of citation…" | "organized by the patterns of citation… rather than by shelf location" |
| Values ¶1 | structured antithesis pair; passive | "This tool was built with different commitments in mind: exploration over ranking, pattern-finding over prestige measurement." | "I built this tool with a different commitment: exploration over ranking." (Kept "to rank, to defund, and to discipline" — an earned tricolon, deliberate rhetoric.) |
| Values ¶2 | v2 no-collective-us (×2) | "frequency tells us something… without telling us everything about worth" | "Frequency measures collective attention; it does not measure worth." |
| Lineage ¶2 | F1 connective em dash | "The code is imperfect — but the tool works, and it improves iteratively." | "The code is imperfect; the tool works, though, and it improves with each pass." |
| Lineage ¶3 | **The flagged sentence.** C1 + C2 adjective tricolon + "observatory" metaphor | "a living, queryable, self-updating disciplinary observatory. It was built not by a team of developers but by a writing teacher who needed it to exist." | "a living, self-updating index of the discipline. I am a writing teacher, and I built it because I needed it to exist." |
| Where to Start lede | F1 em dashes ×2; phrasing duplicated with item 5 | "The last one — Coverage — is the one that most changes how you read everything else" | "The last, Coverage, changes how you read everything else" |
| Where to Start #2 | F1 em dash | "worth reading first — the rankings are intra-corpus" | "worth reading first: the rankings are intra-corpus" |
| Where to Start #5 | duplicate superlative framing | "Understanding coverage is the single thing that most changes how you read the rest of the site." | "The gaps it documents shape every chart downstream." |
| Developer ¶4 | B1 "navigate" | "how community college faculty navigate generative AI" | "how community college faculty take up generative AI" (uptake; closer to RGS vocabulary anyway) |
| Using This Data ¶1 | F1 em dash pair around 13-word aside | "— whether citation counts, publication trends, network analyses, or other findings derived from the index —" | folded into a semicolon clause: "…acknowledgment of the source; that includes citation counts…" |
| Using This Data ¶2 | F1 em dash pair | "missing entirely — and why — is available on" | "missing entirely (and why) appears on" |

Kept deliberately: the Callimachus opening (specific, earned), "I want to make the
field legible to itself" (short distillation sentence, v2-characteristic), the
rhetorical-question run in "What This Is" (human rhythm), "to rank, to defund, and
to discipline" (genuinely three actions).

---

## coverage.html — 13 edits

Already the strongest prose on the site: concrete, hedged honestly, assertive about
limitations. Most edits were punctuation and staleness, not voice.

| Location | Marker | Change |
|---|---|---|
| Intro ¶1 | F1 connective em dash | "what's missing — and explains why" → "what's missing; it also explains why" |
| TLDR callout | stale styling | hardcoded old-brown #5a3e28 → var(--accent); bg → var(--paper) (design-system pass missed inline styles) |
| TLDR item 1 | F1 + stale count | "28 of the 53… underrepresent the others — they show" → "29 of the 54… underrepresent the others: they show". Count updated for WPA (new) + Reflections (now full). |
| Vocabulary ¶ | F1 ×2 | em-dash asides → parens / comma |
| Two Tiers ¶s | F1 ×3 | list-bearing em dashes → colon / parens |
| Rhetoric bullet | redundancy | cut closing "Peitho contributes citation data; the smaller specialized rhetoric venues do not" (both halves already stated in the same bullet) |
| TechComm bullet | B2 copula-dodge | "serves as the primary corpus" → "uses it as its primary corpus" |
| Community Literacy bullet | **stale fact** | Reflections described as RSS-only; rewritten to record the May 2026 Penn State Libraries deposit and that it now contributes citation edges |
| Full-coverage list | stale | added Reflections entry |
| RSS/scrape list | **stale fact** | removed Reflections; added WPA entry (full 1977–present run indexed today from CWPA archive; zero DOIs ever; outreach in progress) |
| OJS list | **wrong fact** | removed WPA line ("older issues are JSTOR-only… recent issues are better" — WPA was never JSTOR/OJS and no issues have DOIs) |
| Books intro | F1 | "series ISSN — a standard that exists" → comma |

Kept deliberately: "not because the work doesn't cite anything, but because reference
lists for those volumes were never deposited" (era breakdown) — looks like C1 but is a
genuine causal correction carrying the page's central argument. Label-separator
dashes in definition-style list items ("*Journal* — status") kept as chrome, not prose.
Flagged for later verification, not changed: LiCS line says reference lists are
"scraped from HTML galleys rather than deposited with CrossRef" — may be stale if the
LiCS deposit pipeline has since run; JAC count reads 1,180 here vs 1,221 in journals.py.

---

## datastories_landing.html — 4 edits

| Marker | Change |
|---|---|
| v2 no-collective-we | "the data confirms what we thought we knew" → "the data confirms the field's received account" |
| C2 "different X" triple | "a different corpus, a substantially different set of computational tools, and a different set of questions" → "a different corpus and a substantially different set of computational tools and questions" |
| F1 connective dash | aside lead-in "**If you found this page…** —" → colon |
| stale styling | remaining #5a3e28 hardcodes → var(--accent) (4 spots) |

Kept deliberately: "narrated, situated, and questioned" — the monograph's own thesis
formulation, not a decorative tricolon. "Working tools, not finished products" — plain
honest contrast, not the flourish form.

## datastories.html (tools index) — 8 edits

Connective em dashes in accordion-card descriptions normalized to colons (6); "not one
main thread but several parallel routes" neg-parallelism inverted to "several parallel
routes…, rather than a single main thread"; hardcoded "The 49 indexed journals" made
count-free ("The indexed journals…") since the count went stale the same day it was
read. Kept "From solo scholar to authoring teams" (Ch. 8 orient) — a genuine historical
trajectory, not a false range.

## _datastories_panels.html — 6 edits

The methodology bodies are the best prose on the site — concrete N's, named articles,
honest caveats ("heuristic upper bound rather than a verified network"). Only surgical
fixes: three "…, indicating [analysis]" trailing participles tightened to colon-claims
(Speed of Influence, Border Crossers, Books Everyone Reads); the First Spark tab-intro
opener "Not one main path but several —" inverted; "pivotal articles" → "anchor
articles" (B1 vocabulary, both occurrences); two connective dashes → colons (Shared
Foundations controls, Two Maps finding); the Two Maps "signaling that…" tail promoted
to its own declarative sentence. Kept "gestured at but not built upon" (Unread Canon) —
sharp two-beat close that carries the panel's claim.

---

## tools.html — 4 edits

Stale "all 44 journals" made count-free; three trailing-participle card descs
("showing how…", "revealing which…") converted to colon-claims. Question-form section
headings kept — human, and they organize the grid by what a reader wants to know.

## glossary.html — 10 edits

"quietly load-bearing assumptions" → "unstated assumptions" (banned metaphor in both
skill tables). Nine connective/appositive em dashes → commas, colons, semicolons, or
parens. Kept "they often disagree and the disagreement is the point" (Centrality) and
the Rich / Cargile Cook worked examples untouched.

## atlas.html — 9 edits

Opener neg-parallelism ("is not a single conversation but a federation") flattened to
the affirmative claim. Six connective dashes → colons/periods/parens. Empty-state
fallback line converted to the name-it / next-step error shape. Old-accent #5a3e28
hardcodes → var(--accent) (~12 spots). Kept "classifying journals by subfield is itself
an argument, not a neutral sort" — the contrast is the claim.

## explore.html — 14 edits

Stale "all 44 journals" and a wrong topic-coverage stat ("73%"; the DB says 61.5%,
matching the Coverage page's 61%). One neg-parallelism ("not just many articles, but
the right articles") → "weights who cites you more heavily than how many do." One
banned metaphor ("doing structural work") rewritten to name the relation. Four
concrete-but-participial sentence tails ("…, ensuring…", "…, reflecting…" ×3)
converted to finite clauses. "navigate outward" → "move outward". Six "— a [noun]"
appositive dashes in tab-intros/how-to-read strings → colons. Methodology-body
appositive dashes left alone per the tool-copy rule (parenthetical-list use, no cap);
each remaining panel-lede carries at most one.

## index.html — 4 edits

Hero counts updated (40,000/49 → 50,000+/54) and its connective dash → colon. Empty-
state hint dash → colon. **Dev-speak error**: "Error starting fetch — check the
terminal" → "The fetch didn't start. Try again in a minute." (production visitors have
no terminal). Old-accent hardcodes → var(--accent) (5 spots).

## Smaller templates — 11 edits

- most-cited.html: intro's two collective-"we" constructions and two em dashes
  rewritten ("is that different from the received account?", "the closest available
  approximation of what the field actually treats as essential reading"); "creating an
  asymmetry" participle → "an asymmetry built into the data"; SAGE/T&F dash pair →
  parens. Kept the closing "a limitation of the available infrastructure, not a
  judgment about the value of any journal's scholarship."
- new.html: stale "more than 40 journals" → "more than 50"; dash → colon.
- authors.html, article.html, _network_trio_compare.html: one dash each.
- institution.html: **dev-speak** empty state ("Run python fetch_institutions.py") →
  "Institution data comes from OpenAlex and can lag the article index."
- coverage.html: **dev-speak** ("Run python coverage_report.py to refresh") cut.
- error.html: 429 message de-scolded ("please slow down" → "Too many requests. Wait a
  moment and try again.").

## Static JS — 9 files

Eight files told users to "run <code>python cite_fetcher.py</code>" in empty states —
the most common dev-leak on the site; all replaced with "No citation data yet.
Reference lists are still being fetched from CrossRef." or simply cut where the
adjacent advice ("try a lower minimum") already carried the next step. All "— try"
empty-state dashes → sentence breaks. Loading/computing messages ("cached after first
run") were already in voice and untouched.

---

## Verification

- no-ai-trace re-scan across templates/ and static/: no remaining hits for the
  vocabulary table, negative parallelisms, collective we/our (outside quotations),
  trailing evaluative participles, dev-speak strings, or stale journal counts.
- All 13 copy-bearing routes render 200 through the Flask test client.
- Full pytest suite passes.

## Known repeats deliberately preserved

- "X, not Y" plain contrasts where the distinction is the claim (foundational ≠ best;
  argument ≠ neutral sort; limitation ≠ judgment). The C1 marker is the flourish form,
  not honest contrast.
- Earned tricolons: "to rank, to defund, and to discipline" (About); "narrated,
  situated, and questioned" (the monograph's thesis); "published, cited, and connected."
- Label-separator dashes in definition-style lists and "Chapter N — Title" headings:
  chrome, not prose.
- "the analytic move/finding" recurs across Datastories panels — consistent project
  vocabulary, kept.
- Stats that will drift: the 61% tag coverage (two pages), 54-journal/50,000-article
  counts (hero, About, Coverage TLDR). Consider templating these from live counts.
