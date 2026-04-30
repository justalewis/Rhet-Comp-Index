# Methodology

This document describes what each analytical tool on the Pinakes Explore page measures, how the measurement is computed, and which prior scholarly work the computation draws on. The companion document, [architecture.md](architecture.md), describes how the system is built. The two documents are paired: the architecture document tells a developer how the pipeline runs, and the methodology document tells a scholar what the visualisations are claiming.

Where the underlying scholarship is substantial, I provide a primary reference rather than the textbook citation. The references are listed in [refactor-notes/12-documentation.md](refactor-notes/12-documentation.md) with full bibliographic detail.

## Reading the corpus

Pinakes covers forty-five venues in Rhetoric and Composition; the full list is in [journal-coverage.md](journal-coverage.md). Articles enter the corpus through three live ingestion paths and one manual fallback. Twenty-eight venues deposit DOIs with CrossRef, four expose machine-readable feeds (RSS, OAI-PMH, or the WordPress REST API), twelve are scraped from custom HTML, and one analog-only journal (*Pre/Text*) was hand-compiled and ingested once. The two-tier metadata distinction matters for several visualisations, and I document which tools rely on tier-1 (CrossRef + OpenAlex) data in the closing section on coverage tiers.

For every visualisation that operates on the citation graph, "internal citations" means citations whose source article and target article are both in the Pinakes corpus. CrossRef returns each indexed article's full reference list; references whose DOI matches an in-corpus article become directed edges in the `citations` table. References that point outside the corpus are recorded as DOI-only edges. They count toward inbound and outbound totals but do not appear as graph nodes. This bounded-corpus property shapes every centrality, community, and path computation that follows.

## Timeline

**What it measures.** Annual article counts per journal across the indexed range, currently 1980 through the most recent fetch.

**Why it's interesting.** A time series of journal output names the historical shape of the discipline. Some journals are continuous, some have gaps, some have ceased publication entirely (*Pre/Text*, the WLN issue series), and some appeared late (the WAC Clearinghouse stable launched in the early 2000s).

**How it's computed.** I aggregate article counts by `SUBSTR(pub_date, 1, 4)` and journal name in [`get_timeline_data`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/articles.py#L192). The chart renders with Chart.js as a stacked area plot. The "Top 8 / All" toggle hides the long tail of low-volume journals when the user wants a cleaner view of the canonical venues.

**Caveats.** Counts reflect what Pinakes has ingested, not what each journal has published. Scrape-based ingestion misses early issues for journals whose archives no longer expose pre-2000 metadata. The timeline is therefore conservative for the lower decades.

**Source.** [`db/articles.py:get_timeline_data`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/articles.py#L192).

## Topics (tag co-occurrence)

**What it measures.** Pairwise co-occurrence of auto-assigned tags across articles in the corpus, rendered as a heatmap.

**Why it's interesting.** Co-occurrence patterns suggest which subfields the discipline treats as adjacent. Callon, Courtial, and Laville (1991) introduce co-word analysis as a way of mapping conceptual structure from textual co-occurrences, and the heatmap is a direct application of that technique to a controlled-vocabulary tag set rather than free-text keywords.

**How it's computed.** [`get_tag_cooccurrence`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/articles.py#L205) iterates the `articles.tags` column (a pipe-delimited string assigned by the auto-tagger), generates all unordered pairs per article, and counts pair frequencies across the corpus. The heatmap renders with D3 using a sequential color scale.

**Caveats.** The tag vocabulary is hand-curated rather than learned. I describe it in [`tagger.py`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/tagger.py). Co-occurrence here measures co-tagging, not semantic similarity. An article tagged with both "transfer" and "genre theory" produces a `(transfer, genre theory)` cell increment regardless of how those concepts relate inside the article.

**Source.** [`db/articles.py:get_tag_cooccurrence`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/articles.py#L205).

**Primary references.** Callon, Courtial, and Laville (1991) on co-word analysis.

## Author network

**What it measures.** A force-directed graph of author co-authorship: nodes are authors, edges are shared articles, edge weight is the number of co-authored pieces.

**Why it's interesting.** Newman (2001) demonstrates that scientific collaboration networks display the small-world structure typical of social networks, with implications for how research moves through a field. Rendering the Pinakes co-authorship graph permits visual identification of collaborative clusters, isolates, and bridge figures.

**How it's computed.** [`get_author_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/authors.py#L14) parses the `articles.authors` column (a semicolon-separated string), generates all unordered pairs of authors per article, and counts co-authorship frequency. I filter to authors with at least `min_papers` publications in the corpus (default three) and cap at `top_n` nodes ranked by total publication count (default 150). The frontend uses D3's force simulation to lay out the result.

**Caveats.** The corpus author field is a string, not a normalised author table. "John Smith" and "J. Smith" become separate nodes. OpenAlex enrichment populates a separate `authors` table with canonical forms and ORCIDs, but the network as built here uses the raw byline text to preserve provenance with the source record.

**Source.** [`db/authors.py:get_author_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/authors.py#L14).

**Primary references.** Newman (2001) on scientific collaboration networks.

## Author co-citation

**What it measures.** Author-level co-citation: two authors are co-cited when a third article cites at least one work by each. The pair count is the number of distinct citing articles that pair them.

**Why it's interesting.** White and Griffith (1981) introduce author co-citation analysis (ACA) as a method for mapping disciplinary structure through citing behavior rather than authorial collaboration. McCain (1990) extends the method into a structured technique for visualising disciplinary "schools." A pair of authors who are frequently co-cited but rarely co-author each other's work signals a perceived intellectual proximity that the collaboration network would miss.

**How it's computed.** [`get_author_cocitation_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L2322) extracts (citing_article, cited_author) pairs from the citations table and the articles table, generates all unordered author pairs per citing article, and counts pair frequency. Pairs of authors who appear together as co-authors on the cited article itself are excluded so the network captures co-citation rather than re-counting collaboration.

**Caveats.** The author-level join is by name match. The same author-spelling caveat that applies to the collaboration network applies here, with the additional complication that citing articles may use different conventions than the canonical record (initials versus full names). Pairs that should merge sometimes do not.

**Source.** [`db/citations.py:get_author_cocitation_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L2322).

**Primary references.** White and Griffith (1981); McCain (1990).

## Most cited

**What it measures.** A ranked list of the corpus's most heavily cited articles, by `internal_cited_by_count`.

**Why it's interesting.** Garfield (1979) argues that citation count is a measurable indicator of an article's role in the conversation that follows it; the list provides a quick read on which articles the field returns to most often. Filters by year range, journal, and tag let a reader scope the list to a specific subdomain.

**How it's computed.** [`get_most_cited`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L255) reads the denormalised `internal_cited_by_count` field on the `articles` table, optionally applies the requested filters, and orders descending. The denormalised count is recomputed from the `citations` table whenever `update_citation_counts` runs.

**Caveats.** "Internal" is the binding word. An article heavily cited in venues outside the Pinakes corpus does not score on this list; the bound is structural, not a bug. CrossRef's `is-referenced-by-count` (stored as `crossref_cited_by_count`) gives the global figure for articles that have been processed by the references pipeline.

**Source.** [`db/citations.py:get_most_cited`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L255).

**Primary references.** Garfield (1979).

## Citation network

**What it measures.** The directed graph of in-corpus citations, filtered to articles with at least `min_citations` incoming edges, capped at `max_nodes` for renderability.

**Why it's interesting.** The directed citation network is the substrate for most of the bibliometric tools that follow. Rendering it directly (rather than through a derived measure) shows which articles function as hubs and which clusters of journals tend to cite within themselves. The journal-color overlay reveals patterns of disciplinary cross-citation.

**How it's computed.** [`get_citation_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L290) selects nodes by descending `internal_cited_by_count`, applies the journal and year filters when present, and uses a CTE to retrieve only the edges where both endpoints are in the node set (avoiding the SQLite parameter-count limit on large IN clauses). The frontend lays the result out with D3's force simulation.

**Caveats.** The threshold parameter is load-bearing. At `min_citations=1` the graph contains every article ever cited internally and quickly becomes unreadable; at `min_citations=10` only the most-cited core remains. The default sits at five. The choice of cutoff is a visualisation decision, not a methodological one.

**Source.** [`db/citations.py:get_citation_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L290).

## Co-citation

**What it measures.** Article-level co-citation: two articles are co-cited when a third article cites both. The edge weight is the count of distinct citing articles.

**Why it's interesting.** Small (1973) introduces co-citation as a method for identifying the "intellectual base" of a research front. Articles frequently co-cited are perceived by the citing community as topically or argumentatively linked, even if their content has no direct overlap. The co-citation network thus captures the field's *received* sense of which works belong together, distinct from how authors themselves arrange their citations.

**How it's computed.** [`get_cocitation_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L404) joins the citations table to itself on `source_article_id`, generates all unordered pairs of `target_article_id` per citing article, and counts pair frequency. Articles must clear `min_cocitations` (default three) to appear. The frontend renders the resulting weighted undirected graph.

**Caveats.** Co-citation requires a third article that cites both. An article cited only once never appears in the network regardless of how important it is. The bias is toward articles old enough to have accumulated co-citations.

**Source.** [`db/citations.py:get_cocitation_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L404).

**Primary references.** Small (1973).

## Bibliographic coupling

**What it measures.** Article-level bibliographic coupling: two articles are coupled when they share at least one cited reference. The edge weight is the size of the shared reference set.

**Why it's interesting.** Kessler (1963) introduces bibliographic coupling as the symmetric counterpart to co-citation. Where co-citation relies on later articles to establish connection, bibliographic coupling reads similarity directly from the citing articles' own reference lists. The two measures answer different questions: co-citation captures the field's perception of relatedness over time, bibliographic coupling captures intellectual proximity at the moment of writing.

**How it's computed.** [`get_bibcoupling_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L541) joins the citations table to itself on `target_doi`, generating all unordered pairs of `source_article_id` that share a target. Pair frequency becomes the coupling strength. Articles must clear `min_coupling` (default three shared references) to appear.

**Caveats.** The measure depends on the citing articles having had their reference lists fetched. Articles ingested before the CrossRef-references pipeline ran do not contribute to this graph until they are reprocessed.

**Source.** [`db/citations.py:get_bibcoupling_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L541).

**Primary references.** Kessler (1963).

## Centrality (eigenvector + betweenness)

**What it measures.** Two complementary centrality measures on the directed citation network: eigenvector centrality (Bonacich 1972) and betweenness centrality (Freeman 1977).

**Why it's interesting.** Bonacich (1972) shows that eigenvector centrality identifies nodes that are connected to other highly central nodes, capturing the recursive sense of "the work everyone in the conversation cites." Freeman (1977) defines betweenness centrality as the fraction of shortest paths between other nodes that pass through a given node, identifying articles that bridge otherwise weakly connected subareas. The two measures often disagree, and the disagreement is itself informative: an article with high eigenvector centrality is canonical within its cluster, while an article with high betweenness sits at the seam between clusters.

**How it's computed.** [`get_citation_centrality`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L934) builds the filtered subgraph in NetworkX, calls `eigenvector_centrality_numpy` for the eigenvector measure (the numpy variant converges reliably on the kinds of graphs Pinakes produces), and `betweenness_centrality` for the betweenness measure. Both are computed on the same node set so the two rankings are directly comparable.

**Caveats.** Both measures are bounded-corpus measures. They reward articles whose connections happen to fall inside the index. Articles whose intellectual work happens primarily in venues Pinakes does not cover (interdisciplinary borrowings into RCWS, work cited heavily by journals outside the discipline) appear lower than their actual standing in the field would suggest.

**Source.** [`db/citations.py:get_citation_centrality`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L934).

**Primary references.** Bonacich (1972) on eigenvector centrality; Freeman (1977) on betweenness centrality.

## Sleeping beauties

**What it measures.** Articles whose citation histories show a long quiet period followed by a delayed peak, scored by Ke, Ferrara, Radicchi, and Flammini's (2015) "beauty coefficient."

**Why it's interesting.** Ke and colleagues (2015) propose a parameter-free beauty coefficient B that quantifies the gap between an article's actual citation trajectory and the line connecting its publication year to its peak-citation year. High B values mark articles that were ignored on publication and recognised much later. The phenomenon is interesting both for what it implies about the timing of disciplinary uptake and for what it implies about the editorial logics that ignore certain kinds of work.

**How it's computed.** [`get_sleeping_beauties`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L681) builds a year-by-year citation timeline for every article with at least `min_total_citations` citations, identifies the peak year, computes the beauty coefficient as Ke et al. define it, and returns articles ranked by B. Per-article timelines render as small-multiples on click.

**Caveats.** A short corpus history compresses the time window in which sleeping behavior can be observed. Pinakes's citation pipeline depends on CrossRef references, which are reliably available only for articles whose journals deposit them; articles published before about 2000 often have empty reference lists from CrossRef and contribute citations only when later articles cite them. The earliest-decades cohort is therefore systematically undercounted as a citing population. The bug documented in [`refactor-notes/01-test-harness.md`](refactor-notes/01-test-harness.md) (the function crashes when an article's publication year exceeds the latest year of any citing article, which can happen with sparse data) is preserved with a workaround in the API; the fix is on the roadmap.

**Source.** [`db/citations.py:get_sleeping_beauties`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L681).

**Primary references.** Ke, Ferrara, Radicchi, and Flammini (2015).

## Journal flow

**What it measures.** Inter-journal citation flow as a chord diagram: arc thickness between two journals indicates the volume of citations from articles in one journal to articles in the other.

**Why it's interesting.** Journal-level citation flow names the discipline's internal traffic patterns. Some journals cite each other heavily (intra-clique behavior); others sit on the receiving end of citations from across the field (canonical reference points); a few rarely cite or are cited within RCWS, suggesting interdisciplinary positioning. The chord layout makes asymmetries visible at a glance.

**How it's computed.** [`get_journal_citation_flow`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L1059) joins the citations table to the articles table on both endpoints, groups by `(citing.journal, cited.journal)`, and counts. The chord renderer takes the resulting square matrix and computes arc widths proportional to the row-and-column-summed flows.

**Caveats.** Self-citation (a journal citing itself) is included in the matrix and shows up as the diagonal; whether to include or exclude it is a presentation choice. The current default keeps the diagonal because intra-journal citation is itself an interesting variable.

**Source.** [`db/citations.py:get_journal_citation_flow`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L1059).

## Half-life

**What it measures.** Per-journal citation half-life: the median age of citations *to* and *from* articles in a given journal, plus the 25th and 75th percentiles for shape.

**Why it's interesting.** Burton and Kebler (1960) introduce the citation half-life concept as a way of distinguishing fast-moving from slow-moving fields. Glänzel (1996) refines the half-life into a per-journal indicator that pairs well with impact factor as a measure of scholarly tempo. A journal with a short citing half-life publishes articles whose authors lean on recent literature; a journal with a long cited half-life publishes articles whose own work continues to be cited many years later. The two halves of the measure (citing and cited) are independent, and the contrast between them is informative.

**How it's computed.** [`get_journal_half_life`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L1183) computes, for each journal: the distribution of `citing.year - cited.year` for citations originating in that journal (citing half-life), and the distribution of `citing.year - cited.year` for citations targeting that journal (cited half-life). The median is the half-life; the 25th and 75th percentiles characterise the spread. Optional time-series and distribution views are computed when requested.

**Caveats.** The computation requires citation timestamps on both endpoints. Articles missing `pub_date` are excluded from both distributions, which biases the measure toward better-metadata journals.

**Source.** [`db/citations.py:get_journal_half_life`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L1183).

**Primary references.** Burton and Kebler (1960); Glänzel (1996).

## Communities (Louvain)

**What it measures.** A partition of the citation network into communities via the Louvain method (Blondel, Guillaume, Lambiotte, and Lefebvre 2008), with the resulting modularity score.

**Why it's interesting.** Newman and Girvan (2004) define modularity as a quantitative measure of how well a partition divides a network into densely-connected subgroups with sparse inter-group edges. Blondel and colleagues (2008) propose the Louvain method as a fast greedy algorithm for finding partitions of high modularity. Applied to the Pinakes citation network, the partition recovers communities of articles that cite each other heavily, often clustering around shared topics or journal homes. Reading the resulting communities is a useful way to ask whether the discipline's structure as named by its citing behavior matches its structure as named by editorial venue or by author collaboration.

**How it's computed.** [`get_community_detection`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L1371) builds the filtered citation subgraph in NetworkX and calls `networkx.community.louvain_communities` with the configured `resolution` parameter (default 1.0). Each node receives a community label; the frontend colors the force-layout by community.

**Caveats.** Louvain is non-deterministic. Different runs can produce different partitions of similar quality. The current implementation uses NetworkX's default seed; for reproducible community labels in future work I would seed the algorithm explicitly. The resolution parameter controls partition granularity (higher values produce more, smaller communities); the default produces communities at roughly the scale of subdisciplines rather than individual research programs.

**Source.** [`db/citations.py:get_community_detection`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L1371).

**Primary references.** Newman and Girvan (2004) on modularity; Blondel, Guillaume, Lambiotte, and Lefebvre (2008) on Louvain.

## Main path

**What it measures.** The longest "main path" through the directed citation network: a sequence of articles where each cites the next, chosen so the path traverses the network's most heavily traveled edges.

**Why it's interesting.** Hummon and Doreian (1989) propose main path analysis as a method for tracing the principal developmental trajectory through a citation network. Each edge is weighted by the number of source-to-sink paths that traverse it (search path count, SPC); the main path is the maximum-weight path from a source node (no incoming citations) to a sink node (no outgoing citations). Reading the main path of an RCWS subfield yields a candidate genealogy, useful as a starting point for narrative reconstruction even where the linear story it tells flattens reality.

**How it's computed.** [`get_main_path`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L1580) computes SPC weights on the filtered citation subgraph, identifies the maximum-weight path, and returns the path together with the SPC-weighted edges around it. The frontend renders the path as a linear D3 layout with edge thickness proportional to SPC.

**Caveats.** Main path analysis assumes the citation graph is acyclic. Real citation networks often contain small cycles where two articles cite each other (review pairs, errata, special-issue arrangements). The current implementation breaks ties by edge ID rather than by a principled disambiguation, which means the main path can shift slightly between runs when cycles are present.

**Source.** [`db/citations.py:get_main_path`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L1580).

**Primary references.** Hummon and Doreian (1989).

## Temporal evolution

**What it measures.** A time-resolved view of the citation network: for each year in the corpus, the cumulative citation graph as it stood at that point. Two views are exposed: a time-series of network-level metrics (modularity, mean degree, component sizes) and a snapshot force layout for any chosen year.

**Why it's interesting.** Leicht, Holme, and Newman (2007) treat time-resolved networks as a class in their own right, noting that aggregate snapshots erase the dynamics of network growth. The time-series view names how the discipline's citation density changes; the snapshot view names what the network looked like at a moment a reader can choose.

**How it's computed.** [`get_temporal_network_evolution`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L1793) iterates over years from the earliest indexed year to the most recent, building the cumulative subgraph at each step (articles published on or before that year, citations between such articles), and computes the requested metrics. The snapshot view returns the full subgraph for a single year on demand.

**Caveats.** "Cumulative" is the choice that makes the view tractable, but it elides the dynamics of citation arrivals (articles continue to receive citations long after publication). A more sophisticated implementation would distinguish "the network as it could have been read in year Y" from "the network of articles published by year Y." The current view approximates the latter.

**Source.** [`db/citations.py:get_temporal_network_evolution`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L1793).

**Primary references.** Leicht, Holme, and Newman (2007).

## Reading path

**What it measures.** A reading order around a seed article, suggesting articles to read before, with, and after the seed.

**Why it's interesting.** Boyack and Klavans (2014) argue that citation-derived reading orders are a useful complement to topic-based recommendation: they capture how a research community itself organises its predecessors and contemporaries. The reading path tool combines four cues at once for a chosen seed article: works the seed cites (reading-before), works that cite the seed (reading-after), works frequently co-cited with the seed (reading-with, by Small's measure), and works bibliographically coupled with the seed (reading-near, by Kessler's measure).

**How it's computed.** [`get_reading_path`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L2066) executes four queries against the citations table: outbound edges from the seed (cites), inbound edges (cited-by), top co-citation partners, and top bibliographic-coupling partners. The frontend renders all four around the seed in a force layout, with a list view available for export to BibTeX or plain text.

**Caveats.** A reading path is a recommendation, not a curriculum. The list reflects the corpus's citation structure as it currently stands, which is a function of which articles' references have been processed.

**Source.** [`db/citations.py:get_reading_path`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L2066).

**Primary references.** Boyack and Klavans (2014).

## Ego network

**What it measures.** A two-step neighborhood around a focal article: the focal node, its directly cited and citing articles (one-step neighbors), and their direct neighbors (two-step), filtered to keep the rendering legible.

**Why it's interesting.** Wasserman and Faust (1994) describe ego networks as the unit of analysis for understanding an actor's local position in a larger network. For an individual article, the ego network at radius two captures the immediate intellectual conversation: what it draws on, what it influences, and the second-order relations that frame both.

**How it's computed.** [`get_ego_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L119) starts from the focal article ID, retrieves all citations with the focal as either source or target, then expands to the one-step neighbors of those neighbors. Edges are returned only when both endpoints are inside the ego set. The `/article/<id>` page exposes the ego network as the article's "Citation network" panel.

**Caveats.** Two-step ego networks grow quickly. For heavily cited focal articles, the unfiltered neighborhood at radius two contains hundreds of nodes and ceases to be readable. The default rendering caps the neighborhood at the most heavily connected subset.

**Source.** [`db/citations.py:get_ego_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/db/citations.py#L119).

**Primary references.** Wasserman and Faust (1994).

## Coverage tiers

The forty-five venues in the Pinakes corpus arrive through ingestion paths of uneven metadata quality, and several visualisations depend on tier-1 metadata (CrossRef plus OpenAlex enrichment) that is only present for some of those venues. I summarise here which tools work uniformly across the corpus and which are restricted to tier-1 venues.

**Tier 1 (CrossRef + OpenAlex enrichment).** The twenty-eight CrossRef-indexed journals plus any RSS-fetched journal that OpenAlex covers (typically because OpenAlex aggregates from CrossRef and the open-access feeds I also use). For these venues, every article has a DOI, every article has an abstract, every article has been processed by the OpenAlex enrichment pass to populate OA status and author affiliations, and every article has a CrossRef reference list ingested into the `citations` table. All eighteen Explore tools work on tier-1 venues.

**Tier 2 (scrape-only).** The twelve custom-scraped journals plus the manually-indexed *Pre/Text*. Articles in these venues lack DOIs, often lack abstracts, and never have CrossRef reference lists. They appear in the **timeline**, **most cited** (where their internal-citation count is meaningful), **author network**, **journal flow**, and **half-life** tools, because each of these depends only on metadata I do have. They do not appear in **co-citation**, **bibliographic coupling**, **citation network**, **centrality**, **sleeping beauties**, **communities**, **main path**, **temporal evolution**, **reading path**, or **ego network**, because each of these requires the article's own reference list (or both endpoints of an edge to be in the citations table). The **topics** tool depends on abstracts for auto-tagging and so works only on the subset of tier-2 articles where the scraper recovered an abstract; for most tier-2 articles, the tag set is empty.

A future direction is to populate the missing reference lists for tier-2 articles via OpenAlex (which holds reference data for many open-access articles even when CrossRef does not). The pipeline for that work is sketched in [`enrich_openalex.py`](https://github.com/justalewis/Rhet-Comp-Index/blob/0698720def2f376d4442d6c1627c65eb93cd21b9/enrich_openalex.py); it has not yet been turned on for reference resolution, only for abstract and affiliation enrichment.

For the live counts of which articles fall into which tier, see [`/coverage`](https://pinakes.xyz/coverage), which builds the breakdown freshly on each request.
