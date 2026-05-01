// static/js/viz/author_cocitation.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { renderExportToolbar } from "./_ds_export.js";
import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


let acocitSimulation = null;
let acocitSvgEl = null;
let acocitZoomBehavior = null;
let acocitNodes = [];
let acocitLinks = [];
let acocitLinkSel = null;
let acocitNodeSel = null;
let acocitDebounce = null;
let acocitPairsData = [];

function initAuthorCocitation() {
  document.getElementById('acocit-infobar-close').addEventListener('click', clearAcocitInfobar);

  // Search filter
  document.getElementById('acocit-search').addEventListener('input', function() {
    const q = this.value.trim().toLowerCase();
    if (!acocitNodeSel) return;
    if (!q) {
      acocitNodeSel.style('opacity', 1);
      if (acocitLinkSel) acocitLinkSel.style('stroke-opacity', l => 0.15 + 0.4 * (l.weight / (d3.max(acocitLinks, ll => ll.weight) || 1)));
      return;
    }
    acocitNodeSel.style('opacity', d => d.name.toLowerCase().includes(q) ? 1 : 0.08);
    if (acocitLinkSel) acocitLinkSel.style('stroke-opacity', 0.04);
  });
}

function onAcocitSliderChange(val) {
  document.getElementById('acocit-min-val').textContent = val;
}

function toggleAllAcocitJournals(cb) {
  document.querySelectorAll('.acocit-journal-check').forEach(c => c.checked = cb.checked);
  updateAcocitJournalCount();
}

function updateAcocitJournalCount() {
  const total   = document.querySelectorAll('.acocit-journal-check').length;
  const checked = document.querySelectorAll('.acocit-journal-check:checked').length;
  document.getElementById('acocit-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}\u202f/\u202f${total})`;
  const allCb = document.getElementById('acocit-check-all');
  if (allCb) allCb.checked = (checked === total);
  if (allCb) allCb.indeterminate = (checked > 0 && checked < total);
}

let _exportWired_loadAuthorCocitation = false;

async function loadAuthorCocitation() {
  if (!_exportWired_loadAuthorCocitation) {
    renderExportToolbar('tab-authorcocit', { svgSelector: '#authorcocit-container svg', dataProvider: () => (window.__expAuthorCocitation && window.__expAuthorCocitation.nodes || []) });
    _exportWired_loadAuthorCocitation = true;
  }
  const container = document.getElementById('acocit-container');
  container.innerHTML = '<div class="loading-msg">Computing author co-citation network\u2026 this may take a few seconds.</div>';
  document.getElementById('acocit-stats').textContent = '';
  document.getElementById('acocit-legend').innerHTML  = '';
  document.getElementById('acocit-pairs-section').style.display = 'none';
  clearAcocitInfobar();

  if (acocitSimulation) { acocitSimulation.stop(); acocitSimulation = null; }

  const minCocit    = document.getElementById('acocit-min-slider').value;
  const maxAuthors  = document.getElementById('acocit-max-authors').value;
  let yearFrom      = document.getElementById('acocit-year-from').value;
  let yearTo        = document.getElementById('acocit-year-to').value;
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo)) {
    [yearFrom, yearTo] = [yearTo, yearFrom];
    document.getElementById('acocit-year-from').value = yearFrom;
    document.getElementById('acocit-year-to').value   = yearTo;
  }
  const checked = [...document.querySelectorAll('.acocit-journal-check:checked')].map(c => c.value);
  const total   = document.querySelectorAll('.acocit-journal-check').length;

  const params = new URLSearchParams({ min_cocitations: minCocit, max_authors: maxAuthors });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/author-cocitation?' + params.toString());
    data = await resp.json();
    window.__expAuthorCocitation = data;
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load author co-citation data.</p>';
    return;
  }

  if (!data.nodes || data.nodes.length === 0) {
    const minVal = parseInt(minCocit);
    let hint = 'No author co-citation pairs meet the current threshold.';
    if (minVal > 1) hint += ` Try lowering the minimum co-citations.`;
    container.innerHTML = '<p class="explore-hint">' + hint + '</p>';
    return;
  }

  acocitPairsData = data.pairs || [];
  renderAuthorCocitation(container, data);
  renderAcocitPairs(data.pairs || []);
}

function renderAuthorCocitation(container, data) {
  container.innerHTML = '';
  acocitNodes = data.nodes.map(d => ({ ...d }));
  acocitLinks = data.edges.map(d => ({ ...d }));

  // Stats bar
  const maxWeight = d3.max(acocitLinks, l => l.weight) || 1;
  document.getElementById('acocit-stats').textContent =
    `${data.stats.total_authors} authors\u2002\u00b7\u2002${data.stats.total_edges} co-citation pairs` +
    `\u2002\u00b7\u2002strongest pair co-cited ${data.stats.max_cocitation}\u00d7`;

  // Legend — colour by top journal
  const seenJournals = [...new Set(acocitNodes.map(n => n.top_journal).filter(Boolean))].sort();
  const legendEl = document.getElementById('acocit-legend');
  legendEl.innerHTML = seenJournals.map(j =>
    `<span class="citnet-legend-item">` +
    `<span class="citnet-legend-dot" style="background:${citnetJournalColor(j)}"></span>` +
    `${escapeHtml(j)}</span>`
  ).join('');

  const W = container.clientWidth || 820;
  const H = 600;

  // Node radius: by co-citation strength
  const maxStrength = d3.max(acocitNodes, n => n.total_cocitation_strength) || 1;
  const rScale = d3.scaleSqrt().domain([0, maxStrength]).range([4, 22]);

  // Edge thickness: by co-citation weight
  const edgeScale = d3.scaleLinear().domain([1, Math.max(maxWeight, 2)]).range([0.6, 5]);

  // SVG + zoom
  acocitSvgEl = d3.select('#acocit-container')
    .append('svg')
    .attr('width', W)
    .attr('height', H);

  acocitZoomBehavior = d3.zoom()
    .scaleExtent([0.1, 8])
    .on('zoom', (event) => gRoot.attr('transform', event.transform));

  acocitSvgEl.call(acocitZoomBehavior);
  const gRoot = acocitSvgEl.append('g');

  // Links
  acocitLinkSel = gRoot.append('g').attr('class', 'acocit-links')
    .selectAll('line')
    .data(acocitLinks)
    .enter().append('line')
    .style('stroke',         '#ccc7bb')
    .style('stroke-opacity', d => 0.15 + 0.4 * (d.weight / maxWeight))
    .style('stroke-width',   d => edgeScale(d.weight));

  // Tooltip
  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip acocit-tip')
    .style('display',    'none')
    .style('max-width',  '340px')
    .style('line-height','1.45');

  // Node groups
  const nodeGroup = gRoot.append('g').attr('class', 'acocit-nodes')
    .selectAll('g')
    .data(acocitNodes)
    .enter().append('g')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => {
        if (!event.active) acocitSimulation.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end',   (event, d) => {
        if (!event.active) acocitSimulation.alphaTarget(0);
        d.fx = null; d.fy = null;
      })
    );

  // Circles
  nodeGroup.append('circle')
    .attr('r', d => rScale(d.total_cocitation_strength))
    .attr('fill', d => citnetJournalColor(d.top_journal || ''))
    .attr('stroke', '#fff')
    .attr('stroke-width', 1.5);

  // Labels for large nodes
  nodeGroup.filter(d => rScale(d.total_cocitation_strength) >= 9)
    .append('text')
    .attr('font-size', '8px')
    .attr('text-anchor', 'middle')
    .attr('dy', d => -(rScale(d.total_cocitation_strength) + 3))
    .attr('fill', '#3a2a18')
    .text(d => {
      const parts = d.name.split(/\s+/);
      return parts[parts.length - 1]; // last name only
    });

  acocitNodeSel = nodeGroup;

  // Hover
  nodeGroup.on('mouseover', (event, d) => {
    // Top partners
    const partners = acocitLinks
      .filter(l => {
        const sid = typeof l.source === 'object' ? l.source.id : l.source;
        const tid = typeof l.target === 'object' ? l.target.id : l.target;
        return sid === d.id || tid === d.id;
      })
      .map(l => {
        const sid = typeof l.source === 'object' ? l.source.id : l.source;
        const tid = typeof l.target === 'object' ? l.target.id : l.target;
        const partnerId = sid === d.id ? tid : sid;
        const partner = acocitNodes.find(n => n.id === partnerId);
        return { name: partner ? partner.name : '?', weight: l.weight };
      })
      .sort((a, b) => b.weight - a.weight)
      .slice(0, 3);

    const partnerHtml = partners.length > 0
      ? '<br><span style="font-size:0.78rem;color:#9c9890;">Top co-cited with:</span>' +
        partners.map(p =>
          `<br><span style="font-size:0.78rem;">\u2022 ${escapeHtml(p.name)} (${p.weight}\u00d7)</span>`
        ).join('')
      : '';

    tip.style('display', 'block').html(
      `<strong>${escapeHtml(d.name)}</strong><br>` +
      `<span style="font-size:0.82rem;">${d.article_count} articles in index</span><br>` +
      `<span style="font-size:0.82rem;">Co-citation strength: <strong>${d.total_cocitation_strength}</strong></span>` +
      (d.top_journal ? `<br><em style="font-size:0.78rem;">${escapeHtml(d.top_journal)}</em>` : '') +
      partnerHtml
    );

    // Highlight edges
    acocitLinkSel.style('stroke-opacity', l => {
      const sid = typeof l.source === 'object' ? l.source.id : l.source;
      const tid = typeof l.target === 'object' ? l.target.id : l.target;
      return (sid === d.id || tid === d.id) ? 0.85 : 0.04;
    }).style('stroke', l => {
      const sid = typeof l.source === 'object' ? l.source.id : l.source;
      const tid = typeof l.target === 'object' ? l.target.id : l.target;
      return (sid === d.id || tid === d.id) ? '#8b6045' : '#ccc7bb';
    });
  })
  .on('mousemove', (event) => {
    positionTooltip(tip, event);
  })
  .on('mouseout', () => {
    tip.style('display', 'none');
    acocitLinkSel.style('stroke-opacity', l => 0.15 + 0.4 * (l.weight / maxWeight))
                  .style('stroke', '#ccc7bb');
  })
  .on('click', (event, d) => {
    event.stopPropagation();
    showAcocitInfobar(d);
  });

  // Click background to clear
  acocitSvgEl.on('click', () => clearAcocitInfobar());

  // Force simulation
  acocitSimulation = d3.forceSimulation(acocitNodes)
    .force('link', d3.forceLink(acocitLinks).id(d => d.id).distance(100))
    .force('charge', d3.forceManyBody().strength(-180))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => rScale(d.total_cocitation_strength) + 2))
    .on('tick', () => {
      acocitLinkSel
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeGroup
        .attr('transform', d => `translate(${d.x},${d.y})`);
    });
}

function showAcocitInfobar(d) {
  const infobar = document.getElementById('acocit-infobar');
  document.getElementById('acocit-infobar-name').textContent = d.name;

  // Find top partners
  const partners = acocitLinks
    .filter(l => {
      const sid = typeof l.source === 'object' ? l.source.id : l.source;
      const tid = typeof l.target === 'object' ? l.target.id : l.target;
      return sid === d.id || tid === d.id;
    })
    .map(l => {
      const sid = typeof l.source === 'object' ? l.source.id : l.source;
      const tid = typeof l.target === 'object' ? l.target.id : l.target;
      const partnerId = sid === d.id ? tid : sid;
      const partner = acocitNodes.find(n => n.id === partnerId);
      return { name: partner ? partner.name : '?', weight: l.weight };
    })
    .sort((a, b) => b.weight - a.weight);

  const topStr = partners.slice(0, 5).map(p => `${p.name} (${p.weight}\u00d7)`).join(', ');

  document.getElementById('acocit-infobar-detail').innerHTML =
    `${d.article_count} articles · strength ${d.total_cocitation_strength}` +
    (topStr ? `<br><small>Top co-cited: ${escapeHtml(topStr)}</small>` : '');

  document.getElementById('acocit-infobar-link').href = '/author/' + encodeURIComponent(d.name);
  infobar.style.display = 'flex';
}

function clearAcocitInfobar() {
  document.getElementById('acocit-infobar').style.display = 'none';
}

function renderAcocitPairs(pairs) {
  if (!pairs || pairs.length === 0) return;

  document.getElementById('acocit-pairs-section').style.display = 'block';
  const container = document.getElementById('acocit-pairs-container');

  let html = '<table style="width:100%;border-collapse:collapse;font-size:0.88rem;">';
  html += '<thead><tr style="border-bottom:2px solid #d4bc99;text-align:left;">' +
    '<th style="padding:0.4rem 0.6rem;width:3rem;">#</th>' +
    '<th style="padding:0.4rem 0.6rem;">Author A</th>' +
    '<th style="padding:0.4rem 0.6rem;">Author B</th>' +
    '<th style="padding:0.4rem 0.6rem;width:6rem;text-align:right;">Co-cited</th>' +
    '</tr></thead><tbody>';

  pairs.slice(0, 50).forEach((p, i) => {
    html += `<tr style="border-bottom:1px solid #ede8e0;">` +
      `<td style="padding:0.4rem 0.6rem;color:#8b6045;font-weight:600;">${i + 1}</td>` +
      `<td style="padding:0.4rem 0.6rem;"><a href="/author/${encodeURIComponent(p.author1)}" style="color:#3a2a18;">${escapeHtml(p.author1)}</a></td>` +
      `<td style="padding:0.4rem 0.6rem;"><a href="/author/${encodeURIComponent(p.author2)}" style="color:#3a2a18;">${escapeHtml(p.author2)}</a></td>` +
      `<td style="padding:0.4rem 0.6rem;text-align:right;font-weight:600;">${p.cocitation_count}\u00d7</td>` +
      `</tr>`;
  });

  html += '</tbody></table>';

  if (pairs.length > 50) {
    html += `<p style="font-size:0.82rem;color:#8b6045;margin-top:0.5rem;">${pairs.length - 50} more pairs not shown.</p>`;
  }

  container.innerHTML = html;
}

// ── Inline-handler globals ────────────────────────────────────
window.onAcocitSliderChange = onAcocitSliderChange;
window.toggleAllAcocitJournals = toggleAllAcocitJournals;
window.updateAcocitJournalCount = updateAcocitJournalCount;
window.loadAuthorCocitation = loadAuthorCocitation;

window.initAuthorCocitation = initAuthorCocitation;
