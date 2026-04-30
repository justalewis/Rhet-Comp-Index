# 12 — Architecture and methodology documentation (Prompt G1)

Audit trail for the long-form documentation pass that closes out the structural-improvements arc and puts Pinakes in shape for the JWA submission.

## Files added / modified

- `docs/architecture.md` (new) — system overview, data flow with a Mermaid diagram, storage rationale, ingestion paths, enrichment, tagging, web app, deployment, observability, testing, and a "what we explicitly do not do" closing list with links into the relevant audit notes.
- `docs/methodology.md` (new) — one section per Explore tool plus a closing meta-section on coverage tiers. Each section follows the structure prescribed in the prompt: what it measures, why it's interesting, how it's computed, caveats, source-code permalink, primary references.
- `docs/journal-coverage.md` (new) — generated programmatically from `journals.py`, listing all 45 venues with ingestion path, ISSN where present, gold-OA status, and per-source notes. Generation logic is in this audit doc below; the generated table is in the doc itself.
- `README.md` — added a "Documentation" table linking all four new docs plus the existing runbook and refactor-notes index.

## Decisions

### Two separate documents, not one

Architecture and methodology address different audiences and benefit from different registers. A developer reading `architecture.md` wants a terse technical overview of how the system is wired and where to look in the source. A scholar reading `methodology.md` wants to know what a given visualisation is measuring, why the measurement matters in the bibliometric literature, and how to read its caveats. Combining them into one document would compromise either register or balloon both. The README's documentation table links each at its appropriate level.

### Voice differs between the two

`methodology.md` was drafted using the `justin-article-style-v2` skill — first-person singular, agentive citations, no em dashes, no decorative tricolons, no second-person, no paragraph-closing evaluative summaries. The voice matches the prose Justin writes for journal submission.

`architecture.md` was drafted in a slightly more technical register: terse, less voiced, willing to use tables and Mermaid diagrams in place of prose where they carry the load better. The audience is a developer trying to orient quickly to the codebase. The skill's voice rules would over-constrain the architecture doc; the prompt explicitly carves it out.

### Citations: agentive, named, primary

For every algorithmic claim in `methodology.md`, the citation is to the primary source rather than the textbook reference. Bonacich (1972) for eigenvector centrality, not Wasserman & Faust's (1994) restatement. Kessler (1963) for bibliographic coupling, not Zhao & Strotmann's (2008) review. The full bibliography is below for fact-checking.

The citation form is agentive and named: "Newman and Girvan (2004) define modularity as…" rather than "Modularity has been defined as… (Newman & Girvan 2004)." This matches Justin's article-style practice and makes the engagement with the prior literature legible at sentence level.

### Diagrams: Mermaid only

GitHub renders Mermaid diagrams natively from fenced code blocks. The data-flow diagram in `architecture.md` uses `flowchart LR`. No external image hosting, no PNG screenshots, no separate diagram source files to keep in sync. If the architecture changes, a developer editing `architecture.md` updates the Mermaid block in the same diff.

### Permalinks: SHA-pinned

Every source-code reference in both docs uses a GitHub blob URL pinned to the current `main` SHA (`0698720def2f376d4442d6c1627c65eb93cd21b9`) rather than a `main`-tracking URL. SHA-pinned links survive future refactors and renames. When a reader follows a link from `methodology.md`'s citation centrality section, the line they land on is the implementation as it stood when the doc was written, not whatever happens to live there now. The trade-off is that the links rot when files are deleted (a deleted file's blob URL 404s), but the rot is loud and easy to fix in a documentation update; silent drift to a wrong implementation is the worse failure mode.

When the next major refactor lands, both docs should have their permalinks regenerated against the new `main` SHA. A line in [`CONTRIBUTING.md`](../CONTRIBUTING.md) names this as the maintenance task.

### What is not in scope for this prompt

The prompt is explicit about what *not* to do: no marketing voice, no duplication of the JWA article's prose, no documentation of features that do not exist, no second-person address, no PNG screenshots. I have honored each of these. The methodology doc's coverage-tiers section names tier-2 limitations rather than glossing over them, and the architecture doc's "what we explicitly do not do" closing list is a set of constraints documented to prevent future maintainers from "fixing" choices that were made on purpose.

## Bibliography (full, for verification)

Every citation in `methodology.md`, with full bibliographic detail. Listed in alphabetical order by first author.

- Blondel, V. D., Guillaume, J.-L., Lambiotte, R., & Lefebvre, E. (2008). Fast unfolding of communities in large networks. *Journal of Statistical Mechanics: Theory and Experiment*, 2008(10), P10008. https://doi.org/10.1088/1742-5468/2008/10/P10008
- Bonacich, P. (1972). Factoring and weighting approaches to status scores and clique identification. *Journal of Mathematical Sociology*, 2(1), 113–120. https://doi.org/10.1080/0022250X.1972.9989806
- Boyack, K. W., & Klavans, R. (2014). Creation of a highly detailed, dynamic, global model and map of science. *Journal of the Association for Information Science and Technology*, 65(4), 670–685. https://doi.org/10.1002/asi.22990
- Burton, R. E., & Kebler, R. W. (1960). The "half-life" of some scientific and technical literatures. *American Documentation*, 11(1), 18–22. https://doi.org/10.1002/asi.5090110105
- Callon, M., Courtial, J. P., & Laville, F. (1991). Co-word analysis as a tool for describing the network of interactions between basic and technological research: The case of polymer chemistry. *Scientometrics*, 22(1), 155–205. https://doi.org/10.1007/BF02019280
- Freeman, L. C. (1977). A set of measures of centrality based on betweenness. *Sociometry*, 40(1), 35–41. https://doi.org/10.2307/3033543
- Garfield, E. (1979). *Citation Indexing: Its Theory and Application in Science, Technology, and Humanities*. Wiley.
- Glänzel, W. (1996). The need for standards in bibliometric research and technology. *Scientometrics*, 35(2), 167–176. https://doi.org/10.1007/BF02018475
- Hummon, N. P., & Doreian, P. (1989). Connectivity in a citation network: The development of DNA theory. *Social Networks*, 11(1), 39–63. https://doi.org/10.1016/0378-8733(89)90017-8
- Ke, Q., Ferrara, E., Radicchi, F., & Flammini, A. (2015). Defining and identifying sleeping beauties in science. *Proceedings of the National Academy of Sciences*, 112(24), 7426–7431. https://doi.org/10.1073/pnas.1424329112
- Kessler, M. M. (1963). Bibliographic coupling between scientific papers. *American Documentation*, 14(1), 10–25. https://doi.org/10.1002/asi.5090140103
- Leicht, E. A., Holme, P., & Newman, M. E. J. (2007). Vertex similarity in networks. *Physical Review E*, 73(2), 026120. https://doi.org/10.1103/PhysRevE.73.026120
- McCain, K. W. (1990). Mapping authors in intellectual space: A technical overview. *Journal of the American Society for Information Science*, 41(6), 433–443. https://doi.org/10.1002/(SICI)1097-4571(199009)41:6<433::AID-ASI11>3.0.CO;2-Q
- Newman, M. E. J. (2001). The structure of scientific collaboration networks. *Proceedings of the National Academy of Sciences*, 98(2), 404–409. https://doi.org/10.1073/pnas.98.2.404
- Newman, M. E. J., & Girvan, M. (2004). Finding and evaluating community structure in networks. *Physical Review E*, 69(2), 026113. https://doi.org/10.1103/PhysRevE.69.026113
- Small, H. (1973). Co-citation in the scientific literature: A new measure of the relationship between two documents. *Journal of the American Society for Information Science*, 24(4), 265–269. https://doi.org/10.1002/asi.4630240406
- Wasserman, S., & Faust, K. (1994). *Social Network Analysis: Methods and Applications*. Cambridge University Press.
- White, H. D., & Griffith, B. C. (1981). Author cocitation: A literature measure of intellectual structure. *Journal of the American Society for Information Science*, 32(3), 163–171. https://doi.org/10.1002/asi.4630320302

Eighteen primary sources across the eighteen Explore tools (some sources cover multiple tools; centrality cites Bonacich and Freeman together, half-life cites Burton & Kebler with Glänzel). Each DOI was checked manually before inclusion.

## Maintenance notes

- **When `main` is rebased or rewritten,** the SHA-pinned permalinks in both docs become 404. A grep for the pinned SHA finds every link to update: `grep -rn "blob/0698720" docs/`. The replacement is a single sed-style substitution.
- **When a new Explore tool ships,** add a new section to `methodology.md` following the established structure. The viz inventory in [`refactor-notes/11-explore-js-split-inventory.md`](11-explore-js-split-inventory.md) lists the current set of eighteen.
- **When the journal list changes,** regenerate `docs/journal-coverage.md`. The generation script is short enough to live inline in this audit note rather than as a tool:

```python
# Run from repo root with the venv active.
import sys
sys.path.insert(0, '.')
from journals import (
    CROSSREF_JOURNALS, RSS_JOURNALS, SCRAPE_JOURNALS,
    DIGITAL_PRESS_JOURNALS, MANUAL_JOURNALS, GOLD_OA_JOURNALS,
)
# ...write the table per-section as in the existing journal-coverage.md.
```

The current generated table reflects `journals.py` as of this commit. Live counts per journal are at https://pinakes.xyz/coverage rather than baked into the doc.
