// static/js/viz/reading_path.js
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


let rpData = null;       // full response from /api/citations/reading-path
let rpSeedId = null;     // selected seed article id
let rpSvg = null;        // D3 svg selection
let rpSimulation = null; // D3 force simulation
let rpDebounce = null;   // search debounce timer

const RP_COLORS = {
  seed:     '#e74c3c',
  cites:    '#3498db',
  cited_by: '#2ecc71',
  cocited:  '#e67e22',
  coupled:  '#9b59b6',
};


function initReadingPath() {
  // Wire the shared SVG/PNG/CSV export toolbar. CSV flattens the four
  // relationship buckets (cites, cited_by, cocited, coupled) plus the
  // seed into a single rows array, so the export reflects the assembled
  // path rather than just one bucket.
  renderExportToolbar('tab-readingpath', {
    svgSelector: '#rp-graph-container svg',
    dataProvider: () => {
      const d = window.__expReadingPath;
      if (!d) return [];
      const rows = [];
      if (d.seed) rows.push(Object.assign({ relation: 'seed' }, d.seed));
      ['cites', 'cited_by', 'cocited', 'coupled'].forEach(rel => {
        (d[rel] || []).forEach(a => rows.push(Object.assign({ relation: rel }, a)));
      });
      return rows;
    },
  });

  const searchInput = document.getElementById('rp-search');
  const acBox = document.getElementById('rp-autocomplete');
  const clearBtn = document.getElementById('rp-clear-btn');
  const buildBtn = document.getElementById('rp-build-btn');

  // Debounced autocomplete search
  searchInput.addEventListener('input', function () {
    clearTimeout(rpDebounce);
    const q = this.value.trim();
    if (q.length < 2) { acBox.style.display = 'none'; return; }
    rpDebounce = setTimeout(() => rpSearch(q), 250);
  });

  // Close autocomplete on outside click
  document.addEventListener('click', function (e) {
    if (!e.target.closest('#rp-search') && !e.target.closest('#rp-autocomplete')) {
      acBox.style.display = 'none';
    }
  });

  // Close autocomplete on Escape
  searchInput.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') acBox.style.display = 'none';
  });

  clearBtn.addEventListener('click', rpClearSeed);
  buildBtn.addEventListener('click', rpBuild);

  // Export buttons
  document.getElementById('rp-export-bibtex').addEventListener('click', rpExportBibtex);
  document.getElementById('rp-export-text').addEventListener('click', rpExportText);

  // Check if we have a seed from URL parameter
  const urlParams = new URLSearchParams(location.search);
  const seedParam = urlParams.get('seed');
  if (seedParam) {
    rpSeedId = parseInt(seedParam);
    // Fetch seed info and auto-build
    rpBuildFromId(rpSeedId);
  }
}

async function rpSearch(q) {
  const acBox = document.getElementById('rp-autocomplete');
  try {
    const resp = await fetch('/api/articles/search?q=' + encodeURIComponent(q) + '&limit=8');
    const results = await resp.json();
    if (!results.length) {
      acBox.innerHTML = '<div style="padding:0.6rem 0.8rem;color:#8b6045;font-size:0.85rem;">No results found</div>';
      acBox.style.display = 'block';
      return;
    }
    acBox.innerHTML = results.map(a => `
      <div class="rp-ac-item" data-id="${a.id}" style="padding:0.5rem 0.8rem;cursor:pointer;border-bottom:1px solid #f0ebe3;transition:background 0.15s;">
        <div style="font-weight:600;font-size:0.88rem;color:#3a2a18;">${escHtml(a.title || '(no title)')}</div>
        <div style="font-size:0.78rem;color:#8b6045;">${escHtml(a.authors || '')}${a.pub_date ? ' · ' + a.pub_date.slice(0, 4) : ''} · ${escHtml(a.journal || '')}</div>
      </div>
    `).join('');
    acBox.style.display = 'block';

    // Wire click handlers
    acBox.querySelectorAll('.rp-ac-item').forEach(item => {
      item.addEventListener('mouseenter', () => item.style.background = '#f5f0e8');
      item.addEventListener('mouseleave', () => item.style.background = '');
      item.addEventListener('click', () => {
        const id = parseInt(item.getAttribute('data-id'));
        const art = results.find(r => r.id === id);
        if (art) rpSelectSeed(art);
        acBox.style.display = 'none';
      });
    });
  } catch (e) {
    console.error('Reading path search error:', e);
  }
}

function rpSelectSeed(article) {
  rpSeedId = article.id;
  document.getElementById('rp-search').value = '';
  document.getElementById('rp-seed-title').textContent = article.title || '(no title)';
  document.getElementById('rp-seed-authors').textContent = article.authors || '';
  const meta = [article.journal, article.pub_date ? article.pub_date.slice(0, 4) : ''].filter(Boolean).join(' · ');
  document.getElementById('rp-seed-meta').textContent = meta;
  document.getElementById('rp-seed-card').style.display = 'block';
  document.getElementById('rp-results').style.display = 'none';
}

function rpClearSeed() {
  rpSeedId = null;
  rpData = null;
  document.getElementById('rp-seed-card').style.display = 'none';
  document.getElementById('rp-results').style.display = 'none';
  document.getElementById('rp-search').value = '';
  document.getElementById('rp-search').focus();
  if (rpSimulation) { rpSimulation.stop(); rpSimulation = null; }
}

async function rpBuildFromId(articleId) {
  // Auto-build when seed comes from URL param or cross-link
  rpSeedId = articleId;
  document.getElementById('rp-loading').style.display = 'block';
  document.getElementById('rp-results').style.display = 'none';
  document.getElementById('rp-seed-card').style.display = 'none';

  try {
    const resp = await fetch('/api/citations/reading-path?article=' + articleId);
    rpData = await resp.json();
    window.__expReadingPath = rpData;
    if (rpData.error) {
      document.getElementById('rp-loading').style.display = 'none';
      alert('Error: ' + rpData.error);
      return;
    }
    // Show seed card
    rpSelectSeed(rpData.seed);
    document.getElementById('rp-loading').style.display = 'none';
    rpRenderResults();
  } catch (e) {
    document.getElementById('rp-loading').style.display = 'none';
    console.error('Reading path build error:', e);
  }
}

async function rpBuild() {
  if (!rpSeedId) return;
  document.getElementById('rp-loading').style.display = 'block';
  document.getElementById('rp-results').style.display = 'none';
  document.getElementById('rp-build-btn').disabled = true;

  try {
    const resp = await fetch('/api/citations/reading-path?article=' + rpSeedId);
    rpData = await resp.json();
    window.__expReadingPath = rpData;
    if (rpData.error) {
      document.getElementById('rp-loading').style.display = 'none';
      document.getElementById('rp-build-btn').disabled = false;
      alert('Error: ' + rpData.error);
      return;
    }
    document.getElementById('rp-loading').style.display = 'none';
    document.getElementById('rp-build-btn').disabled = false;
    rpRenderResults();
  } catch (e) {
    document.getElementById('rp-loading').style.display = 'none';
    document.getElementById('rp-build-btn').disabled = false;
    console.error('Reading path build error:', e);
  }
}

function rpRenderResults() {
  if (!rpData) return;
  const stats = rpData.stats;

  // Stats bar
  const statsEl = document.getElementById('rp-stats');
  statsEl.innerHTML = [
    `<span><strong>${stats.unique_articles}</strong> related articles</span>`,
    `<span><strong>${stats.cites_count}</strong> backward citations</span>`,
    `<span><strong>${stats.cited_by_count}</strong> forward citations</span>`,
    `<span><strong>${stats.cocited_count}</strong> co-cited</span>`,
    `<span><strong>${stats.coupled_count}</strong> bib. coupled</span>`,
  ].join('');

  document.getElementById('rp-results').style.display = 'block';

  // Show graph by default
  rpShowView('graph', document.querySelector('#rp-results .explore-tab.active'));
  rpRenderGraph();
  rpRenderList(rpData.reading_list);
}

function rpShowView(view, btn) {
  // Toggle view between graph and list
  const parent = document.getElementById('rp-results');
  parent.querySelectorAll('.explore-tab').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  document.getElementById('rp-graph-view').style.display = view === 'graph' ? 'block' : 'none';
  document.getElementById('rp-list-view').style.display  = view === 'list'  ? 'block' : 'none';
}

function rpRenderGraph() {
  if (!rpData || !rpData.graph) return;
  const container = document.getElementById('rp-graph-container');
  container.innerHTML = '';

  const width = container.clientWidth;
  const height = container.clientHeight || 560;
  const graph = rpData.graph;

  if (!graph.nodes || graph.nodes.length === 0) {
    container.innerHTML = '<p class="explore-hint" style="padding:2rem;">No citation relationships found for this article.</p>';
    return;
  }

  const svg = d3.select(container).append('svg')
    .attr('width', width).attr('height', height)
    .style('font-family', 'system-ui, sans-serif');
  rpSvg = svg;

  const g = enableZoomPan(svg, { scaleExtent: [0.2, 5] });

  // Determine node color based on primary relationship type
  function nodeColor(d) {
    if (d.is_seed) return RP_COLORS.seed;
    const types = d.rel_types || [];
    if (types.length === 1) return RP_COLORS[types[0]] || '#999';
    // Multiple types — use the "strongest" one
    const priority = ['cites', 'cited_by', 'cocited', 'coupled'];
    for (const t of priority) {
      if (types.includes(t)) return RP_COLORS[t];
    }
    return '#999';
  }

  // Node radius based on score
  function nodeRadius(d) {
    if (d.is_seed) return 14;
    const s = d.score || 1;
    return Math.max(5, Math.min(12, 3 + s * 1.2));
  }

  // Edge color based on type
  function edgeColor(d) {
    return RP_COLORS[d.type] || '#ccc';
  }

  // Build simulation
  if (rpSimulation) rpSimulation.stop();

  // Create node map for quick lookup
  const nodeMap = {};
  graph.nodes.forEach(n => { nodeMap[n.id] = n; });

  // Ensure links reference actual node objects
  const links = graph.links.filter(l => {
    const src = typeof l.source === 'object' ? l.source.id : l.source;
    const tgt = typeof l.target === 'object' ? l.target.id : l.target;
    return nodeMap[src] && nodeMap[tgt];
  });

  rpSimulation = d3.forceSimulation(graph.nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(80))
    .force('charge', d3.forceManyBody().strength(-200))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius(d => nodeRadius(d) + 2));

  // Draw edges
  const link = g.append('g')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('stroke', edgeColor)
    .attr('stroke-opacity', 0.35)
    .attr('stroke-width', d => Math.max(1, Math.min(3, d.weight || 1)));

  // Draw nodes
  const node = g.append('g')
    .selectAll('circle')
    .data(graph.nodes)
    .join('circle')
    .attr('r', nodeRadius)
    .attr('fill', nodeColor)
    .attr('stroke', d => d.is_seed ? '#c0392b' : '#fff')
    .attr('stroke-width', d => d.is_seed ? 3 : 1.5)
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => {
        if (!event.active) rpSimulation.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end', (event, d) => {
        if (!event.active) rpSimulation.alphaTarget(0);
        d.fx = null; d.fy = null;
      })
    );

  // Labels for seed and high-score nodes
  const labels = g.append('g')
    .selectAll('text')
    .data(graph.nodes.filter(d => d.is_seed || (d.score || 0) >= 6))
    .join('text')
    .attr('font-size', d => d.is_seed ? '11px' : '9px')
    .attr('font-weight', d => d.is_seed ? '700' : '400')
    .attr('fill', '#3a2a18')
    .attr('text-anchor', 'middle')
    .attr('dy', d => -(nodeRadius(d) + 4))
    .text(d => {
      const t = d.title || '';
      return t.length > 40 ? t.slice(0, 38) + '…' : t;
    });

  // Tooltip
  const tooltip = document.getElementById('rp-graph-tooltip');

  node.on('mouseover', (event, d) => {
    tooltip.innerHTML = `
      <div style="font-weight:600;margin-bottom:0.2rem;">${escHtml(d.title || '')}</div>
      <div style="font-size:0.78rem;color:#8b6045;">${escHtml(d.authors || '')}</div>
      <div style="font-size:0.78rem;color:#8b6045;">${escHtml(d.journal || '')}${d.pub_date ? ' · ' + d.pub_date.slice(0, 4) : ''}</div>
      ${d.is_seed ? '<div style="margin-top:0.2rem;font-weight:600;color:#e74c3c;">Seed article</div>' :
        `<div style="margin-top:0.2rem;font-size:0.78rem;">Score: ${d.score || 0} · ${(d.rel_types || []).join(', ')}</div>`
      }
    `;
    tooltip.style.display = 'block';
  })
  .on('mousemove', (event) => {
    const rect = container.getBoundingClientRect();
    tooltip.style.left = (event.clientX - rect.left + 12) + 'px';
    tooltip.style.top  = (event.clientY - rect.top - 10) + 'px';
  })
  .on('mouseout', () => { tooltip.style.display = 'none'; })
  .on('click', (event, d) => {
    window.open('/article/' + d.id, '_blank');
  });

  // Tick
  rpSimulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    node
      .attr('cx', d => d.x).attr('cy', d => d.y);
    labels
      .attr('x', d => d.x).attr('y', d => d.y);
  });

  // Fit view after simulation settles
  rpSimulation.on('end', () => {
    const bounds = g.node().getBBox();
    if (bounds.width > 0 && bounds.height > 0) {
      const pad = 40;
      const scale = Math.min(
        (width - pad * 2) / bounds.width,
        (height - pad * 2) / bounds.height,
        1.5
      );
      const tx = width / 2 - (bounds.x + bounds.width / 2) * scale;
      const ty = height / 2 - (bounds.y + bounds.height / 2) * scale;
      svg.transition().duration(600)
        .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
    }
  });
}

function rpRenderList(list, filter) {
  const container = document.getElementById('rp-list-container');
  const filtered = filter && filter !== 'all'
    ? list.filter(a => a.rel_types && a.rel_types.includes(filter))
    : list;

  if (!filtered.length) {
    container.innerHTML = '<p class="explore-hint">No articles match this filter.</p>';
    return;
  }

  container.innerHTML = filtered.map((a, i) => {
    const relBadges = (a.rel_types || []).map(t =>
      `<span style="display:inline-block;padding:0.1rem 0.4rem;border-radius:3px;font-size:0.72rem;color:#fff;background:${RP_COLORS[t] || '#999'};margin-right:0.3rem;">${t.replace('_', ' ')}</span>`
    ).join('');

    return `
    <div class="rp-list-item" style="padding:0.7rem 0;border-bottom:1px solid #ede8e0;${i === 0 ? 'border-top:1px solid #ede8e0;' : ''}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;">
        <div style="flex:1;min-width:0;">
          <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.2rem;">
            <span style="font-weight:700;color:#8b6045;font-size:0.82rem;">#${i + 1}</span>
            <span style="font-size:0.78rem;color:#5a3e28;">score ${a.score}</span>
            ${relBadges}
          </div>
          <a href="/article/${a.id}" style="font-weight:600;color:#3a2a18;text-decoration:none;font-size:0.95rem;" target="_blank">${escHtml(a.title || '(no title)')}</a>
          <div style="font-size:0.82rem;color:#5a3e28;margin-top:0.15rem;">${escHtml(a.authors || '')}</div>
          <div style="font-size:0.78rem;color:#8b6045;margin-top:0.1rem;">
            ${escHtml(a.journal || '')}${a.pub_date ? ' · ' + a.pub_date.slice(0, 4) : ''}
            ${a.internal_cited_by_count ? ' · cited ' + a.internal_cited_by_count + '×' : ''}
          </div>
          <div style="font-size:0.78rem;color:#8b6045;margin-top:0.15rem;font-style:italic;">${escHtml(a.reason || '')}</div>
        </div>
      </div>
    </div>`;
  }).join('');
}

function rpFilterList(filter, btn) {
  document.querySelectorAll('.rp-filter-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  if (rpData && rpData.reading_list) {
    rpRenderList(rpData.reading_list, filter);
  }
}

function rpExportBibtex() {
  if (!rpData || !rpData.reading_list) return;
  const lines = [];
  rpData.reading_list.forEach(a => {
    const lastWord = (a.authors || 'unknown').split(';')[0].trim().split(/\s+/).pop().replace(/[^a-z0-9]/gi, '').toLowerCase() || 'unknown';
    const year = (a.pub_date || '').slice(0, 4);
    const firstTitleWord = (a.title || 'untitled').split(/\s+/)[0].replace(/[^a-z0-9]/gi, '').toLowerCase() || 'untitled';
    const key = lastWord + year + firstTitleWord;
    const authors = (a.authors || '').split(';').map(s => s.trim()).filter(Boolean).join(' and ');
    const title = (a.title || '').replace(/[{}]/g, '');
    lines.push(`@article{${key},`);
    if (authors) lines.push(`  author  = {${authors}},`);
    lines.push(`  title   = {${title}},`);
    if (a.journal) lines.push(`  journal = {${a.journal}},`);
    if (year) lines.push(`  year    = {${year}},`);
    if (a.doi) lines.push(`  doi     = {${a.doi}},`);
    if (a.url) lines.push(`  url     = {${a.url}},`);
    lines.push('}');
    lines.push('');
  });
  rpDownloadFile('reading-path.bib', lines.join('\n'), 'application/x-bibtex');
}

function rpExportText() {
  if (!rpData || !rpData.reading_list) return;
  const lines = [`Reading Path — ${rpData.seed.title}`, `Seed: ${rpData.seed.authors || ''} (${(rpData.seed.pub_date || '').slice(0, 4)})`, ''];
  rpData.reading_list.forEach((a, i) => {
    lines.push(`${i + 1}. ${a.title || '(no title)'}`);
    lines.push(`   ${a.authors || ''}`);
    lines.push(`   ${a.journal || ''}${a.pub_date ? ', ' + a.pub_date.slice(0, 4) : ''}`);
    lines.push(`   Relevance: ${a.score} — ${a.reason || ''}`);
    if (a.doi) lines.push(`   DOI: ${a.doi}`);
    lines.push('');
  });
  rpDownloadFile('reading-path.txt', lines.join('\n'), 'text/plain');
}

function rpDownloadFile(filename, content, mime) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function escHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ── Inline-handler globals ────────────────────────────────────
window.rpShowView = rpShowView;
window.rpFilterList = rpFilterList;

// ── Inline-handler globals ────────────────────────────────────
window.initReadingPath = initReadingPath;
