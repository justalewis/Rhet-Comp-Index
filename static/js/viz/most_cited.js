// static/js/viz/most_cited.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


async function loadCitations() {
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
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load citation data.</p>';
    return;
  }

  if (!data || data.length === 0) {
    container.innerHTML = '<p class="explore-hint">No citation data yet — run <code>python cite_fetcher.py</code> to populate.</p>';
    return;
  }

  let html = '<ol class="most-cited-list">';
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
}

// ── Inline-handler globals ────────────────────────────────────
window.loadCitations = loadCitations;
