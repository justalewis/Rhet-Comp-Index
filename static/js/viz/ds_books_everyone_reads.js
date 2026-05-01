// static/js/viz/ds_books_everyone_reads.js — Ch 7, Tool 19: The Books Everyone Reads
import { setLoading, setError, fetchJson, escapeHtml } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";

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
  const skipped = (s.skipped_tools || []);
  let html = `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:0.6rem;">
    <div style="padding:0.5rem 0.8rem;background:#fdfbf7;border-left:3px solid #5a3e28;font-size:0.84rem;">
      <div style="font-size:0.74rem;color:#9c9890;text-transform:uppercase;">Articles surfaced</div>
      <div style="font-size:1.3rem;font-weight:700;color:#3a3026;">${(s.n_articles || 0).toLocaleString()}</div>
    </div>
    <div style="padding:0.5rem 0.8rem;background:#fdfbf7;border-left:3px solid #5a3e28;font-size:0.84rem;">
      <div style="font-size:0.74rem;color:#9c9890;text-transform:uppercase;">Tools aggregated</div>
      <div style="font-size:1.3rem;font-weight:700;color:#3a3026;">${s.n_tools_used || 0}</div>
    </div>
  </div>`;
  if (skipped.length) {
    // The aggregator skips heavy @cached tools (Border Crossers,
    // Walls and Bridges) when their cache is cold, to avoid hitting
    // gunicorn's worker timeout. Tell the user how to fill them in.
    html += '<div style="margin-top:0.6rem;padding:0.6rem 0.8rem;background:#fef6e8;border-left:3px solid #b38a6a;font-size:0.82rem;color:#5a3e28;">' +
      '<strong>Partial result.</strong> ' + skipped.length +
      (skipped.length === 1 ? ' tool was' : ' tools were') +
      ' skipped because their result wasn’t cached: <em>' +
      skipped.map(t => escapeHtml(t)).join(', ') + '</em>. ' +
      'Visit each tool once (links in the Datastories nav) to populate its cache, then reload this page for the full master list.' +
      '</div>';
  }
  document.getElementById('ds-be-summary').innerHTML = html;
}

function renderTable(data) {
  const el = document.getElementById('ds-be-table');
  const rows = (data.articles || []).slice(0, 100);
  if (!rows.length) { el.innerHTML = '<p class="explore-hint">No articles.</p>'; return; }

  let html = '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
  html += '<thead><tr>';
  ['#','Article','Year','Journal','Tools', ''].forEach(h => html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>');
  html += '</tr></thead><tbody>';
  rows.forEach((r, i) => {
    const toolsList = (r.tools || []).map(t => escapeHtml(t)).join(', ');
    html += '<tr class="be-main-row" data-row="' + i + '">';
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${i + 1}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;"><a href="/article/${r.id}" style="color:#5a3e28;">${escapeHtml(r.title || '#'+r.id)}</a></td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${r.year || '—'}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${escapeHtml(r.journal || '—')}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;font-weight:600;color:#5a3e28;">${r.n_tools}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">` +
              `<button type="button" class="be-toggle" data-row="${i}" ` +
              `style="padding:0.1rem 0.45rem;background:#fdfbf7;border:1px solid #c8c4bc;cursor:pointer;font-size:0.74rem;border-radius:9px;">tools ▾</button></td>`;
    html += '</tr>';
    // Hidden detail row — expanded on toggle. Lists every tool the article
    // appeared in plus its citation_count + cluster bucket if present.
    html += `<tr class="be-detail-row" data-row="${i}" style="display:none;background:#fdfbf7;">`;
    html += `<td colspan="6" style="padding:0.45rem 0.8rem;border-bottom:1px solid #f1ede6;font-size:0.78rem;color:#3a3026;">`;
    html += `<strong style="color:#5a3e28;">Surfaced by ${r.n_tools} Datastories tool${r.n_tools !== 1 ? 's' : ''}:</strong> `;
    html += toolsList || '<em style="color:#9c9890;">no tools recorded</em>';
    html += `</td></tr>`;
  });
  html += '</tbody></table>';
  el.innerHTML = html;

  el.querySelectorAll('.be-toggle').forEach(btn => {
    btn.addEventListener('click', () => {
      const i = btn.getAttribute('data-row');
      const detail = el.querySelector('tr.be-detail-row[data-row="' + i + '"]');
      if (!detail) return;
      const open = detail.style.display === '';
      detail.style.display = open ? 'none' : '';
      btn.textContent = open ? 'tools ▾' : 'tools ▴';
    });
  });
}

window.loadDsBooksEveryoneReads = loadDsBooksEveryoneReads;
