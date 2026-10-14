// static/js/shared/common.js
//
// Shared helpers for the Datastories (and Explore) viz modules. Imports from
// ../utils/ where possible; defines a few Datastories-specific
// utilities (group colors, status messages, simple table builder).

import { escapeHtml as _escapeHtml } from "../utils/tooltips.js";
import { chrome } from "../utils/theme.js";

export const escapeHtml = _escapeHtml;

// Field-group colors, mirrored from journal_groups.GROUP_COLORS
export const GROUP_COLORS = {
  TPC:       "#5a3e28",
  RHET_COMP: "#3a5a28",
  OTHER:     chrome().muted,
};

export const GROUP_LABELS = {
  TPC:       "TPC",
  RHET_COMP: "RC",
  OTHER:     "OTHER",
};

// Show a transient loading message in `containerId`.
export function setLoading(containerId, message) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = '<div class="loading-msg" style="padding:1rem;color:var(--chart-muted);">' + message + '</div>';
}

// Show an error message in `containerId`.
export function setError(containerId, message) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = '<p class="explore-hint" style="color:#a04545;">' + escapeHtml(message) + '</p>';
}

// Empty the container (for re-renders).
export function clear(containerId) {
  const el = document.getElementById(containerId);
  if (el) el.innerHTML = '';
}

// Async fetch JSON with reasonable error handling. Sends `cache: 'no-cache'`
// so a server-side cache invalidation (e.g. recomputed Ch6 communities)
// reaches the page without manually clearing the browser cache.
export async function fetchJson(url) {
  const resp = await fetch(url, { cache: 'no-cache' });
  if (!resp.ok) {
    let detail = '';
    try { detail = (await resp.text()).slice(0, 200); } catch (e) {}
    throw new Error('HTTP ' + resp.status + ' ' + resp.statusText + (detail ? ': ' + detail : ''));
  }
  return resp.json();
}

// Build a sortable HTML table from rows + columns.
//   rows:    array of objects
//   columns: [{key, label, fmt: optional value -> string, align: optional 'right'}]
//   options.maxRows: cap rendering at this many rows; rest hidden behind "show more"
export function renderTable(containerId, rows, columns, options) {
  const opts = options || {};
  const max = opts.maxRows || 50;
  const el = document.getElementById(containerId);
  if (!el) return;

  if (!rows || rows.length === 0) {
    el.innerHTML = '<p class="explore-hint">No rows.</p>';
    return;
  }

  let html = '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
  html += '<thead><tr>';
  columns.forEach(col => {
    const align = col.align ? ' style="text-align:' + col.align + ';"' : '';
    html += '<th' + align + ' style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(col.label) + '</th>';
  });
  html += '</tr></thead><tbody>';

  rows.slice(0, max).forEach(row => {
    html += '<tr>';
    columns.forEach(col => {
      const raw = row[col.key];
      const v = col.fmt ? col.fmt(raw, row) : (raw == null ? '' : String(raw));
      const align = col.align ? ' style="text-align:' + col.align + ';padding:0.35rem 0.5rem;border-bottom:1px solid var(--chart-grid);"' : ' style="padding:0.35rem 0.5rem;border-bottom:1px solid var(--chart-grid);"';
      html += '<td' + align + '>' + v + '</td>';
    });
    html += '</tr>';
  });
  html += '</tbody></table>';
  if (rows.length > max) {
    html += '<p class="explore-hint" style="margin-top:0.5rem;">Showing ' + max + ' of ' + rows.length + ' rows.</p>';
  }
  el.innerHTML = html;
}

// Convenience: link to /article/<id> with title text.
export function articleLink(id, title) {
  if (!id) return escapeHtml(title || '');
  return '<a href="/article/' + id + '" style="color:#5a3e28;">' + escapeHtml(title || ('Article #' + id)) + '</a>';
}

// Stub for tools that haven't been wired yet — show a friendly placeholder.
export function showStub(containerId, toolName) {
  const el = document.getElementById(containerId);
  if (!el) return;
  el.innerHTML = '<p class="explore-hint" style="padding:1rem;color:var(--chart-muted);">'
    + escapeHtml(toolName) + ' is wired up but the viz is not yet implemented.</p>';
}


// ── Shared interaction helpers for D3 viz modules ───────────────────────────
//
// Every force-directed network in the Datastories section should call
// enableZoomPan(svg) on its top-level <svg> selection and pass the returned
// inner <g> to the rest of its rendering. enableDrag(simulation) returns a
// drag behavior ready to .call() on the node selection.
//
// Scatters / dense charts that benefit from zoom-only (no drag, no force
// simulation) can use enableZoomPan with the inner-g pattern as well.


/**
 * Wrap an existing <svg> selection in a zoomable/pannable inner group.
 *
 * Usage:
 *   const svg = d3.select(el).append('svg').attr('width', w).attr('height', h);
 *   const root = enableZoomPan(svg);
 *   // Append all viz content to `root` (not `svg`).
 *
 * @param {d3.Selection} svg - the d3 SVG selection
 * @param {object} [opts]
 * @param {[number, number]} [opts.scaleExtent=[0.2, 8]] - min/max zoom
 * @param {boolean} [opts.addResetButton=true] - draw a small "Reset view" button in the corner
 * @param {boolean} [opts.fitWidth=false] - keep the <svg> as wide as its container (see fitWidthToContainer)
 * @returns {d3.Selection} the inner <g> selection
 */
export function enableZoomPan(svg, opts) {
  opts = opts || {};
  const scaleExtent = opts.scaleExtent || [0.2, 8];
  const wantReset = opts.addResetButton !== false;
  let chip = null;

  const gRoot = svg.append('g').attr('class', 'ds-zoom-root');
  const zoomBehavior = d3.zoom()
    .scaleExtent(scaleExtent)
    .on('start', () => {
      // Hide any open d3-tooltip during the zoom/pan gesture so it doesn't
      // hover over the moving content. Native browser tooltips (those backed
      // by <title>) are unaffected. Tooltips reappear naturally on the next
      // mouseover, so we don't need a matching restore on 'end'.
      document.querySelectorAll('.heatmap-tooltip, [class$="-tip"]').forEach(el => {
        el.style.display = 'none';
      });
    })
    .on('zoom', (event) => { gRoot.attr('transform', event.transform); });
  svg.call(zoomBehavior);
  // Double-click resets the zoom rather than zooming in (the d3 default).
  svg.on('dblclick.zoom', null);
  svg.on('dblclick', () => svg.transition().duration(220).call(zoomBehavior.transform, d3.zoomIdentity));

  if (wantReset) {
    const w = +svg.attr('width') || svg.node().clientWidth || 0;
    // Floating reset chip in the top-right of the SVG. Drawn outside the
    // zoom-root so it stays fixed during zoom/pan.
    chip = svg.append('g').attr('class', 'ds-zoom-reset')
      .attr('transform', 'translate(' + (w - 78) + ', 8)')
      .style('cursor', 'pointer')
      .on('click', (event) => {
        event.stopPropagation();
        svg.transition().duration(220).call(zoomBehavior.transform, d3.zoomIdentity);
      });
    chip.append('rect')
      .attr('width', 70).attr('height', 22).attr('rx', 3)
      .attr('fill', chrome().halo).attr('stroke', chrome().grid).attr('stroke-width', 0.7);
    chip.append('text')
      .attr('x', 35).attr('y', 15).attr('text-anchor', 'middle')
      .attr('font-size', 11).attr('fill', '#5a3e28').text('Reset view');
  }

  if (opts.fitWidth) fitWidthToContainer(svg, chip);

  // Stash zoom behavior on the SVG node so callers can manipulate it later.
  svg.node().__dsZoomBehavior = zoomBehavior;

  return gRoot;
}


// A force layout is drawn once, at the width its container had then. When
// the container later widens or narrows (a window resize, the figure toggle
// redrawing while the pane was narrow), resize the <svg> to match and let a
// viewBox keep the original W x H scene centred at 1:1: a wider container
// shows more of the layout around it, a narrower one crops both sides evenly.
// Nothing is redrawn or refetched. A hidden container (width 0) is skipped
// until it is shown again, and the observer lets go once the <svg> is replaced.
function fitWidthToContainer(svg, chip) {
  const node = svg.node();
  const container = node.parentNode;
  const W = +svg.attr('width'), H = +svg.attr('height');
  if (!container || !W || !H || typeof ResizeObserver === 'undefined') return;
  // Without this the fixed width holds the page open on a narrower screen
  // (it counts toward the layout column's minimum width), so the container
  // never shrinks and the observer never sees a change.
  svg.style('max-width', '100%');
  const ro = new ResizeObserver(() => {
    if (!node.isConnected) { ro.disconnect(); return; }
    const w = container.clientWidth;
    if (!w) return;
    const x0 = (W - w) / 2;
    svg.attr('width', w).attr('viewBox', `${x0} 0 ${w} ${H}`);
    if (chip) chip.attr('transform', `translate(${x0 + w - 78}, 8)`);
  });
  ro.observe(container);
}


/**
 * Arrowhead marker for a directed network (citing -> cited). Call once per
 * <svg>, then give each line .attr('marker-end', 'url(#' + id + ')') and end
 * it at the target's rim (lineEndAtRim) so the head isn't hidden under the
 * node.
 *
 * @param {d3.Selection} svg - the d3 SVG selection
 * @param {string} id - marker id, unique on the page
 */
export function addArrowMarker(svg, id) {
  svg.append('defs').append('marker')
    .attr('id', id)
    .attr('viewBox', '0 -5 10 10')
    .attr('refX', 10).attr('refY', 0)
    .attr('markerUnits', 'userSpaceOnUse')
    .attr('markerWidth', 6).attr('markerHeight', 6)
    .attr('orient', 'auto')
    .append('path')
    .attr('d', 'M0,-5L10,0L0,5')
    .attr('fill', chrome().muted)
    .attr('fill-opacity', 0.6);
}

/** [x, y] where a source->target link meets a target node of radius r. */
export function lineEndAtRim(d, r) {
  const dx = d.target.x - d.source.x;
  const dy = d.target.y - d.source.y;
  const len = Math.hypot(dx, dy) || 1;
  return [d.target.x - dx / len * r, d.target.y - dy / len * r];
}


/**
 * Standard drag behavior for force-graph nodes. Pin (fx/fy) on drag, release
 * on drag-end so the simulation can re-settle. Pass the d3.forceSimulation
 * the nodes belong to so we can wake it on drag start.
 *
 * Usage:
 *   nodeSelection.call(enableDrag(simulation));
 *
 * @param {d3.Simulation} simulation
 * @returns {d3.DragBehavior}
 */
export function enableDrag(simulation) {
  // clickDistance(5): if the pointer moves > 5 pixels between mousedown and
  // mouseup, suppress the subsequent click event entirely. This is d3-drag's
  // built-in click-vs-drag separator and stops the ubiquitous "I dragged a
  // node and the page navigated to it" misfire on every force-graph in the
  // Explore section.
  return d3.drag()
    .clickDistance(5)
    .on('start', (event, d) => {
      if (!event.active) simulation.alphaTarget(0.3).restart();
      d.fx = d.x; d.fy = d.y;
    })
    .on('drag', (event, d) => {
      d.fx = event.x; d.fy = event.y;
    })
    .on('end', (event, d) => {
      if (!event.active) simulation.alphaTarget(0);
      d.fx = null; d.fy = null;
    });
}
