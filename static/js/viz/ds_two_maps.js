// static/js/viz/ds_two_maps.js — Ch 7, Tool 18: Two Maps of the Field
import { setLoading, setError, fetchJson, escapeHtml } from "./_ds_common.js";
import { renderFilterBar, filterParams } from "./_ds_filters.js";
import { renderExportToolbar } from "./_ds_export.js";

let _filtersWired_loadDsTwoMaps = false;

let _exportWired_loadDsTwoMaps = false;

async function loadDsTwoMaps() {
  if (!_exportWired_loadDsTwoMaps) {
    renderExportToolbar('tab-ds-two-maps', { svgSelector: '#ds-tm-comparison svg', dataProvider: () => (window.__dsTmData && window.__dsTmData.comparison || []) });
    _exportWired_loadDsTwoMaps = true;
  }
  if (!_filtersWired_loadDsTwoMaps) {
    renderFilterBar('tab-ds-two-maps', {  onApply: () => loadDsTwoMaps() });
    _filtersWired_loadDsTwoMaps = true;
  }
  setLoading('ds-tm-summary', 'Comparing coupling clusters and citation communities…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-two-maps').toString(); return fetchJson('/api/datastories/ch7-two-maps' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsTmData = data;
    renderSummary(data);
    renderHeatmap(data);
  } catch (e) {
    setError('ds-tm-summary', 'Failed to load: ' + e.message);
  }
}

function renderSummary(data) {
  const s = data.summary || {};
  const html = `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:0.6rem;">
    ${card('Articles compared', (s.n || 0).toLocaleString(), '')}
    ${card('Concordance rate', ((s.concordance_rate || 0) * 100).toFixed(1) + '%', s.n_concordant + ' concordant')}
    ${card('NMI', s.nmi != null ? s.nmi.toFixed(3) : '—', 'normalized mutual info')}
    ${card('ARI', s.ari != null ? s.ari.toFixed(3) : '—', 'adjusted rand index')}
    ${card('Coupling clusters', (s.n_coupling_clusters || 0).toString(), '')}
    ${card('Citation communities', (s.n_citation_communities || 0).toString(), '')}
  </div>`;
  document.getElementById('ds-tm-summary').innerHTML = html;
}

function card(label, big, sub) {
  return `<div style="padding:0.5rem 0.8rem;background:#fdfbf7;border-left:3px solid #5a3e28;font-size:0.84rem;">
    <div style="font-size:0.74rem;color:#9c9890;text-transform:uppercase;letter-spacing:0.04em;">${escapeHtml(label)}</div>
    <div style="font-size:1.2rem;font-weight:700;color:#3a3026;">${big}</div>
    ${sub ? `<div style="color:#7a7268;font-size:0.78rem;">${escapeHtml(sub)}</div>` : ''}
  </div>`;
}

function renderHeatmap(data) {
  const el = d3.select('#ds-tm-comparison');
  el.selectAll('*').remove();
  const arts = data.comparison || [];
  if (!arts.length) { el.append('p').attr('class','explore-hint').text('No data.'); return; }

  // Build co-occurrence: coupling cluster x citation community -> count
  const counts = {};
  arts.forEach(a => {
    const k = a.coupling_cluster + '__' + a.citation_community;
    counts[k] = (counts[k] || 0) + 1;
  });
  const couplings = [...new Set(arts.map(a => a.coupling_cluster))].sort((a, b) => a - b);
  const citations = [...new Set(arts.map(a => a.citation_community))].sort((a, b) => a - b);

  const w = el.node().clientWidth || 720;
  const cellSize = Math.min(28, Math.max(14, Math.floor((w - 80) / Math.max(1, citations.length))));
  const h = couplings.length * cellSize + 80;
  const svg = el.append('svg').attr('width', w).attr('height', h);

  const max = d3.max(Object.values(counts)) || 1;
  const color = d3.scaleSequential([0, max], d3.interpolateBrBG).clamp(true);

  // Cells
  couplings.forEach((cl, ri) => {
    citations.forEach((ci, gi) => {
      const k = cl + '__' + ci;
      const v = counts[k] || 0;
      svg.append('rect')
        .attr('x', 50 + gi * cellSize).attr('y', 30 + ri * cellSize)
        .attr('width', cellSize - 1).attr('height', cellSize - 1)
        .attr('fill', v ? color(v) : '#f1ede6')
        .append('title').text(`coupling cluster ${cl} × citation community ${ci}: ${v} articles`);
    });
  });

  // Axes
  citations.forEach((ci, gi) => {
    svg.append('text').attr('x', 50 + gi * cellSize + cellSize / 2).attr('y', 24)
      .attr('text-anchor','middle').attr('font-size', 9).attr('fill', '#7a7268').text(ci);
  });
  couplings.forEach((cl, ri) => {
    svg.append('text').attr('x', 44).attr('y', 30 + ri * cellSize + cellSize / 2 + 3)
      .attr('text-anchor','end').attr('font-size', 9).attr('fill', '#7a7268').text(cl);
  });
  svg.append('text').attr('x', 50).attr('y', 14).attr('font-size', 11).attr('fill', '#7a7268')
    .text('Citation community →');
  svg.append('text').attr('x', 12).attr('y', h / 2).attr('font-size', 11).attr('fill', '#7a7268')
    .attr('transform', `rotate(-90, 12, ${h / 2})`).text('Coupling cluster ↑');
}

window.loadDsTwoMaps = loadDsTwoMaps;
