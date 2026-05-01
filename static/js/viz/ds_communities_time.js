// static/js/viz/ds_communities_time.js — Ch 6, Tool 14: Communities Over Time
import { setLoading, setError, fetchJson, escapeHtml } from "./_ds_common.js";
import { renderFilterBar, filterParams } from "./_ds_filters.js";
import { renderExportToolbar } from "./_ds_export.js";

let _filtersWired_loadDsCommunitiesTime = false;

let _exportWired_loadDsCommunitiesTime = false;

async function loadDsCommunitiesTime() {
  if (!_exportWired_loadDsCommunitiesTime) {
    renderExportToolbar('tab-ds-communities-time', { svgSelector: '#ds-ct-sankey svg', dataProvider: () => (window.__dsCtData && (window.__dsCtData.decades || []).flatMap(d => (d.communities || []).map(c => ({decade: d.label, modularity: d.modularity, rank: c.rank, size: c.size, top_journal: (c.top_journals[0]||[])[0], top_tag: (c.top_tags[0]||[])[0]})))) });
    _exportWired_loadDsCommunitiesTime = true;
  }
  if (!_filtersWired_loadDsCommunitiesTime) {
    renderFilterBar('tab-ds-communities-time', {  onApply: () => loadDsCommunitiesTime() });
    _filtersWired_loadDsCommunitiesTime = true;
  }
  const loading = document.getElementById('ds-ct-loading');
  if (loading) loading.style.display = 'block';
  setLoading('ds-ct-sankey', 'Running Louvain across decade windows (cached after first run)…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-communities-time').toString(); return fetchJson('/api/datastories/ch6-communities-time' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsCtData = data;
    if (loading) loading.style.display = 'none';
    renderSankey(data);
    renderDetail(data);
  } catch (e) {
    if (loading) loading.style.display = 'none';
    setError('ds-ct-sankey', 'Failed to load: ' + e.message);
  }
}

function renderSankey(data) {
  const el = d3.select('#ds-ct-sankey');
  el.selectAll('*').remove();
  const nodes = (data.sankey_nodes || []).map(n => ({...n}));
  const links = (data.sankey_links || []).map(l => ({...l}));
  if (!nodes.length || !links.length) {
    el.append('p').attr('class','explore-hint').text('Not enough cross-decade overlap to draw an alluvial flow.');
    return;
  }

  const w = el.node().clientWidth || 720, h = 540;

  // ── Color scheme: by top journal ──────────────────────────────────────
  // Every community's color is determined by its dominant journal so the
  // ribbons read as "this is the College Composition and Communication
  // strand, this is the Technical Communication Quarterly strand, ..."
  // rather than a meaningless hash.
  const journalsSeen = Array.from(new Set(nodes.map(n => n.top_journal || '—')));
  const palette = d3.schemeTableau10.concat(d3.schemeSet3);
  const journalColor = {};
  journalsSeen.forEach((j, i) => { journalColor[j] = palette[i % palette.length]; });
  const colorOfNode = d => journalColor[d.top_journal || '—'] || '#9c9890';

  // Reserve space on the right for a legend.
  const legendW = 220;
  const sankey = d3.sankey()
    .nodeWidth(15)
    .nodePadding(10)
    .extent([[10, 10], [w - legendW - 10, h - 10]]);
  const graph = sankey({ nodes, links });

  const svg = el.append('svg').attr('width', w).attr('height', h).style('font','11px system-ui,sans-serif');

  // Legend — list each journal with its swatch, sorted by total article count
  // across all communities that have that journal as their top journal.
  const journalSize = {};
  graph.nodes.forEach(n => {
    const j = n.top_journal || '—';
    journalSize[j] = (journalSize[j] || 0) + (n.size || 0);
  });
  const legendOrder = journalsSeen.slice().sort((a, b) => (journalSize[b] || 0) - (journalSize[a] || 0));

  const legend = svg.append('g').attr('transform', 'translate(' + (w - legendW + 4) + ', 10)');
  legend.append('text').attr('x', 0).attr('y', 12)
    .attr('font-size', 11).attr('font-weight', 600).attr('fill', '#3a3026')
    .text('Color = top journal of community');
  legendOrder.forEach((j, i) => {
    const ly = 26 + i * 16;
    legend.append('rect').attr('x', 0).attr('y', ly - 9).attr('width', 12).attr('height', 12)
      .attr('fill', journalColor[j]).attr('opacity', 0.85);
    const labelText = j.length > 32 ? j.slice(0, 30) + '…' : j;
    legend.append('text').attr('x', 18).attr('y', ly).attr('font-size', 10).attr('fill', '#3a3026').text(labelText)
      .append('title').text(j + ' — ' + (journalSize[j] || 0) + ' articles total');
  });

  // Links — coloured by their source community's top journal so a ribbon's
  // colour matches what flows "out of" that community.
  svg.append('g').selectAll('path').data(graph.links).join('path')
    .attr('d', d3.sankeyLinkHorizontal())
    .attr('stroke', d => colorOfNode(d.source))
    .attr('stroke-width', d => Math.max(1, d.width))
    .attr('fill','none').attr('stroke-opacity', 0.45)
    .append('title').text(d =>
      d.source.name + ' → ' + d.target.name +
      '\n' + (d.source.top_journal || '—') + ' → ' + (d.target.top_journal || '—') +
      '\nshared ' + d.value + ' articles, jaccard ' + d.jaccard);

  // Nodes — coloured the same way; tooltip shows top journal explicitly.
  const ng = svg.append('g').selectAll('g').data(graph.nodes).join('g');
  ng.append('rect').attr('x', d => d.x0).attr('y', d => d.y0)
    .attr('width', d => d.x1 - d.x0).attr('height', d => Math.max(2, d.y1 - d.y0))
    .attr('fill', colorOfNode).attr('opacity', 0.85)
    .append('title').text(d => d.name + ' (' + d.size + ' articles)\ntop journal: ' + (d.top_journal || '—'));

  // Inline label for each node — community label + the abbreviated top journal
  // so even without consulting the legend you can see what a strand represents.
  ng.append('text').attr('x', d => d.x0 < (w - legendW) / 2 ? d.x1 + 6 : d.x0 - 6)
    .attr('y', d => (d.y0 + d.y1) / 2).attr('dy', '0.35em')
    .attr('text-anchor', d => d.x0 < (w - legendW) / 2 ? 'start' : 'end')
    .attr('fill', '#3a3026').attr('font-size', 10)
    .text(d => {
      const tj = (d.top_journal || '').trim();
      const tjShort = tj.length > 22 ? tj.slice(0, 20) + '…' : tj;
      return d.name + (tjShort ? ' · ' + tjShort : '');
    });
}

function renderDetail(data) {
  const el = document.getElementById('ds-ct-detail');
  const decades = data.decades || [];
  if (!decades.length) { el.innerHTML = ''; return; }

  let html = '<h4 class="methodology-heading">Per-decade community detail</h4>';
  decades.forEach(d => {
    html += `<div style="margin:0.8rem 0;padding:0.6rem 0.8rem;background:#fdfbf7;border-left:3px solid #b38a6a;">
      <div style="font-weight:600;color:#3a3026;">${escapeHtml(d.label)}</div>
      <div style="font-size:0.78rem;color:#7a7268;">modularity ${d.modularity} · ${d.n_nodes} articles · ${d.n_edges} edges · ${d.communities.length} communities</div>`;
    if (d.communities.length) {
      html += '<table style="width:100%;border-collapse:collapse;font-size:0.82rem;margin-top:0.4rem;"><thead><tr>';
      ['#','Size','Top journal','Top tag','Top article'].forEach(h => html += '<th style="text-align:left;border-bottom:1px solid #e8e4de;padding:0.25rem 0.4rem;">' + h + '</th>');
      html += '</tr></thead><tbody>';
      d.communities.slice(0, 6).forEach(c => {
        const tj = (c.top_journals[0] && c.top_journals[0][0]) || '—';
        const tt = (c.top_tags[0] && c.top_tags[0][0]) || '—';
        const ta = c.top_articles[0];
        html += `<tr><td style="padding:0.2rem 0.4rem;">#${c.rank + 1}</td><td style="padding:0.2rem 0.4rem;">${c.size}</td><td style="padding:0.2rem 0.4rem;">${escapeHtml(tj)}</td><td style="padding:0.2rem 0.4rem;">${escapeHtml(tt)}</td><td style="padding:0.2rem 0.4rem;">${ta ? `<a href="/article/${ta.id}" style="color:#5a3e28;">${escapeHtml((ta.title || '#'+ta.id).slice(0, 50))}</a>` : '—'}</td></tr>`;
      });
      html += '</tbody></table>';
    }
    html += '</div>';
  });
  el.innerHTML = html;
}

window.loadDsCommunitiesTime = loadDsCommunitiesTime;
