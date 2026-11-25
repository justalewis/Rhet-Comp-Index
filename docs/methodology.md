# Methodology

This document describes what each analytical tool on the Pinakes Explore page measures, how the measurement is computed, and which prior scholarly work the computation draws on. Explore has seventeen tool tabs, plus links to the Most Cited, What's New, and Author Profiles pages. Each tab has a section below, as do the Most Cited page and the per-article citation network page. The twenty-six Datastories tools are a separate, password-gated surface and are out of scope here; each documents its own method on the page. The companion document, [architecture.md](architecture.md), describes how the system is built. The two documents are paired: the architecture document tells a developer how the pipeline runs, and the methodology document tells a scholar what the visualisations are claiming.

Where the underlying scholarship is substantial, I provide a primary reference rather than the textbook citation. The references are listed in [refactor-notes/12-documentation.md](refactor-notes/12-documentation.md) with full bibliographic detail, except Batagelj (2003) and Leicht, Clarkson, Shedden, and Newman (2007), which are identified in full where they are cited.

## Reading the corpus

Pinakes covers seventy venues in Rhetoric and Composition: sixty-nine journals and one digital book press. The full list is in [journal-coverage.md](journal-coverage.md). Fifty-one journals are harvested from CrossRef, five expose machine-readable feeds (RSS, OAI-PMH, or the WordPress REST API, with scrapers filling in the back archives of two of them), eleven are scraped from custom HTML, and the Computers and Composition Digital Press catalog is scraped for its books and chapters. Two print-era journals with no DOIs were ingested once by script: *Pre/Text*, from a hand-compiled index, and *JAC*, from CompPile records. Metadata depth varies across these paths and within them, and several visualisations depend on it; the closing section on coverage tiers sets out which tools need what.

For every visualisation that operates on the citation graph, "internal citations" means citations whose source article and target article are both in the Pinakes corpus. Reference lists come mainly from CrossRef, and only for journals whose publishers deposit them; some deposit none (the *Journal of Basic Writing*, for one). A CrossRef reference becomes a directed edge in the `citations` table when its DOI matches an indexed article. For *Literacy in Composition Studies*, which deposits no references, [a scraper](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/scrape_lics_refs.py#L347) reads the works-cited lists from the HTML galleys and matches each entry to an indexed article by DOI or, failing that, by title; a few hand-ingested issues carry their own lists. References that point outside the corpus are stored too, and they appear in an article's reference list, but they never become graph nodes and they are not part of the internal counts: [`update_citation_counts`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L285) sets `internal_cited_by_count` and `internal_cites_count` to the number of distinct in-corpus articles on the other end. This bounded-corpus property shapes every centrality, community, and path computation that follows.

## Timeline

**What it measures.** Annual article counts per journal across the full indexed history.

**Why it's interesting.** A time series of journal output names the historical shape of the discipline. Some journals are continuous, some have gaps, some have ceased publication (*Pre/Text*, *JAC*), some stop where the index stops rather than where the journal did (the scraped *Writing Lab Newsletter* run ends in 2015, when the newsletter became *WLN*, which is not yet indexed), and some appeared late (the *Journal of Writing Analytics* in 2017, the *Revista Latinoamericana de Estudios de la Escritura* in 2024).

**How it's computed.** I aggregate article counts by `SUBSTR(pub_date, 1, 4)` and journal name in [`get_timeline_data`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/articles.py#L466). There is no fixed starting year; a floor at 1900 drops only malformed dates, and an optional year range narrows the view. The chart renders with Chart.js as a stacked bar chart, one bar per year. It draws the eight highest-output journals as separate series and folds the rest into a single "Other" band, because the palette carries eight distinguishable colors. The "Show top 8 journals only" toggle drops the Other band when the user wants a cleaner view of the largest venues.

**Caveats.** Counts reflect what Pinakes has ingested, not what each journal has published. Scrape-based ingestion misses early issues for journals whose archives no longer expose older metadata (*Composition Studies* before 2016 is full-issue PDF only and is not scraped). The timeline is therefore conservative for the lower decades.

**Source.** [`db/articles.py:get_timeline_data`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/articles.py#L466).

## Institutions

**What it measures.** The twenty-five institutions with the most affiliated articles in the index, and, for the top ten, affiliated articles per year.

**Why it's interesting.** Counting by institution shows where the field's published scholarship comes from and whether that concentration has shifted over the decades. It is a measure of production, not of influence.

**How it's computed.** [`get_top_institutions_v2`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/institutions.py#L232) counts distinct articles per institution through the `article_author_institutions` table, which [`fetch_institutions.py`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/fetch_institutions.py#L80) fills from OpenAlex authorship records, looked up by DOI with a title search as the fallback. An article with authors at two institutions counts once for each. [`get_institution_timeline_v2`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/institutions.py#L275) counts the top ten's articles per publication year from 1990 on. When the normalised tables are empty, or their most recent affiliated article is more than five years old, both functions fall back to the older `author_article_affiliations` table, which groups by the institution name as a string. A year range restricts both the ranking and the timeline. The frontend draws the ranking as a bar chart and the timeline as a line chart with Chart.js.

**Caveats.** The tool sees only what OpenAlex holds. Articles OpenAlex cannot match, or matches without institutional affiliations, do not count, so the ranking describes the affiliated subset of the index rather than all of it.

**Source.** [`db/institutions.py:get_top_institutions_v2`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/institutions.py#L232).

## Topics (tag co-occurrence)

**What it measures.** Pairwise co-occurrence of auto-assigned tags across articles in the corpus, rendered as a heatmap.

**Why it's interesting.** Co-occurrence patterns suggest which subfields the discipline treats as adjacent. Callon, Courtial, and Laville (1991) present co-word analysis as a tool for mapping the structure of a research area from the co-occurrence of keywords, and the heatmap applies that technique to a controlled-vocabulary tag set rather than free-text keywords.

**How it's computed.** [`get_tag_cooccurrence`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/articles.py#L488) iterates the `articles.tags` column (a pipe-delimited string assigned by the auto-tagger), generates all unordered pairs per article, and counts pair frequencies across the corpus, optionally within a year range. The [auto-tagger](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/tagger.py#L946) matches trigger phrases against the title plus the abstract, or the title alone when there is no abstract. The heatmap shows the thirty most frequent tags and renders with D3 using a sequential color scale.

**Caveats.** The tag vocabulary is hand-curated rather than learned. I describe it in [`tagger.py`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/tagger.py). Co-occurrence here measures co-tagging, not semantic similarity. An article tagged with both "transfer" and "genre theory" produces a `(transfer, genre theory)` cell increment regardless of how those concepts relate inside the article. Articles without abstracts are tagged from their titles alone and tend to carry fewer tags.

**Source.** [`db/articles.py:get_tag_cooccurrence`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/articles.py#L488).

**Primary references.** Callon, Courtial, and Laville (1991) on co-word analysis.

## Author network

**What it measures.** A force-directed graph of author co-authorship: nodes are authors, edges are shared articles, edge weight is the number of co-authored pieces.

**Why it's interesting.** Newman (2001) demonstrates that scientific collaboration networks display the small-world structure typical of social networks, with implications for how research moves through a field. Rendering the Pinakes co-authorship graph permits visual identification of collaborative clusters, isolates, and bridge figures.

**How it's computed.** [`get_author_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/authors.py#L14) parses the `articles.authors` column (a semicolon-separated string), generates all unordered pairs of authors per article, and counts co-authorship frequency. I filter to authors with at least `min_papers` publications in the corpus (default three) and cap at `top_n` nodes ranked by total publication count (default 150). A year range, when set, restricts the articles before either threshold applies. The frontend uses D3's force simulation to lay out the result.

**Caveats.** The corpus author field is a string, not a normalised author table. "John Smith" and "J. Smith" become separate nodes. OpenAlex enrichment populates a separate `authors` table with OpenAlex IDs and ORCIDs, but the network as built here uses the raw byline text to preserve provenance with the source record.

**Source.** [`db/authors.py:get_author_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/authors.py#L14).

**Primary references.** Newman (2001) on scientific collaboration networks.

## Author co-citation

**What it measures.** Author-level co-citation: two authors are co-cited when a third article cites at least one work by each. The pair count is the number of distinct citing articles that pair them.

**Why it's interesting.** White and Griffith (1981) introduce author co-citation analysis (ACA) as a method for mapping disciplinary structure through citing behavior rather than authorial collaboration. McCain (1990) extends the method into a structured technique for visualising disciplinary "schools." A pair of authors who are frequently co-cited but rarely co-author each other's work signals a perceived intellectual proximity that the collaboration network would miss.

**How it's computed.** [`get_author_cocitation_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L2399) takes every distinct (citing article, cited article) pair inside the index where the cited article has a byline and at least two internal citations, and reads the cited article's authors from its own record. For each citing article it pairs every two authors it cites and counts the pair once. A citing article does not count toward a pair when it reaches both authors only through the same cited work, so co-authors cited only through their shared article do not register as co-cited. Pairs must reach `min_cocitations` distinct citing articles (default three), and the network keeps the 200 authors with the highest total co-citation strength. Year and journal filters apply to the citing articles. Results are cached on disk and recomputed when articles or citations change.

**Caveats.** Authors are identified by the name string on each cited article's record in the index. The same author-spelling caveat that applies to the collaboration network applies here: one person spelled differently in different journals' metadata (initials versus full names) becomes two nodes, and two people who share a name become one. Pairs that should merge sometimes do not.

**Source.** [`db/citations.py:get_author_cocitation_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L2399).

**Primary references.** White and Griffith (1981); McCain (1990).

## Most cited

**What it measures.** A ranked list of the corpus's most heavily cited articles, by `internal_cited_by_count`, on the Most Cited page that Explore links to.

**Why it's interesting.** Garfield (1979) argues that citation count is a measurable indicator of an article's role in the conversation that follows it; the list provides a quick read on which articles the field returns to most often. Filters by year range, journal, and tag let a reader scope the list to a specific subdomain.

**How it's computed.** [`get_most_cited`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L316) reads the denormalised `internal_cited_by_count` field on the `articles` table, keeps articles with at least one internal citation, optionally applies the requested filters, and orders descending. The denormalised count is recomputed from the `citations` table whenever `update_citation_counts` runs.

**Caveats.** "Internal" is the binding word. An article heavily cited in venues outside the Pinakes corpus does not score on this list; the bound is structural, not a bug. CrossRef's `is-referenced-by-count` (stored as `crossref_cited_by_count`) gives the global figure for articles that have been processed by the references pipeline.

**Source.** [`db/citations.py:get_most_cited`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L316).

**Primary references.** Garfield (1979).

## Citation network

**What it measures.** The directed graph of in-corpus citations, filtered to articles with at least `min_citations` incoming edges, capped at `max_nodes` for renderability.

**Why it's interesting.** The directed citation network is the substrate for most of the bibliometric tools that follow. Rendering it directly (rather than through a derived measure) shows which articles function as hubs and which clusters of journals tend to cite within themselves. The journal-color overlay reveals patterns of disciplinary cross-citation.

**How it's computed.** [`get_citation_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L351) selects up to 500 nodes by descending `internal_cited_by_count`, applies the journal and year filters when present, and uses a CTE to retrieve the distinct citation links where both endpoints are in the node set (avoiding the SQLite parameter-count limit on large IN clauses). The frontend lays the result out with D3's force simulation.

**Caveats.** The threshold parameter is load-bearing. At `min_citations=1` the graph contains every article ever cited internally and quickly becomes unreadable; at `min_citations=10` only the most-cited core remains. The default sits at five. The choice of cutoff is a visualisation decision, not a methodological one.

**Source.** [`db/citations.py:get_citation_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L351).

## Citation trends

**What it measures.** For each publication year from 1990 on, the average number of indexed works an article from that year cites, among articles whose reference lists have been processed.

**Why it's interesting.** The series tracks how much of the indexed literature a typical article draws on, year by year, and so how densely the field cites within the venues Pinakes covers.

**How it's computed.** [`get_citation_trends`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L423) averages `internal_cites_count` (the number of distinct indexed articles an article cites) by publication year over articles whose `references_fetched_at` is set, and drops years with fewer than two such articles. It accepts a journal and a year range. The chart draws the average as a line over bars showing how many articles with citation data each year contributes.

**Caveats.** Only internal references count: an article that cites forty works, none of them indexed, scores zero. Later articles have more indexed prior work available to cite, so a rising line partly reflects the depth of the index, not only changing citation habits. `references_fetched_at` is stamped even when CrossRef returns an empty reference list, so years with many articles from journals that deposit no references pull the average down.

**Source.** [`db/citations.py:get_citation_trends`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L423).

## Co-citation

**What it measures.** Article-level co-citation: two articles are co-cited when a third article cites both. The edge weight is the count of distinct citing articles.

**Why it's interesting.** Small (1973) introduces co-citation as a method for identifying the "intellectual base" of a research front. Articles frequently co-cited are perceived by the citing community as topically or argumentatively linked, even if their content has no direct overlap. The co-citation network thus captures the field's *received* sense of which works belong together, distinct from how authors themselves arrange their citations.

**How it's computed.** [`get_cocitation_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L470) joins the citations table to itself on `source_article_id`, generates all unordered pairs of `target_article_id` per citing article, and counts the distinct citing articles behind each pair. A pair must be co-cited by at least `min_cocitations` articles (default three) to count. The network keeps the 400 articles with the highest total co-citation strength; journal and year filters apply to the cited articles. The frontend renders the resulting weighted undirected graph.

**Caveats.** Co-citation requires third articles that cite both. At the default threshold an article cited fewer than three times inside the index cannot appear, however important it is. The bias is toward articles old enough to have accumulated co-citations.

**Source.** [`db/citations.py:get_cocitation_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L470).

**Primary references.** Small (1973).

## Bibliographic coupling

**What it measures.** Article-level bibliographic coupling: two articles are coupled when they share at least one cited reference inside the index. The edge weight is the number of shared indexed references.

**Why it's interesting.** Kessler (1963) introduces bibliographic coupling as the symmetric counterpart to co-citation. Where co-citation relies on later articles to establish connection, bibliographic coupling reads similarity directly from the citing articles' own reference lists. The two measures answer different questions: co-citation captures the field's perception of relatedness over time, bibliographic coupling captures intellectual proximity at the moment of writing.

**How it's computed.** [`get_bibcoupling_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L607) joins the citations table to itself on `target_article_id`, generating all unordered pairs of `source_article_id` that cite the same indexed article. The number of distinct shared targets becomes the coupling strength. Pairs must share at least `min_coupling` references (default three), and the network keeps the 400 articles with the highest total coupling strength; journal and year filters apply to the citing articles.

**Caveats.** A shared reference counts only when the shared work is itself in the index. Two articles that cite the same twenty books share nothing here. The measure also depends on the citing articles having had their reference lists fetched: articles from journals that deposit no references never couple, and new arrivals do not contribute until the citation harvester has processed them.

**Source.** [`db/citations.py:get_bibcoupling_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L607).

**Primary references.** Kessler (1963).

## Centrality (eigenvector + betweenness)

**What it measures.** Two complementary centrality measures on the directed citation network: an eigenvector-type score (Bonacich 1972), computed as PageRank, and betweenness centrality (Freeman 1977).

**Why it's interesting.** Bonacich (1972) shows that eigenvector centrality identifies nodes that are connected to other highly central nodes, capturing the recursive sense of "the work everyone in the conversation cites." Freeman (1977) defines betweenness centrality as the fraction of shortest paths between other nodes that pass through a given node, identifying articles that bridge otherwise weakly connected subareas. The two measures often disagree, and the disagreement is itself informative: an article with a high eigenvector score is canonical within its cluster, while an article with high betweenness sits at the seam between clusters.

**How it's computed.** [`get_citation_centrality`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L1005) builds a directed NetworkX graph from the top `max_nodes` articles by internal citation count (600 by default, with a minimum of two internal citations in the Explore tool) and the citations among them. The score the tool labels eigenvector centrality is PageRank with damping α = 0.85, computed by a [pure-Python power iteration](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L965) rather than an eigenvector solver. PageRank is the damped variant of eigenvector centrality, and the damping keeps scores meaningful on a citation graph with many disconnected pieces, which classical eigenvector centrality handles badly. Because edges run from citing to cited article, score flows toward cited work. Betweenness is NetworkX's `betweenness_centrality` on the same directed graph. Each score is then divided by its maximum in the current node set, so the leader on each list scores 1.0.

**Caveats.** Both measures are bounded-corpus measures. They reward articles whose connections happen to fall inside the index. Articles whose intellectual work happens primarily in venues Pinakes does not cover (interdisciplinary borrowings into RCWS, work cited heavily by journals outside the discipline) appear lower than their actual standing in the field would suggest. Because each score is normalised to the leader of the current filtered set, scores compare articles within one run, not across runs with different filters.

**Source.** [`db/citations.py:get_citation_centrality`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L1005).

**Primary references.** Bonacich (1972) on eigenvector centrality; Freeman (1977) on betweenness centrality.

## Sleeping beauties

**What it measures.** Articles whose citation histories show a long quiet period followed by a delayed peak, scored by a variant of Ke, Ferrara, Radicchi, and Flammini's (2015) "beauty coefficient."

**Why it's interesting.** Ke and colleagues (2015) propose a parameter-free beauty coefficient B that quantifies the gap between an article's actual citation trajectory and the line connecting its publication year to its peak-citation year. High B values mark articles that were ignored on publication and recognised much later. The phenomenon is interesting both for what it implies about the timing of disciplinary uptake and for what it implies about the editorial logics that ignore certain kinds of work.

**How it's computed.** [`get_sleeping_beauties`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L747) takes every article with at least `min_total_citations` internal citations (default five), counts distinct citing articles per year by the citing article's publication year, fills the years between publication and the latest citation with zeros, and finds the peak year. Articles that peak in their publication year are skipped, as are articles whose citing articles all carry earlier dates (an online-first or reprint date on one side), since they have no post-publication curve to score. The coefficient follows Ke et al.'s area-between-curves idea with two differences: the reference line runs from zero in the publication year to the peak (Ke et al. start it at the publication-year count), and the yearly gaps are summed without Ke et al.'s division by max{1, c_t}, so B here is measured in citations and grows with an article's citation volume. A correction for the bounded corpus follows. When an article's CrossRef global citation count is more than three times its internal count, B is multiplied by internal / (internal + global), which pushes down articles that are heavily cited outside the index and only look dormant inside it; articles with no CrossRef count are not scaled. The awakening year is a threshold rule rather than Ke et al.'s point of maximum distance from the reference line: the first year whose count reaches the larger of one and the sleep-period mean plus a fifth of the way to the peak, provided the next year also reaches it or the year is the peak. Results are ranked by B. Each row carries a sparkline of its yearly citations, and clicking a row opens a larger chart of the timeline.

**Caveats.** A short corpus history compresses the time window in which sleeping behavior can be observed. Reference lists are available only where journals deposit them (or, for *Literacy in Composition Studies*, where the galleys are scraped), and older articles often have empty reference lists, so they contribute citations only when later articles cite them. The earliest-decades cohort is therefore systematically undercounted as a citing population; the era breakdown on [`/coverage`](https://pinakes.wacclearinghouse.org/coverage) shows the outbound-citation yield by decade. Because B is not normalised per year, it is best read as a ranking within one run rather than as a value comparable to the coefficients Ke and colleagues report.

**Source.** [`db/citations.py:get_sleeping_beauties`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L747).

**Primary references.** Ke, Ferrara, Radicchi, and Flammini (2015).

## Journal flow

**What it measures.** Inter-journal citation flow as a chord diagram: arc thickness between two journals indicates the volume of citations from articles in one journal to articles in the other.

**Why it's interesting.** Journal-level citation flow names the discipline's internal traffic patterns. Some journals cite each other heavily (intra-clique behavior); others sit on the receiving end of citations from across the field (canonical reference points); a few rarely cite or are cited within RCWS, suggesting interdisciplinary positioning. The chord layout makes asymmetries visible at a glance.

**How it's computed.** [`get_journal_citation_flow`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L1131) counts distinct citing-cited article pairs inside the index, grouped by `(citing.journal, cited.journal)`. The journal filter restricts both ends, the year range restricts the citing articles, and cells below `min_citations` (default one) are dropped. The journals that remain are ordered alphabetically into a square matrix. D3's chord layout sizes each journal's arc by its row total (the citations its articles make to journals in the matrix) and sizes each end of a ribbon by the flow in that direction.

**Caveats.** Self-citation (a journal citing itself) is included in the matrix and shows up as the diagonal; the stats line above the diagram reports its share. I keep the diagonal because intra-journal citation is itself an interesting variable. Only journals with reference lists in the index can appear on the citing side, so a journal that deposits none shows inbound flow only.

**Source.** [`db/citations.py:get_journal_citation_flow`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L1131).

## Half-life

**What it measures.** Per-journal citation half-life: the median age of citations *to* and *from* articles in a given journal, plus the 25th and 75th percentiles for shape.

**Why it's interesting.** Burton and Kebler (1960) introduce the citation half-life concept as a way of distinguishing fast-moving from slow-moving fields. Clarivate's Journal Citation Reports publishes it per journal in both directions, as cited half-life and citing half-life. A journal with a short citing half-life publishes articles whose authors lean on recent literature; a journal with a long cited half-life publishes articles whose own work continues to be cited many years later. The two halves of the measure (citing and cited) are independent, and the contrast between them is informative.

**How it's computed.** [`get_journal_half_life`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L1255) computes, for each journal, the distribution of `citing.year - cited.year` over distinct in-index citation pairs: grouped by the citing journal for the citing half-life, and by the cited journal for the cited half-life. Pairs in which the citing article carries an earlier year than the cited one are dropped. The median is the half-life; the 25th and 75th percentiles characterise the spread. The journal filter applies to both ends and the year range to the citing articles. On request the function also returns the full distributions and a citing half-life per citing year (for years with at least ten citations).

**Caveats.** The computation requires publication years on both endpoints. Articles missing `pub_date` are excluded from both distributions, which biases the measure toward better-metadata journals. Only in-index citations count, so a journal's citing half-life describes the age of the indexed work it cites, not of its whole reference list, and a journal with no reference lists in the index has no citing half-life at all. Journal Citation Reports computes each figure from a single census year; the medians here pool every in-index citation across all years.

**Source.** [`db/citations.py:get_journal_half_life`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L1255).

**Primary references.** Burton and Kebler (1960).

## Communities (Louvain)

**What it measures.** A partition of the citation network into communities via the Louvain method (Blondel, Guillaume, Lambiotte, and Lefebvre 2008), with the resulting modularity score.

**Why it's interesting.** Newman and Girvan (2004) define modularity as a quantitative measure of how well a partition divides a network into densely-connected subgroups with sparse inter-group edges. Blondel and colleagues (2008) propose the Louvain method as a fast greedy algorithm for finding partitions of high modularity. Applied to the Pinakes citation network, the partition recovers communities of articles that cite each other heavily, often clustering around shared topics or journal homes. Reading the resulting communities is a useful way to ask whether the discipline's structure as named by its citing behavior matches its structure as named by editorial venue or by author collaboration.

**How it's computed.** [`get_community_detection`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L1443) builds an undirected NetworkX graph from the top `max_nodes` articles by internal citation count (600; minimum two internal citations by default) and the citations among them. An edge weighs two when the articles cite each other and one otherwise, and isolated nodes are dropped. It calls `networkx.community.louvain_communities` with the configured `resolution` parameter (default 1.0; the Explore slider runs from 0.5 to 2.0) and a fixed seed of 42, then reports the partition's modularity. Communities are numbered by size. For each community of two or more articles the tool lists its three most common journals, its five most-cited members, and up to five characteristic title words, scored by how much more often they appear in the community's titles than across the whole graph. The frontend colors the force layout by community.

**Caveats.** Louvain is a heuristic: it finds a high-modularity partition, not necessarily the best one, and a small change to the graph (new citations, a different filter) can move articles between communities. The fixed seed makes the same graph partition the same way every time, so community labels are reproducible for a given state of the index. The resolution parameter controls partition granularity (higher values produce more, smaller communities); the default produces communities at roughly the scale of subdisciplines rather than individual research programs.

**Source.** [`db/citations.py:get_community_detection`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L1443).

**Primary references.** Newman and Girvan (2004) on modularity; Blondel, Guillaume, Lambiotte, and Lefebvre (2008) on Louvain.

## Main path

**What it measures.** A single "main path" through the directed citation network: a sequence of articles where each cites the next, traced along the network's most heavily traveled edges.

**Why it's interesting.** Hummon and Doreian (1989) propose main path analysis as a method for tracing the principal developmental trajectory through a citation network, weighting each link by how often search paths through the network traverse it. The weight used here is the search path count (SPC) that Batagelj (2003) added to their family of indices: the number of source-to-sink paths that pass through an edge, where a source is an article nothing else in the graph cites and a sink is an article that cites nothing else in the graph. Reading the main path of an RCWS subfield yields a candidate genealogy, useful as a starting point for narrative reconstruction even where the linear story it tells flattens reality.

**How it's computed.** [`get_main_path`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L1653) builds a directed graph (citing to cited) from the top `max_nodes` articles by internal citation count (800; minimum two internal citations by default) and the citations among them. It first removes cycles: for each cycle it finds, it drops the edge that runs furthest backward in time, the one whose citing article predates the article it cites by the most years (or a same-year citation when none runs backward), and repeats until the graph is acyclic. It then computes SPC for every edge as the number of paths from any source to the edge's citing end, times the number of paths from its cited end to any sink. The path is traced greedily: it starts at the source whose outgoing edges carry the most total SPC and at each step follows the outgoing edge with the highest SPC until it reaches a sink. Only the path's articles and edges are returned, each edge with its SPC and its SPC as a fraction of the graph's maximum. The frontend shows the path as a vertical sequence of article cards from the research frontier (the source) to the foundation (the sink), with each link's SPC printed between cards.

**Caveats.** Main path analysis assumes the citation graph is acyclic. Real citation networks often contain small cycles where two articles cite each other (review pairs, errata, special-issue arrangements) or where dates are off on one side. The cycle removal above discards one real citation per cycle, chosen by publication date, and the stats line reports how many it removed. The greedy trace is a local main path: each step takes the best next edge, which need not produce the path with the largest total SPC. Because the node set is filtered and capped, the path describes the most-cited part of the selected corpus.

**Source.** [`db/citations.py:get_main_path`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L1653).

**Primary references.** Hummon and Doreian (1989); Batagelj (2003) on the search path count (*Efficient Algorithms for Citation Network Analysis*, arXiv:cs/0309023).

## Temporal evolution

**What it measures.** A time-resolved view of the citation network in windows of one or more years. Two views are exposed: a time series of network-level metrics (density, clustering, giant-component share, modularity, mean degree, path length, and cumulative counts) and a snapshot force layout of the network as it stood by any chosen year.

**Why it's interesting.** Leicht, Clarkson, Shedden, and Newman (2007) read the large-scale structure of a citation network against the dates of its documents, showing on the network of U.S. Supreme Court opinions that its groupings and its most authoritative documents change across eras in ways a single aggregate picture does not show. The time-series view names how the discipline's citation density changes; the snapshot view names what the network looked like at a moment a reader can choose.

**How it's computed.** [`get_temporal_network_evolution`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L1868) takes the articles with at least `min_citations` internal citations (default one) and the citations among them, then steps through windows of `window_size` years (default one; the slider runs to ten) from the earliest year to the latest. For each window it builds an undirected graph of the articles published in that window and the citations between them, and computes node and edge counts, density, mean degree, transitivity, the number of components and the giant component's share, Louvain modularity (seed 42, when the window has at least ten articles and five citations), and the average shortest path length within the giant component (when it has between 3 and 1,500 articles). Alongside these it keeps cumulative figures: the articles published by the end of the window, the citations among them made by then, and their density and giant-component share. The snapshot view returns the articles published up to the chosen year and the citations among them, capped at the 500 most-cited, with isolates removed. Results are cached on disk and recomputed when articles or citations change.

**Caveats.** The per-window metrics describe a graph that contains only citations between articles published inside the same window. With the default one-year window that means same-year citations only, a thin slice of the network; wider windows give steadier figures. The cumulative series describe the network of indexed articles published by the end of each window. Neither is "the network as it could have been read in year Y": articles qualify by their citation counts today, so an article enters the early windows only if it has been cited since. The cumulative giant-component share is not computed once the cumulative graph passes 8,000 articles and reads as zero from there.

**Source.** [`db/citations.py:get_temporal_network_evolution`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L1868).

**Primary references.** Leicht, Clarkson, Shedden, and Newman (2007), "Large-scale structure of time evolving citation networks," *European Physical Journal B* 59, https://doi.org/10.1140/epjb/e2007-00271-7.

## Reading path

**What it measures.** A ranked reading list around a seed article, drawn from the works it cites, the works that cite it, and its strongest co-citation and coupling partners.

**Why it's interesting.** Boyack and Klavans (2014) build a model of nearly twenty million articles by clustering them on co-citation, and use bibliographic coupling to place current papers, which have not yet had time to be cited, into those clusters. The two measures complement each other in the same way around a single article: co-citation (Small's measure) finds the works the field already reads alongside it, and coupling (Kessler's measure) finds work that draws on the same sources, including work too recent to have been co-cited. The reading path tool combines both with the seed's direct citations in each direction.

**How it's computed.** [`get_reading_path`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L2142) executes four queries against the citations table: the indexed articles the seed cites, the indexed articles that cite it, its top twenty co-citation partners (by number of distinct co-citing articles), and its top twenty coupling partners (by number of shared indexed references). Each article found gets a relevance score: two points for a direct citation in either direction, the co-citation or coupling count capped at five, one point if it shares an auto-assigned tag with the seed, and one if it is from the same journal. An article found more than one way sums its points. The frontend draws the seed and every scored article in a force layout and lists them by score, with exports to BibTeX, plain text, and annotated Markdown.

**Caveats.** A reading path is a recommendation, not a curriculum. The list reflects the corpus's citation structure as it currently stands, which is a function of which articles' references have been processed. The weights are set by hand, not learned.

**Source.** [`db/citations.py:get_reading_path`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L2142).

**Primary references.** Boyack and Klavans (2014).

## Ego network

**What it measures.** A one-step neighborhood around a focal article: the focal node, the indexed articles it cites, the indexed articles that cite it, and the citation links among all of them.

**Why it's interesting.** Wasserman and Faust (1994) describe ego networks as the unit of analysis for understanding an actor's local position in a larger network. For an individual article, the one-step ego network captures the immediate intellectual conversation: what it draws on, what draws on it, and how those neighbors cite one another.

**How it's computed.** [`get_ego_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L149) collects the focal article and every indexed article on the other end of one of its citations, in either direction, then returns every distinct citation link between two members of that set. It does not expand to neighbors of neighbors. The network is not an Explore tab: it has its own page, `/citations?article=<id>`, reached from the article page's "View in citation network" link, which appears when the article has citation data.

**Caveats.** No cap is applied. A heavily cited focal article brings in every indexed article that cites it, which can run to hundreds of nodes. To keep the rendering legible, the page labels only the focal article and the neighbors whose internal citation count reaches a fifth of the highest count in view (and at least two).

**Source.** [`db/citations.py:get_ego_network`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/citations.py#L149).

**Primary references.** Wasserman and Faust (1994).

## Coverage tiers

The seventy venues in the Pinakes corpus arrive through ingestion paths of uneven metadata quality, and the tools depend on different parts of the record. I summarise here which tools work uniformly across the corpus and which are restricted to the articles that have what they need.

The citation tools depend on two things that only some articles have. The first is an outbound reference list in the index: a CrossRef reference list, for journals whose publishers deposit one; the scraped galley lists for *Literacy in Composition Studies*; or a hand-ingested list for a few issues. The second is a way for other articles' references to find the article, which in practice means a DOI, since the CrossRef pipeline resolves references by DOI alone and only the LiCS scraper falls back to title matching. Having a DOI does not guarantee either: CrossRef journals vary in what they deposit (the *Journal of Basic Writing* deposits no reference lists, the *Journal of Second Language Writing* no abstracts). OpenAlex enrichment, which adds open-access status, affiliations, and missing abstracts, runs only on articles with a DOI.

**Tools that work across the whole corpus.** The **timeline** needs only a journal name and a publication date. **Topics** works wherever there is a title, since the auto-tagger reads the title and the abstract when there is one; articles without abstracts are tagged from the title alone and tend to carry fewer tags. The **author network** works wherever the record carries a byline, which scraped sources supply unevenly (the *Writing Lab Newsletter* entries, for example, are issue-level).

**Tools that need affiliations.** **Institutions** counts only articles that OpenAlex matched and assigned institutional affiliations.

**Tools that need in-index citations.** Everything else, and they divide by direction. **Bibliographic coupling** and **citation trends** read an article's own reference list, so articles from journals with no reference lists in the index never appear in them. **Most cited**, **co-citation**, **author co-citation**, and **sleeping beauties** read the citations an article receives, so an article recorded without a DOI, reachable only through the LiCS scraper's title matches, rarely scores, however often the field cites it in print. **Journal flow** and **half-life** use both directions, so a journal without reference lists appears only as a cited journal, with no citing half-life. The **citation network**, **centrality**, **communities**, **main path**, and **temporal evolution** select their nodes by internal citations received and draw their edges from the citing articles' reference lists, so they need both. The **reading path** and the **ego network** accept any article as a seed but return only the relationships the citation table holds for it, which for an article with no reference list and no DOI is usually nothing.

For the live breakdown, see [`/coverage`](https://pinakes.wacclearinghouse.org/coverage). [`tier_journals`](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/coverage_report.py#L428) sorts each journal by the share of its indexed articles that carry an outbound reference list: half or more, some, or none (CrossRef journals with metadata only, and feed or scraped journals without references, are listed separately, as are the manually indexed journals). The page computes its figures from the live database and [caches them for an hour](https://github.com/justalewis/Rhet-Comp-Index/blob/05caca7dd95c9f43e786fdbea2f0d391bd321b53/db/coverage.py#L93).
