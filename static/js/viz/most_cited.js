// static/js/viz/most_cited.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { renderExportToolbar } from "../shared/export.js";
import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


let _exportWired_loadCitations = false;

async function loadCitations() {
  if (!_exportWired_loadCitations) {
    renderExportToolbar('tab-citations', { dataProvider: () => (window.__expMostCited || []) });
    _exportWired_loadCitations = true;
  }
  const container = document.getElementById('citations-list-container');
  container.innerHTML = '<div class="loading-msg">Loading…</div>';

  const yearFrom = document.getElementById('cite-year-from').value;
  const yearTo   = document.getElementById('cite-year-to').value;
  const journal  = document.getElementById('cite-journal').value;
  const tag      = document.getElementById('cite-tag').value;

  const params = new URLSearchParams({ limit: 50 });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (journal)  params.set('journal',   journal);
  if (tag)      params.set('tag',       tag);

  let data;
  try {
    const resp = await fetch('/api/stats/most-cited?' + params.toString());
    data = await resp.json();
    window.__expMostCited = data;
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load citation data.</p>';
    return;
  }

  if (!data || data.length === 0) {
    container.innerHTML = '<p class="explore-hint">No citation data yet — run <code>python cite_fetcher.py</code> to populate.</p>';
    return;
  }

  // Per-journal breakdown sub-bar above the list. Each segment is sized
  // proportionally to the number of articles in the current top-N from
  // that journal. Click a segment to filter the list to that journal
  // (sets the journal selector and re-runs loadCitations).
  const journalCounts = {};
  data.forEach(d => {
    const j = d.journal || '—';
    journalCounts[j] = (journalCounts[j] || 0) + 1;
  });
  const breakdown = Object.entries(journalCounts).sort((a, b) => b[1] - a[1]);
  const total = data.length;
  let breakdownHtml = '';
  if (breakdown.length > 1) {
    const palette = ['#5a3e28','#3a5a28','#a04525','#8b6045','#4a6a8a','#6a5a8a','#5a7a4a','#7a6a2a','#3a6a6a','#7a4a3a'];
    breakdownHtml = '<div class="most-cited-breakdown" style="margin:0 0 0.8rem;">' +
      '<div style="font-size:0.74rem;color:#9c9890;text-transform:uppercase;letter-spacing:0.04em;margin-bottom:0.25rem;">' +
        'Top ' + total + ' by journal' +
      '</div>' +
      '<div style="display:flex;height:18px;border:1px solid #e8e4de;cursor:pointer;font-size:0.72rem;color:#fdfbf7;">' +
        breakdown.map(([j, n], i) => {
          const pct = (n / total) * 100;
          const c = palette[i % palette.length];
          return '<div data-journal="' + escapeHtml(j) + '" ' +
            'style="background:' + c + ';width:' + pct + '%;display:flex;align-items:center;justify-content:center;overflow:hidden;" ' +
            'title="' + escapeHtml(j) + ': ' + n + ' of ' + total + '">' +
            (pct >= 6 ? n : '') +
          '</div>';
        }).join('') +
      '</div>' +
      '<div style="font-size:0.72rem;color:#7a7268;margin-top:0.2rem;">Click a segment to filter to that journal</div>' +
    '</div>';
  }

  let html = breakdownHtml + '<ol class="most-cited-list">';
  data.forEach((item, i) => {
    const year = item.pub_date ? item.pub_date.slice(0, 4) : '';
    const authors = item.authors || '';
    const firstAuthor = authors.split(';')[0].trim();
    const lastName = firstAuthor.split(' ').filter(Boolean).pop() || '';
    const multiAuthor = authors.includes(';');
    const byline = lastName
      ? (lastName + (multiAuthor ? ' et al.' : '') + (year ? ` (${year})` : ''))
      : (year || '');
    const count = item.internal_cited_by_count;
    const citesLabel = count === 1 ? '1 citation' : `${count} citations`;

    html += `
      <li class="most-cited-item">
        <span class="most-cited-rank">${i + 1}</span>
        <div class="most-cited-body">
          <div class="most-cited-title">
            <a href="/article/${item.id}">${escapeHtml(item.title)}</a>
          </div>
          <div class="most-cited-meta">
            ${byline ? `<span class="most-cited-byline">${escapeHtml(byline)}</span>` : ''}
            <span class="most-cited-journal">${escapeHtml(item.journal)}</span>
            <span class="most-cited-count">${citesLabel} in this index</span>
          </div>
        </div>
      </li>`;
  });
  html += '</ol>';

  container.innerHTML = html;

  // Wire breakdown segments — clicking one sets the citations-journal
  // selector (if present) and re-fetches via loadCitations.
  container.querySelectorAll('.most-cited-breakdown [data-journal]').forEach(seg => {
    seg.addEventListener('click', () => {
      const j = seg.getAttribute('data-journal');
      const sel = document.getElementById('citations-journal');
      if (sel) {
        // If the journal is already selected, clicking clears the filter.
        sel.value = (sel.value === j) ? '' : j;
        loadCitations();
      }
    });
  });
}

// ── Inline-handler globals ────────────────────────────────────
window.loadCitations = loadCitations;
