// static/js/viz/cocitation.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


let cocitSimulation   = null;
let cocitZoomBehavior = null;
let cocitSvgEl        = null;
let cocitNodeSel      = null;
let cocitLinkSel      = null;
let cocitNodes        = [];
let cocitLinks        = [];
let cocitDebounce     = null;

function onCocitSliderChange(val) {
  document.getElementById('cocit-min-val').textContent = val;
  clearTimeout(cocitDebounce);
  cocitDebounce = setTimeout(loadCocitation, 700);
}

function toggleAllCocitJournals(cb) {
  document.querySelectorAll('.cocit-journal-check').forEach(c => c.checked = cb.checked);
  updateCocitJournalCount();
}

function updateCocitJournalCount() {
  const total   = document.querySelectorAll('.cocit-journal-check').length;
  const checked = document.querySelectorAll('.cocit-journal-check:checked').length;
  document.getElementById('cocit-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}\u202f/\u202f${total})`;
  const allCb = document.getElementById('cocit-check-all');
  if (allCb) allCb.checked = (checked === total);
  if (allCb) allCb.indeterminate = (checked > 0 && checked < total);
}

async function loadCocitation() {
  clearTimeout(cocitDebounce);

  const container = document.getElementById('cocit-container');
  container.innerHTML = '<div class="loading-msg">Computing co-citation network\u2026</div>';
  document.getElementById('cocit-stats').textContent = '';
  document.getElementById('cocit-legend').innerHTML  = '';

  if (cocitSimulation) { cocitSimulation.stop(); cocitSimulation = null; }
  d3.selectAll('.cocit-tip').remove();

  const minCocit = document.getElementById('cocit-min-slider').value;
  let yearFrom = document.getElementById('cocit-year-from').value;
  let yearTo   = document.getElementById('cocit-year-to').value;
  // Swap if user entered years in reverse order
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo)) {
    [yearFrom, yearTo] = [yearTo, yearFrom];
    document.getElementById('cocit-year-from').value = yearFrom;
    document.getElementById('cocit-year-to').value   = yearTo;
  }
  const checked  = [...document.querySelectorAll('.cocit-journal-check:checked')].map(c => c.value);
  const total    = document.querySelectorAll('.cocit-journal-check').length;

  const params = new URLSearchParams({ min_cocitations: minCocit });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/citations/cocitation?' + params.toString());
    data = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load co-citation data.</p>';
    return;
  }

  if (!data.nodes || data.nodes.length === 0) {
    const minVal = parseInt(document.getElementById('cocit-min-slider').value);
    let hint = 'No co-citation pairs meet the current threshold.';
    if (minVal > 1) {
      hint += ` Try lowering the minimum co-citations \u2014 recent articles and smaller journals often haven\u2019t accumulated enough co-citations to appear at ${minVal}+.`;
    }
    const yearFrom = document.getElementById('cocit-year-from').value;
    const yearTo   = document.getElementById('cocit-year-to').value;
    if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo)) {
      hint += ' <strong>Note:</strong> your \u201cFrom\u201d year is later than your \u201cTo\u201d year \u2014 the server swapped them automatically, but double-check the range.';
    }
    container.innerHTML = '<p class="explore-hint">' + hint + '</p>';
    return;
  }

  renderCocitation(container, data);
}

function renderCocitation(container, data) {
  container.innerHTML = '';
  cocitNodes = data.nodes.map(d => ({ ...d }));
  cocitLinks = data.links.map(d => ({ ...d }));

  // Stats bar
  const maxWeight = d3.max(cocitLinks, l => l.weight) || 1;
  document.getElementById('cocit-stats').textContent =
    `${data.node_count} articles\u2002\u00b7\u2002${data.link_count} co-citation pairs` +
    `\u2002\u00b7\u2002strongest pair co-cited ${maxWeight}\u00d7`;

  // Legend — colour by journal
  const seenJournals = [...new Set(cocitNodes.map(n => n.journal))].sort();
  const legendEl = document.getElementById('cocit-legend');
  legendEl.innerHTML = seenJournals.map(j =>
    `<span class="citnet-legend-item">` +
    `<span class="citnet-legend-dot" style="background:${citnetJournalColor(j)}"></span>` +
    `${escapeHtml(j)}</span>`
  ).join('');

  const W = container.clientWidth || 820;
  const H = 600;

  // Node radius: by co-citation strength (sum of edge weights)
  const maxStrength = d3.max(cocitNodes, n => n.cocitation_strength) || 1;
  const rScale = d3.scaleSqrt().domain([0, maxStrength]).range([3, 20]);

  // Edge thickness: by co-citation weight
  const edgeScale = d3.scaleLinear().domain([1, Math.max(maxWeight, 2)]).range([0.8, 5]);

  // SVG + zoom
  cocitSvgEl = d3.select('#cocit-container')
    .append('svg')
    .attr('width', W)
    .attr('height', H);

  cocitZoomBehavior = d3.zoom()
    .scaleExtent([0.1, 8])
    .on('zoom', (event) => gRoot.attr('transform', event.transform));

  cocitSvgEl.call(cocitZoomBehavior);
  const gRoot = cocitSvgEl.append('g');

  // Links (undirected, weighted)
  cocitLinkSel = gRoot.append('g').attr('class', 'cocit-links')
    .selectAll('line')
    .data(cocitLinks)
    .enter().append('line')
    .style('stroke',         '#ccc7bb')
    .style('stroke-opacity', d => 0.15 + 0.55 * (d.weight / maxWeight))
    .style('stroke-width',   d => edgeScale(d.weight));

  // Tooltip
  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip cocit-tip')
    .style('display',    'none')
    .style('max-width',  '320px')
    .style('line-height','1.45');

  // Node groups
  const nodeGroup = gRoot.append('g').attr('class', 'cocit-nodes')
    .selectAll('g')
    .data(cocitNodes)
    .enter().append('g')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => {
        if (!event.active) cocitSimulation.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end',   (event, d) => {
        if (!event.active) cocitSimulation.alphaTarget(0);
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

      // Find top co-citation partners for this node
      const partners = cocitLinks
        .filter(l => {
          const sid = typeof l.source === 'object' ? l.source.id : l.source;
          const tid = typeof l.target === 'object' ? l.target.id : l.target;
          return sid === d.id || tid === d.id;
        })
        .map(l => {
          const sid = typeof l.source === 'object' ? l.source.id : l.source;
          const tid = typeof l.target === 'object' ? l.target.id : l.target;
          const partnerId = sid === d.id ? tid : sid;
          const partner = cocitNodes.find(n => n.id === partnerId);
          return { name: partner ? partner.title : '?', weight: l.weight };
        })
        .sort((a, b) => b.weight - a.weight)
        .slice(0, 5);

      const partnerHtml = partners.length > 0
        ? '<br><span style="font-size:0.78rem;color:#9c9890;">Top co-cited with:</span>' +
          partners.map(p =>
            `<br><span style="font-size:0.78rem;">\u2022 ${escapeHtml(p.name.length > 60 ? p.name.slice(0, 57) + '\u2026' : p.name)} (${p.weight}\u00d7)</span>`
          ).join('')
        : '';

      tip.style('display', 'block').html(
        `<strong>${escapeHtml(d.title)}</strong><br>` +
        (byline ? `${escapeHtml(byline)}<br>` : '') +
        `<em>${escapeHtml(d.journal)}</em><br>` +
        `Co-citation strength: <strong>${d.cocitation_strength}</strong>` +
        (d.internal_cited_by_count ? ` \u2002\u00b7\u2002 Cited ${d.internal_cited_by_count}\u00d7` : '') +
        partnerHtml
      );

      // Highlight connected edges
      cocitLinkSel.style('stroke-opacity', l => {
        const sid = typeof l.source === 'object' ? l.source.id : l.source;
        const tid = typeof l.target === 'object' ? l.target.id : l.target;
        return (sid === d.id || tid === d.id)
          ? 0.8
          : 0.06;
      });
      nodeGroup.select('circle').style('fill-opacity', n => {
        if (n.id === d.id) return 1;
        const connected = cocitLinks.some(l => {
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
      cocitLinkSel.style('stroke-opacity', l => 0.15 + 0.55 * (l.weight / maxWeight));
      nodeGroup.select('circle').style('fill-opacity', 0.88);
    });

  cocitNodeSel = nodeGroup;

  // Circles
  nodeGroup.append('circle')
    .attr('r', d => rScale(d.cocitation_strength))
    .style('fill',         d => citnetJournalColor(d.journal))
    .style('fill-opacity', 0.88)
    .style('stroke',       '#fff')
    .style('stroke-width', 1.5);

  // Labels for top 20 nodes by co-citation strength
  const sortedByStrength = [...cocitNodes].sort((a, b) => b.cocitation_strength - a.cocitation_strength);
  const labelIds = new Set(sortedByStrength.slice(0, 20).map(n => n.id));

  nodeGroup.filter(d => labelIds.has(d.id))
    .append('text')
    .text(d => {
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      return first.split(' ').filter(Boolean).pop() || '';
    })
    .attr('dy',           d => rScale(d.cocitation_strength) + 10)
    .attr('text-anchor',  'middle')
    .style('font-family', 'system-ui, sans-serif')
    .style('font-size',   '9px')
    .style('fill',        '#5a3e28')
    .style('pointer-events', 'none');

  // Dismiss tooltip on background click
  cocitSvgEl.on('click', () => {
    tip.style('display', 'none');
    cocitLinkSel.style('stroke-opacity', l => 0.15 + 0.55 * (l.weight / maxWeight));
    nodeGroup.select('circle').style('fill-opacity', 0.88);
  });

  // Force simulation — undirected, weighted
  // Stronger co-citation pulls nodes closer; higher weight = shorter distance
  cocitSimulation = d3.forceSimulation(cocitNodes)
    .force('link', d3.forceLink(cocitLinks)
      .id(d => d.id)
      .distance(d => Math.max(20, 80 - d.weight * 3))
      .strength(d => 0.2 + 0.6 * (d.weight / maxWeight)))
    .force('charge',    d3.forceManyBody().strength(-80))
    .force('center',    d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => rScale(d.cocitation_strength) + 2))
    .alphaDecay(0.025)
    .on('tick', () => {
      cocitLinkSel
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeGroup.attr('transform', d => `translate(${d.x},${d.y})`);
    });
}

// ── Inline-handler globals ────────────────────────────────────
window.onCocitSliderChange = onCocitSliderChange;
window.toggleAllCocitJournals = toggleAllCocitJournals;
window.updateCocitJournalCount = updateCocitJournalCount;
window.loadCocitation = loadCocitation;
