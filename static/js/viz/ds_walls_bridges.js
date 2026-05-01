// static/js/viz/ds_walls_bridges.js — Ch 6, Tool 15: Walls and Bridges
import { setLoading, setError, fetchJson, escapeHtml } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";

const CLASS_COLOR = {
  insular:     "#a04525",
  moderate:    "#b38a6a",
  integrative: "#3a5a28",
};

let _filtersWired_loadDsWallsBridges = false;

let _exportWired_loadDsWallsBridges = false;

async function loadDsWallsBridges() {
  if (!_exportWired_loadDsWallsBridges) {
    renderExportToolbar('tab-ds-walls-bridges', { svgSelector: '#ds-wb-scatter svg', dataProvider: () => (window.__dsWbData && (window.__dsWbData.communities || []).map(c => ({rank: c.rank, n_articles: c.n_articles, internal_density: c.internal_density, external_density: c.external_density, insularity_ratio: c.insularity_ratio, classification: c.classification, top_journal: (c.top_journals[0]||[])[0], top_tag: (c.top_tags[0]||[])[0]}))) });
    _exportWired_loadDsWallsBridges = true;
  }
  if (!_filtersWired_loadDsWallsBridges) {
    renderFilterBar('tab-ds-walls-bridges', {  onApply: () => loadDsWallsBridges() });
    _filtersWired_loadDsWallsBridges = true;
  }
  const loading = document.getElementById('ds-wb-loading');
  if (loading) loading.style.display = 'block';
  setLoading('ds-wb-scatter', 'Computing community partition (cached after first run)…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-walls-bridges').toString(); return fetchJson('/api/datastories/ch6-walls-bridges' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsWbData = data;
    if (loading) loading.style.display = 'none';
    renderScatter(data);
    renderTable(data);
  } catch (e) {
    if (loading) loading.style.display = 'none';
    setError('ds-wb-scatter', 'Failed to load: ' + e.message);
  }
}

function renderScatter(data) {
  const el = d3.select('#ds-wb-scatter');
  el.selectAll('*').remove();
  const comms = data.communities || [];
  if (!comms.length) { el.append('p').attr('class','explore-hint').text('No communities.'); return; }

  const w = el.node().clientWidth || 720, h = 480;
  const m = { top: 30, right: 30, bottom: 50, left: 60 };
  const svg = el.append('svg').attr('width', w).attr('height', h);

  const xMax = d3.max(comms, c => c.internal_density) || 0.001;
  const yMax = d3.max(comms, c => c.external_density) || 0.0001;
  const x = d3.scaleLinear().domain([0, xMax * 1.1]).range([m.left, w - m.right]);
  const y = d3.scaleLinear().domain([0, yMax * 1.1]).range([h - m.bottom, m.top]);
  const r = d3.scaleSqrt().domain([0, d3.max(comms, c => c.n_articles) || 1]).range([4, 22]);

  svg.append('g').attr('transform', `translate(0,${h - m.bottom})`).call(d3.axisBottom(x).ticks(6, '~e'))
    .selectAll('text').style('font-size','10px');
  svg.append('g').attr('transform', `translate(${m.left},0)`).call(d3.axisLeft(y).ticks(6, '~e'))
    .selectAll('text').style('font-size','10px');
  svg.append('text').attr('x', w/2).attr('y', h - 8).attr('text-anchor','middle').attr('font-size',11).attr('fill','#7a7268')
    .text('Internal density (within-community edges per possible)');
  svg.append('text').attr('x', 14).attr('y', h/2).attr('transform', `rotate(-90, 14, ${h/2})`).attr('text-anchor','middle').attr('font-size',11).attr('fill','#7a7268')
    .text('External density (cross-community)');

  const dots = svg.append('g').selectAll('circle').data(comms).join('circle')
    .attr('cx', d => x(d.internal_density))
    .attr('cy', d => y(d.external_density))
    .attr('r', d => r(d.n_articles))
    .attr('fill', d => CLASS_COLOR[d.classification] || '#9c9890')
    .attr('opacity', 0.6).attr('stroke', '#3a3026').attr('stroke-width', 0.5)
    .on('mouseover', function(_, d) { dots.attr('opacity', 0.08); d3.select(this).attr('opacity', 1).attr('r', r(d.n_articles) * 1.4).raise(); })
    .on('mouseout',  function(_, d) { dots.attr('opacity', 0.6); d3.select(this).attr('r', r(d.n_articles)); });
  dots.append('title').text(d => 'Community #' + (d.rank + 1) + '  · ' + d.n_articles + ' articles\n' +
      'insularity ' + d.insularity_ratio + ' · ' + d.classification + '\n' +
      'top journal: ' + ((d.top_journals[0] && d.top_journals[0][0]) || '—'));

  // Legend
  const legend = svg.append('g').attr('transform', `translate(${w - m.right - 160}, ${m.top})`);
  Object.entries(CLASS_COLOR).forEach(([k, c], i) => {
    legend.append('circle').attr('cx', 8).attr('cy', i * 18 + 8).attr('r', 6).attr('fill', c).attr('opacity', 0.7);
    legend.append('text').attr('x', 22).attr('y', i * 18 + 12).attr('font-size', 11).text(k);
  });
}

function renderTable(data) {
  const el = document.getElementById('ds-wb-table');
  const rows = data.communities || [];
  if (!rows.length) { el.innerHTML = '<p class="explore-hint">No communities.</p>'; return; }

  let html = '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
  html += '<thead><tr>';
  ['#','Articles','Insularity','Class','Top journal','Top tag','Top article'].forEach(h =>
    html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>');
  html += '</tr></thead><tbody>';
  rows.forEach(r => {
    const topJournal = (r.top_journals[0] && r.top_journals[0][0]) || '—';
    const topTag = (r.top_tags[0] && r.top_tags[0][0]) || '—';
    const topArt = r.top_articles[0];
    html += '<tr>';
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">#${r.rank + 1}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${r.n_articles}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${r.insularity_ratio.toFixed(2)}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;color:${CLASS_COLOR[r.classification] || '#3a3026'};">${escapeHtml(r.classification || '—')}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${escapeHtml(topJournal)}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${escapeHtml(topTag)}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${topArt ? `<a href="/article/${topArt.id}" style="color:#5a3e28;">${escapeHtml(topArt.title || '#'+topArt.id)}</a>` : '—'}</td>`;
    html += '</tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

window.loadDsWallsBridges = loadDsWallsBridges;
