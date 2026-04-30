// static/js/viz/main_path.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


function toggleAllMpJournals(master) {
  document.querySelectorAll('.mp-journal-check').forEach(c => c.checked = master.checked);
  updateMpJournalCount();
}

function updateMpJournalCount() {
  const checked = document.querySelectorAll('.mp-journal-check:checked').length;
  const total   = document.querySelectorAll('.mp-journal-check').length;
  document.getElementById('mp-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}/${total})`;
  document.getElementById('mp-check-all').checked = (checked === total);
}

async function loadMainPath() {
  const container = document.getElementById('mp-container');
  container.innerHTML = '<div class="loading-msg">Building citation DAG and computing main path\u2026</div>';
  document.getElementById('mp-stats').textContent = '';

  const minCit = document.getElementById('mp-min-slider').value;
  let yearFrom = document.getElementById('mp-year-from').value;
  let yearTo   = document.getElementById('mp-year-to').value;
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo))
    [yearFrom, yearTo] = [yearTo, yearFrom];
  const checked = [...document.querySelectorAll('.mp-journal-check:checked')].map(c => c.value);
  const total   = document.querySelectorAll('.mp-journal-check').length;

  const params = new URLSearchParams({ min_citations: minCit });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/citations/main-path?' + params.toString());
    data = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load main path data.</p>';
    return;
  }

  if (!data.path || data.path.length < 2) {
    container.innerHTML =
      '<p class="explore-hint">No main path found \u2014 the citation DAG may be too sparse. ' +
      'Try lowering the minimum citation threshold or widening the year range.</p>';
    return;
  }

  const s = data.stats;
  document.getElementById('mp-stats').textContent =
    `DAG: ${s.dag_nodes} articles, ${s.dag_edges} citation links \u00b7 ` +
    `Main path: ${s.path_length} articles \u00b7 ` +
    `${s.source_count} frontier \u00b7 ${s.sink_count} foundational` +
    (s.cycles_removed ? ` \u00b7 ${s.cycles_removed} cycles removed` : '');

  renderMainPath(container, data);
}

function renderMainPath(container, data) {
  container.innerHTML = '';

  const path = data.path;
  const edges = data.edges;

  // Render as a vertical timeline of article cards
  const el = document.createElement('div');
  el.style.cssText = 'max-width:44rem;margin:0 auto;padding:1rem 0;';

  for (let i = 0; i < path.length; i++) {
    const node = path[i];
    const yr = (node.pub_date || '').substring(0, 4);
    const authors = node.authors || '';
    const firstAuthor = authors.split(',')[0].split(';')[0].trim();
    const title = node.title || 'Untitled';
    const jColor = citnetJournalColor(node.journal);

    // Edge weight bar (between nodes)
    if (i > 0 && edges[i - 1]) {
      const e = edges[i - 1];
      const barW = Math.max(8, Math.round(e.spc_normalized * 100));
      const edgeDiv = document.createElement('div');
      edgeDiv.style.cssText = 'display:flex;align-items:center;justify-content:center;padding:0.3rem 0;';
      edgeDiv.innerHTML =
        `<div style="width:2px;height:20px;background:#b38a6a;"></div>` +
        `<span style="font-size:0.68rem;color:#999;margin-left:0.5rem;">SPC ${e.spc_weight.toLocaleString()}</span>`;
      el.appendChild(edgeDiv);
    }

    // Article card
    const card = document.createElement('a');
    card.href = '/article/' + node.id;
    card.target = '_blank';
    card.style.cssText =
      'display:block;border:1px solid #e5ddd0;border-left:4px solid ' + jColor +
      ';border-radius:6px;padding:0.7rem 1rem;background:#faf8f4;' +
      'text-decoration:none;color:#3a2e1f;transition:box-shadow 0.15s;';
    card.onmouseover = () => card.style.boxShadow = '0 2px 8px rgba(0,0,0,0.1)';
    card.onmouseout  = () => card.style.boxShadow = 'none';

    const posLabel = i === 0 ? '<span style="color:#3a5a28;font-weight:600;font-size:0.72rem;">FRONTIER</span> ' :
                     i === path.length - 1 ? '<span style="color:#8b6045;font-weight:600;font-size:0.72rem;">FOUNDATION</span> ' : '';

    card.innerHTML =
      `<div style="display:flex;justify-content:space-between;align-items:baseline;">` +
        `<div style="font-weight:600;font-size:0.92rem;line-height:1.35;flex:1;">${posLabel}${title}</div>` +
        `<span style="font-size:0.78rem;color:#999;white-space:nowrap;margin-left:0.8rem;">cited ${node.internal_cited_by_count}\u00d7</span>` +
      `</div>` +
      `<div style="font-size:0.8rem;color:#777;margin-top:0.25rem;">` +
        `${firstAuthor}${authors.includes(',') || authors.includes(';') ? ' et al.' : ''} \u00b7 ${yr} \u00b7 ` +
        `<span style="color:${jColor};">${jflowAbbrev(node.journal)}</span>` +
      `</div>`;

    el.appendChild(card);
  }

  container.appendChild(el);
}

// ── Inline-handler globals ────────────────────────────────────
window.toggleAllMpJournals = toggleAllMpJournals;
window.updateMpJournalCount = updateMpJournalCount;
window.loadMainPath = loadMainPath;
