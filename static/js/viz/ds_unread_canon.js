// static/js/viz/ds_unread_canon.js — Ch 9, Tool 26: The Unread Canon
import { setLoading, setError, fetchJson, escapeHtml } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";
import { chrome } from "../utils/theme.js";

let _filtersWired_loadDsUnreadCanon = false;

let _exportWired_loadDsUnreadCanon = false;

async function loadDsUnreadCanon() {
  if (!_exportWired_loadDsUnreadCanon) {
    renderExportToolbar('tab-ds-unread-canon', { svgSelector: '#ds-uc-scatter svg', dataProvider: () => (window.__dsUcData && window.__dsUcData.articles || []) });
    _exportWired_loadDsUnreadCanon = true;
  }
  if (!_filtersWired_loadDsUnreadCanon) {
    renderFilterBar('tab-ds-unread-canon', {  onApply: () => loadDsUnreadCanon() });
    _filtersWired_loadDsUnreadCanon = true;
  }
  setLoading('ds-uc-summary', 'Identifying unread canon articles…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-unread-canon').toString(); return fetchJson('/api/datastories/ch9-unread-canon' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsUcData = data;
    renderSummary(data);
    renderScatter(data);
    renderTable(data);
  } catch (e) {
    setError('ds-uc-summary', 'Failed to load: ' + e.message);
  }
}

function renderSummary(data) {
  const s = data.summary || {};
  const html = `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:0.6rem;">
    <div style="padding:0.5rem 0.8rem;background:var(--chart-halo);border-left:3px solid #5a3e28;font-size:0.84rem;">
      <div style="font-size:0.74rem;color:var(--chart-muted);text-transform:uppercase;">Articles</div>
      <div style="font-size:1.3rem;font-weight:700;color:var(--chart-ink);">${(s.n || 0).toLocaleString()}</div>
      <div style="color:var(--chart-muted);font-size:0.78rem;">global ≥ ${s.min_global || 100}, internal ≤ ${s.max_internal || 2}</div>
    </div>
  </div>`;
  document.getElementById('ds-uc-summary').innerHTML = html;
}

function renderScatter(data) {
  const el = d3.select('#ds-uc-scatter');
  el.selectAll('*').remove();
  const arts = data.articles || [];
  if (!arts.length) { el.append('p').attr('class','explore-hint').text('No unread-canon articles found.'); return; }

  const w = el.node().clientWidth || 720, h = 480;
  const m = { top: 20, right: 30, bottom: 50, left: 60 };
  const svg = el.append('svg').attr('width', w).attr('height', h);

  const x = d3.scaleLog().domain([1, d3.max(arts, a => a.global_count) || 1]).range([m.left, w - m.right]);
  const y = d3.scaleLinear().domain([0, d3.max(arts, a => a.internal) + 1]).range([h - m.bottom, m.top]);

  svg.append('g').attr('transform', `translate(0,${h - m.bottom})`).call(d3.axisBottom(x).ticks(5, '~s'))
    .selectAll('text').style('font-size','10px');
  svg.append('g').attr('transform', `translate(${m.left},0)`).call(d3.axisLeft(y).ticks(3))
    .selectAll('text').style('font-size','10px');
  svg.append('text').attr('x', w/2).attr('y', h - 10).attr('text-anchor','middle').attr('font-size',11).attr('fill',chrome().muted)
    .text('Global cited-by count (log scale)');
  svg.append('text').attr('x', 14).attr('y', h/2).attr('transform', `rotate(-90, 14, ${h/2})`).attr('text-anchor','middle').attr('font-size',11).attr('fill',chrome().muted)
    .text('Internal corpus citations');

  const dots = svg.append('g').selectAll('circle').data(arts).join('circle')
    .attr('cx', d => x(d.global_count))
    .attr('cy', d => y(d.internal) + (Math.random() - 0.5) * 6)  // jitter
    .attr('r', 3.5).attr('fill', '#a04525').attr('opacity', 0.6).attr('stroke', chrome().ink).attr('stroke-width', 0.4)
    .style('cursor','pointer')
    .on('click', (e, d) => { window.location.href = '/article/' + d.id; })
    .on('mouseover', function() { dots.attr('opacity', 0.08); d3.select(this).attr('opacity', 1).attr('r', 6).raise(); })
    .on('mouseout',  function() { dots.attr('opacity', 0.6).attr('r', 3.5); });
  dots.append('title').text(d => (d.title || '#'+d.id) + '\n' + (d.journal || '') + ' (' + (d.year || '—') + ')\nglobal ' + d.global_count + ' / internal ' + d.internal);
}

function renderTable(data) {
  const el = document.getElementById('ds-uc-table');
  const rows = (data.articles || []).slice(0, 30);
  if (!rows.length) { el.innerHTML = ''; return; }
  let html = '<h4 class="methodology-heading">Top unread-canon articles</h4>';
  html += '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
  html += '<thead><tr>';
  ['#','Article','Year','Journal','Global','Internal','Ratio'].forEach(h => html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>');
  html += '</tr></thead><tbody>';
  rows.forEach((r, i) => {
    html += '<tr>';
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid var(--chart-grid);">${i + 1}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid var(--chart-grid);"><a href="/article/${r.id}" style="color:#5a3e28;">${escapeHtml(r.title || '#'+r.id)}</a></td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid var(--chart-grid);">${r.year || '—'}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid var(--chart-grid);">${escapeHtml(r.journal || '—')}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid var(--chart-grid);font-weight:600;">${r.global_count.toLocaleString()}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid var(--chart-grid);">${r.internal}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid var(--chart-grid);">${r.ratio.toFixed(0)}×</td>`;
    html += '</tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

window.loadDsUnreadCanon = loadDsUnreadCanon;
