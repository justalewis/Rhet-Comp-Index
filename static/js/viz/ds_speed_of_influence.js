// static/js/viz/ds_speed_of_influence.js — Ch 4, Tool 5: The Speed of Influence
import { setLoading, setError, fetchJson, escapeHtml } from "./_ds_common.js";
import { renderFilterBar, filterParams } from "./_ds_filters.js";
import { renderExportToolbar } from "./_ds_export.js";

const DIR_COLORS = {
  TPC_TO_TPC: "#5a3e28",
  TPC_TO_RC:  "#c08545",
  RC_TO_TPC:  "#3a5a28",
  RC_TO_RC:   "#608aac",
};

let _filtersWired_loadDsSpeedOfInfluence = false;

let _exportWired_loadDsSpeedOfInfluence = false;

async function loadDsSpeedOfInfluence() {
  if (!_exportWired_loadDsSpeedOfInfluence) {
    renderExportToolbar('tab-ds-speed-of-influence', { svgSelector: '#ds-soi-distributions svg', dataProvider: () => (window.__dsSoiData && Object.entries(window.__dsSoiData.stats || {}).filter(([,v]) => v).map(([dir, st]) => Object.assign({direction: dir}, st))) });
    _exportWired_loadDsSpeedOfInfluence = true;
  }
  if (!_filtersWired_loadDsSpeedOfInfluence) {
    renderFilterBar('tab-ds-speed-of-influence', {  onApply: () => loadDsSpeedOfInfluence() });
    _filtersWired_loadDsSpeedOfInfluence = true;
  }
  setLoading('ds-soi-summary', 'Computing delay distributions…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-speed-of-influence').toString(); return fetchJson('/api/datastories/ch4-speed-of-influence' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsSoiData = data;
    renderSummary(data);
    renderDistributions(data);
    renderTrends(data);
  } catch (e) {
    setError('ds-soi-summary', 'Failed to load: ' + e.message);
  }
}

function renderSummary(data) {
  const el = document.getElementById('ds-soi-summary');
  el.innerHTML = '';
  const grid = document.createElement('div');
  grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));gap:0.6rem;';
  Object.entries(data.stats || {}).forEach(([dir, st]) => {
    if (!st) return;
    const card = document.createElement('div');
    card.style.cssText = 'padding:0.6rem 0.9rem;background:#fdfbf7;border-left:3px solid ' + (DIR_COLORS[dir]||'#9c9890') + ';font-size:0.84rem;';
    card.innerHTML = '<div style="font-size:0.78rem;color:#9c9890;text-transform:uppercase;">'
      + escapeHtml(data.labels && data.labels[dir] || dir) + '</div>'
      + '<div style="font-size:1.2rem;font-weight:700;color:#3a3026;">median ' + st.median + ' yrs</div>'
      + '<div style="color:#7a7268;">mean ' + st.mean + ' · IQR ' + st.p25 + '–' + st.p75 + ' · n=' + st.count.toLocaleString() + '</div>';
    grid.appendChild(card);
  });
  el.appendChild(grid);

  const sig = data.significance || {};
  if (Object.keys(sig).length) {
    const note = document.createElement('div');
    note.style.cssText = 'margin-top:0.8rem;font-size:0.78rem;color:#7a7268;';
    note.innerHTML = '<strong>Mann-Whitney U:</strong> ' +
      Object.entries(sig).map(([k, v]) =>
        escapeHtml(k.replace(/_/g, ' ')) + ': p=' + v.p.toExponential(2)
      ).join('  ·  ');
    el.appendChild(note);
  }
}

function renderDistributions(data) {
  const el = d3.select('#ds-soi-distributions');
  el.selectAll('*').remove();
  const w = el.node().clientWidth || 720, h = 280;
  const m = { top: 20, right: 30, bottom: 40, left: 50 };
  const svg = el.append('svg').attr('width', w).attr('height', h);

  const dists = data.distributions || {};
  const x = d3.scaleLinear().domain([0, 50]).range([m.left, w - m.right]);
  const lines = [];
  Object.entries(dists).forEach(([dir, arr]) => {
    if (!arr || !arr.length) return;
    const total = arr.reduce((s, [, v]) => s + v, 0) || 1;
    const points = arr.map(([yr, v]) => ({ x: yr, y: v / total }));
    lines.push({ dir, points });
  });
  const ymax = d3.max(lines.flatMap(l => l.points.map(p => p.y))) || 0.1;
  const y = d3.scaleLinear().domain([0, ymax]).range([h - m.bottom, m.top]);

  svg.append('g').attr('transform', `translate(0,${h - m.bottom})`).call(d3.axisBottom(x).ticks(10))
    .selectAll('text').style('font-size', '10px');
  svg.append('g').attr('transform', `translate(${m.left},0)`).call(d3.axisLeft(y).ticks(5).tickFormat(d3.format('.1%')))
    .selectAll('text').style('font-size', '10px');

  const line = d3.line().x(p => x(p.x)).y(p => y(p.y)).curve(d3.curveMonotoneX);
  lines.forEach(l => {
    svg.append('path').datum(l.points).attr('fill', 'none')
      .attr('stroke', DIR_COLORS[l.dir] || '#9c9890').attr('stroke-width', 2.2).attr('opacity', 0.85).attr('d', line);
  });

  const legend = svg.append('g').attr('transform', `translate(${w - m.right - 180}, ${m.top})`);
  Object.entries(data.labels || {}).forEach(([dir, label], i) => {
    legend.append('line').attr('x1', 0).attr('x2', 16).attr('y1', i * 16 + 8).attr('y2', i * 16 + 8)
      .attr('stroke', DIR_COLORS[dir]).attr('stroke-width', 2.5);
    legend.append('text').attr('x', 22).attr('y', i * 16 + 11).attr('font-size', 11).attr('fill', '#3a3026').text(label);
  });

  svg.append('text').attr('x', w / 2).attr('y', h - 5).attr('text-anchor', 'middle').attr('font-size', 11).attr('fill', '#7a7268')
    .text('Citation delay (years)');
}

function renderTrends(data) {
  const el = d3.select('#ds-soi-trends');
  el.selectAll('*').remove();
  const w = el.node().clientWidth || 720, h = 280;
  const m = { top: 20, right: 30, bottom: 40, left: 50 };
  const svg = el.append('svg').attr('width', w).attr('height', h);

  const temporal = data.temporal || {};
  const allDecades = Array.from(new Set(
    Object.values(temporal).flatMap(d => Object.keys(d))
  )).sort();
  if (!allDecades.length) {
    svg.append('text').attr('x', m.left).attr('y', 40).attr('fill', '#9c9890').text('Not enough data for decade trends.');
    return;
  }
  const x = d3.scalePoint().domain(allDecades).range([m.left, w - m.right]).padding(0.5);
  const ymax = d3.max(Object.values(temporal).flatMap(d => Object.values(d))) || 10;
  const y = d3.scaleLinear().domain([0, ymax * 1.1]).range([h - m.bottom, m.top]);

  svg.append('g').attr('transform', `translate(0,${h - m.bottom})`).call(d3.axisBottom(x))
    .selectAll('text').style('font-size', '10px');
  svg.append('g').attr('transform', `translate(${m.left},0)`).call(d3.axisLeft(y).ticks(5))
    .selectAll('text').style('font-size', '10px');

  const line = d3.line().x(d => x(d.dec)).y(d => y(d.mean)).curve(d3.curveMonotoneX);
  Object.entries(temporal).forEach(([dir, decades]) => {
    const points = Object.entries(decades).map(([dec, mean]) => ({ dec, mean })).sort((a, b) => a.dec.localeCompare(b.dec));
    if (!points.length) return;
    svg.append('path').datum(points).attr('fill', 'none')
      .attr('stroke', DIR_COLORS[dir] || '#9c9890').attr('stroke-width', 2.2).attr('d', line);
    points.forEach(p => {
      svg.append('circle').attr('cx', x(p.dec)).attr('cy', y(p.mean)).attr('r', 3).attr('fill', DIR_COLORS[dir] || '#9c9890');
    });
  });

  svg.append('text').attr('x', m.left).attr('y', 14).attr('font-size', 11).attr('fill', '#7a7268')
    .text('Mean delay (years), per citing decade');
}

window.loadDsSpeedOfInfluence = loadDsSpeedOfInfluence;
