// static/js/viz/bibcoupling.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { renderExportToolbar } from "../shared/export.js";
import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


let bibcoupSimulation   = null;
let bibcoupZoomBehavior = null;
let bibcoupSvgEl        = null;
let bibcoupNodeSel      = null;
let bibcoupLinkSel      = null;
let bibcoupNodes        = [];
let bibcoupLinks        = [];
let bibcoupDebounce     = null;

function onBibcoupSliderChange(val) {
  document.getElementById('bibcoup-min-val').textContent = val;
  clearTimeout(bibcoupDebounce);
  bibcoupDebounce = setTimeout(loadBibcoupling, 700);
}

function toggleAllBibcoupJournals(cb) {
  document.querySelectorAll('.bibcoup-journal-check').forEach(c => c.checked = cb.checked);
  updateBibcoupJournalCount();
}

function updateBibcoupJournalCount() {
  const total   = document.querySelectorAll('.bibcoup-journal-check').length;
  const checked = document.querySelectorAll('.bibcoup-journal-check:checked').length;
  document.getElementById('bibcoup-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}\u202f/\u202f${total})`;
  const allCb = document.getElementById('bibcoup-check-all');
  if (allCb) allCb.checked = (checked === total);
  if (allCb) allCb.indeterminate = (checked > 0 && checked < total);
}

let _exportWired_loadBibcoupling = false;

async function loadBibcoupling() {
  if (!_exportWired_loadBibcoupling) {
    renderExportToolbar('tab-bibcoupling', { svgSelector: '#bibcoupling-container svg', dataProvider: () => (window.__expBibcoupling && window.__expBibcoupling.nodes || []) });
    _exportWired_loadBibcoupling = true;
  }
  clearTimeout(bibcoupDebounce);

  const container = document.getElementById('bibcoup-container');
  container.innerHTML = '<div class="loading-msg">Computing bibliographic coupling\u2026</div>';
  document.getElementById('bibcoup-stats').textContent = '';
  document.getElementById('bibcoup-legend').innerHTML  = '';

  if (bibcoupSimulation) { bibcoupSimulation.stop(); bibcoupSimulation = null; }
  d3.selectAll('.bibcoup-tip').remove();

  const minCoup  = document.getElementById('bibcoup-min-slider').value;
  let yearFrom = document.getElementById('bibcoup-year-from').value;
  let yearTo   = document.getElementById('bibcoup-year-to').value;
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo)) {
    [yearFrom, yearTo] = [yearTo, yearFrom];
    document.getElementById('bibcoup-year-from').value = yearFrom;
    document.getElementById('bibcoup-year-to').value   = yearTo;
  }
  const checked  = [...document.querySelectorAll('.bibcoup-journal-check:checked')].map(c => c.value);
  const total    = document.querySelectorAll('.bibcoup-journal-check').length;

  const params = new URLSearchParams({ min_coupling: minCoup });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/citations/bibcoupling?' + params.toString());
    data = await resp.json();
    window.__expBibcoupling = data;
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load bibliographic coupling data.</p>';
    return;
  }

  if (!data.nodes || data.nodes.length === 0) {
    const minVal = parseInt(document.getElementById('bibcoup-min-slider').value);
    let hint = 'No bibliographic coupling pairs meet the current threshold.';
    if (minVal > 1) {
      hint += ` Try lowering the minimum shared references \u2014 articles need reference lists deposited with CrossRef to appear here.`;
    }
    container.innerHTML = '<p class="explore-hint">' + hint + '</p>';
    return;
  }

  renderBibcoupling(container, data);
}

function renderBibcoupling(container, data) {
  container.innerHTML = '';
  bibcoupNodes = data.nodes.map(d => ({ ...d }));
  bibcoupLinks = data.links.map(d => ({ ...d }));

  const maxWeight = d3.max(bibcoupLinks, l => l.weight) || 1;
  document.getElementById('bibcoup-stats').textContent =
    `${data.node_count} articles\u2002\u00b7\u2002${data.link_count} coupling pairs` +
    `\u2002\u00b7\u2002strongest pair shares ${maxWeight} references`;

  // Legend — colour by journal
  const seenJournals = [...new Set(bibcoupNodes.map(n => n.journal))].sort();
  const legendEl = document.getElementById('bibcoup-legend');
  legendEl.innerHTML = seenJournals.map(j =>
    `<span class="citnet-legend-item">` +
    `<span class="citnet-legend-dot" style="background:${citnetJournalColor(j)}"></span>` +
    `${escapeHtml(j)}</span>`
  ).join('');

  const W = container.clientWidth || 820;
  const H = 600;

  const maxStrength = d3.max(bibcoupNodes, n => n.coupling_strength) || 1;
  const rScale = d3.scaleSqrt().domain([0, maxStrength]).range([3, 20]);

  const edgeScale = d3.scaleLinear().domain([1, Math.max(maxWeight, 2)]).range([0.8, 5]);

  bibcoupSvgEl = d3.select('#bibcoup-container')
    .append('svg')
    .attr('width', W)
    .attr('height', H);

  bibcoupZoomBehavior = d3.zoom()
    .scaleExtent([0.1, 8])
    .on('zoom', (event) => gRoot.attr('transform', event.transform));

  bibcoupSvgEl.call(bibcoupZoomBehavior);
  const gRoot = bibcoupSvgEl.append('g');

  // Links
  bibcoupLinkSel = gRoot.append('g').attr('class', 'bibcoup-links')
    .selectAll('line')
    .data(bibcoupLinks)
    .enter().append('line')
    .style('stroke',         '#ccc7bb')
    .style('stroke-opacity', d => 0.15 + 0.55 * (d.weight / maxWeight))
    .style('stroke-width',   d => edgeScale(d.weight));

  // Tooltip
  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip bibcoup-tip')
    .style('display',    'none')
    .style('max-width',  '320px')
    .style('line-height','1.45');

  // Node groups
  const nodeGroup = gRoot.append('g').attr('class', 'bibcoup-nodes')
    .selectAll('g')
    .data(bibcoupNodes)
    .enter().append('g')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => {
        if (!event.active) bibcoupSimulation.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end',   (event, d) => {
        if (!event.active) bibcoupSimulation.alphaTarget(0);
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

      // Top coupling partners
      const partners = bibcoupLinks
        .filter(l => {
          const sid = typeof l.source === 'object' ? l.source.id : l.source;
          const tid = typeof l.target === 'object' ? l.target.id : l.target;
          return sid === d.id || tid === d.id;
        })
        .map(l => {
          const sid = typeof l.source === 'object' ? l.source.id : l.source;
          const tid = typeof l.target === 'object' ? l.target.id : l.target;
          const partnerId = sid === d.id ? tid : sid;
          const partner = bibcoupNodes.find(n => n.id === partnerId);
          return { name: partner ? partner.title : '?', weight: l.weight };
        })
        .sort((a, b) => b.weight - a.weight)
        .slice(0, 5);

      const partnerHtml = partners.length > 0
        ? '<br><span style="font-size:0.78rem;color:#9c9890;">Most coupled with:</span>' +
          partners.map(p =>
            `<br><span style="font-size:0.78rem;">\u2022 ${escapeHtml(p.name.length > 60 ? p.name.slice(0, 57) + '\u2026' : p.name)} (${p.weight} shared)</span>`
          ).join('')
        : '';

      tip.style('display', 'block').html(
        `<strong>${escapeHtml(d.title)}</strong><br>` +
        (byline ? `${escapeHtml(byline)}<br>` : '') +
        `<em>${escapeHtml(d.journal)}</em><br>` +
        `Coupling strength: <strong>${d.coupling_strength}</strong>` +
        (d.internal_cites_count ? ` \u2002\u00b7\u2002 ${d.internal_cites_count} refs in index` : '') +
        partnerHtml
      );

      // Highlight connected edges
      bibcoupLinkSel.style('stroke-opacity', l => {
        const sid = typeof l.source === 'object' ? l.source.id : l.source;
        const tid = typeof l.target === 'object' ? l.target.id : l.target;
        return (sid === d.id || tid === d.id) ? 0.8 : 0.06;
      });
      nodeGroup.select('circle').style('fill-opacity', n => {
        if (n.id === d.id) return 1;
        const connected = bibcoupLinks.some(l => {
          const sid = typeof l.source === 'object' ? l.source.id : l.source;
          const tid = typeof l.target === 'object' ? l.target.id : l.target;
          return (sid === d.id && tid === n.id) || (tid === d.id && sid === n.id);
        });
        return connected ? 0.9 : 0.15;
      });
    })
    .on('mousemove', (event) => {
      positionTooltip(tip, event, { pad: 14, vPad: -38 });
    })
    .on('mouseout', () => {
      tip.style('display', 'none');
      bibcoupLinkSel.style('stroke-opacity', l => 0.15 + 0.55 * (l.weight / maxWeight));
      nodeGroup.select('circle').style('fill-opacity', 0.88);
    });

  bibcoupNodeSel = nodeGroup;

  // Circles
  nodeGroup.append('circle')
    .attr('r', d => rScale(d.coupling_strength))
    .style('fill',         d => citnetJournalColor(d.journal))
    .style('fill-opacity', 0.88)
    .style('stroke',       '#fff')
    .style('stroke-width', 1.5);

  // Labels for top 20 nodes by coupling strength
  const sortedByStrength = [...bibcoupNodes].sort((a, b) => b.coupling_strength - a.coupling_strength);
  const labelIds = new Set(sortedByStrength.slice(0, 20).map(n => n.id));

  nodeGroup.filter(d => labelIds.has(d.id))
    .append('text')
    .text(d => {
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      return first.split(' ').filter(Boolean).pop() || '';
    })
    .attr('dy',           d => rScale(d.coupling_strength) + 10)
    .attr('text-anchor',  'middle')
    .style('font-family', 'system-ui, sans-serif')
    .style('font-size',   '9px')
    .style('fill',        '#5a3e28')
    .style('pointer-events', 'none');

  // Dismiss tooltip on background click
  bibcoupSvgEl.on('click', () => {
    tip.style('display', 'none');
    bibcoupLinkSel.style('stroke-opacity', l => 0.15 + 0.55 * (l.weight / maxWeight));
    nodeGroup.select('circle').style('fill-opacity', 0.88);
  });

  // Force simulation
  bibcoupSimulation = d3.forceSimulation(bibcoupNodes)
    .force('link', d3.forceLink(bibcoupLinks)
      .id(d => d.id)
      .distance(d => Math.max(20, 80 - d.weight * 3))
      .strength(d => 0.2 + 0.6 * (d.weight / maxWeight)))
    .force('charge',    d3.forceManyBody().strength(-80))
    .force('center',    d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => rScale(d.coupling_strength) + 2))
    .alphaDecay(0.025)
    .on('tick', () => {
      bibcoupLinkSel
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeGroup.attr('transform', d => `translate(${d.x},${d.y})`);
    });
}

// ── Inline-handler globals ────────────────────────────────────
window.onBibcoupSliderChange = onBibcoupSliderChange;
window.toggleAllBibcoupJournals = toggleAllBibcoupJournals;
window.updateBibcoupJournalCount = updateBibcoupJournalCount;
window.loadBibcoupling = loadBibcoupling;
