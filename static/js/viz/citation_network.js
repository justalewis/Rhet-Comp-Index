// static/js/viz/citation_network.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { renderExportToolbar } from "../shared/export.js";
import { enableZoomPan } from "../shared/common.js";
import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


let citnetSimulation   = null;
let citnetZoomBehavior = null;
let citnetSvgEl        = null;
let citnetNodeSel      = null;   // d3 selection of <g> node groups
let citnetLinkSel      = null;   // d3 selection of <line> link elements
let citnetNodes        = [];
let citnetLinks        = [];
let citnetDebounce     = null;

function onCitnetSliderChange(val) {
  document.getElementById('citnet-min-val').textContent = val;
  clearTimeout(citnetDebounce);
  citnetDebounce = setTimeout(loadCitationNetwork, 700);
}

function toggleAllCitnetJournals(cb) {
  document.querySelectorAll('.citnet-journal-check').forEach(c => c.checked = cb.checked);
  updateCitnetJournalCount();
}

function updateCitnetJournalCount() {
  const total   = document.querySelectorAll('.citnet-journal-check').length;
  const checked = document.querySelectorAll('.citnet-journal-check:checked').length;
  document.getElementById('citnet-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}\u202f/\u202f${total})`;
  const allCb = document.getElementById('citnet-check-all');
  if (allCb) allCb.checked = (checked === total);
  if (allCb) allCb.indeterminate = (checked > 0 && checked < total);
}

let _exportWired_loadCitationNetwork = false;

async function loadCitationNetwork() {
  if (!_exportWired_loadCitationNetwork) {
    renderExportToolbar('tab-citnet', { svgSelector: '#citnet-container svg', dataProvider: () => (window.__expCitnet && window.__expCitnet.nodes || []) });
    _exportWired_loadCitationNetwork = true;
  }
  clearTimeout(citnetDebounce);

  const container = document.getElementById('citnet-container');
  container.innerHTML = '<div class="loading-msg">Loading…</div>';
  document.getElementById('citnet-stats').textContent = '';
  document.getElementById('citnet-legend').innerHTML  = '';
  // Clear previous search highlights
  document.getElementById('citnet-search').value = '';
  document.getElementById('citnet-search-count').textContent = '';

  if (citnetSimulation) { citnetSimulation.stop(); citnetSimulation = null; }
  d3.selectAll('.citnet-tip').remove();

  const minCit   = document.getElementById('citnet-min-slider').value;
  const yearFrom = document.getElementById('citnet-year-from').value;
  const yearTo   = document.getElementById('citnet-year-to').value;
  const checked  = [...document.querySelectorAll('.citnet-journal-check:checked')].map(c => c.value);
  const total    = document.querySelectorAll('.citnet-journal-check').length;

  const params = new URLSearchParams({ min_citations: minCit });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/citations/network?' + params.toString());
    data = await resp.json();
    window.__expCitnet = data;
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load network data.</p>';
    return;
  }

  if (!data.nodes || data.nodes.length === 0) {
    container.innerHTML =
      '<p class="explore-hint">No articles match these filters — try a lower minimum citation count, ' +
      'or run <code>python cite_fetcher.py</code> to populate citation data.</p>';
    return;
  }

  document.getElementById('citnet-stats').textContent =
    `${data.node_count} articles\u2002·\u2002${data.link_count} citation links`;

  // Legend: unique journals present in this result set. Clickable —
  // a click on the swatch ticks/unticks the matching journal filter
  // checkbox and triggers a reload, so the legend doubles as a fast
  // "show only this journal" / "remove this journal" affordance.
  const seenJournals = [...new Set(data.nodes.map(n => n.journal))].sort();
  document.getElementById('citnet-legend').innerHTML = seenJournals.map(j =>
    `<span class="citnet-legend-item" data-journal="${escapeHtml(j)}" ` +
       `style="cursor:pointer;" title="Click to toggle this journal in the filter">` +
    `<span class="citnet-legend-dot" style="background:${citnetJournalColor(j)}"></span>` +
    `${escapeHtml(j)}</span>`
  ).join('');
  document.querySelectorAll('#citnet-legend .citnet-legend-item').forEach(el => {
    el.addEventListener('click', () => {
      const name = el.getAttribute('data-journal');
      const cb = [...document.querySelectorAll('.citnet-journal-check')]
        .find(c => c.value === name);
      if (cb) {
        cb.checked = !cb.checked;
        updateCitnetJournalCount();
        loadCitationNetwork();
      }
    });
  });

  renderCitationNetwork(container, data);
}

function renderCitationNetwork(container, data) {
  container.innerHTML = '';
  citnetNodes = data.nodes.map(d => ({ ...d }));
  citnetLinks = data.links.map(d => ({ ...d }));

  const W = container.clientWidth  || 820;
  const H = 580;

  const maxCit = d3.max(citnetNodes, d => d.internal_cited_by_count) || 1;
  const rScale = d3.scaleSqrt().domain([0, maxCit]).range([3, 18]);

  // SVG canvas + zoom
  citnetSvgEl = d3.select('#citnet-container')
    .append('svg')
    .attr('width',  W)
    .attr('height', H);

  const gRoot = enableZoomPan(citnetSvgEl, { scaleExtent: [0.1, 8] });
  citnetZoomBehavior = citnetSvgEl.node().__dsZoomBehavior;

  // Links
  citnetLinkSel = gRoot.append('g').attr('class', 'citnet-links')
    .selectAll('line')
    .data(citnetLinks)
    .enter().append('line')
    .style('stroke',         '#ccc7bb')
    .style('stroke-opacity', 0.35)
    .style('stroke-width',   0.7);

  // Tooltip (one per render; removed at top of loadCitationNetwork on re-render)
  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip citnet-tip')
    .style('display',    'none')
    .style('max-width',  '260px')
    .style('line-height','1.45');

  // Node groups
  const nodeGroup = gRoot.append('g').attr('class', 'citnet-nodes')
    .selectAll('g')
    .data(citnetNodes)
    .enter().append('g')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => {
        if (!event.active) citnetSimulation.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end',   (event, d) => {
        if (!event.active) citnetSimulation.alphaTarget(0);
        d.fx = null; d.fy = null;
      })
    )
    .on('click', (event, d) => {
      event.stopPropagation();
      window.location = '/article/' + d.id;
    })
    .on('mouseover', (event, d) => {
      const year  = d.pub_date ? d.pub_date.slice(0, 4) : '';
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      const last  = first.split(' ').filter(Boolean).pop() || '';
      const byline = last
        + (auth.includes(';') ? ' et al.' : '')
        + (year ? ` (${year})` : '');
      tip.style('display', 'block').html(
        `<strong>${escapeHtml(d.title)}</strong><br>` +
        (byline ? `${escapeHtml(byline)}<br>` : '') +
        `<em>${escapeHtml(d.journal)}</em><br>` +
        `Cited <strong>${d.internal_cited_by_count}</strong>\u00d7 in this index`
      );
    })
    .on('mousemove', (event) => {
      positionTooltip(tip, event, { pad: 14, vPad: -38 });
    })
    .on('mouseout', () => tip.style('display', 'none'));

  citnetNodeSel = nodeGroup;

  // Circles
  nodeGroup.append('circle')
    .attr('r', d => rScale(d.internal_cited_by_count))
    .style('fill',         d => citnetJournalColor(d.journal))
    .style('fill-opacity', 0.85)
    .style('stroke',       '#fff')
    .style('stroke-width', 1.5);

  // Labels for top-cited nodes (top 25% of range or threshold of 3)
  const labelThreshold = Math.max(3, Math.ceil(maxCit * 0.25));
  nodeGroup.filter(d => d.internal_cited_by_count >= labelThreshold)
    .append('text')
    .text(d => {
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      return first.split(' ').filter(Boolean).pop() || '';
    })
    .attr('dy',           d => rScale(d.internal_cited_by_count) + 10)
    .attr('text-anchor',  'middle')
    .style('font-family', 'system-ui, sans-serif')
    .style('font-size',   '9px')
    .style('fill',        '#5a3e28')
    .style('pointer-events', 'none');

  // Dismiss tooltip on background click
  citnetSvgEl.on('click', () => tip.style('display', 'none'));

  // Force simulation
  citnetSimulation = d3.forceSimulation(citnetNodes)
    .force('link', d3.forceLink(citnetLinks)
      .id(d => d.id)
      .distance(50)
      .strength(0.35))
    .force('charge',    d3.forceManyBody().strength(-80))
    .force('center',    d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => rScale(d.internal_cited_by_count) + 2))
    .alphaDecay(0.025)
    .on('tick', () => {
      citnetLinkSel
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeGroup.attr('transform', d => `translate(${d.x},${d.y})`);
    });
}

function searchCitationNetwork(query) {
  if (!citnetNodeSel || citnetNodes.length === 0) return;

  const countEl = document.getElementById('citnet-search-count');
  const q = query.trim().toLowerCase();

  if (!q) {
    citnetNodeSel.select('circle')
      .style('opacity',      1)
      .style('stroke',       '#fff')
      .style('stroke-width', 1.5);
    if (citnetLinkSel) citnetLinkSel.style('stroke-opacity', 0.35);
    countEl.textContent = '';
    return;
  }

  const matchIds = new Set(
    citnetNodes
      .filter(d =>
        (d.title   && d.title.toLowerCase().includes(q)) ||
        (d.authors && d.authors.toLowerCase().includes(q))
      )
      .map(d => d.id)
  );

  citnetNodeSel.select('circle')
    .style('opacity',      d => matchIds.has(d.id) ? 1 : 0.07)
    .style('stroke',       d => matchIds.has(d.id) ? '#e08020' : '#fff')
    .style('stroke-width', d => matchIds.has(d.id) ? 2.5 : 1.5);

  if (citnetLinkSel) {
    citnetLinkSel.style('stroke-opacity', d => {
      const s = typeof d.source === 'object' ? d.source.id : d.source;
      const t = typeof d.target === 'object' ? d.target.id : d.target;
      return (matchIds.has(s) || matchIds.has(t)) ? 0.55 : 0.04;
    });
  }

  const count = matchIds.size;
  countEl.textContent = count ? `${count} match${count !== 1 ? 'es' : ''}` : 'No matches';

  // Pan + zoom to first matching node
  if (count >= 1) {
    const first = citnetNodes.find(d => matchIds.has(d.id));
    if (first && first.x != null && citnetSvgEl && citnetZoomBehavior) {
      const W     = document.getElementById('citnet-container').clientWidth || 820;
      const H     = 580;
      const scale = 2;
      citnetSvgEl.transition().duration(500).call(
        citnetZoomBehavior.transform,
        d3.zoomIdentity
          .translate(W / 2 - first.x * scale, H / 2 - first.y * scale)
          .scale(scale)
      );
    }
  }
}

// ── Inline-handler globals ────────────────────────────────────
window.onCitnetSliderChange = onCitnetSliderChange;
window.toggleAllCitnetJournals = toggleAllCitnetJournals;
window.updateCitnetJournalCount = updateCitnetJournalCount;
window.loadCitationNetwork = loadCitationNetwork;
window.searchCitationNetwork = searchCitationNetwork;
