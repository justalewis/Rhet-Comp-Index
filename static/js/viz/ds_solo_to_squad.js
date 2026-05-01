// static/js/viz/ds_solo_to_squad.js — Ch 8, Tool 21: Solo to Squad
import { setLoading, setError, fetchJson, escapeHtml } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";

let _filtersWired_loadDsSoloToSquad = false;

let _exportWired_loadDsSoloToSquad = false;

async function loadDsSoloToSquad() {
  if (!_exportWired_loadDsSoloToSquad) {
    renderExportToolbar('tab-ds-solo-to-squad', { svgSelector: '#ds-st-trends svg', dataProvider: () => (window.__dsStData && window.__dsStData.yearly || []) });
    _exportWired_loadDsSoloToSquad = true;
  }
  if (!_filtersWired_loadDsSoloToSquad) {
    renderFilterBar('tab-ds-solo-to-squad', {  onApply: () => loadDsSoloToSquad() });
    _filtersWired_loadDsSoloToSquad = true;
  }
  setLoading('ds-st-trends', 'Computing team-size trends…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-solo-to-squad').toString(); return fetchJson('/api/datastories/ch8-solo-to-squad' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsStData = data;
    renderTrends(data);
    renderTable(data);
  } catch (e) {
    setError('ds-st-trends', 'Failed to load: ' + e.message);
  }
}

function renderTrends(data) {
  const el = d3.select('#ds-st-trends');
  el.selectAll('*').remove();
  const ys = data.yearly || [];
  if (!ys.length) { el.append('p').attr('class','explore-hint').text('No data.'); return; }

  const w = el.node().clientWidth || 720, h = 340;
  const m = { top: 30, right: 60, bottom: 40, left: 50 };
  const svg = el.append('svg').attr('width', w).attr('height', h);

  const x = d3.scaleLinear().domain(d3.extent(ys, d => d.year)).range([m.left, w - m.right]);
  const yL = d3.scaleLinear().domain([0, d3.max(ys, d => d.mean_authors) * 1.1]).range([h - m.bottom, m.top]);
  const yR = d3.scaleLinear().domain([0, 1]).range([h - m.bottom, m.top]);

  // Single-author share area (right axis)
  const area = d3.area().x(d => x(d.year)).y0(yR(0)).y1(d => yR(d.single_author_pct));
  svg.append('path').datum(ys).attr('d', area).attr('fill', '#d4cec5').attr('opacity', 0.5);

  // Mean / median lines (left axis)
  const lineMean = d3.line().x(d => x(d.year)).y(d => yL(d.mean_authors)).curve(d3.curveMonotoneX);
  const lineMed = d3.line().x(d => x(d.year)).y(d => yL(d.median)).curve(d3.curveMonotoneX);
  svg.append('path').datum(ys).attr('d', lineMean).attr('fill', 'none').attr('stroke', '#5a3e28').attr('stroke-width', 2);
  svg.append('path').datum(ys).attr('d', lineMed).attr('fill', 'none').attr('stroke', '#3a5a28').attr('stroke-width', 2).attr('stroke-dasharray', '5 3');

  // Milestones
  (data.milestones || []).forEach(ms => {
    const xm = x(ms.year);
    if (xm < m.left || xm > w - m.right) return;
    svg.append('line').attr('x1', xm).attr('x2', xm).attr('y1', m.top).attr('y2', h - m.bottom)
      .attr('stroke', '#a04525').attr('stroke-dasharray', '2 3').attr('opacity', 0.6);
    svg.append('text').attr('x', xm + 4).attr('y', m.top + 12).attr('font-size', 10).attr('fill', '#a04525').text(ms.event);
  });

  svg.append('g').attr('transform', `translate(0,${h - m.bottom})`).call(d3.axisBottom(x).tickFormat(d3.format('d'))).selectAll('text').style('font-size','10px');
  svg.append('g').attr('transform', `translate(${m.left},0)`).call(d3.axisLeft(yL)).selectAll('text').style('font-size','10px');
  svg.append('g').attr('transform', `translate(${w - m.right},0)`).call(d3.axisRight(yR).tickFormat(d3.format('.0%'))).selectAll('text').style('font-size','10px');

  svg.append('text').attr('x', m.left).attr('y', 14).attr('font-size', 11).attr('fill', '#7a7268')
    .text('Mean (solid) and median (dashed) authors per article — single-author share (grey area)');
}

function renderTable(data) {
  const el = document.getElementById('ds-st-table');
  const rows = data.largest_teams || [];
  if (!rows.length) { el.innerHTML = ''; return; }
  let html = '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
  html += '<thead><tr>';
  ['Authors','Year','Article','Journal'].forEach(h => html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>');
  html += '</tr></thead><tbody>';
  rows.forEach(r => {
    html += '<tr>';
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;font-weight:600;">${r.n_authors}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${r.year}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;"><a href="/article/${r.id}" style="color:#5a3e28;">${escapeHtml(r.title || '#'+r.id)}</a></td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${escapeHtml(r.journal || '—')}</td>`;
    html += '</tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

window.loadDsSoloToSquad = loadDsSoloToSquad;
