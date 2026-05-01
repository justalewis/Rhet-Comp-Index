// static/js/viz/ds_shifting_canons.js — Ch 5, Tool 11: Shifting Canons
import { setLoading, setError, fetchJson, escapeHtml } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";

const CAT_COLOR = {
  enduring:          "#3a5a28",
  rising:            "#5a3e28",
  fading:            "#a04525",
  intermittent:      "#b38a6a",
  generational_only: "#9c9890",
};

let _filtersWired_loadDsShiftingCanons = false;

let _exportWired_loadDsShiftingCanons = false;

async function loadDsShiftingCanons() {
  if (!_exportWired_loadDsShiftingCanons) {
    renderExportToolbar('tab-ds-shifting-canons', { svgSelector: '#ds-canon-heatmap svg', dataProvider: () => (window.__dsCanonsData && window.__dsCanonsData.comparison || []) });
    _exportWired_loadDsShiftingCanons = true;
  }
  if (!_filtersWired_loadDsShiftingCanons) {
    renderFilterBar('tab-ds-shifting-canons', {  onApply: () => loadDsShiftingCanons() });
    _filtersWired_loadDsShiftingCanons = true;
  }
  setLoading('ds-canon-summary', 'Computing per-generation lists…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-shifting-canons').toString(); return fetchJson('/api/datastories/ch5-shifting-canons' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsCanonsData = data;
    renderSummary(data);
    renderHeatmap(data);
    renderTable(data);
  } catch (e) {
    setError('ds-canon-summary', 'Failed to load: ' + e.message);
  }
}

function renderSummary(data) {
  const cats = (data.summary && data.summary.categories) || {};
  const html = `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:0.6rem;">
    ${Object.entries(cats).map(([k, n]) => `
      <div style="padding:0.5rem 0.8rem;background:#fdfbf7;border-left:3px solid ${CAT_COLOR[k] || '#9c9890'};font-size:0.84rem;">
        <div style="font-size:0.74rem;color:#9c9890;text-transform:uppercase;letter-spacing:0.04em;">${escapeHtml(k.replace(/_/g, ' '))}</div>
        <div style="font-size:1.3rem;font-weight:700;color:#3a3026;">${n}</div>
      </div>`).join('')}
  </div>`;
  document.getElementById('ds-canon-summary').innerHTML = html;
}

function renderHeatmap(data) {
  const el = d3.select('#ds-canon-heatmap');
  el.selectAll('*').remove();
  const items = (data.comparison || []).slice(0, 60);
  if (!items.length) { el.append('p').attr('class','explore-hint').text('No data.'); return; }

  const gens = data.generations || [];
  const w = el.node().clientWidth || 720;
  const cellH = 16, leftLabelW = 360;
  const cellW = (w - leftLabelW - 60) / gens.length;
  const h = items.length * cellH + 40;
  const svg = el.append('svg').attr('width', w).attr('height', h);

  // Header row — labels rotated 30° to fit narrow cells without overlapping
  const SHORT = { 'Pre-1995': '<1995', '1995-2004': "'95–04", '2005-2014': "'05–14", '2015+': "'15+" };
  gens.forEach((g, i) => {
    const cx = leftLabelW + i * cellW + cellW / 2;
    svg.append('text')
      .attr('x', cx).attr('y', 18)
      .attr('text-anchor', 'middle').attr('font-size', 10).attr('fill', '#7a7268')
      .text(SHORT[g.label] || g.label);
  });

  items.forEach((it, ri) => {
    const y0 = 24 + ri * cellH;
    // Title (clickable)
    const a = svg.append('a').attr('href', '/article/' + it.id);
    const lbl = ((it.title || '#'+it.id)).slice(0, 60) + ((it.title||'').length > 60 ? '…' : '');
    a.append('text').attr('x', leftLabelW - 6).attr('y', y0 + 12).attr('text-anchor','end')
      .attr('font-size', 10).attr('fill','#3a3026').text(lbl);

    it.ranks.forEach((rk, gi) => {
      const x0 = leftLabelW + gi * cellW;
      svg.append('rect').attr('x', x0 + 1).attr('y', y0 + 2).attr('width', cellW - 2).attr('height', cellH - 4)
        .attr('fill', rk == null ? '#f1ede6' : (rk <= 5 ? '#5a3e28' : rk <= 10 ? '#8b6045' : '#c9a882'))
        .attr('opacity', rk == null ? 0.3 : 0.9);
      if (rk != null) {
        svg.append('text').attr('x', x0 + cellW/2).attr('y', y0 + 13).attr('text-anchor','middle')
          .attr('font-size', 9).attr('fill', rk <= 5 ? '#fdfbf7' : '#3a3026').text('#' + rk);
      }
    });
  });
}

function renderTable(data) {
  const el = document.getElementById('ds-canon-table');
  const enduring = (data.comparison || []).filter(r => r.category === 'enduring');
  const rising   = (data.comparison || []).filter(r => r.category === 'rising').slice(0, 10);
  const fading   = (data.comparison || []).filter(r => r.category === 'fading').slice(0, 10);

  function table(label, rows) {
    if (!rows.length) return '';
    let html = '<h5 style="margin-top:1rem;color:#3a3026;">' + escapeHtml(label) + '</h5>';
    html += '<ul style="font-size:0.84rem;list-style:none;padding-left:0;">';
    rows.forEach(r => {
      html += `<li style="padding:0.3rem 0;border-bottom:1px solid #f1ede6;">
        <a href="/article/${r.id}" style="color:#5a3e28;">${escapeHtml(r.title || '#'+r.id)}</a>
        <span style="color:#9c9890;"> · ${escapeHtml(r.journal || '')} · ${r.year || '—'}</span>
      </li>`;
    });
    html += '</ul>';
    return html;
  }

  el.innerHTML = table('Enduring (in top-N every generation)', enduring) +
                 table('Rising (top-N only in latest generations)', rising) +
                 table('Fading (top-N only in earlier generations)', fading);
}

window.loadDsShiftingCanons = loadDsShiftingCanons;
