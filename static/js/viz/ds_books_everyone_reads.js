// static/js/viz/ds_books_everyone_reads.js — Ch 7, Tool 19: The Books Everyone Reads
import { setLoading, setError, fetchJson, escapeHtml } from "./_ds_common.js";
import { renderFilterBar, filterParams } from "./_ds_filters.js";
import { renderExportToolbar } from "./_ds_export.js";

let _filtersWired_loadDsBooksEveryoneReads = false;

let _exportWired_loadDsBooksEveryoneReads = false;

async function loadDsBooksEveryoneReads() {
  if (!_exportWired_loadDsBooksEveryoneReads) {
    renderExportToolbar('tab-ds-books-everyone-reads', { dataProvider: () => (window.__dsBeData && (window.__dsBeData.articles || []).map(a => ({id: a.id, title: a.title, journal: a.journal, year: a.year, n_tools: a.n_tools, tools: (a.tools || []).join('; ')}))) });
    _exportWired_loadDsBooksEveryoneReads = true;
  }
  if (!_filtersWired_loadDsBooksEveryoneReads) {
    renderFilterBar('tab-ds-books-everyone-reads', {  onApply: () => loadDsBooksEveryoneReads() });
    _filtersWired_loadDsBooksEveryoneReads = true;
  }
  setLoading('ds-be-summary', 'Aggregating article appearances across tools…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-books-everyone-reads').toString(); return fetchJson('/api/datastories/ch7-books-everyone-reads' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsBeData = data;
    renderSummary(data);
    renderTable(data);
  } catch (e) {
    setError('ds-be-summary', 'Failed to load: ' + e.message);
  }
}

function renderSummary(data) {
  const s = data.summary || {};
  const html = `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:0.6rem;">
    <div style="padding:0.5rem 0.8rem;background:#fdfbf7;border-left:3px solid #5a3e28;font-size:0.84rem;">
      <div style="font-size:0.74rem;color:#9c9890;text-transform:uppercase;">Articles surfaced</div>
      <div style="font-size:1.3rem;font-weight:700;color:#3a3026;">${(s.n_articles || 0).toLocaleString()}</div>
    </div>
    <div style="padding:0.5rem 0.8rem;background:#fdfbf7;border-left:3px solid #5a3e28;font-size:0.84rem;">
      <div style="font-size:0.74rem;color:#9c9890;text-transform:uppercase;">Tools aggregated</div>
      <div style="font-size:1.3rem;font-weight:700;color:#3a3026;">${s.n_tools_used || 0}</div>
    </div>
  </div>`;
  document.getElementById('ds-be-summary').innerHTML = html;
}

function renderTable(data) {
  const el = document.getElementById('ds-be-table');
  const rows = (data.articles || []).slice(0, 100);
  if (!rows.length) { el.innerHTML = '<p class="explore-hint">No articles.</p>'; return; }

  let html = '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
  html += '<thead><tr>';
  ['#','Article','Year','Journal','Tools'].forEach(h => html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>');
  html += '</tr></thead><tbody>';
  rows.forEach((r, i) => {
    html += '<tr>';
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${i + 1}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;"><a href="/article/${r.id}" style="color:#5a3e28;">${escapeHtml(r.title || '#'+r.id)}</a></td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${r.year || '—'}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${escapeHtml(r.journal || '—')}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;font-weight:600;color:#5a3e28;">${r.n_tools}<span style="color:#9c9890;font-weight:400;font-size:0.78rem;"> · ${escapeHtml(r.tools.slice(0, 3).join(', '))}${r.tools.length > 3 ? '…' : ''}</span></td>`;
    html += '</tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

window.loadDsBooksEveryoneReads = loadDsBooksEveryoneReads;
