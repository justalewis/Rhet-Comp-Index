// static/js/viz/centrality.js
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


let centSimulation   = null;
let centZoomBehavior = null;
let centSvgEl        = null;
let centNodeSel      = null;
let centLinkSel      = null;
let centNodes        = [];
let centLinks        = [];
let centDebounce     = null;
let centData         = null;   // cached API response for re-render on toggle change

// Heat colour scale: warm cream to dark brown
const centHeatScale = d3.scaleSequential(d3.interpolateYlOrBr).domain([0, 1]);

function onCentSliderChange(val) {
  document.getElementById('cent-min-val').textContent = val;
  clearTimeout(centDebounce);
  centDebounce = setTimeout(loadCentrality, 700);
}

function toggleAllCentJournals(cb) {
  document.querySelectorAll('.cent-journal-check').forEach(c => c.checked = cb.checked);
  updateCentJournalCount();
}

function updateCentJournalCount() {
  const total   = document.querySelectorAll('.cent-journal-check').length;
  const checked = document.querySelectorAll('.cent-journal-check:checked').length;
  document.getElementById('cent-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}\u202f/\u202f${total})`;
  const allCb = document.getElementById('cent-check-all');
  if (allCb) allCb.checked = (checked === total);
  if (allCb) allCb.indeterminate = (checked > 0 && checked < total);
}

let _exportWired_loadCentrality = false;

async function loadCentrality() {
  if (!_exportWired_loadCentrality) {
    renderExportToolbar('tab-centrality', { svgSelector: '#centrality-container svg', dataProvider: () => (window.__expCentrality && window.__expCentrality.nodes || []) });
    _exportWired_loadCentrality = true;
  }
  clearTimeout(centDebounce);

  const container = document.getElementById('cent-container');
  container.innerHTML = '<div class="loading-msg">Computing centrality scores — this may take a few seconds\u2026</div>';
  document.getElementById('cent-stats').textContent = '';
  document.getElementById('cent-legend').innerHTML  = '';
  document.getElementById('cent-tables').style.display = 'none';

  if (centSimulation) { centSimulation.stop(); centSimulation = null; }
  d3.selectAll('.cent-tip').remove();

  const minCit   = document.getElementById('cent-min-slider').value;
  let yearFrom = document.getElementById('cent-year-from').value;
  let yearTo   = document.getElementById('cent-year-to').value;
  // Auto-swap reversed year range
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo)) {
    [yearFrom, yearTo] = [yearTo, yearFrom];
  }
  const checked  = [...document.querySelectorAll('.cent-journal-check:checked')].map(c => c.value);
  const total    = document.querySelectorAll('.cent-journal-check').length;

  const params = new URLSearchParams({ min_citations: minCit });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  try {
    const resp = await fetch('/api/citations/centrality?' + params.toString());
    centData = await resp.json();
  window.__expCentrality = centData;
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load centrality data.</p>';
    return;
  }

  if (!centData.nodes || centData.nodes.length === 0) {
    container.innerHTML =
      '<p class="explore-hint">No articles match these filters \u2014 try a lower minimum citation count, ' +
      'or run <code>python cite_fetcher.py</code> to populate citation data.</p>';
    return;
  }

  renderCentrality(container, centData);
  renderCentralityTables(centData);
}

function renderCentrality(container, data) {
  container.innerHTML = '';
  centNodes = data.nodes.map(d => ({ ...d }));
  centLinks = data.links.map(d => ({ ...d }));

  const sizeMode  = document.getElementById('cent-size-mode').value;
  const colorMode = document.getElementById('cent-color-mode').value;

  // Stats bar
  const sizeLabel = sizeMode === 'eigenvector' ? 'eigenvector'
                  : sizeMode === 'betweenness' ? 'betweenness' : 'citations';
  document.getElementById('cent-stats').textContent =
    `${data.node_count} articles\u2002\u00b7\u2002${data.link_count} citation links\u2002\u00b7\u2002` +
    `sized by ${sizeLabel}`;

  // Legend
  const legendEl = document.getElementById('cent-legend');
  if (colorMode === 'journal') {
    const seenJournals = [...new Set(centNodes.map(n => n.journal))].sort();
    legendEl.innerHTML = seenJournals.map(j =>
      `<span class="citnet-legend-item">` +
      `<span class="citnet-legend-dot" style="background:${citnetJournalColor(j)}"></span>` +
      `${escapeHtml(j)}</span>`
    ).join('');
  } else {
    // Heat scale legend
    const label = colorMode === 'eigenvector' ? 'Eigenvector centrality' : 'Betweenness centrality';
    legendEl.innerHTML =
      `<span class="citnet-legend-item" style="display:flex;align-items:center;gap:0.3rem;">` +
      `<span style="font-size:0.78rem;color:#9c9890;">Low</span>` +
      `<span style="display:inline-block;width:120px;height:12px;border-radius:3px;` +
      `background:linear-gradient(to right,${centHeatScale(0)},${centHeatScale(0.25)},${centHeatScale(0.5)},${centHeatScale(0.75)},${centHeatScale(1)});"></span>` +
      `<span style="font-size:0.78rem;color:#9c9890;">High</span>` +
      `<span style="font-size:0.78rem;color:#9c9890;margin-left:0.5rem;">${label}</span>` +
      `</span>`;
  }

  const W = container.clientWidth  || 820;
  const H = 600;

  // Radius scale
  const maxCit = d3.max(centNodes, n => n.internal_cited_by_count) || 1;
  const citRScale = d3.scaleSqrt().domain([0, maxCit]).range([3, 18]);
  function nodeR(d) {
    if (sizeMode === 'eigenvector') return Math.sqrt(d.eigenvector_centrality) * 22 + 3;
    if (sizeMode === 'betweenness') return Math.sqrt(d.betweenness_centrality) * 22 + 3;
    return citRScale(d.internal_cited_by_count);
  }

  // Colour function
  function nodeColor(d) {
    if (colorMode === 'eigenvector') return centHeatScale(d.eigenvector_centrality);
    if (colorMode === 'betweenness') return centHeatScale(d.betweenness_centrality);
    return citnetJournalColor(d.journal);
  }

  // SVG + zoom
  centSvgEl = d3.select('#cent-container')
    .append('svg')
    .attr('width', W)
    .attr('height', H);

  const gRoot = enableZoomPan(centSvgEl, { scaleExtent: [0.1, 8] });
  centZoomBehavior = centSvgEl.node().__dsZoomBehavior;

  // Links
  centLinkSel = gRoot.append('g').attr('class', 'cent-links')
    .selectAll('line')
    .data(centLinks)
    .enter().append('line')
    .style('stroke',         '#ccc7bb')
    .style('stroke-opacity', 0.25)
    .style('stroke-width',   0.6);

  // Tooltip
  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip cent-tip')
    .style('display',    'none')
    .style('max-width',  '300px')
    .style('line-height','1.45');

  // Node groups
  const nodeGroup = gRoot.append('g').attr('class', 'cent-nodes')
    .selectAll('g')
    .data(centNodes)
    .enter().append('g')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => {
        if (!event.active) centSimulation.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end',   (event, d) => {
        if (!event.active) centSimulation.alphaTarget(0);
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
        `Cited <strong>${d.internal_cited_by_count}</strong>\u00d7 in index<br>` +
        `<span style="color:#b38a6a;">Eigenvector: ${(d.eigenvector_centrality * 100).toFixed(1)}%</span><br>` +
        `<span style="color:#6b8f60;">Betweenness: ${(d.betweenness_centrality * 100).toFixed(1)}%</span>`
      );
    })
    .on('mousemove', (event) => {
      positionTooltip(tip, event, { pad: 14, vPad: -38 });
    })
    .on('mouseout', () => tip.style('display', 'none'));

  centNodeSel = nodeGroup;

  // Circles. Tag each <g> with the article id so the per-row hover handler
  // (wired in renderCentralityTables) can find and pop the matching node.
  nodeGroup.attr('data-id', d => d.id);
  nodeGroup.append('circle')
    .attr('r', d => nodeR(d))
    .style('fill',         d => nodeColor(d))
    .style('fill-opacity', 0.88)
    .style('stroke',       '#fff')
    .style('stroke-width', 1.5);

  // Linked-views: when the cursor enters a node group, highlight the
  // matching <li data-id="..."> rows in the two tables below.
  nodeGroup
    .on('mouseenter.link', function(_, d) {
      document.querySelectorAll('#cent-tables li[data-id="' + d.id + '"]').forEach(li => {
        li.style.background = '#fdfbf7';
        li.style.outline = '2px solid #b38a6a';
      });
    })
    .on('mouseleave.link', function(_, d) {
      document.querySelectorAll('#cent-tables li[data-id="' + d.id + '"]').forEach(li => {
        li.style.background = '';
        li.style.outline = '';
      });
    });

  // Labels for top 20 nodes by chosen metric
  const sortedBySize = [...centNodes].sort((a, b) => {
    if (sizeMode === 'eigenvector') return b.eigenvector_centrality - a.eigenvector_centrality;
    if (sizeMode === 'betweenness') return b.betweenness_centrality - a.betweenness_centrality;
    return b.internal_cited_by_count - a.internal_cited_by_count;
  });
  const labelIds = new Set(sortedBySize.slice(0, 20).map(n => n.id));

  nodeGroup.filter(d => labelIds.has(d.id))
    .append('text')
    .text(d => {
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      return first.split(' ').filter(Boolean).pop() || '';
    })
    .attr('dy',           d => nodeR(d) + 10)
    .attr('text-anchor',  'middle')
    .style('font-family', 'system-ui, sans-serif')
    .style('font-size',   '9px')
    .style('fill',        '#5a3e28')
    .style('pointer-events', 'none');

  // Dismiss tooltip on background click
  centSvgEl.on('click', () => tip.style('display', 'none'));

  // Force simulation
  centSimulation = d3.forceSimulation(centNodes)
    .force('link', d3.forceLink(centLinks)
      .id(d => d.id)
      .distance(50)
      .strength(0.35))
    .force('charge',    d3.forceManyBody().strength(-90))
    .force('center',    d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => nodeR(d) + 2))
    .alphaDecay(0.025)
    .on('tick', () => {
      centLinkSel
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeGroup.attr('transform', d => `translate(${d.x},${d.y})`);
    });
}

function updateCentralityViz() {
  // Re-render the graph with the same data but different size/color modes
  if (!centData || !centData.nodes || centData.nodes.length === 0) return;
  if (centSimulation) { centSimulation.stop(); centSimulation = null; }
  d3.selectAll('.cent-tip').remove();
  const container = document.getElementById('cent-container');
  renderCentrality(container, centData);
}

function renderCentralityTables(data) {
  const tablesEl = document.getElementById('cent-tables');
  tablesEl.style.display = 'block';

  function renderTable(containerId, items) {
    const ol = document.getElementById(containerId);
    ol.innerHTML = items.map((d, i) => {
      const year  = d.pub_date ? d.pub_date.slice(0, 4) : '';
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      const last  = first.split(' ').filter(Boolean).pop() || '';
      const byline = last + (auth.includes(';') ? ' et al.' : '');
      const metric = containerId.includes('eigen')
        ? (d.eigenvector_centrality * 100).toFixed(1) + '%'
        : (d.betweenness_centrality * 100).toFixed(1) + '%';

      return `<li class="article" data-id="${d.id}" style="padding:0.4rem 0;border-bottom:1px solid var(--border-color,#e8e4de);transition:background 0.1s;">
        <div style="display:flex;justify-content:space-between;align-items:baseline;gap:0.5rem;">
          <div style="min-width:0;">
            <a href="/article/${d.id}" class="article-title" style="font-size:0.84rem;">${escapeHtml(d.title)}</a>
            <div style="font-size:0.76rem;color:#9c9890;margin-top:0.1rem;">
              ${escapeHtml(byline)}${year ? ' (' + year + ')' : ''} \u2014 <em>${escapeHtml(d.journal)}</em>
              \u2002\u00b7\u2002Cited ${d.internal_cited_by_count}\u00d7
            </div>
          </div>
          <span style="flex-shrink:0;font-weight:600;font-size:0.84rem;color:#5a3e28;font-variant-numeric:tabular-nums;">${metric}</span>
        </div>
      </li>`;
    }).join('');

    // Linked-views: hovering a table row pops the matching node in the SVG.
    ol.querySelectorAll('li[data-id]').forEach(li => {
      const id = li.getAttribute('data-id');
      li.addEventListener('mouseenter', () => {
        const g = document.querySelector('#cent-container g[data-id="' + id + '"]');
        if (!g) return;
        const c = g.querySelector('circle');
        if (c) {
          c.dataset._origR = c.getAttribute('r');
          c.dataset._origStrokeW = c.style.strokeWidth;
          c.setAttribute('r', parseFloat(c.dataset._origR) * 1.6);
          c.style.stroke = '#b38a6a';
          c.style.strokeWidth = 3;
          g.parentNode.appendChild(g);  // raise to top
        }
      });
      li.addEventListener('mouseleave', () => {
        const g = document.querySelector('#cent-container g[data-id="' + id + '"]');
        if (!g) return;
        const c = g.querySelector('circle');
        if (c && c.dataset._origR) {
          c.setAttribute('r', c.dataset._origR);
          c.style.stroke = '#fff';
          c.style.strokeWidth = c.dataset._origStrokeW || '1.5';
        }
      });
    });
  }

  renderTable('cent-table-eigen', data.top_eigenvector);
  renderTable('cent-table-between', data.top_betweenness);
}

// ── Inline-handler globals ────────────────────────────────────
window.onCentSliderChange = onCentSliderChange;
window.toggleAllCentJournals = toggleAllCentJournals;
window.updateCentJournalCount = updateCentJournalCount;
window.loadCentrality = loadCentrality;
window.updateCentralityViz = updateCentralityViz;
