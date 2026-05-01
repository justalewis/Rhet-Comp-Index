// static/js/viz/ds_inside_outside.js — Ch 5, Tool 13: Inside and Outside
import { setLoading, setError, fetchJson, escapeHtml, enableZoomPan } from "./_ds_common.js";
import { renderFilterBar, filterParams } from "./_ds_filters.js";
import { renderExportToolbar } from "./_ds_export.js";

const QUAD_COLOR = {
  shared_canon: "#3a5a28",
  tpc_specific: "#5a3e28",
  imported:     "#a04525",
  background:   "#d4cec5",
};

let _filtersWired_loadDsInsideOutside = false;

let _exportWired_loadDsInsideOutside = false;

async function loadDsInsideOutside() {
  if (!_exportWired_loadDsInsideOutside) {
    renderExportToolbar('tab-ds-inside-outside', { svgSelector: '#ds-io-scatter svg', dataProvider: () => (window.__dsIoData && window.__dsIoData.articles || []) });
    _exportWired_loadDsInsideOutside = true;
  }
  if (!_filtersWired_loadDsInsideOutside) {
    renderFilterBar('tab-ds-inside-outside', {  onApply: () => loadDsInsideOutside() });
    _filtersWired_loadDsInsideOutside = true;
  }
  setLoading('ds-io-summary', 'Computing internal vs global rankings…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-inside-outside').toString(); return fetchJson('/api/datastories/ch5-inside-outside' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsIoData = data;
    renderSummary(data);
    renderScatter(data);
    renderTable(data);
  } catch (e) {
    setError('ds-io-summary', 'Failed to load: ' + e.message);
  }
}

function renderSummary(data) {
  const s = data.summary || {};
  const sp = s.spearman || {};
  const html = `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:0.6rem;">
    ${card('Articles', (s.n || 0).toLocaleString(), '')}
    ${card('Spearman ρ', sp.rho != null ? sp.rho.toFixed(3) : '—', sp.p != null ? 'p=' + sp.p.toExponential(2) : '')}
    ${Object.entries(s.quadrants || {}).map(([k, n]) =>
      card(k.replace(/_/g, ' '), n.toLocaleString(), '', QUAD_COLOR[k])).join('')}
  </div>`;
  document.getElementById('ds-io-summary').innerHTML = html;
}

function card(label, big, sub, color) {
  return `<div style="padding:0.5rem 0.8rem;background:#fdfbf7;border-left:3px solid ${color || '#5a3e28'};font-size:0.84rem;">
    <div style="font-size:0.74rem;color:#9c9890;text-transform:uppercase;letter-spacing:0.04em;">${escapeHtml(label)}</div>
    <div style="font-size:1.2rem;font-weight:700;color:#3a3026;">${big}</div>
    ${sub ? `<div style="color:#7a7268;font-size:0.78rem;">${escapeHtml(sub)}</div>` : ''}
  </div>`;
}

function renderScatter(data) {
  const el = d3.select('#ds-io-scatter');
  el.selectAll('*').remove();
  // Sample down to <= 1500 points for performance
  const arts = (data.articles || []).filter(a => a.internal > 0 || a.global_count > 0);
  const sample = arts.length > 1500 ? arts.slice(0, 1500) : arts;
  if (!sample.length) { el.append('p').attr('class','explore-hint').text('No data.'); return; }

  const w = el.node().clientWidth || 720, h = 480;
  const m = { top: 20, right: 30, bottom: 40, left: 60 };
  const svg = el.append('svg').attr('width', w).attr('height', h);

  // 1500-point scatter — zoom + pan lets the user inspect dense regions.
  const root = enableZoomPan(svg, { scaleExtent: [0.5, 30] });

  const x = d3.scaleLog().domain([1, d3.max(sample, a => a.global_rank) || 1]).range([m.left, w - m.right]);
  const y = d3.scaleLog().domain([1, d3.max(sample, a => a.internal_rank) || 1]).range([m.top, h - m.bottom]);

  // Diagonal where ranks match
  const minR = 1;
  const maxR = Math.min(d3.max(sample, a => a.global_rank), d3.max(sample, a => a.internal_rank));
  root.append('line').attr('x1', x(minR)).attr('y1', y(minR)).attr('x2', x(maxR)).attr('y2', y(maxR))
    .attr('stroke', '#c8c4bc').attr('stroke-dasharray', '4 3');

  root.append('g').attr('transform', `translate(0,${h - m.bottom})`).call(d3.axisBottom(x).ticks(5, '~s'))
    .selectAll('text').style('font-size','10px');
  root.append('g').attr('transform', `translate(${m.left},0)`).call(d3.axisLeft(y).ticks(5, '~s'))
    .selectAll('text').style('font-size','10px');
  root.append('text').attr('x', w/2).attr('y', h - 6).attr('text-anchor','middle').attr('font-size',11).attr('fill','#7a7268')
    .text('Global rank (OpenAlex)');
  root.append('text').attr('x', 16).attr('y', h/2).attr('transform', `rotate(-90, 16, ${h/2})`).attr('text-anchor','middle').attr('font-size',11).attr('fill','#7a7268')
    .text('Internal rank (corpus)');

  root.append('g').selectAll('circle').data(sample).join('circle')
    .attr('cx', d => x(d.global_rank))
    .attr('cy', d => y(d.internal_rank))
    .attr('r', 2)
    .attr('fill', d => QUAD_COLOR[d.quadrant] || '#9c9890')
    .attr('opacity', 0.6)
    .style('cursor', 'pointer')
    .on('click', (e, d) => { if (e.defaultPrevented) return; window.location.href = '/article/' + d.id; })
    .append('title').text(d => (d.title || '#'+d.id) + '\n' + (d.journal || '') + ' · internal #' + d.internal_rank + ' · global #' + d.global_rank);

  // Quadrant guide labels (low-rank = top-cited; corners are interpretive zones)
  function quadLabel(text, qx, qy, color, anchor) {
    root.append('text').attr('x', qx).attr('y', qy)
      .attr('text-anchor', anchor || 'start')
      .attr('font-size', 11).attr('font-weight', 600)
      .attr('fill', color).attr('opacity', 0.8)
      .attr('paint-order', 'stroke').attr('stroke', '#fdfbf7').attr('stroke-width', 4)
      .text(text);
  }
  quadLabel('Shared canon', m.left + 8, m.top + 16, QUAD_COLOR.shared_canon);
  quadLabel('TPC-specific', m.left + 8, h - m.bottom - 8, QUAD_COLOR.tpc_specific);
  quadLabel('Imported, not metabolized', w - m.right - 8, m.top + 16, QUAD_COLOR.imported, 'end');
  quadLabel('Background', w - m.right - 8, h - m.bottom - 8, QUAD_COLOR.background, 'end');
}

function renderTable(data) {
  const el = document.getElementById('ds-io-table');
  let html = '';
  if ((data.tpc_specific || []).length) {
    html += '<h5 style="color:#5a3e28;">TPC-specific canon (top internal, lower global)</h5>';
    html += list(data.tpc_specific.slice(0, 10));
  }
  if ((data.imported || []).length) {
    html += '<h5 style="color:#a04525;margin-top:1rem;">Imported but not metabolized (top global, lower internal)</h5>';
    html += list(data.imported.slice(0, 10));
  }
  el.innerHTML = html || '<p class="explore-hint">No divergent articles.</p>';
}

function list(rows) {
  let html = '<ul style="font-size:0.84rem;list-style:none;padding-left:0;">';
  rows.forEach(r => {
    html += `<li style="padding:0.3rem 0;border-bottom:1px solid #f1ede6;">
      <a href="/article/${r.id}" style="color:#5a3e28;">${escapeHtml(r.title || '#'+r.id)}</a>
      <div style="font-size:0.78rem;color:#9c9890;">${escapeHtml(r.journal || '')} · ${r.year || '—'} · internal #${r.internal_rank} · global #${r.global_rank}</div>
    </li>`;
  });
  return html + '</ul>';
}

window.loadDsInsideOutside = loadDsInsideOutside;
