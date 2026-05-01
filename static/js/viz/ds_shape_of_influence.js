// static/js/viz/ds_shape_of_influence.js — Ch 5, Tool 8: The Shape of Influence
import { setLoading, setError, fetchJson, escapeHtml } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";

let _state = { journal: '' };

let _filtersWired_loadDsShapeOfInfluence = false;

let _exportWired_loadDsShapeOfInfluence = false;

async function loadDsShapeOfInfluence() {
  if (!_exportWired_loadDsShapeOfInfluence) {
    renderExportToolbar('tab-ds-shape-of-influence', { svgSelector: '#ds-shape-lorenz svg', dataProvider: () => (window.__dsShapeData && (window.__dsShapeData.frequency_table || [])) });
    _exportWired_loadDsShapeOfInfluence = true;
  }
  if (!_filtersWired_loadDsShapeOfInfluence) {
    renderFilterBar('tab-ds-shape-of-influence', {  onApply: () => loadDsShapeOfInfluence() });
    _filtersWired_loadDsShapeOfInfluence = true;
  }
  // Populate journal selector once
  const sel = document.getElementById('ds-shape-journal');
  if (sel && sel.options.length <= 1 && Array.isArray(window.ALL_JOURNALS)) {
    window.ALL_JOURNALS.forEach(j => {
      const o = document.createElement('option');
      o.value = j.name; o.textContent = j.name;
      sel.appendChild(o);
    });
  }
  setLoading('ds-shape-summary', 'Computing distribution stats…');
  try {
    const params = filterParams('tab-ds-shape-of-influence');
    if (_state.journal) params.set('journal', _state.journal);
    const qs = params.toString();
    const url = '/api/datastories/ch5-shape-of-influence' + (qs ? ('?' + qs) : '');
    const data = await fetchJson(url);
    window.__dsShapeData = data;
    renderSummary(data);
    renderLorenz(data);
    renderLogLog(data);
  } catch (e) {
    setError('ds-shape-summary', 'Failed to load: ' + e.message);
  }
}

function dsShapeReload() {
  _state.journal = document.getElementById('ds-shape-journal').value;
  loadDsShapeOfInfluence();
}

function renderSummary(data) {
  const c = data.concentration || {};
  const html = `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0.6rem;">
    ${card('Gini', data.gini.toFixed(3), 'inequality')}
    ${card('Articles', c.n_articles.toLocaleString(), c.n_zeros.toLocaleString() + ' with 0 cites')}
    ${card('Top 10 share', (c.top_10_share * 100).toFixed(1) + '%', '')}
    ${card('Top 1% share', (c.top_1pct_share * 100).toFixed(1) + '%', '')}
    ${card('Total citations', c.total_citations.toLocaleString(), '')}
  </div>`;
  document.getElementById('ds-shape-summary').innerHTML = html;
}

function card(label, big, sub) {
  return `<div style="padding:0.55rem 0.8rem;background:#fdfbf7;border-left:3px solid #5a3e28;font-size:0.84rem;">
    <div style="font-size:0.74rem;color:#9c9890;text-transform:uppercase;letter-spacing:0.04em;">${escapeHtml(label)}</div>
    <div style="font-size:1.3rem;font-weight:700;color:#3a3026;">${big}</div>
    ${sub ? `<div style="color:#7a7268;font-size:0.78rem;">${escapeHtml(sub)}</div>` : ''}
  </div>`;
}

function renderLorenz(data) {
  const el = d3.select('#ds-shape-lorenz');
  el.selectAll('*').remove();
  const w = el.node().clientWidth || 720, h = 340;
  const m = { top: 20, right: 30, bottom: 40, left: 50 };
  const svg = el.append('svg').attr('width', w).attr('height', h);

  const pa = data.lorenz.pct_articles, pc = data.lorenz.pct_citations;
  const x = d3.scaleLinear().domain([0, 1]).range([m.left, w - m.right]);
  const y = d3.scaleLinear().domain([0, 1]).range([h - m.bottom, m.top]);

  // Equality diagonal
  svg.append('line').attr('x1', x(0)).attr('y1', y(0)).attr('x2', x(1)).attr('y2', y(1))
    .attr('stroke', '#c8c4bc').attr('stroke-dasharray', '4 3');

  // Lorenz curve
  const data_pts = pa.map((p, i) => ({ x: p, y: pc[i] }));
  const line = d3.line().x(p => x(p.x)).y(p => y(p.y));
  const area = d3.area().x(p => x(p.x)).y0(p => y(p.x)).y1(p => y(p.y));
  svg.append('path').datum(data_pts).attr('d', area).attr('fill', '#b38a6a').attr('opacity', 0.25);
  svg.append('path').datum(data_pts).attr('d', line).attr('fill', 'none').attr('stroke', '#5a3e28').attr('stroke-width', 2);

  svg.append('g').attr('transform', `translate(0,${h - m.bottom})`)
    .call(d3.axisBottom(x).ticks(10).tickFormat(d3.format('.0%'))).selectAll('text').style('font-size', '10px');
  svg.append('g').attr('transform', `translate(${m.left},0)`)
    .call(d3.axisLeft(y).ticks(10).tickFormat(d3.format('.0%'))).selectAll('text').style('font-size', '10px');
  svg.append('text').attr('x', m.left).attr('y', 14).attr('font-size', 11).attr('fill', '#7a7268').text('Lorenz curve — % articles vs % of all citations');
}

function renderLogLog(data) {
  const el = d3.select('#ds-shape-loglog');
  el.selectAll('*').remove();
  const w = el.node().clientWidth || 720, h = 340;
  const m = { top: 20, right: 20, bottom: 40, left: 50 };
  const halfW = (w - m.left - m.right) / 2 - 10;
  const svg = el.append('svg').attr('width', w).attr('height', h);

  // Frequency table (left)
  const ft = (data.frequency_table || []).filter(d => d.count > 0 && d.n_articles > 0);
  const rf = (data.rank_frequency  || []).filter(d => d.count > 0);

  if (ft.length) {
    const x = d3.scaleLog().domain([1, d3.max(ft, d => d.count) || 1]).range([m.left, m.left + halfW]);
    const y = d3.scaleLog().domain([1, d3.max(ft, d => d.n_articles) || 1]).range([h - m.bottom, m.top]);
    svg.append('g').attr('transform', `translate(0,${h - m.bottom})`).call(d3.axisBottom(x).ticks(5, '~s')).selectAll('text').style('font-size','10px');
    svg.append('g').attr('transform', `translate(${m.left},0)`).call(d3.axisLeft(y).ticks(5, '~s')).selectAll('text').style('font-size','10px');
    svg.append('g').selectAll('circle').data(ft).join('circle')
      .attr('cx', d => x(d.count)).attr('cy', d => y(d.n_articles)).attr('r', 2.5).attr('fill', '#5a3e28').attr('opacity', 0.6);
    svg.append('text').attr('x', m.left).attr('y', 14).attr('font-size', 10).attr('fill', '#7a7268').text('Frequency: count of articles by # citations');
  }

  // Rank-frequency (right)
  if (rf.length) {
    const xr = d3.scaleLog().domain([1, d3.max(rf, d => d.rank) || 1]).range([m.left + halfW + 20, w - m.right]);
    const yr = d3.scaleLog().domain([1, d3.max(rf, d => d.count) || 1]).range([h - m.bottom, m.top]);
    svg.append('g').attr('transform', `translate(0,${h - m.bottom})`).call(d3.axisBottom(xr).ticks(5, '~s')).selectAll('text').style('font-size','10px');
    svg.append('g').attr('transform', `translate(${m.left + halfW + 20},0)`).call(d3.axisLeft(yr).ticks(5, '~s')).selectAll('text').style('font-size','10px');
    svg.append('g').selectAll('circle').data(rf).join('circle')
      .attr('cx', d => xr(d.rank)).attr('cy', d => yr(d.count)).attr('r', 2.5).attr('fill', '#3a5a28').attr('opacity', 0.6);
    svg.append('text').attr('x', m.left + halfW + 20).attr('y', 14).attr('font-size', 10).attr('fill', '#7a7268').text('Rank-frequency (Zipf)');
  }
}

window.loadDsShapeOfInfluence = loadDsShapeOfInfluence;
window.dsShapeReload = dsShapeReload;
