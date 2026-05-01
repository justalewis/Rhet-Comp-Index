// static/js/viz/ds_shifting_currents.js — Ch 4, Tool 4: Shifting Currents
import { setLoading, setError, fetchJson, escapeHtml, GROUP_COLORS } from "./_ds_common.js";
import { renderFilterBar, filterParams } from "./_ds_filters.js";
import { renderExportToolbar } from "./_ds_export.js";

let _filtersWired_loadDsShiftingCurrents = false;

let _exportWired_loadDsShiftingCurrents = false;

async function loadDsShiftingCurrents() {
  if (!_exportWired_loadDsShiftingCurrents) {
    renderExportToolbar('tab-ds-shifting-currents', { svgSelector: '#ds-sc-container svg', dataProvider: () => (window.__dsShiftingCurrentsData && window.__dsShiftingCurrentsData.persistence || []) });
    _exportWired_loadDsShiftingCurrents = true;
  }
  if (!_filtersWired_loadDsShiftingCurrents) {
    renderFilterBar('tab-ds-shifting-currents', {  onApply: () => loadDsShiftingCurrents() });
    _filtersWired_loadDsShiftingCurrents = true;
  }
  const loading = document.getElementById('ds-sc-loading');
  if (loading) loading.style.display = 'block';
  setLoading('ds-sc-container', 'Computing per-decade SPC main paths (this can take 30–90s on first run)…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-shifting-currents').toString(); return fetchJson('/api/datastories/ch4-shifting-currents' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsShiftingCurrentsData = data;
    if (loading) loading.style.display = 'none';
    renderPaths(data);
    renderPersistence(data);
  } catch (e) {
    if (loading) loading.style.display = 'none';
    setError('ds-sc-container', 'Failed to load: ' + e.message);
  }
}

function renderPaths(data) {
  const container = d3.select('#ds-sc-container');
  container.selectAll('*').remove();
  const decades = data.decades || [];
  if (!decades.length) { container.append('p').attr('class', 'explore-hint').text('No paths.'); return; }

  const w = container.node().clientWidth || 720;
  const colW = Math.max(140, Math.floor((w - 60) / decades.length));
  const rowH = 38;
  const maxLen = d3.max(decades, d => d.path && d.path.length) || 0;
  const h = (maxLen || 0) * rowH + 60;
  const m = { top: 36, right: 20, bottom: 20, left: 20 };
  const svg = container.append('svg').attr('width', w).attr('height', h);

  decades.forEach((dec, ci) => {
    const x = m.left + ci * colW + colW / 2;
    // Header
    svg.append('text').attr('x', x).attr('y', 18).attr('text-anchor', 'middle')
      .attr('font-size', 13).attr('font-weight', 600).attr('fill', '#3a3026').text(dec.label);
    svg.append('text').attr('x', x).attr('y', 32).attr('text-anchor', 'middle')
      .attr('font-size', 10).attr('fill', '#9c9890')
      .text((dec.stats && dec.stats.path_len ? dec.stats.path_len + ' nodes  ·  ' : '0 nodes  ·  ') +
            (dec.stats && dec.stats.n_edges || 0).toLocaleString() + ' edges in DAG');

    const path = dec.path || [];
    if (!path.length) {
      svg.append('text').attr('x', x).attr('y', m.top + 16).attr('text-anchor', 'middle')
        .attr('fill', '#9c9890').attr('font-size', 11).text('(no path)');
      return;
    }

    // Vertical chain
    path.forEach((art, i) => {
      const y = m.top + i * rowH + rowH / 2;
      const next = path[i + 1];
      if (next) {
        svg.append('line').attr('x1', x).attr('x2', x)
          .attr('y1', y + 12).attr('y2', y + rowH - 12)
          .attr('stroke', '#c8c4bc').attr('stroke-width', 1);
      }
      // Node
      svg.append('circle').attr('cx', x).attr('cy', y).attr('r', 8)
        .attr('fill', GROUP_COLORS[art.group] || '#9c9890')
        .attr('stroke', '#fdfbf7').attr('stroke-width', 2);
      // Label (clickable)
      const a = svg.append('a').attr('href', '/article/' + art.id);
      const lbl = (art.title || '#' + art.id);
      const auth = art.authors ? art.authors.split(';')[0].trim().split(' ').slice(-1)[0] : '';
      const yr = art.year || '';
      a.append('text').attr('x', x).attr('y', y + 22).attr('text-anchor', 'middle')
        .attr('font-size', 9).attr('fill', '#3a3026')
        .text((auth + (yr ? ' (' + yr + ')' : '')).slice(0, 28));
      a.append('title').text(lbl);
    });
  });
}

function renderPersistence(data) {
  const el = document.getElementById('ds-sc-table');
  const items = data.persistence || [];
  if (!items.length) { el.innerHTML = '<p class="explore-hint">No persistence data.</p>'; return; }

  // Filter to articles on more than one decade
  const multi = items.filter(i => i.n >= 2).slice(0, 25);
  let html = '<h4 class="methodology-heading">Articles on multiple decade paths (persistent backbone)</h4>';
  if (!multi.length) {
    html += '<p class="explore-hint">No articles appear on more than one decade\'s main path. Each era has its own backbone.</p>';
  } else {
    html += '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
    html += '<thead><tr>';
    ['Article', 'Year', 'Decades', 'Count'].forEach(h => html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>');
    html += '</tr></thead><tbody>';
    multi.forEach(i => {
      html += '<tr>';
      html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">'
        + '<a href="/article/' + i.id + '" style="color:#5a3e28;">' + escapeHtml(i.title || '#' + i.id) + '</a>'
        + '<div style="font-size:0.78rem;color:#9c9890;">' + escapeHtml(i.journal || '') + '</div></td>';
      html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + (i.year || '—') + '</td>';
      html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + i.decades.join(', ') + '</td>';
      html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + i.n + '</td>';
      html += '</tr>';
    });
    html += '</tbody></table>';
  }
  el.innerHTML = html;
}

window.loadDsShiftingCurrents = loadDsShiftingCurrents;
