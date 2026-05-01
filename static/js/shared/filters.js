// static/js/shared/filters.js
//
// Shared filter component used by every Datastories tool. Renders a small
// filter bar (cluster select, journal multi-select dropdown, year-from /
// year-to inputs, Apply / Reset buttons) into a panel-scoped container.
//
// API:
//   renderFilterBar(panelId, options)
//     panelId — the tab-panel element id (e.g. 'tab-ds-braided-path').
//     options.onApply — callback invoked with the current filter set when
//       the user clicks Apply (or hits Enter in a year input).
//     options.scope — 'all' (default) | 'no-cluster' | 'no-journal' |
//       'no-year' | 'year-only' — controls which filter rows are rendered.
//     options.compact — if true, render in a single horizontal row.
//
//   readFilters(panelId)
//     Returns {cluster, journals, year_from, year_to} read from the inputs.
//
//   filterParams(panelId)
//     URLSearchParams ready to splat into a fetch URL.
//
// State persists via localStorage keyed by `ds-filters:<panelId>`. Filter
// values also reflect into the URL hash so a shared link reproduces the
// view (`#ch3-braided-path?cluster=composition-and-writing-studies`).

const STORAGE_PREFIX = 'ds-filters:';

// Populated from window.DS_CLUSTER_OPTIONS injected by datastories.html
function _clusterOptions() {
  return Array.isArray(window.DS_CLUSTER_OPTIONS) ? window.DS_CLUSTER_OPTIONS : [];
}

function _allJournals() {
  if (!Array.isArray(window.ALL_JOURNALS)) return [];
  return window.ALL_JOURNALS.map(j => j.name).sort();
}


function _loadSaved(panelId) {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_PREFIX + panelId)) || {};
  } catch (e) {
    return {};
  }
}

function _save(panelId, state) {
  try {
    localStorage.setItem(STORAGE_PREFIX + panelId, JSON.stringify(state));
  } catch (e) { /* quota / private mode — ignore */ }
}


export function renderFilterBar(panelId, options) {
  options = options || {};
  const scope = options.scope || 'all';
  const panel = document.getElementById(panelId);
  if (!panel) return;

  // Insert just under the methodology details, above existing chart-toolbar
  // if any. If a previous bar was rendered, replace it.
  let bar = panel.querySelector(':scope > .ds-filter-bar');
  if (bar) bar.remove();
  bar = document.createElement('div');
  bar.className = 'ds-filter-bar';
  bar.style.cssText = 'display:flex;flex-wrap:wrap;gap:0.6rem;align-items:flex-end;'
    + 'margin:0.4rem 0 0.6rem;padding:0.5rem 0.7rem;background:#fdfbf7;'
    + 'border:1px solid #e8e4de;font-size:0.84rem;';

  const saved = _loadSaved(panelId);

  // Cluster select
  if (scope !== 'no-cluster' && scope !== 'year-only') {
    const wrap = document.createElement('label');
    wrap.style.cssText = 'display:flex;flex-direction:column;gap:0.15rem;min-width:160px;';
    wrap.innerHTML = '<span style="color:#7a7268;font-size:0.74rem;text-transform:uppercase;letter-spacing:0.04em;">Cluster</span>';
    const sel = document.createElement('select');
    sel.id = panelId + '__cluster';
    sel.innerHTML = '<option value="">All clusters</option>'
      + _clusterOptions().map(o => `<option value="${o.slug}">${o.label}</option>`).join('');
    sel.value = saved.cluster || '';
    wrap.appendChild(sel);
    bar.appendChild(wrap);
  }

  // Journal multi-select (presented as a button + popup checkbox list)
  if (scope !== 'no-journal' && scope !== 'year-only') {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;flex-direction:column;gap:0.15rem;min-width:180px;position:relative;';
    wrap.innerHTML = '<span style="color:#7a7268;font-size:0.74rem;text-transform:uppercase;letter-spacing:0.04em;">Journals</span>';

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.id = panelId + '__journal-btn';
    btn.style.cssText = 'padding:0.3rem 0.5rem;border:1px solid #c8c4bc;background:#fff;font-size:0.84rem;cursor:pointer;text-align:left;min-height:1.8rem;';
    btn.textContent = 'Any journal';

    const popup = document.createElement('div');
    popup.id = panelId + '__journal-popup';
    popup.style.cssText = 'display:none;position:absolute;top:100%;left:0;z-index:100;'
      + 'background:#fff;border:1px solid #c8c4bc;padding:0.4rem 0.5rem;'
      + 'max-height:280px;overflow-y:auto;min-width:280px;font-size:0.82rem;'
      + 'box-shadow:0 2px 8px rgba(0,0,0,0.08);';
    const savedJournals = new Set(saved.journals || []);
    _allJournals().forEach((name, i) => {
      const id = panelId + '__j-' + i;
      popup.insertAdjacentHTML('beforeend',
        '<label style="display:flex;align-items:center;gap:0.3rem;padding:0.15rem 0;cursor:pointer;">'
        + '<input type="checkbox" id="' + id + '" data-journal="' + escapeAttr(name) + '"'
        + (savedJournals.has(name) ? ' checked' : '') + '>'
        + '<span>' + escapeHtml(name) + '</span></label>');
    });

    btn.addEventListener('click', e => {
      e.stopPropagation();
      const open = popup.style.display === 'block';
      // Close all other popups first
      document.querySelectorAll('.ds-filter-bar [id$="__journal-popup"]').forEach(p => { p.style.display = 'none'; });
      popup.style.display = open ? 'none' : 'block';
    });
    document.addEventListener('click', e => {
      if (!wrap.contains(e.target)) popup.style.display = 'none';
    });

    wrap.appendChild(btn);
    wrap.appendChild(popup);
    bar.appendChild(wrap);
    _refreshJournalLabel(panelId);
  }

  // Year range
  if (scope !== 'no-year') {
    const yfWrap = document.createElement('label');
    yfWrap.style.cssText = 'display:flex;flex-direction:column;gap:0.15rem;width:90px;';
    yfWrap.innerHTML = '<span style="color:#7a7268;font-size:0.74rem;text-transform:uppercase;letter-spacing:0.04em;">From</span>';
    const yf = document.createElement('input');
    yf.type = 'number';
    yf.id = panelId + '__year-from';
    yf.placeholder = (window.MIN_YEAR || 1900);
    yf.style.cssText = 'padding:0.3rem 0.4rem;border:1px solid #c8c4bc;font-size:0.84rem;width:100%;';
    yf.value = saved.year_from || '';
    yfWrap.appendChild(yf);
    bar.appendChild(yfWrap);

    const ytWrap = document.createElement('label');
    ytWrap.style.cssText = 'display:flex;flex-direction:column;gap:0.15rem;width:90px;';
    ytWrap.innerHTML = '<span style="color:#7a7268;font-size:0.74rem;text-transform:uppercase;letter-spacing:0.04em;">To</span>';
    const yt = document.createElement('input');
    yt.type = 'number';
    yt.id = panelId + '__year-to';
    yt.placeholder = (window.MAX_YEAR || new Date().getFullYear());
    yt.style.cssText = 'padding:0.3rem 0.4rem;border:1px solid #c8c4bc;font-size:0.84rem;width:100%;';
    yt.value = saved.year_to || '';
    ytWrap.appendChild(yt);
    bar.appendChild(ytWrap);
  }

  // Apply / Reset
  const btnWrap = document.createElement('div');
  btnWrap.style.cssText = 'display:flex;gap:0.4rem;align-items:flex-end;';
  const applyBtn = document.createElement('button');
  applyBtn.type = 'button';
  applyBtn.className = 'filter-apply-btn';
  applyBtn.textContent = 'Apply';
  applyBtn.id = panelId + '__apply';
  applyBtn.style.cssText = 'padding:0.4rem 0.9rem;background:#5a3e28;color:#fdfbf7;border:0;cursor:pointer;font-size:0.84rem;';
  const resetBtn = document.createElement('button');
  resetBtn.type = 'button';
  resetBtn.textContent = 'Reset';
  resetBtn.id = panelId + '__reset';
  resetBtn.style.cssText = 'padding:0.4rem 0.6rem;background:transparent;border:1px solid #c8c4bc;cursor:pointer;font-size:0.84rem;color:#7a7268;';
  btnWrap.appendChild(applyBtn);
  btnWrap.appendChild(resetBtn);
  bar.appendChild(btnWrap);

  // Status caption: shows currently-applied filter summary
  const status = document.createElement('div');
  status.id = panelId + '__filter-status';
  status.style.cssText = 'flex-basis:100%;color:#7a7268;font-size:0.78rem;font-style:italic;margin-top:0.2rem;';
  bar.appendChild(status);

  // Insert the bar into the panel — after the methodology details, before
  // any existing toolbar / chart container.
  const methodology = panel.querySelector(':scope > .methodology-section, :scope > details.methodology-section');
  if (methodology && methodology.nextSibling) {
    panel.insertBefore(bar, methodology.nextSibling);
  } else if (methodology) {
    methodology.after(bar);
  } else {
    panel.insertBefore(bar, panel.firstChild);
  }

  // Wire events
  applyBtn.addEventListener('click', () => {
    const f = readFilters(panelId);
    _save(panelId, f);
    _updateStatus(panelId, f);
    _refreshJournalLabel(panelId);
    if (typeof options.onApply === 'function') options.onApply(f);
  });
  resetBtn.addEventListener('click', () => {
    _resetFilters(panelId);
    const f = readFilters(panelId);
    _save(panelId, f);
    _updateStatus(panelId, f);
    _refreshJournalLabel(panelId);
    if (typeof options.onApply === 'function') options.onApply(f);
  });
  // Enter in year inputs triggers Apply
  ['__year-from', '__year-to'].forEach(suf => {
    const el = document.getElementById(panelId + suf);
    if (el) el.addEventListener('keydown', e => { if (e.key === 'Enter') applyBtn.click(); });
  });
  // Journal checkbox change → update label preview without re-running query
  popup_changes: {
    const popup = document.getElementById(panelId + '__journal-popup');
    if (popup) popup.addEventListener('change', () => _refreshJournalLabel(panelId));
  }

  _updateStatus(panelId, _loadSaved(panelId));
}


function _refreshJournalLabel(panelId) {
  const btn = document.getElementById(panelId + '__journal-btn');
  if (!btn) return;
  const popup = document.getElementById(panelId + '__journal-popup');
  if (!popup) return;
  const checked = Array.from(popup.querySelectorAll('input[type=checkbox]:checked'));
  if (checked.length === 0) {
    btn.textContent = 'Any journal';
  } else if (checked.length === 1) {
    const name = checked[0].getAttribute('data-journal');
    btn.textContent = name.length > 28 ? name.slice(0, 28) + '…' : name;
  } else {
    btn.textContent = checked.length + ' journals selected';
  }
}


function _updateStatus(panelId, f) {
  const el = document.getElementById(panelId + '__filter-status');
  if (!el) return;
  const parts = [];
  if (f.cluster) {
    const opt = _clusterOptions().find(o => o.slug === f.cluster);
    parts.push('cluster: ' + (opt ? opt.label : f.cluster));
  }
  if (f.journals && f.journals.length) {
    parts.push((f.journals.length === 1) ? ('journal: ' + f.journals[0]) : (f.journals.length + ' journals'));
  }
  if (f.year_from || f.year_to) {
    parts.push('years: ' + (f.year_from || '…') + '–' + (f.year_to || '…'));
  }
  el.textContent = parts.length ? ('Active filter: ' + parts.join(' · ')) : '';
}


export function readFilters(panelId) {
  const cluster = (document.getElementById(panelId + '__cluster') || {}).value || '';
  const popup = document.getElementById(panelId + '__journal-popup');
  const journals = popup
    ? Array.from(popup.querySelectorAll('input[type=checkbox]:checked')).map(c => c.getAttribute('data-journal'))
    : [];
  const yf = (document.getElementById(panelId + '__year-from') || {}).value || '';
  const yt = (document.getElementById(panelId + '__year-to') || {}).value || '';
  return {
    cluster:   cluster || null,
    journals:  journals.length ? journals : null,
    year_from: yf || null,
    year_to:   yt || null,
  };
}


export function filterParams(panelId) {
  const f = readFilters(panelId);
  const p = new URLSearchParams();
  if (f.cluster) p.set('cluster', f.cluster);
  if (f.journals) f.journals.forEach(j => p.append('journal', j));
  if (f.year_from) p.set('year_from', f.year_from);
  if (f.year_to)   p.set('year_to',   f.year_to);
  return p;
}


function _resetFilters(panelId) {
  const cl = document.getElementById(panelId + '__cluster');
  if (cl) cl.value = '';
  const popup = document.getElementById(panelId + '__journal-popup');
  if (popup) popup.querySelectorAll('input[type=checkbox]').forEach(c => { c.checked = false; });
  const yf = document.getElementById(panelId + '__year-from');
  if (yf) yf.value = '';
  const yt = document.getElementById(panelId + '__year-to');
  if (yt) yt.value = '';
}


// Local copies of escape helpers (would create a circular dep with _ds_common.js)
function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function escapeAttr(str) {
  return escapeHtml(str);
}
