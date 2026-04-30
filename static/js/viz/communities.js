// static/js/viz/communities.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


const COMM_PALETTE = [
  '#8b6045','#3a5a28','#4a6a8a','#8a4a5a','#6a5a8a',
  '#5a7a4a','#8a7a3a','#3a6a6a','#7a4a3a','#5a5a6a',
  '#7a6a2a','#3a4a7a',
];

let commSimulation = null;


function toggleAllCommJournals(master) {
  document.querySelectorAll('.comm-journal-check').forEach(c => c.checked = master.checked);
  updateCommJournalCount();
}

function updateCommJournalCount() {
  const checked = document.querySelectorAll('.comm-journal-check:checked').length;
  const total   = document.querySelectorAll('.comm-journal-check').length;
  document.getElementById('comm-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}/${total})`;
  document.getElementById('comm-check-all').checked = (checked === total);
}

async function loadCommunities() {
  const container = document.getElementById('comm-container');
  container.innerHTML = '<div class="loading-msg">Running community detection\u2026</div>';
  document.getElementById('comm-stats').textContent = '';
  document.getElementById('comm-legend').innerHTML = '';
  document.getElementById('comm-tables').style.display = 'none';
  if (commSimulation) { commSimulation.stop(); commSimulation = null; }
  d3.selectAll('.comm-tip').remove();

  const minCit = document.getElementById('comm-min-slider').value;
  const resolution = document.getElementById('comm-res-slider').value;
  let yearFrom = document.getElementById('comm-year-from').value;
  let yearTo   = document.getElementById('comm-year-to').value;
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo))
    [yearFrom, yearTo] = [yearTo, yearFrom];
  const checked = [...document.querySelectorAll('.comm-journal-check:checked')].map(c => c.value);
  const total   = document.querySelectorAll('.comm-journal-check').length;

  const params = new URLSearchParams({ min_citations: minCit, resolution });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/citations/communities?' + params.toString());
    data = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load community data.</p>';
    return;
  }

  if (!data.nodes || data.nodes.length === 0) {
    container.innerHTML =
      '<p class="explore-hint">No articles match these filters \u2014 try a lower minimum.</p>';
    return;
  }

  document.getElementById('comm-stats').textContent =
    `${data.node_count} articles \u00b7 ${data.link_count} citation links \u00b7 ` +
    `${data.community_count} communities \u00b7 modularity Q\u2009=\u2009${data.modularity}`;

  renderCommunities(container, data);
  renderCommunityTables(data);
}

function renderCommunities(container, data) {
  container.innerHTML = '';
  const commColor = i => COMM_PALETTE[i % COMM_PALETTE.length];

  // Legend
  const legendEl = document.getElementById('comm-legend');
  legendEl.innerHTML = data.communities.map(c => {
    const label = c.topics.length ? c.topics.slice(0,2).join(', ') : `Community ${c.id}`;
    return `<span class="citnet-legend-item" data-comm="${c.id}" style="cursor:pointer;">` +
      `<span class="citnet-legend-dot" style="background:${commColor(c.id)}"></span>` +
      `${label} (${c.size})</span>`;
  }).join('');

  const W = container.clientWidth || 820;
  const H = 600;
  const nodes = data.nodes.map(d => ({ ...d }));
  const links = data.links.map(d => ({ ...d }));

  const citExtent = d3.extent(nodes, d => d.internal_cited_by_count);
  const rScale = d3.scaleSqrt().domain([0, citExtent[1] || 1]).range([3, 18]);

  const svg = d3.select(container).append('svg').attr('width', W).attr('height', H);
  const g = svg.append('g');

  const zoom = d3.zoom().scaleExtent([0.2, 5])
    .on('zoom', e => g.attr('transform', e.transform));
  svg.call(zoom);

  const linkSel = g.append('g').selectAll('line')
    .data(links).enter().append('line')
    .style('stroke', '#ccc7bb').style('stroke-opacity', 0.4)
    .style('stroke-width', d => Math.max(0.5, Math.min(d.weight * 1.5, 4)));

  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip comm-tip')
    .style('opacity', 0).style('pointer-events', 'none');

  const nodeGroup = g.append('g').selectAll('g')
    .data(nodes).enter().append('g')
    .style('cursor', 'pointer')
    .on('click', (e, d) => window.open('/article/' + d.id, '_blank'))
    .on('mouseover', (event, d) => {
      const yr = (d.pub_date || '').substring(0, 4);
      const comm = data.communities.find(c => c.id === d.community);
      const commLabel = comm && comm.topics.length ? comm.topics.slice(0,3).join(', ') : `Community ${d.community}`;
      tip.html(
        `<strong>${d.title || 'Untitled'}</strong><br>` +
        `${d.authors || ''} (${yr})<br>` +
        `<em>${d.journal || ''}</em><br>` +
        `Cited ${d.internal_cited_by_count}\u00d7 internally<br>` +
        `<span style="color:${commColor(d.community)};">\u25CF</span> ${commLabel}`
      ).style('opacity', 1);
      positionTooltip(tip, event);
    })
    .on('mousemove', event => {
      positionTooltip(tip, event);
    })
    .on('mouseout', () => tip.style('opacity', 0))
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) commSimulation.alphaTarget(0.2).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end',   (e, d) => { if (!e.active) commSimulation.alphaTarget(0); d.fx = null; d.fy = null; })
    );

  nodeGroup.append('circle')
    .attr('r', d => rScale(d.internal_cited_by_count))
    .style('fill', d => commColor(d.community))
    .style('stroke', '#fff').style('stroke-width', 0.8);

  // Build link-strength function: stronger within community
  const nodeIdx = {};
  nodes.forEach((n, i) => nodeIdx[n.id] = i);

  commSimulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links)
      .id(d => d.id)
      .distance(60)
      .strength(d => {
        const sComm = nodes.find(n => n.id === (d.source.id || d.source))?.community;
        const tComm = nodes.find(n => n.id === (d.target.id || d.target))?.community;
        return sComm === tComm ? 0.4 : 0.05;
      }))
    .force('charge', d3.forceManyBody().strength(-60))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => rScale(d.internal_cited_by_count) + 2))
    .on('tick', () => {
      linkSel.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
             .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeGroup.attr('transform', d => `translate(${d.x},${d.y})`);
    });

  // Legend click to highlight a community
  legendEl.querySelectorAll('.citnet-legend-item').forEach(el => {
    el.addEventListener('click', () => {
      const cid = parseInt(el.dataset.comm);
      const active = el.classList.toggle('comm-highlight');
      if (active) {
        nodeGroup.style('opacity', d => d.community === cid ? 1 : 0.08);
        linkSel.style('stroke-opacity', d => {
          const s = typeof d.source === 'object' ? d.source : nodes.find(n => n.id === d.source);
          const t = typeof d.target === 'object' ? d.target : nodes.find(n => n.id === d.target);
          return (s?.community === cid || t?.community === cid) ? 0.6 : 0.02;
        });
        legendEl.querySelectorAll('.citnet-legend-item').forEach(e => {
          if (e !== el) e.classList.remove('comm-highlight');
        });
      } else {
        nodeGroup.style('opacity', 1);
        linkSel.style('stroke-opacity', 0.4);
      }
    });
  });
}

function renderCommunityTables(data) {
  const tablesEl = document.getElementById('comm-tables');
  if (!data.communities.length) { tablesEl.style.display = 'none'; return; }
  const commColor = i => COMM_PALETTE[i % COMM_PALETTE.length];

  let html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;">';
  for (const c of data.communities) {
    const topics = c.topics.map(t =>
      `<span style="display:inline-block;padding:0.1rem 0.45rem;font-size:0.72rem;background:#f5f0e8;border-radius:3px;color:#5a3e28;margin:0.1rem 0.15rem;">${t}</span>`
    ).join('');
    const journals = c.top_journals.map(j => `${j.name} (${j.count})`).join(', ');
    const articles = c.top_articles.map(a => {
      const yr = (a.pub_date || '').substring(0, 4);
      return `<li style="margin-bottom:0.3rem;"><a href="/article/${a.id}" style="color:#5a3e28;">${a.title || 'Untitled'}</a> <span style="color:#999;">${yr} &middot; cited ${a.internal_cited_by_count}&times;</span></li>`;
    }).join('');

    html += `<div style="border:1px solid #e5ddd0;border-radius:6px;padding:0.8rem;background:#faf8f4;">
      <h4 style="margin:0 0 0.4rem;font-size:0.9rem;">
        <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${commColor(c.id)};margin-right:0.3rem;"></span>
        Community ${c.id + 1} <span style="font-weight:normal;color:#999;">(${c.size} articles)</span>
      </h4>
      <div style="margin-bottom:0.4rem;">${topics}</div>
      <div style="font-size:0.78rem;color:#888;margin-bottom:0.4rem;">${journals}</div>
      <ol style="font-size:0.82rem;padding-left:1.2rem;margin:0;">${articles}</ol>
    </div>`;
  }
  html += '</div>';
  tablesEl.innerHTML = html;
  tablesEl.style.display = 'block';
}

// ── Inline-handler globals ────────────────────────────────────
window.toggleAllCommJournals = toggleAllCommJournals;
window.updateCommJournalCount = updateCommJournalCount;
window.loadCommunities = loadCommunities;
