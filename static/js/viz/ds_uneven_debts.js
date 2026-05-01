// static/js/viz/ds_uneven_debts.js — Ch 7, Tool 20: Uneven Debts
import { setLoading, setError, fetchJson, escapeHtml, enableZoomPan } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";

let _filtersWired_loadDsUnevenDebts = false;

let _exportWired_loadDsUnevenDebts = false;

async function loadDsUnevenDebts() {
  if (!_exportWired_loadDsUnevenDebts) {
    renderExportToolbar('tab-ds-uneven-debts', { svgSelector: '#ds-ud-scatter svg', dataProvider: () => (window.__dsUdData && window.__dsUdData.pairs || []) });
    _exportWired_loadDsUnevenDebts = true;
  }
  if (!_filtersWired_loadDsUnevenDebts) {
    renderFilterBar('tab-ds-uneven-debts', {  onApply: () => loadDsUnevenDebts() });
    _filtersWired_loadDsUnevenDebts = true;
  }
  const loading = document.getElementById('ds-ud-loading');
  if (loading) loading.style.display = 'block';
  setLoading('ds-ud-scatter', 'Computing pairwise coupling and asymmetry…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-uneven-debts').toString(); return fetchJson('/api/datastories/ch7-uneven-debts' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsUdData = data;
    if (loading) loading.style.display = 'none';
    renderScatter(data);
    renderTable(data);
  } catch (e) {
    if (loading) loading.style.display = 'none';
    setError('ds-ud-scatter', 'Failed to load: ' + e.message);
  }
}

function renderScatter(data) {
  const el = d3.select('#ds-ud-scatter');
  el.selectAll('*').remove();
  const pairs = data.pairs || [];
  if (!pairs.length) { el.append('p').attr('class','explore-hint').text('No pairs.'); return; }

  const w = el.node().clientWidth || 720, h = 480;
  const m = { top: 30, right: 30, bottom: 50, left: 60 };
  const svg = el.append('svg').attr('width', w).attr('height', h);
  const root = enableZoomPan(svg, { scaleExtent: [0.5, 20] });

  const xMax = d3.max(pairs, p => p.ratio_a) || 0.001;
  const yMax = d3.max(pairs, p => p.ratio_b) || 0.001;
  const x = d3.scaleLinear().domain([0, xMax * 1.05]).range([m.left, w - m.right]);
  const y = d3.scaleLinear().domain([0, yMax * 1.05]).range([h - m.bottom, m.top]);

  // Diagonal: perfect symmetry
  const lim = Math.min(xMax, yMax);
  root.append('line').attr('x1', x(0)).attr('y1', y(0)).attr('x2', x(lim)).attr('y2', y(lim))
    .attr('stroke','#c8c4bc').attr('stroke-dasharray','4 3');

  root.append('g').attr('transform', `translate(0,${h - m.bottom})`).call(d3.axisBottom(x).tickFormat(d3.format('.0%')))
    .selectAll('text').style('font-size','10px');
  root.append('g').attr('transform', `translate(${m.left},0)`).call(d3.axisLeft(y).tickFormat(d3.format('.0%')))
    .selectAll('text').style('font-size','10px');
  root.append('text').attr('x', w/2).attr('y', h - 8).attr('text-anchor','middle').attr('font-size',11).attr('fill','#7a7268')
    .text('Coupling A→B (shared / refs in A)');
  root.append('text').attr('x', 14).attr('y', h/2).attr('transform', `rotate(-90, 14, ${h/2})`).attr('text-anchor','middle').attr('font-size',11).attr('fill','#7a7268')
    .text('Coupling B→A (shared / refs in B)');

  root.append('g').selectAll('circle').data(pairs).join('circle')
    .attr('cx', d => x(d.ratio_a))
    .attr('cy', d => y(d.ratio_b))
    .attr('r', d => 2 + Math.sqrt(d.shared))
    .attr('fill', '#5a3e28').attr('opacity', 0.55).attr('stroke', '#3a3026').attr('stroke-width', 0.4)
    .append('title').text(d => `${d.shared} shared refs\nA: ${(d.a_title || '#'+d.a)}\nB: ${(d.b_title || '#'+d.b)}\nasymmetry: ${d.asymmetry}`);
}

function renderTable(data) {
  const el = document.getElementById('ds-ud-table');
  const pairs = (data.pairs || []).slice(0, 25);
  if (!pairs.length) { el.innerHTML = ''; return; }

  let html = '<h4 class="methodology-heading">Most asymmetric pairs</h4>';
  html += '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
  html += '<thead><tr>';
  ['Dependent (depends on…)','Other party','Shared refs','Asymmetry'].forEach(h =>
    html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>');
  html += '</tr></thead><tbody>';
  pairs.forEach(p => {
    const dep = p.dependent === p.a ? 'a' : 'b';
    const oth = dep === 'a' ? 'b' : 'a';
    html += '<tr>';
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;"><a href="/article/${p[dep]}" style="color:#5a3e28;">${escapeHtml(p[dep+'_title'] || '#'+p[dep])}</a><div style="font-size:0.78rem;color:#9c9890;">${escapeHtml(p[dep+'_journal'] || '')} · ${p[dep+'_year'] || '—'}</div></td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;"><a href="/article/${p[oth]}" style="color:#5a3e28;">${escapeHtml(p[oth+'_title'] || '#'+p[oth])}</a><div style="font-size:0.78rem;color:#9c9890;">${escapeHtml(p[oth+'_journal'] || '')} · ${p[oth+'_year'] || '—'}</div></td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${p.shared}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;font-weight:600;">${(p.asymmetry * 100).toFixed(1)}%</td>`;
    html += '</tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

window.loadDsUnevenDebts = loadDsUnevenDebts;
