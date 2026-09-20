// static/js/viz/ds_lasting_partnerships.js — Ch 8, Tool 23: Lasting Partnerships
import { setLoading, setError, fetchJson, escapeHtml } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";
import { chrome } from "../utils/theme.js";

const CAT_COLOR = {
  one_shot:    chrome().grid,
  short_term:  "#b38a6a",
  persistent:  "#3a5a28",
};

let _filtersWired_loadDsLastingPartnerships = false;

let _exportWired_loadDsLastingPartnerships = false;

async function loadDsLastingPartnerships() {
  if (!_exportWired_loadDsLastingPartnerships) {
    renderExportToolbar('tab-ds-lasting-partnerships', { svgSelector: '#ds-lp-bars svg', dataProvider: () => (window.__dsLpData && (window.__dsLpData.persistent || []).map(r => Object.assign({}, r, {journals: (r.journals || []).join('; ')}))) });
    _exportWired_loadDsLastingPartnerships = true;
  }
  if (!_filtersWired_loadDsLastingPartnerships) {
    renderFilterBar('tab-ds-lasting-partnerships', {  onApply: () => loadDsLastingPartnerships() });
    _filtersWired_loadDsLastingPartnerships = true;
  }
  setLoading('ds-lp-summary', 'Counting co-authored articles per pair…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-lasting-partnerships').toString(); return fetchJson('/api/datastories/ch8-lasting-partnerships' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsLpData = data;
    renderSummary(data);
    renderBars(data);
    renderTable(data);
  } catch (e) {
    setError('ds-lp-summary', 'Failed to load: ' + e.message);
  }
}

function renderSummary(data) {
  const s = data.summary || {};
  const cats = s.categories || {};
  const html = `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:0.6rem;">
    <div style="padding:0.5rem 0.8rem;background:var(--chart-halo);border-left:3px solid #5a3e28;font-size:0.84rem;">
      <div style="font-size:0.74rem;color:var(--chart-muted);text-transform:uppercase;">Total pairs</div>
      <div style="font-size:1.3rem;font-weight:700;color:var(--chart-ink);">${(s.n_pairs || 0).toLocaleString()}</div>
    </div>
    ${Object.entries(cats).map(([k, n]) => `
      <div style="padding:0.5rem 0.8rem;background:var(--chart-halo);border-left:3px solid ${CAT_COLOR[k] || chrome().muted};font-size:0.84rem;">
        <div style="font-size:0.74rem;color:var(--chart-muted);text-transform:uppercase;">${escapeHtml(k.replace(/_/g, ' '))}</div>
        <div style="font-size:1.3rem;font-weight:700;color:var(--chart-ink);">${n.toLocaleString()}</div>
      </div>`).join('')}
  </div>`;
  document.getElementById('ds-lp-summary').innerHTML = html;
}

function renderBars(data) {
  const el = d3.select('#ds-lp-bars');
  el.selectAll('*').remove();
  const cats = (data.summary && data.summary.categories) || {};
  if (!Object.keys(cats).length) return;
  const w = el.node().clientWidth || 720, h = 220;
  const m = { top: 20, right: 20, bottom: 40, left: 100 };
  const svg = el.append('svg').attr('width', w).attr('height', h);
  const entries = Object.entries(cats);
  const x = d3.scaleLinear().domain([0, d3.max(entries, e => e[1])]).range([m.left, w - m.right]);
  const y = d3.scaleBand().domain(entries.map(e => e[0])).range([m.top, h - m.bottom]).padding(0.2);

  svg.append('g').selectAll('rect').data(entries).join('rect')
    .attr('x', m.left).attr('y', d => y(d[0]))
    .attr('width', d => x(d[1]) - m.left).attr('height', y.bandwidth())
    .attr('fill', d => CAT_COLOR[d[0]] || chrome().muted);
  svg.append('g').selectAll('text.lbl').data(entries).join('text')
    .attr('x', m.left - 6).attr('y', d => y(d[0]) + y.bandwidth() / 2 + 3).attr('text-anchor','end')
    .attr('font-size', 11).attr('fill',chrome().ink).text(d => d[0].replace(/_/g, ' '));
  svg.append('g').selectAll('text.cnt').data(entries).join('text')
    .attr('x', d => x(d[1]) + 6).attr('y', d => y(d[0]) + y.bandwidth() / 2 + 3)
    .attr('font-size', 10).attr('fill',chrome().muted).text(d => d[1].toLocaleString());
}

function renderTable(data) {
  const el = document.getElementById('ds-lp-table');
  const rows = data.persistent || [];
  if (!rows.length) { el.innerHTML = '<p class="explore-hint">No persistent partnerships.</p>'; return; }
  let html = '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
  html += '<thead><tr>';
  ['Pair','Articles','Span','First','Last','Journals'].forEach(h => html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>');
  html += '</tr></thead><tbody>';
  rows.forEach(r => {
    html += '<tr>';
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid var(--chart-grid);"><a href="/author/${encodeURIComponent(r.a)}" style="color:#5a3e28;">${escapeHtml(r.a)}</a> &amp; <a href="/author/${encodeURIComponent(r.b)}" style="color:#5a3e28;">${escapeHtml(r.b)}</a></td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid var(--chart-grid);font-weight:600;">${r.n}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid var(--chart-grid);">${r.span} years</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid var(--chart-grid);">${r.first}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid var(--chart-grid);">${r.last}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid var(--chart-grid);font-size:0.78rem;">${escapeHtml((r.journals || []).slice(0, 3).join(', '))}${r.journals.length > 3 ? '…' : ''}</td>`;
    html += '</tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

window.loadDsLastingPartnerships = loadDsLastingPartnerships;
