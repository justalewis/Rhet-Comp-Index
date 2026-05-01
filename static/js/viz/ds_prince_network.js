// static/js/viz/ds_prince_network.js — Ch 9, Tool 24: Prince Network
import { setLoading, setError, fetchJson, escapeHtml, enableZoomPan } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";

const TYPE_COLOR = {
  single_catalyst:        "#a04525",
  individual_rediscovery: "#5a3e28",
  journal_specific:       "#608aac",
  subfield_revival:       "#8b6045",
  broad_trend:            "#3a5a28",
  no_data:                "#d4cec5",
};

let _filtersWired_loadDsPrinceNetwork = false;

let _exportWired_loadDsPrinceNetwork = false;

async function loadDsPrinceNetwork() {
  if (!_exportWired_loadDsPrinceNetwork) {
    renderExportToolbar('tab-ds-prince-network', { svgSelector: '#ds-pn-network svg', dataProvider: () => (window.__dsPnData && (window.__dsPnData.beauties || []).map(b => ({beauty_id: b.beauty.id, beauty_title: b.beauty.title, beauty_journal: b.beauty.journal, pub_year: b.beauty.pub_year, awakening_year: b.beauty.awakening_year, sleep_years: b.beauty.sleep_years, beauty_coefficient: b.beauty.beauty_coefficient, awakening_type: b.awakening_type, n_princes: b.n_princes, n_journals: b.n_journals}))) });
    _exportWired_loadDsPrinceNetwork = true;
  }
  if (!_filtersWired_loadDsPrinceNetwork) {
    renderFilterBar('tab-ds-prince-network', {  onApply: () => loadDsPrinceNetwork() });
    _filtersWired_loadDsPrinceNetwork = true;
  }
  setLoading('ds-pn-network', 'Computing prince/beauty pairs…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-prince-network').toString(); return fetchJson('/api/datastories/ch9-prince-network' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsPnData = data;
    renderNetwork(data);
    renderTable(data);
  } catch (e) {
    setError('ds-pn-network', 'Failed to load: ' + e.message);
  }
}

function renderNetwork(data) {
  const container = d3.select('#ds-pn-network');
  container.selectAll('*').remove();
  const beauties = data.beauties || [];
  if (!beauties.length) { container.append('p').attr('class','explore-hint').text('No sleeping beauties.'); return; }

  // Bipartite layout: beauties on left, princes on right
  const w = container.node().clientWidth || 720;
  const beautyH = 36;
  const h = beauties.length * beautyH + 60;
  const m = { left: 12, beautyX: 200, princeX: w - 240, right: 24 };
  const svg = container.append('svg').attr('width', w).attr('height', h)
    .style('background', '#fdfbf7').style('border', '1px solid #e8e4de');
  const root = enableZoomPan(svg, { scaleExtent: [0.5, 6] });

  beauties.forEach((b, i) => {
    const cy = 30 + i * beautyH;

    // Beauty node
    root.append('circle').attr('cx', m.beautyX).attr('cy', cy).attr('r', 7)
      .attr('fill', TYPE_COLOR[b.awakening_type] || '#9c9890');
    const a = root.append('a').attr('href', '/article/' + b.beauty.id);
    a.append('text').attr('x', m.beautyX - 10).attr('y', cy + 4)
      .attr('text-anchor','end').attr('font-size', 10).attr('fill', '#3a3026')
      .text((b.beauty.title || '#'+b.beauty.id).slice(0, 35));
    root.append('text').attr('x', m.beautyX - 10).attr('y', cy + 16)
      .attr('text-anchor','end').attr('font-size', 9).attr('fill', '#9c9890')
      .text(b.beauty.pub_year + ' → awakened ' + b.beauty.awakening_year);

    // Prince fan
    const princes = (b.princes || []).slice(0, 12);
    if (!princes.length) return;
    const stepY = Math.min(beautyH - 4, 16);
    const fanH = (princes.length - 1) * stepY;
    princes.forEach((p, pi) => {
      const py = cy - fanH / 2 + pi * stepY;
      root.append('line').attr('x1', m.beautyX + 8).attr('y1', cy).attr('x2', m.princeX - 4).attr('y2', py)
        .attr('stroke', TYPE_COLOR[b.awakening_type] || '#c8c4bc').attr('stroke-opacity', 0.4).attr('stroke-width', 0.7);
      root.append('circle').attr('cx', m.princeX).attr('cy', py).attr('r', 3.5)
        .attr('fill', '#5a3e28').attr('stroke', '#3a3026').attr('stroke-width', 0.4);
      root.append('text').attr('x', m.princeX + 6).attr('y', py + 3)
        .attr('font-size', 9).attr('fill', '#3a3026').text((p.title || '#'+p.id).slice(0, 30));
    });
  });
}

function renderTable(data) {
  const el = document.getElementById('ds-pn-table');
  const beauties = data.beauties || [];
  const stats = data.stats || {};
  let html = '<h4 class="methodology-heading">Awakening types</h4>';
  html += '<div style="display:flex;gap:0.6rem;flex-wrap:wrap;font-size:0.84rem;">' +
    Object.entries(stats.by_awakening_type || {}).map(([k, n]) =>
      `<div style="padding:0.4rem 0.6rem;background:#fdfbf7;border-left:3px solid ${TYPE_COLOR[k] || '#9c9890'};"><strong>${n}</strong> ${escapeHtml(k.replace(/_/g, ' '))}</div>`
    ).join('') + '</div>';
  html += '<h4 class="methodology-heading" style="margin-top:1rem;">Beauties &amp; their princes</h4>';
  html += '<ul style="font-size:0.84rem;list-style:none;padding-left:0;">';
  beauties.forEach(b => {
    html += `<li style="padding:0.4rem 0;border-bottom:1px solid #f1ede6;">
      <a href="/article/${b.beauty.id}" style="color:#5a3e28;font-weight:600;">${escapeHtml(b.beauty.title || '#'+b.beauty.id)}</a>
      <span style="color:#9c9890;"> · ${escapeHtml(b.beauty.journal || '')} · ${b.beauty.pub_year} → ${b.beauty.awakening_year} · sleep ${b.beauty.sleep_years}y · B=${(b.beauty.beauty_coefficient||0).toFixed(0)}</span>
      <div style="font-size:0.78rem;color:${TYPE_COLOR[b.awakening_type] || '#3a3026'};">${escapeHtml(b.awakening_type.replace(/_/g, ' '))} · ${b.n_princes} princes from ${b.n_journals} journals</div>
    </li>`;
  });
  html += '</ul>';
  el.innerHTML = html;
}

window.loadDsPrinceNetwork = loadDsPrinceNetwork;
