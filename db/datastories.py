"""db.datastories — analytics for the Datastories chapter-organized tools.

These 26 functions are ports of the standalone Python scripts that produced
the Datastories book's figures. Each returns a JSON-serialisable dict that a
blueprint route hands to a D3 viz module client-side.

Ported scripts live in the user's main repo checkout (uncommitted research
code): temporal_sankey.py, source_sink_analysis.py, decade_main_paths.py,
citation_delay_analysis.py, bridge_articles.py, reciprocity_analysis.py,
citation_distribution.py, citation_concentration.py, time_normalized_ranking.py,
generational_canon.py, citation_accumulation.py, internal_external_comparison.py,
temporal_communities.py, community_coherence.py, key_route_main_path.py,
bibliographic_coupling.py, coupling_citation_comparison.py,
compile_master_works_list.py, asymmetric_coupling.py, team_size_analysis.py,
mentorship_detection.py, collaboration_persistence.py, prince_network.py,
disciplinary_events.py, missed_classics.py.

Heavy NetworkX computations are wrapped with @cached so they run at most once
per DB-mtime change (see datastories_cache.py).
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import Counter, defaultdict

from datastories_cache import cached
from journal_groups import (
    JOURNAL_GROUPS, get_journal_group, GROUP_LABELS, GROUP_COLORS,
    resolve_cluster,
)

from .core import get_conn

log = logging.getLogger(__name__)


# ── customization-layer helpers ────────────────────────────────────────────

def _resolve_journal_filter(cluster=None, journals=None):
    """Combine `cluster` slug + per-tool `journals` list into a single
    canonical journal list, or None if no filter was specified.

    Precedence: explicit `journals` if given (overrides cluster); otherwise
    the cluster's journal list. Returns None when neither is set so callers
    can short-circuit "no filter" cases.
    """
    if journals:
        return list(journals)
    return resolve_cluster(cluster)


def _filter_articles_clause(table_alias=None, cluster=None, journals=None,
                            year_from=None, year_to=None, date_col="pub_date"):
    """Build a SQL WHERE-clause fragment + parameter list applying the
    universal filters (cluster, journals, year_from, year_to) to a single
    article-bearing table. Returns ('', []) when no filter is set so the
    caller can splice in `'AND ' + clause` without an empty AND.

    `table_alias` is the SQL alias of the articles table (e.g. 'a' or
    'citing'); pass None when the column references are bare.
    """
    prefix = (table_alias + ".") if table_alias else ""
    parts, params = [], []

    js = _resolve_journal_filter(cluster=cluster, journals=journals)
    if js:
        marks = ",".join("?" * len(js))
        parts.append(f"{prefix}journal IN ({marks})")
        params.extend(js)

    if year_from:
        parts.append(f"CAST(SUBSTR({prefix}{date_col}, 1, 4) AS INTEGER) >= ?")
        params.append(int(year_from))
    if year_to:
        parts.append(f"CAST(SUBSTR({prefix}{date_col}, 1, 4) AS INTEGER) <= ?")
        params.append(int(year_to))

    return (" AND ".join(parts), params)


# ── shared helpers ─────────────────────────────────────────────────────────

def _year_of(pub_date):
    """Extract a 4-digit publication year from a YYYY[-MM[-DD]] string."""
    if not pub_date:
        return None
    try:
        return int(str(pub_date)[:4])
    except (ValueError, TypeError):
        return None


def _decade_of(year):
    if year is None:
        return None
    return (year // 10) * 10


def _gini(values):
    """Standard Gini coefficient on a non-negative iterable."""
    xs = sorted(v for v in values if v is not None and v >= 0)
    n = len(xs)
    if n == 0:
        return 0.0
    cum = 0.0
    s = 0.0
    for i, x in enumerate(xs, start=1):
        cum += x
        s += i * x
    if cum == 0:
        return 0.0
    return (2.0 * s) / (n * cum) - (n + 1.0) / n


# ═════════════════════════════════════════════════════════════════════════════
# Chapter 3 — The Citation Backbone
# ═════════════════════════════════════════════════════════════════════════════

_DECADE_BUCKETS = [
    ("Pre-1990s", 0, 1990),
    ("1990s",  1990, 2000),
    ("2000s",  2000, 2010),
    ("2010s",  2010, 2020),
    ("2020s",  2020, 2100),
]


def _classify_decade(year):
    if year is None:
        return None
    for label, lo, hi in _DECADE_BUCKETS:
        if lo <= year < hi:
            return label
    return None


@cached("ds_braided_path")
def ds_braided_path(cluster=None, journals=None, year_from=None, year_to=None):
    """Citation flows between TPC, RC, and OTHER, decade by decade.

    Returns a Sankey-ready structure: each decade has 6 nodes (3 source-side
    field-groups, 3 target-side) and links carrying citation counts. The
    aggregate pane spans all decades.
    """
    citing_clause, citing_params = _filter_articles_clause(
        table_alias="citing", cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    cited_clause, cited_params = _filter_articles_clause(
        table_alias="cited", cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    where = ["c.target_article_id IS NOT NULL", "citing.pub_date IS NOT NULL"]
    params = []
    if citing_clause: where.append(citing_clause); params.extend(citing_params)
    if cited_clause:  where.append(cited_clause);  params.extend(cited_params)
    sql = f"""
        SELECT
            CAST(SUBSTR(citing.pub_date, 1, 4) AS INTEGER) AS citing_year,
            citing.journal AS from_journal,
            cited.journal  AS to_journal
        FROM citations c
        JOIN articles citing ON citing.id = c.source_article_id
        JOIN articles cited  ON cited.id  = c.target_article_id
        WHERE {" AND ".join(where)}
    """
    rows = []
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    decade_flow = {label: defaultdict(int) for label, _, _ in _DECADE_BUCKETS}
    aggregate_flow = defaultdict(int)
    summary_template = {
        label: {"decade": label, "total": 0, "tpc_tpc": 0, "tpc_rc": 0,
                "rc_tpc": 0, "rc_rc": 0, "other": 0}
        for label, _, _ in _DECADE_BUCKETS
    }

    for r in rows:
        yr = r["citing_year"]
        fg = get_journal_group(r["from_journal"])
        tg = get_journal_group(r["to_journal"])
        decade = _classify_decade(yr)
        aggregate_flow[(fg, tg)] += 1
        if decade is None:
            continue
        decade_flow[decade][(fg, tg)] += 1

        bucket = summary_template[decade]
        bucket["total"] += 1
        if   fg == "TPC"        and tg == "TPC":        bucket["tpc_tpc"] += 1
        elif fg == "TPC"        and tg == "RHET_COMP":  bucket["tpc_rc"]  += 1
        elif fg == "RHET_COMP"  and tg == "TPC":        bucket["rc_tpc"]  += 1
        elif fg == "RHET_COMP"  and tg == "RHET_COMP":  bucket["rc_rc"]   += 1
        else:                                            bucket["other"]   += 1

    def _pane(flow_dict, label):
        # Build 6-node Sankey: <group>__src and <group>__tgt
        groups = ["TPC", "RHET_COMP", "OTHER"]
        nodes = []
        idx = {}
        for g in groups:
            idx[(g, "src")] = len(nodes)
            nodes.append({"name": GROUP_LABELS[g] + " (citing)", "group": g, "side": "src"})
        for g in groups:
            idx[(g, "tgt")] = len(nodes)
            nodes.append({"name": GROUP_LABELS[g] + " (cited)", "group": g, "side": "tgt"})
        links = []
        for (fg, tg), count in flow_dict.items():
            if fg not in groups or tg not in groups or count <= 0:
                continue
            links.append({
                "source": idx[(fg, "src")],
                "target": idx[(tg, "tgt")],
                "value":  count,
            })
        return {
            "label": label,
            "nodes": nodes,
            "links": links,
            "total_edges": sum(flow_dict.values()),
        }

    decades_panes = []
    for label, _, _ in _DECADE_BUCKETS:
        pane = _pane(decade_flow[label], label)
        pane["decade"] = label
        decades_panes.append(pane)

    aggregate_pane = _pane(aggregate_flow, "All decades")
    aggregate_pane["decade"] = "__all__"

    summary_list = [summary_template[l] for l, _, _ in _DECADE_BUCKETS]
    for r in summary_list:
        r["label"] = r["decade"]

    return {
        "decades": decades_panes,
        "aggregate": aggregate_pane,
        "summary": summary_list,
    }


def ds_branching_traditions(year_from=None, year_to=None):
    """Per-tradition (TPC / RC / OTHER) breakdown of indexed journals.

    For each group, lists every journal with article count and year range.
    Year range filters which articles are counted; cluster/journal filters
    don't apply since this tool is the field-composition snapshot itself.
    """
    where = ["journal IS NOT NULL", "journal != ''"]
    params = []
    if year_from:
        where.append("CAST(SUBSTR(pub_date, 1, 4) AS INTEGER) >= ?"); params.append(int(year_from))
    if year_to:
        where.append("CAST(SUBSTR(pub_date, 1, 4) AS INTEGER) <= ?"); params.append(int(year_to))
    sql = f"""
        SELECT journal, COUNT(*) AS n,
               MIN(SUBSTR(pub_date, 1, 4)) AS earliest,
               MAX(SUBSTR(pub_date, 1, 4)) AS latest
          FROM articles
         WHERE {" AND ".join(where)}
         GROUP BY journal
         ORDER BY n DESC
    """
    rows = []
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    grouped = {"TPC": [], "RHET_COMP": [], "OTHER": []}
    counts = {"TPC": 0, "RHET_COMP": 0, "OTHER": 0}
    for r in rows:
        g = get_journal_group(r["journal"])
        grouped[g].append({
            "journal": r["journal"],
            "article_count": r["n"],
            "earliest_year": r["earliest"],
            "latest_year":   r["latest"],
        })
        counts[g] += r["n"]

    return {
        "groups": [
            {
                "group":         g,
                "label":         GROUP_LABELS[g],
                "color":         GROUP_COLORS[g],
                "journal_count": len(grouped[g]),
                "article_count": counts[g],
                "journals":      grouped[g],
            }
            for g in ("TPC", "RHET_COMP", "OTHER")
        ],
    }


_FRONTIER_CUTOFF_YEAR = 2021


@cached("ds_origins_frontiers")
def ds_origins_frontiers(cluster=None, journals=None, year_from=None, year_to=None):
    """Sources (in-degree 0) and sinks (out-degree 0) of the citation graph.

    Sinks are subcategorized into true (old, references known), frontier
    (recent), data-gap (references never fetched).
    """
    fclause, fparams = _filter_articles_clause(
        cluster=cluster, journals=journals, year_from=year_from, year_to=year_to,
    )
    where_extra = (" AND " + fclause) if fclause else ""
    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM articles WHERE 1=1{where_extra}", fparams
        ).fetchone()[0]
        sources = [dict(r) for r in conn.execute(f"""
            SELECT id, journal, title, authors, pub_date, crossref_cited_by_count
              FROM articles
             WHERE COALESCE(internal_cited_by_count, 0) = 0{where_extra}
        """, fparams)]
        sinks_all = [dict(r) for r in conn.execute(f"""
            SELECT id, journal, title, authors, pub_date, references_fetched_at,
                   internal_cited_by_count
              FROM articles
             WHERE COALESCE(internal_cites_count, 0) = 0{where_extra}
        """, fparams)]
        per_journal_total = {r["journal"]: r["n"] for r in conn.execute(
            f"SELECT journal, COUNT(*) AS n FROM articles WHERE journal IS NOT NULL{where_extra} GROUP BY journal",
            fparams,
        )}

    sink_true, sink_frontier, sink_data_gap = [], [], []
    for a in sinks_all:
        yr = _year_of(a.get("pub_date"))
        if not a.get("references_fetched_at"):
            a["sink_category"] = "data-gap"
            sink_data_gap.append(a)
        elif yr is not None and yr >= _FRONTIER_CUTOFF_YEAR:
            a["sink_category"] = "frontier"
            sink_frontier.append(a)
        else:
            a["sink_category"] = "true"
            sink_true.append(a)

    src_per_journal   = Counter(a["journal"] for a in sources)
    sink_per_journal  = Counter(a["journal"] for a in sink_true)
    journal_rates = []
    for j, t in per_journal_total.items():
        if t == 0:
            continue
        journal_rates.append({
            "journal":     j,
            "n_articles":  t,
            "source_rate": src_per_journal.get(j, 0)  / t,
            "sink_rate":   sink_per_journal.get(j, 0) / t,
            "n_sources":   src_per_journal.get(j, 0),
            "n_sinks":     sink_per_journal.get(j, 0),
        })
    journal_rates.sort(key=lambda x: -x["n_articles"])

    # Year distribution for the stacked chart
    year_dist = defaultdict(lambda: {"sources": 0, "sinks": 0, "other": 0})
    src_ids  = {a["id"] for a in sources}
    sink_ids = {a["id"] for a in sink_true}
    with get_conn() as conn:
        ydist_extra = (" AND " + fclause) if fclause else ""
        for r in conn.execute(
            f"SELECT id, SUBSTR(pub_date, 1, 4) AS yr FROM articles WHERE pub_date IS NOT NULL{ydist_extra}",
            fparams,
        ):
            yr = r["yr"]
            if not yr or len(yr) != 4:
                continue
            try:
                int(yr)
            except ValueError:
                continue
            if r["id"] in src_ids:
                year_dist[yr]["sources"] += 1
            elif r["id"] in sink_ids:
                year_dist[yr]["sinks"] += 1
            else:
                year_dist[yr]["other"] += 1
    year_distributions = dict(sorted(year_dist.items()))

    # Notable lists
    def _slim(a, extra=None):
        d = {
            "id": a["id"], "title": a.get("title"),
            "journal": a.get("journal"), "pub_date": a.get("pub_date"),
            "authors": a.get("authors"),
        }
        if extra:
            d.update(extra)
        return d

    high_global = sorted(
        (a for a in sources if (a.get("crossref_cited_by_count") or 0) >= 20),
        key=lambda a: -(a.get("crossref_cited_by_count") or 0),
    )[:15]
    oldest_true = sorted(sink_true, key=lambda a: a.get("pub_date") or "9999")[:15]
    recent_frontier = sorted(sink_frontier, key=lambda a: a.get("pub_date") or "0000", reverse=True)[:15]

    summary = {
        "n_articles":   total,
        "n_sources":    len(sources),
        "pct_sources":  100 * len(sources) / total if total else 0,
        "n_sinks_all":  len(sinks_all),
        "n_true_sinks": len(sink_true),
        "n_frontier":   len(sink_frontier),
        "pct_frontier": 100 * len(sink_frontier) / total if total else 0,
        "n_data_gap":   len(sink_data_gap),
        "pct_data_gap": 100 * len(sink_data_gap) / total if total else 0,
        "frontier_cutoff_year": _FRONTIER_CUTOFF_YEAR,
    }

    return {
        "summary": summary,
        "journal_rates": journal_rates,
        "year_distributions": year_distributions,
        "notable": {
            "top_sources":  [_slim(a, {"crossref_cited_by": a.get("crossref_cited_by_count") or 0}) for a in high_global],
            "top_data_gap": [_slim(a) for a in oldest_true],
            "top_frontier": [_slim(a) for a in recent_frontier],
        },
    }


# ═════════════════════════════════════════════════════════════════════════════
# Placeholders — chapters 4–9. Each returns a {"_pending": True, ...} stub so
# the blueprint route returns valid JSON; full implementations are appended
# in this file as each chapter is built.
# ═════════════════════════════════════════════════════════════════════════════

def _pending(name):
    return {"_pending": True, "name": name,
            "message": f"{name}: implementation in progress."}

# ═════════════════════════════════════════════════════════════════════════════
# Chapter 4 — Two Fields or One?
# ═════════════════════════════════════════════════════════════════════════════

# Decade windows for chapter 4 analyses (different from Braided Path's pre-1990s
# bucket — chapter 4 only looks at 1970+ and uses 5 decade panes).
_CH4_DECADES = [
    ("1970s-80s", 1970, 1990),
    ("1990s",     1990, 2000),
    ("2000s",     2000, 2010),
    ("2010s",     2010, 2020),
    ("2020s",     2020, 2100),
]


def _classify_ch4_decade(year):
    if year is None:
        return None
    for label, lo, hi in _CH4_DECADES:
        if lo <= year < hi:
            return label
    return None


def _fetch_classified_edges(cluster=None, journals=None,
                            year_from=None, year_to=None):
    """Pull every resolved edge with both citing/cited group + year.

    Accepts the universal filter set (cluster, journals, year_from, year_to)
    and applies it to BOTH the citing and cited side: an edge survives only
    if both endpoints fall inside the filter. This is the right semantics
    for cross-field flow / community / coupling analyses where the question
    is "what is the picture INSIDE this scope?"
    """
    citing_clause, citing_params = _filter_articles_clause(
        table_alias="citing", cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    cited_clause, cited_params = _filter_articles_clause(
        table_alias="cited", cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )

    where_parts = ["c.target_article_id IS NOT NULL"]
    params = []
    if citing_clause:
        where_parts.append(citing_clause); params.extend(citing_params)
    if cited_clause:
        where_parts.append(cited_clause); params.extend(cited_params)
    where_sql = " AND ".join(where_parts)

    sql = f"""
        SELECT c.source_article_id AS citing_id, c.target_article_id AS cited_id,
               citing.journal AS cj, cited.journal AS dj,
               CAST(SUBSTR(citing.pub_date, 1, 4) AS INTEGER) AS cy,
               CAST(SUBSTR(cited.pub_date,  1, 4) AS INTEGER) AS dy
          FROM citations c
          JOIN articles citing ON citing.id = c.source_article_id
          JOIN articles cited  ON cited.id  = c.target_article_id
         WHERE {where_sql}
    """

    out = []
    with get_conn() as conn:
        for r in conn.execute(sql, params):
            out.append({
                "citing_id":    r["citing_id"],
                "cited_id":     r["cited_id"],
                "citing_journal": r["cj"],
                "cited_journal":  r["dj"],
                "citing_year": r["cy"],
                "cited_year":  r["dy"],
                "citing_group": get_journal_group(r["cj"]),
                "cited_group":  get_journal_group(r["dj"]),
            })
    return out


@cached("ds_shifting_currents")
def ds_shifting_currents(cluster=None, journals=None,
                         year_from=None, year_to=None):
    """Per-decade Hummon-Doreian SPC main paths.

    Builds a separate citation DAG for each of five decade windows; computes
    SPC (search path count) on every edge; reports the highest-SPC chain.
    Heavy: typically 30-60 s.
    """
    import networkx as nx

    edges = _fetch_classified_edges(cluster=cluster, journals=journals,
                                    year_from=year_from, year_to=year_to)
    # Article metadata index
    article_meta = {}
    with get_conn() as conn:
        for r in conn.execute(
            "SELECT id, title, authors, journal, pub_date FROM articles "
            "WHERE pub_date IS NOT NULL"
        ):
            article_meta[r["id"]] = {
                "id":      r["id"],
                "title":   r["title"],
                "authors": r["authors"],
                "journal": r["journal"],
                "year":    _year_of(r["pub_date"]),
                "group":   get_journal_group(r["journal"]),
            }

    decade_paths = []
    article_index = defaultdict(set)  # article_id -> set of decade labels

    for label, lo, hi in _CH4_DECADES:
        # Edges where both endpoints fall in this decade window
        dag_edges = [
            (e["cited_id"], e["citing_id"])  # SPC orients from sink to source
            for e in edges
            if e["cited_year"] is not None and e["citing_year"] is not None
               and lo <= e["citing_year"] < hi and lo <= e["cited_year"] < hi
        ]
        # Build DAG; remove cycles by topological-sort breaks
        G = nx.DiGraph()
        G.add_edges_from(dag_edges)
        try:
            order = list(nx.topological_sort(G))
        except nx.NetworkXUnfeasible:
            # Strip cycles greedily
            while True:
                try:
                    cyc = nx.find_cycle(G, orientation="original")
                except nx.NetworkXNoCycle:
                    break
                # Remove one edge of the cycle
                u, v = cyc[0][0], cyc[0][1]
                G.remove_edge(u, v)
            order = list(nx.topological_sort(G))

        if G.number_of_nodes() == 0:
            decade_paths.append({
                "decade": label, "label": label,
                "path": [], "edges": [],
                "stats": {"n_nodes": 0, "n_edges": 0, "spc_total": 0},
            })
            continue

        # SPC: for each edge (u, v), count = paths(source -> u) * paths(v -> sink)
        # Compute paths_to (number of source-to-x paths) in topological order
        sources = [n for n in G.nodes if G.in_degree(n) == 0]
        sinks   = [n for n in G.nodes if G.out_degree(n) == 0]
        paths_to   = {n: (1 if G.in_degree(n)  == 0 else 0) for n in G.nodes}
        for n in order:
            for succ in G.successors(n):
                paths_to[succ] += paths_to[n]
        paths_from = {n: (1 if G.out_degree(n) == 0 else 0) for n in G.nodes}
        for n in reversed(order):
            for pred in G.predecessors(n):
                paths_from[pred] += paths_from[n]

        edge_spc = {}
        for u, v in G.edges():
            edge_spc[(u, v)] = paths_to[u] * paths_from[v]

        # Find max-SPC source -> sink path: greedy walk forward from highest-SPC source
        if not sources or not sinks:
            best_path = []
        else:
            # Pick the source with the highest aggregate outgoing SPC
            src = max(sources, key=lambda n: sum(edge_spc.get((n, s), 0) for s in G.successors(n)) or 0)
            best_path = [src]
            cur = src
            visited = {src}
            while G.out_degree(cur) > 0:
                # Pick the successor with highest edge SPC
                successors = [(s, edge_spc.get((cur, s), 0)) for s in G.successors(cur) if s not in visited]
                if not successors:
                    break
                nxt, _ = max(successors, key=lambda x: x[1])
                if nxt in visited:
                    break
                best_path.append(nxt)
                visited.add(nxt)
                cur = nxt

        # Format output
        path_meta = [article_meta.get(n, {"id": n}) for n in best_path]
        edge_list = [
            {"source": u, "target": v, "spc": edge_spc.get((u, v), 0)}
            for u, v in zip(best_path, best_path[1:])
        ]
        for n in best_path:
            article_index[n].add(label)

        decade_paths.append({
            "decade": label, "label": label,
            "path": path_meta,
            "edges": edge_list,
            "stats": {
                "n_nodes":   G.number_of_nodes(),
                "n_edges":   G.number_of_edges(),
                "spc_total": sum(edge_spc.values()),
                "path_len":  len(best_path),
            },
        })

    # Persistence summary
    persistence = []
    for art_id, decades_set in article_index.items():
        if not decades_set:
            continue
        meta = article_meta.get(art_id, {"id": art_id})
        persistence.append({
            "id":       art_id,
            "title":    meta.get("title"),
            "journal":  meta.get("journal"),
            "year":     meta.get("year"),
            "decades":  sorted(decades_set),
            "n":        len(decades_set),
        })
    persistence.sort(key=lambda r: (-r["n"], r["year"] or 0))

    return {
        "decades":     decade_paths,
        "persistence": persistence,
    }


def ds_speed_of_influence(cluster=None, journals=None,
                          year_from=None, year_to=None):
    """Per-direction citation-delay distribution (TPC->TPC, TPC->RC, RC->TPC,
    RC->RC). Mean / median / IQR. Decade trends. Optional Mann-Whitney."""
    edges = _fetch_classified_edges(cluster=cluster, journals=journals,
                                    year_from=year_from, year_to=year_to)

    DIRS = {
        ("TPC", "TPC"):       "TPC_TO_TPC",
        ("TPC", "RHET_COMP"): "TPC_TO_RC",
        ("RHET_COMP", "TPC"): "RC_TO_TPC",
        ("RHET_COMP", "RHET_COMP"): "RC_TO_RC",
    }
    DIR_LABELS = {
        "TPC_TO_TPC": "TPC → TPC",
        "TPC_TO_RC":  "TPC → RC",
        "RC_TO_TPC":  "RC → TPC",
        "RC_TO_RC":   "RC → RC",
    }

    by_dir = defaultdict(list)
    decade_by_dir = defaultdict(lambda: defaultdict(list))
    for e in edges:
        cy, dy = e["citing_year"], e["cited_year"]
        if cy is None or dy is None:
            continue
        delay = cy - dy
        if delay < 0:
            continue
        d = DIRS.get((e["citing_group"], e["cited_group"]))
        if not d:
            continue
        by_dir[d].append(delay)
        decade = (cy // 10) * 10
        decade_by_dir[d][str(decade)].append(delay)

    def _stats(arr):
        if not arr:
            return None
        s = sorted(arr)
        n = len(s)
        return {
            "count":  n,
            "mean":   round(statistics.fmean(s), 2),
            "median": s[n // 2],
            "p25":    s[n // 4],
            "p75":    s[(3 * n) // 4],
            "stdev":  round(statistics.pstdev(s), 2) if n > 1 else 0,
            "max":    s[-1],
        }

    stats   = {d: _stats(by_dir[d]) for d in DIRS.values()}

    # Distributions: capped at 50 years
    distributions = {}
    for d in DIRS.values():
        c = Counter(min(x, 50) for x in by_dir[d])
        distributions[d] = sorted(c.items())  # list of (delay, count)

    # Decade trends: mean per decade per direction
    temporal = {d: {} for d in DIRS.values()}
    for d, decades in decade_by_dir.items():
        for dec, vals in decades.items():
            if len(vals) < 5:
                continue
            temporal[d][dec] = round(statistics.fmean(vals), 2)

    # Mann-Whitney comparisons
    significance = {}
    try:
        from scipy.stats import mannwhitneyu
        comparisons = [
            ("TPC_TO_RC", "RC_TO_TPC"),
            ("TPC_TO_TPC", "TPC_TO_RC"),
            ("RC_TO_RC",  "RC_TO_TPC"),
        ]
        for a, b in comparisons:
            if not by_dir[a] or not by_dir[b]:
                continue
            u, p = mannwhitneyu(by_dir[a], by_dir[b], alternative="two-sided")
            significance[f"{a}_vs_{b}"] = {"u": float(u), "p": float(p)}
    except Exception:
        pass

    return {
        "stats": stats,
        "distributions": distributions,
        "temporal": temporal,
        "significance": significance,
        "labels": DIR_LABELS,
    }


@cached("ds_border_crossers")
def ds_border_crossers(cluster=None, journals=None,
                       year_from=None, year_to=None):
    """Articles with high betweenness centrality at the TPC/RC divide.

    Computes exact betweenness on the undirected citation graph; returns the
    top 25 with their boundary-crossing flow stats and a 1-hop neighbourhood
    subgraph for D3 rendering. Heavy: 30-90 s on the full corpus.
    """
    import networkx as nx

    edges = _fetch_classified_edges(cluster=cluster, journals=journals,
                                    year_from=year_from, year_to=year_to)
    G = nx.Graph()  # undirected for betweenness
    DG = nx.DiGraph()  # directed for in/out degree by group
    for e in edges:
        if e["citing_id"] == e["cited_id"]:
            continue
        G.add_edge(e["citing_id"], e["cited_id"])
        DG.add_edge(e["citing_id"], e["cited_id"])

    if G.number_of_nodes() == 0:
        return {"top_bridges": [], "neighborhood": {"nodes": [], "links": []}, "stats": {}}

    # Article metadata
    article_meta = {}
    with get_conn() as conn:
        for r in conn.execute(
            "SELECT id, title, authors, journal, pub_date, internal_cited_by_count "
            "FROM articles WHERE id IN (" + ",".join(str(x) for x in G.nodes) + ")"
        ):
            article_meta[r["id"]] = {
                "id":       r["id"],
                "title":    r["title"],
                "authors":  r["authors"],
                "journal":  r["journal"],
                "year":     _year_of(r["pub_date"]),
                "group":    get_journal_group(r["journal"]),
                "cited_by": r["internal_cited_by_count"] or 0,
            }

    # Exact betweenness
    bc = nx.betweenness_centrality(G)

    # Boundary-crossing degree per node
    def _boundary_score(n):
        meta = article_meta.get(n)
        if not meta:
            return 0.0
        own_group = meta["group"]
        cross = 0
        for nbr in G.neighbors(n):
            nbr_meta = article_meta.get(nbr)
            if not nbr_meta:
                continue
            if nbr_meta["group"] != own_group and own_group != "OTHER":
                cross += 1
        return cross

    ranked = []
    for n in G.nodes:
        meta = article_meta.get(n)
        if not meta:
            continue
        ranked.append({
            **meta,
            "betweenness": bc.get(n, 0.0),
            "boundary":    _boundary_score(n),
        })
    ranked.sort(key=lambda r: (-r["betweenness"], -r["boundary"]))
    top = ranked[:25]

    # Neighbourhood subgraph: union of 1-hop neighbourhoods of top bridges
    seed_ids = {r["id"] for r in top}
    neigh_ids = set(seed_ids)
    for s in seed_ids:
        for n in G.neighbors(s):
            neigh_ids.add(n)
    neigh_ids = list(neigh_ids)[:300]  # cap to keep render manageable

    nodes = []
    for n in neigh_ids:
        meta = article_meta.get(n)
        if not meta:
            continue
        nodes.append({
            **meta,
            "betweenness": bc.get(n, 0.0),
            "is_seed":     n in seed_ids,
        })
    node_set = {nd["id"] for nd in nodes}
    links = []
    for u, v in G.edges():
        if u in node_set and v in node_set:
            links.append({"source": u, "target": v})

    return {
        "top_bridges": top,
        "neighborhood": {"nodes": nodes, "links": links},
        "stats": {
            "n_articles": G.number_of_nodes(),
            "n_edges":    G.number_of_edges(),
        },
    }


def ds_two_way_street(cluster=None, journals=None,
                      year_from=None, year_to=None):
    """Reciprocity of citation traffic between TPC and RC.

    Per-article reciprocity: count of distinct other-group articles each
    article both cites and is cited by. Edge-level reciprocity: fraction of
    A->B edges with a B->A counterpart.
    """
    edges = _fetch_classified_edges(cluster=cluster, journals=journals,
                                    year_from=year_from, year_to=year_to)

    # Indices
    tpc_cites_rc   = defaultdict(set)   # TPC id -> set of RC ids it cites
    tpc_cited_byrc = defaultdict(set)   # TPC id -> set of RC ids citing it
    rc_cites_tpc   = defaultdict(set)
    rc_cited_bytpc = defaultdict(set)
    edge_set = set()  # (a, b) directed
    for e in edges:
        a, b = e["citing_id"], e["cited_id"]
        edge_set.add((a, b))
        ag, bg = e["citing_group"], e["cited_group"]
        if ag == "TPC" and bg == "RHET_COMP":
            tpc_cites_rc[a].add(b)
            rc_cited_bytpc[b].add(a)
        elif ag == "RHET_COMP" and bg == "TPC":
            rc_cites_tpc[a].add(b)
            tpc_cited_byrc[b].add(a)

    # Article-level reciprocity counts
    tpc_with_rc_outgoing = set(tpc_cites_rc)
    tpc_reciprocated     = set(a for a in tpc_with_rc_outgoing if tpc_cited_byrc.get(a))
    rc_with_tpc_outgoing = set(rc_cites_tpc)
    rc_reciprocated      = set(a for a in rc_with_tpc_outgoing if rc_cited_bytpc.get(a))

    article_reciprocity = {
        "tpc_with_rc_outgoing": len(tpc_with_rc_outgoing),
        "tpc_reciprocated":     len(tpc_reciprocated),
        "tpc_rate":             (len(tpc_reciprocated) / max(1, len(tpc_with_rc_outgoing))),
        "rc_with_tpc_outgoing": len(rc_with_tpc_outgoing),
        "rc_reciprocated":      len(rc_reciprocated),
        "rc_rate":              (len(rc_reciprocated) / max(1, len(rc_with_tpc_outgoing))),
    }

    # Edge-level reciprocity (TPC->RC and RC->TPC pairs)
    cross_pairs = []
    for e in edges:
        if (e["citing_group"], e["cited_group"]) in (("TPC", "RHET_COMP"), ("RHET_COMP", "TPC")):
            cross_pairs.append((e["citing_id"], e["cited_id"]))
    n_cross = len(cross_pairs)
    n_recip = sum(1 for (a, b) in cross_pairs if (b, a) in edge_set)
    edge_reciprocity = {
        "n_cross":     n_cross,
        "n_reciprocal": n_recip,
        "rate":        n_recip / max(1, n_cross),
    }

    # Trends per citing decade
    decade_buckets = defaultdict(lambda: {"cross": 0, "recip": 0})
    for e in edges:
        if (e["citing_group"], e["cited_group"]) not in (("TPC", "RHET_COMP"), ("RHET_COMP", "TPC")):
            continue
        cy = e["citing_year"]
        if cy is None:
            continue
        decade = (cy // 10) * 10
        decade_buckets[decade]["cross"] += 1
        if (e["cited_id"], e["citing_id"]) in edge_set:
            decade_buckets[decade]["recip"] += 1
    temporal = []
    for dec in sorted(decade_buckets):
        d = decade_buckets[dec]
        temporal.append({
            "decade": dec,
            "cross":  d["cross"],
            "recip":  d["recip"],
            "rate":   d["recip"] / max(1, d["cross"]),
        })

    # Most-reciprocated TPC articles
    most_recip = []
    for a in tpc_reciprocated:
        rc_cited_set    = tpc_cites_rc.get(a, set())
        rc_citing_set   = tpc_cited_byrc.get(a, set())
        mutual_partners = rc_cited_set & rc_citing_set
        most_recip.append({
            "id":               a,
            "n_cites_rc":       len(rc_cited_set),
            "n_cited_by_rc":    len(rc_citing_set),
            "n_mutual_partners": len(mutual_partners),
        })
    most_recip.sort(key=lambda r: (-r["n_mutual_partners"], -(r["n_cites_rc"] + r["n_cited_by_rc"])))
    most_recip = most_recip[:25]

    # Hydrate top with metadata
    if most_recip:
        ids = [r["id"] for r in most_recip]
        meta = {}
        with get_conn() as conn:
            for r in conn.execute(
                "SELECT id, title, authors, journal, pub_date FROM articles WHERE id IN (" +
                ",".join("?" * len(ids)) + ")", ids
            ):
                meta[r["id"]] = dict(r)
        for row in most_recip:
            m = meta.get(row["id"], {})
            row["title"]   = m.get("title")
            row["journal"] = m.get("journal")
            row["authors"] = m.get("authors")
            row["year"]    = _year_of(m.get("pub_date"))

    return {
        "article_reciprocity": article_reciprocity,
        "edge_reciprocity":    edge_reciprocity,
        "temporal":            temporal,
        "most_reciprocated":   most_recip,
    }

# ═════════════════════════════════════════════════════════════════════════════
# Chapter 5 — The Canon Machine
# ═════════════════════════════════════════════════════════════════════════════

def ds_shape_of_influence(cluster=None, journals=None, journal=None,
                          year_from=None, year_to=None):
    """Lorenz curve, Gini, and concentration stats for the citation
    distribution. Optionally filtered to a journal/cluster scope."""
    # `journal` (singular) is kept for backward compatibility; merge with `journals`.
    if journal and not journals:
        journals = [journal]
    fclause, fparams = _filter_articles_clause(
        cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    where = " WHERE " + fclause if fclause else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT COALESCE(internal_cited_by_count, 0) AS c FROM articles{where}",
            fparams,
        ).fetchall()
        all_journals = [r["journal"] for r in conn.execute(
            "SELECT DISTINCT journal FROM articles WHERE journal IS NOT NULL ORDER BY journal"
        )]

    counts = sorted(int(r["c"]) for r in rows)
    n = len(counts)
    total_cit = sum(counts) or 1

    # Lorenz: cumulative pct of articles vs cumulative pct of citations
    lorenz = {"pct_articles": [0.0], "pct_citations": [0.0]}
    cum = 0
    for i, v in enumerate(counts, start=1):
        cum += v
        lorenz["pct_articles"].append(i / n)
        lorenz["pct_citations"].append(cum / total_cit)

    gini = _gini(counts)

    # Concentration shares
    desc = sorted(counts, reverse=True)
    def _top_share(k):
        return sum(desc[:k]) / total_cit if total_cit else 0
    concentration = {
        "n_articles":  n,
        "n_zeros":     sum(1 for c in counts if c == 0),
        "total_citations": total_cit,
        "top_10_share":  _top_share(10),
        "top_50_share":  _top_share(50),
        "top_100_share": _top_share(100),
        "top_1pct_share": _top_share(max(1, n // 100)),
    }

    # Frequency table for log-log scatter
    freq = Counter(counts)
    frequency_table = sorted(
        [{"count": k, "n_articles": v} for k, v in freq.items() if k > 0]
    , key=lambda r: r["count"])

    # Rank-frequency (Zipf)
    rank_freq = [{"rank": i + 1, "count": v} for i, v in enumerate(desc) if v > 0][:500]

    return {
        "filter_journal": journal,
        "all_journals":  all_journals,
        "lorenz":        lorenz,
        "gini":          round(gini, 4),
        "concentration": concentration,
        "frequency_table": frequency_table[:200],
        "rank_frequency":  rank_freq,
    }


def ds_long_tail(top_n=50, cluster=None, journals=None,
                 year_from=None, year_to=None):
    """Per-article concentration metrics for the top-N most-cited articles.

    For each article: self-journal rate, unique citing authors, top-citer
    share, breadth classification.
    """
    fclause, fparams = _filter_articles_clause(
        cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    extra = (" AND " + fclause) if fclause else ""
    with get_conn() as conn:
        top = [dict(r) for r in conn.execute(f"""
            SELECT id, title, authors, journal, pub_date, internal_cited_by_count
              FROM articles
             WHERE COALESCE(internal_cited_by_count, 0) > 0{extra}
             ORDER BY internal_cited_by_count DESC
             LIMIT ?
        """, fparams + [top_n])]
        # For all of them, pull the citing-side info in one shot
        if not top:
            return {"articles": [], "categories": {}, "n": 0}
        ids = [a["id"] for a in top]
        marks = ",".join("?" * len(ids))
        cite_rows = list(conn.execute(f"""
            SELECT c.target_article_id AS tgt, citing.journal AS j, citing.authors AS a
              FROM citations c
              JOIN articles citing ON citing.id = c.source_article_id
             WHERE c.target_article_id IN ({marks})
        """, ids))

    by_target = defaultdict(list)
    for r in cite_rows:
        by_target[r["tgt"]].append(r)

    out = []
    for art in top:
        rows = by_target.get(art["id"], [])
        total = len(rows)
        if total == 0:
            continue
        journal_counts = Counter(r["j"] for r in rows)
        author_counts  = Counter()
        for r in rows:
            if r["a"]:
                for nm in r["a"].split(";"):
                    nm = nm.strip()
                    if nm:
                        author_counts[nm] += 1
        unique_authors = len(author_counts)
        top_citer = author_counts.most_common(1)[0] if author_counts else (None, 0)
        author_gini = _gini(author_counts.values()) if author_counts else 0
        self_jrate = journal_counts.get(art["journal"], 0) / total
        # Breadth classification
        broad = narrow = 0
        if unique_authors >= 30: broad += 1
        elif unique_authors < 15: narrow += 1
        if self_jrate < 0.30:    broad += 1
        elif self_jrate > 0.50:  narrow += 1
        if author_gini < 0.30:   broad += 1
        elif author_gini > 0.50: narrow += 1
        if   broad >= 2: breadth = "broadly_canonical"
        elif narrow >= 2: breadth = "narrowly_canonical"
        else:             breadth = "mixed"

        out.append({
            "id": art["id"],
            "title": art["title"],
            "authors": art["authors"],
            "journal": art["journal"],
            "year": _year_of(art.get("pub_date")),
            "total_citations":      total,
            "self_journal_rate":    round(self_jrate, 3),
            "unique_citing_journals": len(journal_counts),
            "unique_citing_authors":  unique_authors,
            "author_gini":          round(author_gini, 3),
            "top_citer_name":       top_citer[0],
            "top_citer_share":      round(top_citer[1] / total, 3) if total else 0,
            "breadth":              breadth,
        })

    cats = Counter(r["breadth"] for r in out)
    return {
        "articles": out,
        "categories": dict(cats),
        "n": len(out),
    }


def ds_fair_ranking(exclude_recent_years=2, top_n=50,
                    cluster=None, journals=None,
                    year_from=None, year_to=None):
    """Citations per year of life. Compares raw rank vs normalized rank."""
    import datetime
    current_year = datetime.datetime.now().year
    cutoff_year  = current_year - exclude_recent_years

    fclause, fparams = _filter_articles_clause(
        cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    extra = (" AND " + fclause) if fclause else ""
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(f"""
            SELECT id, title, authors, journal, pub_date, internal_cited_by_count
              FROM articles
             WHERE COALESCE(internal_cited_by_count, 0) > 0{extra}
        """, fparams)]

    enriched = []
    for r in rows:
        yr = _year_of(r.get("pub_date"))
        if yr is None or yr > cutoff_year:
            continue
        cit = r["internal_cited_by_count"] or 0
        years_since = max(1, current_year - yr)
        enriched.append({
            "id": r["id"],
            "title": r["title"],
            "authors": r["authors"],
            "journal": r["journal"],
            "year": yr,
            "cited_by": cit,
            "cit_per_year": round(cit / years_since, 3),
            "years_since": years_since,
        })

    # Compute ranks
    by_raw = sorted(enriched, key=lambda r: -r["cited_by"])
    by_norm = sorted(enriched, key=lambda r: -r["cit_per_year"])
    raw_rank = {r["id"]: i + 1 for i, r in enumerate(by_raw)}
    norm_rank = {r["id"]: i + 1 for i, r in enumerate(by_norm)}
    for r in enriched:
        r["raw_rank"]  = raw_rank[r["id"]]
        r["norm_rank"] = norm_rank[r["id"]]
        r["rank_shift"] = r["raw_rank"] - r["norm_rank"]
    # Categorize
    for r in enriched:
        if r["raw_rank"] <= top_n and r["norm_rank"] <= top_n:
            r["category"] = "stable_canon"
        elif r["raw_rank"] <= top_n and r["norm_rank"] > top_n:
            r["category"] = "age_advantage"
        elif r["norm_rank"] <= top_n and r["raw_rank"] > top_n:
            r["category"] = "rising_fast"
        else:
            r["category"] = "off"

    comparison = [r for r in enriched if r["category"] != "off"]
    comparison.sort(key=lambda r: r["norm_rank"])
    cat_counts = Counter(r["category"] for r in comparison)

    return {
        "comparison": comparison,
        "categories": dict(cat_counts),
        "current_year": current_year,
        "cutoff_year":  cutoff_year,
        "top_n":        top_n,
        "n_total":      len(enriched),
    }


def ds_shifting_canons(top_n=25, cluster=None, journals=None,
                       year_from=None, year_to=None):
    """Top-N most-cited articles per generation of citing scholars.

    Filters apply to BOTH the citing and cited side (the "scope of the
    conversation"): the picture is "what was top-cited inside this slice
    of the field, by other articles inside that slice."
    """
    GENERATIONS = [
        ("Pre-1995", 0,    1995),
        ("1995-2004", 1995, 2005),
        ("2005-2014", 2005, 2015),
        ("2015+",   2015, 2100),
    ]

    citing_clause, citing_params = _filter_articles_clause(
        table_alias="citing", cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    cited_clause, cited_params = _filter_articles_clause(
        table_alias="cited", cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    where = ["c.target_article_id IS NOT NULL", "citing.pub_date IS NOT NULL"]
    params = []
    if citing_clause: where.append(citing_clause); params.extend(citing_params)
    if cited_clause:  where.append(cited_clause);  params.extend(cited_params)

    with get_conn() as conn:
        # All resolved citations with citing pub_date and target metadata
        edges = list(conn.execute(f"""
            SELECT c.target_article_id AS tgt,
                   CAST(SUBSTR(citing.pub_date, 1, 4) AS INTEGER) AS cy
              FROM citations c
              JOIN articles citing ON citing.id = c.source_article_id
              JOIN articles cited  ON cited.id  = c.target_article_id
             WHERE {" AND ".join(where)}
        """, params))
        article_ids_seen = set(e["tgt"] for e in edges)
        if not article_ids_seen:
            return {"generations": [], "comparison": [], "summary": {}}
        marks = ",".join("?" * len(article_ids_seen))
        meta = {
            r["id"]: dict(r) for r in conn.execute(
                f"SELECT id, title, authors, journal, pub_date FROM articles WHERE id IN ({marks})",
                list(article_ids_seen)
            )
        }

    # For each generation, count citations to each target by edges where citing year is in window
    gen_lists = []
    rank_by_gen = []
    for label, lo, hi in GENERATIONS:
        counts = Counter()
        for e in edges:
            if lo <= e["cy"] < hi:
                counts[e["tgt"]] += 1
        ranked = counts.most_common(top_n)
        gen_lists.append({
            "label": label,
            "lo": lo, "hi": hi,
            "top": [
                {**(meta.get(tid) or {"id": tid}), "n_citations": n,
                 "year": _year_of((meta.get(tid) or {}).get("pub_date"))}
                for tid, n in ranked
            ],
        })
        rank_by_gen.append({tid: r + 1 for r, (tid, _) in enumerate(ranked)})

    # Cross-generation comparison: for any article that was top-N in any gen
    union = set()
    for r in rank_by_gen:
        union.update(r.keys())
    comparison = []
    for tid in union:
        ranks = [r.get(tid) for r in rank_by_gen]
        meta_a = meta.get(tid) or {"id": tid}
        present_in = [GENERATIONS[i][0] for i, rk in enumerate(ranks) if rk is not None]
        if all(r is not None for r in ranks):
            cat = "enduring"
        elif sum(1 for r in ranks if r is not None) == 1:
            cat = "generational_only"
        elif ranks[0] is None and ranks[-1] is not None:
            cat = "rising"
        elif ranks[0] is not None and ranks[-1] is None:
            cat = "fading"
        else:
            cat = "intermittent"
        comparison.append({
            "id":    meta_a.get("id"),
            "title": meta_a.get("title"),
            "journal": meta_a.get("journal"),
            "authors": meta_a.get("authors"),
            "year":  _year_of(meta_a.get("pub_date")),
            "ranks": ranks,
            "category": cat,
            "in_n":  len(present_in),
        })
    comparison.sort(key=lambda r: (-(r["in_n"]), r["title"] or ""))

    return {
        "generations": gen_lists,
        "comparison":  comparison,
        "summary": {
            "n_top_per_gen": top_n,
            "categories":    dict(Counter(r["category"] for r in comparison)),
        },
    }


def ds_reach_of_citation(top_n=100, cluster=None, journals=None,
                         year_from=None, year_to=None):
    """For each top-cited article, citations per year + pattern classification."""
    fclause, fparams = _filter_articles_clause(
        cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    extra = (" AND " + fclause) if fclause else ""
    with get_conn() as conn:
        top = list(conn.execute(f"""
            SELECT id, title, authors, journal, pub_date, internal_cited_by_count
              FROM articles
             WHERE COALESCE(internal_cited_by_count, 0) > 0{extra}
             ORDER BY internal_cited_by_count DESC
             LIMIT ?
        """, fparams + [top_n]))
        if not top:
            return {"articles": [], "patterns": {}}
        ids = [r["id"] for r in top]
        marks = ",".join("?" * len(ids))
        edges = list(conn.execute(f"""
            SELECT c.target_article_id AS tgt,
                   CAST(SUBSTR(citing.pub_date, 1, 4) AS INTEGER) AS cy
              FROM citations c
              JOIN articles citing ON citing.id = c.source_article_id
             WHERE c.target_article_id IN ({marks}) AND citing.pub_date IS NOT NULL
        """, ids))

    import datetime
    current_year = datetime.datetime.now().year

    by_target = defaultdict(list)
    for e in edges:
        by_target[e["tgt"]].append(e["cy"])

    articles_out = []
    for r in top:
        py = _year_of(r["pub_date"])
        years = sorted(by_target.get(r["id"], []))
        if not years or py is None:
            continue
        # Build series from py to current
        annual = Counter(y for y in years if y >= py)
        cumulative = 0
        series = []
        for yr in range(py, current_year + 1):
            n = annual.get(yr, 0)
            cumulative += n
            series.append({"year": yr, "annual": n, "cumulative": cumulative,
                           "years_since_pub": yr - py})

        # Pattern classification
        total = cumulative
        years_since = current_year - py
        peak_year = max(annual, key=annual.get) if annual else py
        peak_age  = peak_year - py
        first_three = sum(annual.get(py + i, 0) for i in range(3))
        if total < 5:
            pattern = "too_few"
        elif years_since <= 4:
            pattern = "too_recent"
        elif first_three / total >= 0.6:
            pattern = "front_loaded"
        elif peak_age >= 5 and annual.get(peak_year, 0) >= total / years_since * 2:
            # peak count well above the average rate
            pattern = "late_bloomer"
        elif annual.get(peak_year, 0) / total >= 0.5:
            pattern = "one_wave"
        else:
            pattern = "steady_classic"

        articles_out.append({
            "id":      r["id"],
            "title":   r["title"],
            "authors": r["authors"],
            "journal": r["journal"],
            "year":    py,
            "total_citations": total,
            "pattern":  pattern,
            "peak_year": peak_year,
            "peak_count": annual.get(peak_year, 0),
            "series":  series,
        })

    return {
        "articles":  articles_out,
        "patterns":  dict(Counter(a["pattern"] for a in articles_out)),
        "current_year": current_year,
    }


def ds_inside_outside(cluster=None, journals=None,
                      year_from=None, year_to=None):
    """Internal (corpus) vs global (OpenAlex) citation rank."""
    fclause, fparams = _filter_articles_clause(
        cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    extra = (" AND " + fclause) if fclause else ""
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(f"""
            SELECT id, title, authors, journal, pub_date,
                   COALESCE(internal_cited_by_count, 0) AS internal,
                   COALESCE(crossref_cited_by_count, 0) AS global_count
              FROM articles
             WHERE (COALESCE(internal_cited_by_count, 0) > 0
                OR COALESCE(crossref_cited_by_count, 0) > 0){extra}
        """, fparams)]

    if not rows:
        return {"articles": [], "summary": {"n": 0}}

    # Ranks (1 = top)
    by_int = sorted(rows, key=lambda r: -r["internal"])
    by_glob = sorted(rows, key=lambda r: -r["global_count"])
    int_rank  = {r["id"]: i + 1 for i, r in enumerate(by_int)}
    glob_rank = {r["id"]: i + 1 for i, r in enumerate(by_glob)}

    enriched = []
    for r in rows:
        ir, gr = int_rank[r["id"]], glob_rank[r["id"]]
        # Quadrant
        N = len(rows)
        threshold = max(50, N // 50)  # top 2% or 50 absolute
        if ir <= threshold and gr <= threshold:
            quad = "shared_canon"
        elif ir <= threshold and gr > threshold:
            quad = "tpc_specific"
        elif ir > threshold and gr <= threshold:
            quad = "imported"
        else:
            quad = "background"
        enriched.append({
            "id":    r["id"],
            "title": r["title"],
            "authors": r["authors"],
            "journal": r["journal"],
            "year":   _year_of(r["pub_date"]),
            "internal":     r["internal"],
            "global_count": r["global_count"],
            "internal_rank": ir,
            "global_rank":   gr,
            "quadrant":      quad,
        })

    # Spearman rank correlation
    spearman = None
    try:
        from scipy.stats import spearmanr
        rho, p = spearmanr(
            [r["internal_rank"] for r in enriched],
            [r["global_rank"]   for r in enriched],
        )
        spearman = {"rho": float(rho), "p": float(p)}
    except Exception:
        pass

    # Top 25 in each divergent quadrant
    tpc_specific = sorted(
        [r for r in enriched if r["quadrant"] == "tpc_specific"],
        key=lambda r: r["internal_rank"]
    )[:25]
    imported = sorted(
        [r for r in enriched if r["quadrant"] == "imported"],
        key=lambda r: r["global_rank"]
    )[:25]

    return {
        "articles": enriched,
        "tpc_specific": tpc_specific,
        "imported":     imported,
        "summary": {
            "n":            len(enriched),
            "spearman":     spearman,
            "quadrants":    dict(Counter(r["quadrant"] for r in enriched)),
        },
    }

# ═════════════════════════════════════════════════════════════════════════════
# Chapter 6 — Community Structure
# ═════════════════════════════════════════════════════════════════════════════

_CT_DECADES = [
    ("1980s-90s", 1980, 2000),
    ("2000s",     2000, 2010),
    ("2010s",     2010, 2020),
    ("2020s",     2020, 2100),
]


@cached("ds_communities_time")
def ds_communities_time(cluster=None, journals=None,
                        year_from=None, year_to=None):
    """Per-decade Louvain communities + alluvial flow showing splits/merges."""
    import networkx as nx

    edges = _fetch_classified_edges(cluster=cluster, journals=journals,
                                    year_from=year_from, year_to=year_to)
    article_meta = {}
    with get_conn() as conn:
        for r in conn.execute(
            "SELECT id, title, journal, pub_date, internal_cited_by_count, tags "
            "FROM articles"
        ):
            article_meta[r["id"]] = {
                "id": r["id"], "title": r["title"], "journal": r["journal"],
                "year": _year_of(r["pub_date"]),
                "cited_by": r["internal_cited_by_count"] or 0,
                "tags": r["tags"] or "",
            }

    decade_partitions = []  # list of {label, communities, modularity, sizes, top_articles, top_journals, top_tags}

    for label, lo, hi in _CT_DECADES:
        # Undirected weighted graph: edges where the CITING article is in the
        # decade window (cited may be older). This means a "decade community"
        # is the citing-side of a community as it existed in that period; the
        # same older article can appear as a member of multiple decade-graphs
        # if it was cited in each — which is what the alluvial alignment relies on.
        G = nx.Graph()
        for e in edges:
            cy = e["citing_year"]
            if cy is None:
                continue
            if not (lo <= cy < hi):
                continue
            if e["citing_id"] == e["cited_id"]:
                continue
            if G.has_edge(e["citing_id"], e["cited_id"]):
                G[e["citing_id"]][e["cited_id"]]["weight"] += 1
            else:
                G.add_edge(e["citing_id"], e["cited_id"], weight=1)

        if G.number_of_nodes() < 5:
            decade_partitions.append({
                "label": label, "lo": lo, "hi": hi,
                "communities": [], "modularity": 0.0,
                "n_nodes": G.number_of_nodes(),
                "n_edges": G.number_of_edges(),
            })
            continue

        comms = nx.community.louvain_communities(G, weight="weight", seed=42)
        try:
            mod = nx.community.modularity(G, comms, weight="weight")
        except Exception:
            mod = 0.0

        # Order communities by size; cap at top 12 for readability
        comms_sorted = sorted(comms, key=len, reverse=True)[:12]
        communities = []
        for ci, members in enumerate(comms_sorted):
            members = list(members)
            metas = [article_meta.get(m) for m in members if m in article_meta]
            top_arts = sorted(metas, key=lambda m: -(m.get("cited_by") or 0))[:5]
            journal_counter = Counter(m["journal"] for m in metas if m and m.get("journal"))
            tag_counter = Counter()
            for m in metas:
                for t in (m.get("tags") or "").split(","):
                    t = t.strip()
                    if t:
                        tag_counter[t] += 1
            communities.append({
                "id":         f"{label}_{ci}",
                "decade":     label,
                "rank":       ci,
                "size":       len(members),
                "members":    members[:200],  # cap to keep payload small
                "top_articles": [{"id": m["id"], "title": m.get("title"),
                                  "journal": m.get("journal"), "year": m.get("year"),
                                  "cited_by": m.get("cited_by")} for m in top_arts],
                "top_journals": journal_counter.most_common(5),
                "top_tags":     tag_counter.most_common(5),
            })

        decade_partitions.append({
            "label": label, "lo": lo, "hi": hi,
            "communities": communities,
            "modularity":  round(mod, 3),
            "n_nodes":     G.number_of_nodes(),
            "n_edges":     G.number_of_edges(),
        })

    # Build sankey/alluvial: link each decade's community to the next where article overlap is high
    sankey_nodes = []
    sankey_links = []
    node_idx = {}
    for dp in decade_partitions:
        for c in dp["communities"]:
            node_idx[c["id"]] = len(sankey_nodes)
            sankey_nodes.append({
                "id":     c["id"],
                "name":   dp["label"] + " #" + str(c["rank"] + 1),
                "decade": dp["label"],
                "size":   c["size"],
                "top_journal": (c["top_journals"][0][0] if c["top_journals"] else None),
            })

    for i in range(len(decade_partitions) - 1):
        a, b = decade_partitions[i], decade_partitions[i + 1]
        for ca in a["communities"]:
            mems_a = set(ca["members"])
            if not mems_a: continue
            for cb in b["communities"]:
                mems_b = set(cb["members"])
                if not mems_b: continue
                shared = mems_a & mems_b
                if len(shared) < 2:
                    continue
                jaccard = len(shared) / len(mems_a | mems_b)
                if jaccard < 0.05:
                    continue
                sankey_links.append({
                    "source":  node_idx[ca["id"]],
                    "target":  node_idx[cb["id"]],
                    "value":   len(shared),
                    "jaccard": round(jaccard, 3),
                })

    return {
        "decades":      [
            {**d, "communities": [{**c, "members": []} for c in d["communities"]]}
            for d in decade_partitions
        ],  # strip member lists to keep payload small
        "sankey_nodes": sankey_nodes,
        "sankey_links": sankey_links,
    }


@cached("ds_walls_bridges")
def ds_walls_bridges(cluster=None, journals=None,
                     year_from=None, year_to=None):
    """Per-community insularity: internal vs external citation density."""
    import networkx as nx

    edges = _fetch_classified_edges(cluster=cluster, journals=journals,
                                    year_from=year_from, year_to=year_to)
    G = nx.Graph()
    for e in edges:
        if e["citing_id"] == e["cited_id"]:
            continue
        if G.has_edge(e["citing_id"], e["cited_id"]):
            G[e["citing_id"]][e["cited_id"]]["weight"] += 1
        else:
            G.add_edge(e["citing_id"], e["cited_id"], weight=1)

    if G.number_of_nodes() == 0:
        return {"communities": [], "summary": {"n": 0}}

    comms = nx.community.louvain_communities(G, weight="weight", seed=42)
    comms_sorted = sorted(comms, key=len, reverse=True)[:25]

    article_meta = {}
    with get_conn() as conn:
        all_ids = list({n for c in comms_sorted for n in c})
        marks = ",".join("?" * len(all_ids))
        for r in conn.execute(
            f"SELECT id, title, journal, pub_date, internal_cited_by_count, tags "
            f"FROM articles WHERE id IN ({marks})", all_ids
        ):
            article_meta[r["id"]] = {
                "id": r["id"], "title": r["title"], "journal": r["journal"],
                "year": _year_of(r["pub_date"]),
                "cited_by": r["internal_cited_by_count"] or 0,
                "tags": r["tags"] or "",
            }

    communities = []
    for ci, mems in enumerate(comms_sorted):
        mems_set = set(mems)
        n = len(mems_set)
        if n < 5:
            continue
        internal = 0
        external = 0
        for u, v, attrs in G.edges(data=True):
            ua = u in mems_set
            va = v in mems_set
            if ua and va:
                internal += attrs.get("weight", 1)
            elif ua or va:
                external += attrs.get("weight", 1)
        # Densities (max possible = n*(n-1)/2 for internal, n*(N-n) for external)
        N = G.number_of_nodes()
        max_int = max(1, n * (n - 1) // 2)
        max_ext = max(1, n * (N - n))
        internal_density = internal / max_int
        external_density = external / max_ext
        insularity = internal_density / max(external_density, 1e-9)

        metas = [article_meta.get(m) for m in mems if m in article_meta]
        top_arts = sorted(metas, key=lambda m: -(m.get("cited_by") or 0))[:5]
        journals = Counter(m["journal"] for m in metas if m and m.get("journal"))
        tag_counter = Counter()
        for m in metas:
            for t in (m.get("tags") or "").split(","):
                t = t.strip()
                if t:
                    tag_counter[t] += 1
        communities.append({
            "rank": ci,
            "n_articles": n,
            "internal_edges": internal,
            "external_edges": external,
            "internal_density": round(internal_density, 5),
            "external_density": round(external_density, 5),
            "insularity_ratio": round(insularity, 2),
            "top_articles": [{"id": m["id"], "title": m.get("title"),
                              "journal": m.get("journal"), "year": m.get("year"),
                              "cited_by": m.get("cited_by")} for m in top_arts],
            "top_journals": journals.most_common(5),
            "top_tags": tag_counter.most_common(5),
        })

    # Distribution-based classification (insularity ratio quartiles)
    if communities:
        ratios = sorted(c["insularity_ratio"] for c in communities)
        q1 = ratios[len(ratios) // 4]
        q3 = ratios[(3 * len(ratios)) // 4]
        for c in communities:
            if c["insularity_ratio"] < q1:
                c["classification"] = "integrative"
            elif c["insularity_ratio"] > q3:
                c["classification"] = "insular"
            else:
                c["classification"] = "moderate"

    return {
        "communities": communities,
        "summary": {
            "n": len(communities),
            "graph_nodes": G.number_of_nodes(),
            "graph_edges": G.number_of_edges(),
        },
    }


@cached("ds_first_spark")
def ds_first_spark(cluster=None, journals=None,
                   year_from=None, year_to=None):
    """Key-route SPC main paths through the citation DAG.

    Computes SPC across the full DAG, extracts top-K edges, returns their
    connected components as routes. Slowest tool — 60-90s on first run.
    """
    import networkx as nx

    edges = _fetch_classified_edges(cluster=cluster, journals=journals,
                                    year_from=year_from, year_to=year_to)
    DG = nx.DiGraph()
    for e in edges:
        cy, dy = e["citing_year"], e["cited_year"]
        if cy is None or dy is None or e["citing_id"] == e["cited_id"]:
            continue
        # SPC orient: cited -> citing (knowledge flow)
        DG.add_edge(e["cited_id"], e["citing_id"])

    # Strip cycles greedily until DAG
    while True:
        try:
            cyc = nx.find_cycle(DG, orientation="original")
        except nx.NetworkXNoCycle:
            break
        DG.remove_edge(cyc[0][0], cyc[0][1])

    if DG.number_of_nodes() == 0:
        return {"routes": [], "global_path": [], "all_articles": {}, "stats": {}}

    order = list(nx.topological_sort(DG))
    paths_to   = {n: (1 if DG.in_degree(n)  == 0 else 0) for n in DG.nodes}
    for n in order:
        for s in DG.successors(n):
            paths_to[s] += paths_to[n]
    paths_from = {n: (1 if DG.out_degree(n) == 0 else 0) for n in DG.nodes}
    for n in reversed(order):
        for p in DG.predecessors(n):
            paths_from[p] += paths_from[n]

    edge_spc = {}
    for u, v in DG.edges():
        edge_spc[(u, v)] = paths_to[u] * paths_from[v]

    # Top-K edges; iteratively grow K until at least 3 connected route components emerge
    sorted_edges = sorted(edge_spc.items(), key=lambda kv: -kv[1])
    routes = []
    for K in (40, 80, 120, 200, 300, 500):
        sub = nx.DiGraph()
        for (u, v), spc in sorted_edges[:K]:
            sub.add_edge(u, v)
        comps = list(nx.weakly_connected_components(sub))
        if len(comps) >= 3 or K >= 500:
            # Extract longest path within each component
            for comp in comps:
                comp_g = sub.subgraph(comp).copy()
                # Find a longest path (DAG -> longest_path is fast)
                try:
                    longest = nx.dag_longest_path(comp_g)
                except Exception:
                    longest = list(comp)
                if len(longest) < 3:
                    continue
                spc_sum = sum(edge_spc.get((longest[i], longest[i + 1]), 0)
                              for i in range(len(longest) - 1))
                routes.append({
                    "n_nodes":  len(longest),
                    "n_edges":  len(longest) - 1,
                    "spc_total": spc_sum,
                    "path":     longest,
                })
            break

    routes.sort(key=lambda r: -r["spc_total"])
    routes = routes[:5]

    # Global path: highest-SPC source-to-sink chain (greedy from top source)
    sources = [n for n in DG.nodes if DG.in_degree(n)  == 0]
    if sources:
        seed = max(sources, key=lambda n: sum(edge_spc.get((n, s), 0) for s in DG.successors(n)) or 0)
        cur = seed
        global_path = [cur]
        seen = {cur}
        while DG.out_degree(cur) > 0:
            succ = [(s, edge_spc.get((cur, s), 0)) for s in DG.successors(cur) if s not in seen]
            if not succ:
                break
            nxt, _ = max(succ, key=lambda x: x[1])
            global_path.append(nxt)
            seen.add(nxt)
            cur = nxt
    else:
        global_path = []

    # Article metadata for everything that appears
    needed = set(global_path)
    for r in routes:
        needed.update(r["path"])
    all_articles = {}
    if needed:
        with get_conn() as conn:
            marks = ",".join("?" * len(needed))
            for r in conn.execute(
                f"SELECT id, title, authors, journal, pub_date, internal_cited_by_count "
                f"FROM articles WHERE id IN ({marks})", list(needed)
            ):
                all_articles[r["id"]] = {
                    "id": r["id"], "title": r["title"], "authors": r["authors"],
                    "journal": r["journal"], "year": _year_of(r["pub_date"]),
                    "cited_by": r["internal_cited_by_count"] or 0,
                    "group": get_journal_group(r["journal"]),
                }

    return {
        "routes": [
            {**r, "articles": [all_articles.get(n, {"id": n}) for n in r["path"]]}
            for r in routes
        ],
        "global_path": [all_articles.get(n, {"id": n}) for n in global_path],
        "stats": {
            "n_nodes": DG.number_of_nodes(),
            "n_edges": DG.number_of_edges(),
            "n_routes": len(routes),
        },
    }

# ═════════════════════════════════════════════════════════════════════════════
# Chapter 7 — Reference Networks
# ═════════════════════════════════════════════════════════════════════════════

def _compute_coupling_pairs(min_coupling=3, max_articles=600,
                            cluster=None, journals=None,
                            year_from=None, year_to=None):
    """Build bibliographic coupling pairs from the citations table.

    Two articles A and B are bibliographically coupled if they cite the same
    third article. Coupling strength = count of shared references. Returns
    a dict (a_id, b_id) -> {shared, jaccard, refs_a, refs_b}.
    """
    # Resolve which articles are in scope from the filter, then restrict the
    # citing side to that set.
    citing_clause, citing_params = _filter_articles_clause(
        table_alias="a", cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    join = "JOIN articles a ON a.id = c.source_article_id" if citing_clause else ""
    extra = (" AND " + citing_clause) if citing_clause else ""

    # Fetch all (citing_id, target_ref) edges, including unresolved (string) refs
    refs_by_article = defaultdict(set)
    sql = f"""
        SELECT c.source_article_id, c.target_article_id, c.target_doi
          FROM citations c
          {join}
         WHERE c.source_article_id IS NOT NULL{extra}
    """
    with get_conn() as conn:
        for r in conn.execute(sql, citing_params):
            tgt = r["target_article_id"] if r["target_article_id"] else r["target_doi"]
            if tgt:
                refs_by_article[r["source_article_id"]].add(tgt)
    # Filter to articles that have at least 5 references (otherwise too noisy)
    refs_by_article = {a: refs for a, refs in refs_by_article.items() if len(refs) >= 5}

    # Inverted index: ref -> {citing articles}
    citing_of_ref = defaultdict(set)
    for a, refs in refs_by_article.items():
        for ref in refs:
            citing_of_ref[ref].add(a)

    # For each ref cited by >= 2 articles, the pairs of those articles
    # accumulate +1 to coupling strength
    raw = defaultdict(int)
    for ref, citers in citing_of_ref.items():
        if len(citers) < 2 or len(citers) > 200:
            continue  # skip extreme refs (maximally generic refs would dominate)
        clist = sorted(citers)
        for i in range(len(clist)):
            for j in range(i + 1, len(clist)):
                raw[(clist[i], clist[j])] += 1

    # Convert to enriched pair list
    pairs = []
    for (a, b), shared in raw.items():
        if shared < min_coupling:
            continue
        ra = len(refs_by_article.get(a, ()))
        rb = len(refs_by_article.get(b, ()))
        pairs.append({
            "a": a, "b": b,
            "shared": shared,
            "refs_a": ra, "refs_b": rb,
            "jaccard": shared / max(1, ra + rb - shared),
        })
    pairs.sort(key=lambda p: -p["shared"])
    return pairs[:max_articles * max_articles]  # cap for memory


@cached("ds_shared_foundations")
def ds_shared_foundations(min_coupling=3, cluster=None, journals=None,
                          year_from=None, year_to=None):
    """Bibliographic coupling network: top coupled pairs as a force graph."""
    pairs = _compute_coupling_pairs(
        min_coupling=min_coupling, max_articles=400,
        cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )

    # Pick the top 400 articles by coupling-degree centrality
    deg = Counter()
    for p in pairs:
        deg[p["a"]] += p["shared"]
        deg[p["b"]] += p["shared"]
    top_ids = [a for a, _ in deg.most_common(400)]
    top_set = set(top_ids)

    # Filter pairs to top set
    edges = [p for p in pairs if p["a"] in top_set and p["b"] in top_set][:2000]

    if not top_ids:
        return {"nodes": [], "links": [], "stats": {"min_coupling": min_coupling, "n_pairs": 0}}

    with get_conn() as conn:
        marks = ",".join("?" * len(top_ids))
        meta = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT id, title, journal, pub_date, internal_cited_by_count "
            f"FROM articles WHERE id IN ({marks})", top_ids
        )}
    nodes = [{
        "id":      n,
        "title":   meta.get(n, {}).get("title"),
        "journal": meta.get(n, {}).get("journal"),
        "year":    _year_of(meta.get(n, {}).get("pub_date")),
        "cited_by": meta.get(n, {}).get("internal_cited_by_count") or 0,
        "group":   get_journal_group(meta.get(n, {}).get("journal")),
        "degree":  deg.get(n, 0),
    } for n in top_ids]
    links = [{"source": p["a"], "target": p["b"], "value": p["shared"]} for p in edges]

    return {
        "nodes": nodes, "links": links,
        "stats": {
            "min_coupling": min_coupling,
            "n_pairs":      len(pairs),
            "n_nodes":      len(nodes),
            "n_edges":      len(links),
        },
    }


@cached("ds_two_maps")
def ds_two_maps(cluster=None, journals=None,
                year_from=None, year_to=None):
    """Compare coupling clusters to citation communities; report concordance."""
    import networkx as nx

    # Coupling clusters via Louvain on coupling graph
    pairs = _compute_coupling_pairs(
        min_coupling=3, max_articles=600,
        cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    Gc = nx.Graph()
    for p in pairs:
        Gc.add_edge(p["a"], p["b"], weight=p["shared"])
    if Gc.number_of_nodes() == 0:
        return {"summary": {"n": 0}, "comparison": [], "labels": {}}

    coupling_comms = nx.community.louvain_communities(Gc, weight="weight", seed=42)
    coupling_label = {}
    for i, c in enumerate(coupling_comms):
        for n in c:
            coupling_label[n] = i

    # Citation communities via Louvain on undirected citation graph
    edges = _fetch_classified_edges(cluster=cluster, journals=journals,
                                    year_from=year_from, year_to=year_to)
    Gci = nx.Graph()
    for e in edges:
        if e["citing_id"] == e["cited_id"]:
            continue
        if Gci.has_edge(e["citing_id"], e["cited_id"]):
            Gci[e["citing_id"]][e["cited_id"]]["weight"] += 1
        else:
            Gci.add_edge(e["citing_id"], e["cited_id"], weight=1)
    cit_comms = nx.community.louvain_communities(Gci, weight="weight", seed=42)
    cit_label = {}
    for i, c in enumerate(cit_comms):
        for n in c:
            cit_label[n] = i

    # Find articles labelled by both
    shared_ids = sorted(set(coupling_label) & set(cit_label))
    if not shared_ids:
        return {"summary": {"n": 0}, "comparison": [], "labels": {}}

    cl = [coupling_label[i] for i in shared_ids]
    ci = [cit_label[i]      for i in shared_ids]

    # NMI / ARI
    nmi = ari = None
    try:
        from sklearn.metrics import normalized_mutual_info_score, adjusted_rand_score
        nmi = float(normalized_mutual_info_score(cl, ci))
        ari = float(adjusted_rand_score(cl, ci))
    except Exception:
        pass

    # For each article: concordant if both labels' top-mode pair line up
    # We approximate with: count, per coupling cluster, how many articles share the same citation cluster majority
    from collections import defaultdict as _dd
    coup_to_cit = _dd(Counter)
    for i in shared_ids:
        coup_to_cit[coupling_label[i]][cit_label[i]] += 1
    coup_to_cit_majority = {cl_: cit.most_common(1)[0][0] for cl_, cit in coup_to_cit.items()}

    # Article hydration
    with get_conn() as conn:
        ids_chunk = shared_ids[:2000]  # cap
        marks = ",".join("?" * len(ids_chunk))
        meta = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT id, title, journal, pub_date, internal_cited_by_count "
            f"FROM articles WHERE id IN ({marks})", ids_chunk
        )}
    comparison = []
    for i in ids_chunk:
        m = meta.get(i, {"id": i})
        cl_ = coupling_label[i]
        ci_ = cit_label[i]
        concordant = (coup_to_cit_majority.get(cl_) == ci_)
        comparison.append({
            "id": i,
            "title":   m.get("title"),
            "journal": m.get("journal"),
            "year":    _year_of(m.get("pub_date")),
            "cited_by": m.get("internal_cited_by_count") or 0,
            "coupling_cluster":   cl_,
            "citation_community": ci_,
            "concordant":          concordant,
        })

    n_conc = sum(1 for r in comparison if r["concordant"])
    return {
        "summary": {
            "n":          len(comparison),
            "n_concordant": n_conc,
            "concordance_rate": n_conc / max(1, len(comparison)),
            "n_coupling_clusters":    len(coupling_comms),
            "n_citation_communities": len(cit_comms),
            "nmi": nmi,
            "ari": ari,
        },
        "comparison": comparison[:300],  # ship a sample
    }


def ds_books_everyone_reads(cluster=None, journals=None,
                            year_from=None, year_to=None):
    """Master-works list: articles that appear across the most Datastories tools.

    Aggregates article IDs from a fixed roster of tools in this module that
    return ranked article lists (Ch4 border crossers, Ch5 long-tail, fair
    ranking, shifting canons, reach-of-citation, Ch6 walls-and-bridges top
    articles). Filters propagate to each underlying tool, so a cluster /
    year-range scope produces a master-works list specific to that scope.
    """
    fkw = dict(cluster=cluster, journals=journals,
               year_from=year_from, year_to=year_to)

    article_to_tools = defaultdict(set)
    article_meta = {}

    def _record(tool_name, articles):
        for a in (articles or []):
            aid = a.get("id") if isinstance(a, dict) else None
            if not aid:
                continue
            article_to_tools[aid].add(tool_name)
            if aid not in article_meta and isinstance(a, dict):
                article_meta[aid] = {
                    "id": aid,
                    "title":   a.get("title"),
                    "journal": a.get("journal"),
                    "year":    a.get("year"),
                    "authors": a.get("authors"),
                }

    # Pull from each tool's output (they're cached so this is cheap)
    try:
        _record("Border Crossers", ds_border_crossers(**fkw).get("top_bridges"))
    except Exception:
        pass
    try:
        _record("The Long Tail", ds_long_tail(top_n=50, **fkw).get("articles"))
    except Exception:
        pass
    try:
        _record("The Fair Ranking", ds_fair_ranking(**fkw).get("comparison"))
    except Exception:
        pass
    try:
        for gen in ds_shifting_canons(**fkw).get("generations", []):
            _record("Shifting Canons", gen.get("top"))
    except Exception:
        pass
    try:
        _record("The Reach of a Citation", ds_reach_of_citation(**fkw).get("articles"))
    except Exception:
        pass
    try:
        for c in ds_walls_bridges(**fkw).get("communities", []):
            _record("Walls and Bridges", c.get("top_articles"))
    except Exception:
        pass

    # Hydrate any missing metadata
    missing = [a for a in article_to_tools if a not in article_meta]
    if missing:
        with get_conn() as conn:
            ms = ",".join("?" * min(500, len(missing)))
            for r in conn.execute(
                f"SELECT id, title, authors, journal, pub_date FROM articles "
                f"WHERE id IN ({ms})", missing[:500]
            ):
                article_meta[r["id"]] = {
                    "id": r["id"], "title": r["title"], "journal": r["journal"],
                    "authors": r["authors"], "year": _year_of(r["pub_date"]),
                }

    rows = []
    for aid, tools in article_to_tools.items():
        m = article_meta.get(aid, {"id": aid})
        rows.append({**m, "tools": sorted(tools), "n_tools": len(tools)})
    rows.sort(key=lambda r: (-r["n_tools"], r.get("title") or ""))

    return {
        "articles": rows[:200],
        "summary":  {
            "n_articles":   len(rows),
            "n_tools_used": len({t for r in rows for t in r["tools"]}),
        },
    }


@cached("ds_uneven_debts")
def ds_uneven_debts(cluster=None, journals=None,
                    year_from=None, year_to=None):
    """Asymmetric bibliographic coupling — pairs where one article depends
    on the other's reading more than the reverse."""
    pairs = _compute_coupling_pairs(
        min_coupling=4, max_articles=400,
        cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    if not pairs:
        return {"pairs": [], "summary": {"n": 0}}

    out = []
    for p in pairs:
        ratio_a = p["shared"] / max(1, p["refs_a"])
        ratio_b = p["shared"] / max(1, p["refs_b"])
        asym = abs(ratio_a - ratio_b)
        dependent = p["a"] if ratio_a > ratio_b else p["b"]
        out.append({
            "a": p["a"], "b": p["b"],
            "shared": p["shared"],
            "refs_a": p["refs_a"], "refs_b": p["refs_b"],
            "ratio_a": round(ratio_a, 4),
            "ratio_b": round(ratio_b, 4),
            "asymmetry": round(asym, 4),
            "dependent": dependent,
        })
    out.sort(key=lambda p: -p["asymmetry"])
    out = out[:200]

    # Hydrate metadata
    needed = set()
    for p in out:
        needed.update([p["a"], p["b"]])
    with get_conn() as conn:
        marks = ",".join("?" * len(needed))
        meta = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT id, title, journal, pub_date FROM articles WHERE id IN ({marks})",
            list(needed)
        )}
    for p in out:
        for k in ("a", "b"):
            m = meta.get(p[k], {})
            p[k + "_title"]   = m.get("title")
            p[k + "_journal"] = m.get("journal")
            p[k + "_year"]    = _year_of(m.get("pub_date"))

    return {
        "pairs":   out,
        "summary": {
            "n":         len(out),
            "max_asym":  max(p["asymmetry"] for p in out) if out else 0,
            "median_asym": sorted(p["asymmetry"] for p in out)[len(out) // 2] if out else 0,
        },
    }

# ═════════════════════════════════════════════════════════════════════════════
# Chapter 8 — Authorship & Collaboration
# ═════════════════════════════════════════════════════════════════════════════

def _split_authors(s):
    if not s:
        return []
    return [a.strip() for a in s.split(";") if a.strip()]


def ds_solo_to_squad(cluster=None, journals=None,
                     year_from=None, year_to=None):
    """Per-year team-size statistics + milestones."""
    fclause, fparams = _filter_articles_clause(
        cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    extra = (" AND " + fclause) if fclause else ""
    yearly = defaultdict(list)  # year -> list of author counts per article
    largest_teams = []  # capped to top 25
    with get_conn() as conn:
        for r in conn.execute(
            f"SELECT id, title, journal, pub_date, authors FROM articles "
            f"WHERE pub_date IS NOT NULL AND authors IS NOT NULL{extra}",
            fparams,
        ):
            yr = _year_of(r["pub_date"])
            authors = _split_authors(r["authors"])
            if yr is None or not authors:
                continue
            yearly[yr].append(len(authors))
            if len(authors) >= 5:
                largest_teams.append({
                    "id":      r["id"],
                    "title":   r["title"],
                    "journal": r["journal"],
                    "year":    yr,
                    "n_authors": len(authors),
                    "authors":   r["authors"],
                })

    yearly_stats = []
    for y in sorted(yearly):
        counts = yearly[y]
        n = len(counts)
        mean = sum(counts) / n
        median = sorted(counts)[n // 2]
        p75 = sorted(counts)[(3 * n) // 4]
        single = sum(1 for c in counts if c == 1)
        yearly_stats.append({
            "year": y,
            "n_articles":     n,
            "single_author_pct": single / n,
            "mean_authors":   round(mean, 2),
            "median":         median,
            "p75":            p75,
            "max":            max(counts),
        })

    decade_stats = defaultdict(lambda: {"n": 0, "single": 0, "total_authors": 0, "max": 0})
    for ys in yearly_stats:
        d = (ys["year"] // 10) * 10
        bucket = decade_stats[d]
        bucket["n"]      += ys["n_articles"]
        bucket["single"] += int(ys["single_author_pct"] * ys["n_articles"])
        bucket["total_authors"] += ys["mean_authors"] * ys["n_articles"]
        bucket["max"]    = max(bucket["max"], ys["max"])
    decade_out = []
    for d in sorted(decade_stats):
        b = decade_stats[d]
        decade_out.append({
            "decade":   d,
            "n":        b["n"],
            "single_author_pct": (b["single"] / b["n"]) if b["n"] else 0,
            "mean_authors":      (b["total_authors"] / b["n"]) if b["n"] else 0,
            "max":      b["max"],
        })

    # Milestones
    first_5 = next((y for y in yearly_stats if y["max"] >= 5), None)
    first_10 = next((y for y in yearly_stats if y["max"] >= 10), None)
    crossover = next((y for y in yearly_stats if y["single_author_pct"] < 0.5), None)
    milestones = []
    if first_5:    milestones.append({"event": "First 5+ author article", "year": first_5["year"]})
    if first_10:   milestones.append({"event": "First 10+ author article", "year": first_10["year"]})
    if crossover:  milestones.append({"event": "Single-author share dropped below 50%", "year": crossover["year"]})

    largest_teams.sort(key=lambda t: -t["n_authors"])
    largest_teams = largest_teams[:25]

    return {
        "yearly":    yearly_stats,
        "decadal":   decade_out,
        "milestones": milestones,
        "largest_teams": largest_teams,
    }


def ds_academic_lineages(min_gap=10, cluster=None, journals=None,
                         year_from=None, year_to=None):
    """Probable mentor → mentee co-authorship graph."""
    fclause, fparams = _filter_articles_clause(
        cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    extra = (" AND " + fclause) if fclause else ""
    with get_conn() as conn:
        rows = list(conn.execute(
            f"SELECT id, pub_date, authors FROM articles WHERE pub_date IS NOT NULL AND authors IS NOT NULL{extra}",
            fparams,
        ))

    # First-pub year for each author
    first_pub = {}
    for r in rows:
        yr = _year_of(r["pub_date"])
        if yr is None: continue
        for a in _split_authors(r["authors"]):
            if a not in first_pub or first_pub[a] > yr:
                first_pub[a] = yr

    # For each article, examine its co-author pairs
    pair_counts = defaultdict(lambda: {"n": 0, "first_year": None, "last_year": None})
    for r in rows:
        yr = _year_of(r["pub_date"])
        authors = _split_authors(r["authors"])
        if yr is None or len(authors) < 2:
            continue
        for i in range(len(authors)):
            for j in range(i + 1, len(authors)):
                a, b = sorted([authors[i], authors[j]])
                pc = pair_counts[(a, b)]
                pc["n"] += 1
                if pc["first_year"] is None or yr < pc["first_year"]:
                    pc["first_year"] = yr
                if pc["last_year"] is None or yr > pc["last_year"]:
                    pc["last_year"] = yr

    # Filter to mentor-like pairs (gap >= min_gap)
    mentorships = []
    for (a, b), pc in pair_counts.items():
        ya = first_pub.get(a, 9999)
        yb = first_pub.get(b, 9999)
        gap = abs(ya - yb)
        if gap < min_gap:
            continue
        mentor, mentee = (a, b) if ya < yb else (b, a)
        mentorships.append({
            "mentor": mentor, "mentee": mentee,
            "mentor_first_pub": min(ya, yb),
            "mentee_first_pub": max(ya, yb),
            "year_gap": gap,
            "n_coauthored": pc["n"],
            "first_coauthored": pc["first_year"],
            "last_coauthored":  pc["last_year"],
        })
    mentorships.sort(key=lambda m: -m["n_coauthored"])

    # Mentor → [mentees] graph
    mentor_to = defaultdict(list)
    for m in mentorships:
        mentor_to[m["mentor"]].append(m["mentee"])

    prolific_mentors = sorted(
        [{"mentor": m, "n_mentees": len(set(mentees))} for m, mentees in mentor_to.items() if len(set(mentees)) >= 3],
        key=lambda m: -m["n_mentees"],
    )

    # Build the network for force-graph rendering
    nodes_set = set()
    for m in mentorships[:120]:
        nodes_set.add(m["mentor"])
        nodes_set.add(m["mentee"])
    nodes = [{"id": n, "first_pub": first_pub.get(n)} for n in nodes_set]
    links = [
        {"source": m["mentor"], "target": m["mentee"],
         "value": m["n_coauthored"], "gap": m["year_gap"]}
        for m in mentorships[:120]
    ]

    return {
        "mentorships":      mentorships[:200],
        "prolific_mentors": prolific_mentors[:30],
        "graph":            {"nodes": nodes, "links": links},
        "summary": {
            "n_pairs":    len(mentorships),
            "n_mentors":  len(mentor_to),
            "min_gap":    min_gap,
        },
    }


def ds_lasting_partnerships(cluster=None, journals=None,
                            year_from=None, year_to=None):
    """Co-author pairs classified by persistence: one-shot / short-term / persistent."""
    fclause, fparams = _filter_articles_clause(
        cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    extra = (" AND " + fclause) if fclause else ""
    pair_counts = defaultdict(lambda: {"n": 0, "first": None, "last": None, "journals": set()})
    with get_conn() as conn:
        for r in conn.execute(
            f"SELECT pub_date, authors, journal FROM articles "
            f"WHERE pub_date IS NOT NULL AND authors IS NOT NULL{extra}",
            fparams,
        ):
            yr = _year_of(r["pub_date"])
            authors = _split_authors(r["authors"])
            if yr is None or len(authors) < 2:
                continue
            for i in range(len(authors)):
                for j in range(i + 1, len(authors)):
                    a, b = sorted([authors[i], authors[j]])
                    pc = pair_counts[(a, b)]
                    pc["n"] += 1
                    if pc["first"] is None or yr < pc["first"]:
                        pc["first"] = yr
                    if pc["last"] is None or yr > pc["last"]:
                        pc["last"] = yr
                    if r["journal"]:
                        pc["journals"].add(r["journal"])

    rows = []
    for (a, b), pc in pair_counts.items():
        span = (pc["last"] - pc["first"]) if pc["first"] is not None else 0
        if pc["n"] == 1:
            cat = "one_shot"
        elif pc["n"] == 2:
            cat = "short_term"
        elif pc["n"] >= 3 and span >= 5:
            cat = "persistent"
        else:
            cat = "short_term"
        rows.append({
            "a": a, "b": b,
            "n":      pc["n"],
            "first":  pc["first"],
            "last":   pc["last"],
            "span":   span,
            "journals": sorted(pc["journals"]),
            "category": cat,
        })
    rows.sort(key=lambda r: (-r["n"], -r["span"]))

    cats = Counter(r["category"] for r in rows)

    return {
        "summary": {
            "n_pairs":      len(rows),
            "categories":   dict(cats),
        },
        "persistent": [r for r in rows if r["category"] == "persistent"][:50],
        "all_top":    rows[:25],
    }

# ═════════════════════════════════════════════════════════════════════════════
# Chapter 9 — Sleeping Beauties
# ═════════════════════════════════════════════════════════════════════════════

@cached("ds_prince_network")
def ds_prince_network(cluster=None, journals=None,
                      year_from=None, year_to=None):
    """For each sleeping beauty, the 'princes' (citing articles in the
    awakening window) and the type of awakening they collectively represent."""
    from .citations import get_sleeping_beauties

    # The existing get_sleeping_beauties supports `journals` and year range.
    js = _resolve_journal_filter(cluster=cluster, journals=journals)
    sb_resp = get_sleeping_beauties(
        min_total_citations=10, max_results=15,
        journals=js, year_from=year_from, year_to=year_to,
    ) or {}
    beauties = sb_resp.get("articles", [])
    if not beauties:
        return {"beauties": [], "stats": {"n": 0}}

    # For each beauty, pull the princes (articles citing it whose pub_date
    # is within the awakening window).
    out = []
    for b in beauties:
        bid = b["id"]
        awake_year = b.get("awakening_year")
        with get_conn() as conn:
            citing_rows = list(conn.execute("""
                SELECT citing.id AS cid, citing.title AS title, citing.authors AS authors,
                       citing.journal AS journal, citing.pub_date AS pub_date
                  FROM citations c
                  JOIN articles citing ON citing.id = c.source_article_id
                 WHERE c.target_article_id = ?
            """, (bid,)))
        princes = []
        for r in citing_rows:
            yr = _year_of(r["pub_date"])
            if awake_year is not None and yr is not None and yr >= awake_year - 1:
                princes.append({
                    "id":      r["cid"],
                    "title":   r["title"],
                    "authors": r["authors"],
                    "journal": r["journal"],
                    "year":    yr,
                })

        # Awakening-type heuristic
        journals = Counter(p["journal"] for p in princes)
        unique_journals = len(journals)
        unique_first_authors = len({(p["authors"] or "").split(";")[0].strip() for p in princes if p["authors"]})
        if len(princes) == 0:
            atype = "no_data"
        elif len(princes) == 1:
            atype = "single_catalyst"
        elif unique_journals == 1:
            atype = "journal_specific"
        elif unique_first_authors == 1:
            atype = "individual_rediscovery"
        elif unique_journals >= 4 and len(princes) >= 8:
            atype = "broad_trend"
        else:
            atype = "subfield_revival"

        out.append({
            "beauty": {
                "id":             b["id"],
                "title":          b.get("title"),
                "journal":        b.get("journal"),
                "authors":        b.get("authors"),
                "pub_year":       _year_of(b.get("pub_date")) if b.get("pub_date") else None,
                "awakening_year": awake_year,
                "beauty_coefficient": b.get("beauty_coefficient"),
                "sleep_years":    b.get("sleep_years"),
            },
            "princes":         princes,
            "n_princes":       len(princes),
            "n_journals":      unique_journals,
            "awakening_type":  atype,
        })

    return {
        "beauties":     out,
        "stats": {
            "n":           len(out),
            "by_awakening_type": dict(Counter(b["awakening_type"] for b in out)),
        },
    }


# Curated disciplinary events used by ds_disciplinary_calendar.
# Year + short label + type. Curated; the user can extend this list.
_DISCIPLINARY_EVENTS = [
    {"year": 1949, "type": "external_crisis",  "title": "Shannon's information theory published"},
    {"year": 1949, "type": "journal_founded", "title": "College Composition and Communication founded"},
    {"year": 1968, "type": "landmark_article","title": "Macrorie's 'Engfish' coinage"},
    {"year": 1971, "type": "landmark_article","title": "Britton et al. 'The Development of Writing Abilities'"},
    {"year": 1974, "type": "external_crisis", "title": "CCCC 'Students' Right to Their Own Language' resolution"},
    {"year": 1977, "type": "landmark_article","title": "Mina Shaughnessy, Errors and Expectations"},
    {"year": 1981, "type": "landmark_article","title": "Flower & Hayes cognitive process model"},
    {"year": 1984, "type": "landmark_article","title": "Bruffee, Collaborative Learning"},
    {"year": 1986, "type": "journal_founded", "title": "Journal of Business and Technical Communication founded"},
    {"year": 1992, "type": "journal_founded", "title": "Technical Communication Quarterly founded"},
    {"year": 1996, "type": "journal_founded", "title": "Kairos founded"},
    {"year": 1997, "type": "landmark_article","title": "Russell, Rethinking Genre in School and Society"},
    {"year": 2004, "type": "landmark_article","title": "Selfe & Hawisher, Literate Lives"},
    {"year": 2009, "type": "external_crisis", "title": "Open access and OA mandates expand"},
    {"year": 2013, "type": "landmark_article","title": "Agboka, Participatory Localization"},
    {"year": 2016, "type": "landmark_article","title": "Jones, Moore, Walton — 'Disrupting the Past'"},
    {"year": 2020, "type": "external_crisis", "title": "COVID-19 disrupts higher education"},
    {"year": 2022, "type": "external_crisis", "title": "Public ChatGPT release"},
]


@cached("ds_disciplinary_calendar")
def ds_disciplinary_calendar(cluster=None, journals=None,
                             year_from=None, year_to=None):
    """Curated event timeline overlaid against sleeping-beauty awakening spikes."""
    from .citations import get_sleeping_beauties
    js = _resolve_journal_filter(cluster=cluster, journals=journals)
    sb_resp = get_sleeping_beauties(
        min_total_citations=5, max_results=200,
        journals=js, year_from=year_from, year_to=year_to,
    ) or {}
    beauties = sb_resp.get("articles", [])

    # Awakening spike per year
    spikes = Counter()
    for b in beauties:
        ay = b.get("awakening_year")
        if ay is None:
            continue
        spikes[int(ay)] += 1

    # Per-event-type post-event awakening rate (5-year window)
    by_type = defaultdict(list)
    for ev in _DISCIPLINARY_EVENTS:
        for off in range(5):
            by_type[ev["type"]].append(spikes.get(ev["year"] + off, 0))
    type_means = {
        t: round(sum(vs) / max(1, len(vs)), 2) for t, vs in by_type.items()
    }

    # Convert spikes for output
    spike_series = sorted([{"year": y, "n": n} for y, n in spikes.items()], key=lambda r: r["year"])

    return {
        "events":       _DISCIPLINARY_EVENTS,
        "awakenings":   spike_series,
        "type_means":   type_means,
        "summary":      {
            "n_events":     len(_DISCIPLINARY_EVENTS),
            "n_beauties":   len(beauties),
            "year_range":   [spike_series[0]["year"], spike_series[-1]["year"]] if spike_series else None,
        },
    }


def ds_unread_canon(cluster=None, journals=None,
                    year_from=None, year_to=None):
    """Articles with high global citation count (OpenAlex / CrossRef) but low
    internal citation count — work the field has not metabolized."""
    fclause, fparams = _filter_articles_clause(
        cluster=cluster, journals=journals,
        year_from=year_from, year_to=year_to,
    )
    extra = (" AND " + fclause) if fclause else ""
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(f"""
            SELECT id, title, authors, journal, pub_date,
                   COALESCE(internal_cited_by_count, 0) AS internal,
                   COALESCE(crossref_cited_by_count, 0) AS global_count
              FROM articles
             WHERE COALESCE(crossref_cited_by_count, 0) >= 100
               AND COALESCE(internal_cited_by_count, 0) <= 2{extra}
        """, fparams)]

    rows.sort(key=lambda r: -r["global_count"])
    enriched = [{
        "id":           r["id"],
        "title":        r["title"],
        "authors":      r["authors"],
        "journal":      r["journal"],
        "year":         _year_of(r["pub_date"]),
        "internal":     r["internal"],
        "global_count": r["global_count"],
        "ratio":        r["global_count"] / max(1, r["internal"] + 1),
    } for r in rows[:200]]

    return {
        "articles": enriched,
        "summary": {
            "n":            len(enriched),
            "min_global":   100,
            "max_internal": 2,
        },
    }

