"""citations Blueprint — citation-network analytics API endpoints."""

import logging

from flask import Blueprint, request, jsonify

from db import (
    get_citation_network, get_cocitation_network, get_bibcoupling_network,
    get_citation_centrality, get_sleeping_beauties, get_journal_citation_flow,
    get_journal_half_life, get_community_detection, get_main_path,
    get_temporal_network_evolution, get_reading_path,
    get_citation_trends, get_ego_network,
)
from rate_limit import limiter, LIMITS
from web_helpers import _safe_int, _safe_float, cache_response

log = logging.getLogger(__name__)

bp = Blueprint("citations", __name__)


@bp.route("/api/citations/ego")
@limiter.limit(LIMITS["citations"])
def api_ego_network():
    """JSON: 2-degree ego network around a specific article."""
    article_id = request.args.get("article", type=int)
    if not article_id:
        return jsonify({"error": "article parameter required"}), 400
    return jsonify(get_ego_network(article_id))


@bp.route("/api/stats/citation-trends")
@limiter.limit(LIMITS["stats"])
@cache_response(seconds=3600)
def api_citation_trends():
    """JSON: avg internal citations per article per year, filtered by optional journal."""
    journal = request.args.get("journal", "").strip()
    return jsonify(get_citation_trends(journal=journal or None))


@bp.route("/api/citations/network")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_citations_network():
    """JSON: force-graph nodes and directed edges for the citation network."""
    min_citations = _safe_int(request.args.get("min_citations", 5), 5, lo=1)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_citation_network(
        min_citations=min_citations,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
    )
    return jsonify(data)


@bp.route("/api/citations/cocitation")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_cocitation_network():
    """JSON: co-citation network — undirected weighted graph of articles co-cited together."""
    min_cocitations = _safe_int(request.args.get("min_cocitations", 3), 3, lo=1)
    max_nodes       = _safe_int(request.args.get("max_nodes", 400), 400, lo=50, hi=600)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_cocitation_network(
        min_cocitations=min_cocitations,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
        max_nodes=max_nodes,
    )
    return jsonify(data)


@bp.route("/api/citations/bibcoupling")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_bibcoupling_network():
    """JSON: bibliographic coupling network — articles linked by shared references."""
    min_coupling = _safe_int(request.args.get("min_coupling", 3), 3, lo=1)
    max_nodes    = _safe_int(request.args.get("max_nodes", 400), 400, lo=50, hi=600)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_bibcoupling_network(
        min_coupling=min_coupling,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
        max_nodes=max_nodes,
    )
    return jsonify(data)


@bp.route("/api/citations/centrality")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_citation_centrality():
    """JSON: citation network with eigenvector and betweenness centrality scores."""
    min_citations = _safe_int(request.args.get("min_citations", 2), 2, lo=1)
    max_nodes     = _safe_int(request.args.get("max_nodes", 600), 600, lo=50, hi=800)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_citation_centrality(
        min_citations=min_citations,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
        max_nodes=max_nodes,
    )
    return jsonify(data)


@bp.route("/api/citations/sleeping-beauties")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_sleeping_beauties():
    """JSON: articles with delayed citation recognition (Sleeping Beauties)."""
    min_citations = _safe_int(request.args.get("min_citations", 5), 5, lo=3)
    max_results   = _safe_int(request.args.get("max_results", 50), 50, lo=10, hi=100)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_sleeping_beauties(
        min_total_citations=min_citations,
        max_results=max_results,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
    )
    return jsonify(data)


@bp.route("/api/citations/journal-flow")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_journal_citation_flow():
    """JSON: journal-to-journal citation flow matrix for chord diagram."""
    min_citations = _safe_int(request.args.get("min_citations", 1), 1, lo=1)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_journal_citation_flow(
        min_citations=min_citations,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
    )
    return jsonify(data)


@bp.route("/api/citations/half-life")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_citation_half_life():
    """JSON: citing and cited half-life per journal."""
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()
    include_distribution = request.args.get("distribution", "").lower() in ("1", "true")
    include_timeseries   = request.args.get("timeseries",   "").lower() in ("1", "true")

    data = get_journal_half_life(
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
        include_distribution=include_distribution,
        include_timeseries=include_timeseries,
    )
    return jsonify(data)


@bp.route("/api/citations/communities")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_citation_communities():
    """JSON: community detection via Louvain modularity optimization."""
    min_citations = _safe_int(request.args.get("min_citations", 2), 2, lo=1)
    max_nodes     = _safe_int(request.args.get("max_nodes", 600), 600, lo=50, hi=800)
    resolution    = _safe_float(request.args.get("resolution", 1.0), 1.0, lo=0.1, hi=3.0)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_community_detection(
        min_citations=min_citations,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
        max_nodes=max_nodes,
        resolution=resolution,
    )
    return jsonify(data)


@bp.route("/api/citations/main-path")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_main_path():
    """JSON: main path analysis — the backbone of knowledge flow."""
    min_citations = _safe_int(request.args.get("min_citations", 2), 2, lo=1)
    max_nodes     = _safe_int(request.args.get("max_nodes", 800), 800, lo=50, hi=1000)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()

    data = get_main_path(
        min_citations=min_citations,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
        max_nodes=max_nodes,
    )
    return jsonify(data)


@bp.route("/api/citations/temporal-evolution")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_temporal_evolution():
    """JSON: temporal network evolution — structural metrics over time windows."""
    min_citations = _safe_int(request.args.get("min_citations", 1), 1, lo=1)
    max_nodes     = _safe_int(request.args.get("max_nodes", 500), 500, lo=50, hi=800)
    window_size   = _safe_int(request.args.get("window_size", 1), 1, lo=1, hi=10)
    journals  = request.args.getlist("journal")
    year_from = request.args.get("year_from", "").strip()
    year_to   = request.args.get("year_to",   "").strip()
    snapshot_year = request.args.get("snapshot_year", "").strip()

    data = get_temporal_network_evolution(
        min_citations=min_citations,
        journals=journals or None,
        year_from=year_from or None,
        year_to=year_to or None,
        window_size=window_size,
        max_nodes_per_window=max_nodes,
        snapshot_year=int(snapshot_year) if snapshot_year else None,
    )
    return jsonify(data)


@bp.route("/api/citations/reading-path")
@limiter.limit(LIMITS["citations"])
@cache_response(seconds=600)
def api_reading_path():
    """JSON: reading path around a seed article — cites, cited-by, co-citation, bib coupling."""
    article_id = request.args.get("article", type=int)
    if not article_id:
        return jsonify({"error": "article parameter required"}), 400
    result = get_reading_path(article_id)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)

