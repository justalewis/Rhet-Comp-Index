// static/js/viz/ds_two_way_street.js — Ch 4, Tool 7: The Two-Way Street
import { setLoading, setError, fetchJson, escapeHtml } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";

let _filtersWired_loadDsTwoWayStreet = false;

let _exportWired_loadDsTwoWayStreet = false;

async function loadDsTwoWayStreet() {
  if (!_exportWired_loadDsTwoWayStreet) {
    renderExportToolbar('tab-ds-two-way-street', { svgSelector: '#ds-tw-trends svg', dataProvider: () => (window.__dsTwData && window.__dsTwData.most_reciprocated || []) });
    _exportWired_loadDsTwoWayStreet = true;
  }
  if (!_filtersWired_loadDsTwoWayStreet) {
    renderFilterBar('tab-ds-two-way-street', {  onApply: () => loadDsTwoWayStreet() });
    _filtersWired_loadDsTwoWayStreet = true;
  }
  setLoading('ds-tw-summary', 'Computing reciprocity rates…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-two-way-street').toString(); return fetchJson('/api/datastories/ch4-two-way-street' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsTwData = data;
    renderSummary(data);
    renderTrends(data);
    renderTable(data);
  } catch (e) {
    setError('ds-tw-summary', 'Failed to load: ' + e.message);
  }
}

function renderSummary(data) {
  const el = document.getElementById('ds-tw-summary');
  el.innerHTML = '';
  const ar = data.article_reciprocity || {};
  const er = data.edge_reciprocity || {};

  const grid = document.createElement('div');
  grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));gap:0.8rem;';

  const cards = [
    {
      title: 'TPC articles citing RC',
      big: (ar.tpc_with_rc_outgoing || 0).toLocaleString(),
      sub: 'reciprocated: ' + (ar.tpc_reciprocated || 0).toLocaleString() + ' (' + ((ar.tpc_rate || 0) * 100).toFixed(1) + '%)',
      color: '#5a3e28',
    },
    {
      title: 'RC articles citing TPC',
      big: (ar.rc_with_tpc_outgoing || 0).toLocaleString(),
      sub: 'reciprocated: ' + (ar.rc_reciprocated || 0).toLocaleString() + ' (' + ((ar.rc_rate || 0) * 100).toFixed(1) + '%)',
      color: '#3a5a28',
    },
    {
      title: 'Cross-field edges',
      big: (er.n_cross || 0).toLocaleString(),
      sub: (er.n_reciprocal || 0).toLocaleString() + ' have a reverse edge (' + ((er.rate || 0) * 100).toFixed(2) + '%)',
      color: '#608aac',
    },
  ];
  cards.forEach(c => {
    const card = document.createElement('div');
    card.style.cssText = 'padding:0.6rem 0.9rem;background:#fdfbf7;border-left:3px solid ' + c.color + ';font-size:0.84rem;';
    card.innerHTML = '<div style="font-size:0.78rem;color:#9c9890;text-transform:uppercase;">' + escapeHtml(c.title) + '</div>'
      + '<div style="font-size:1.3rem;font-weight:700;color:#3a3026;">' + c.big + '</div>'
      + '<div style="color:#7a7268;">' + c.sub + '</div>';
    grid.appendChild(card);
  });
  el.appendChild(grid);
}

function renderTrends(data) {
  const el = d3.select('#ds-tw-trends');
  el.selectAll('*').remove();
  const trends = data.temporal || [];
  if (!trends.length) {
    el.append('p').attr('class', 'explore-hint').text('Not enough data for decade trends.');
    return;
  }
  const w = el.node().clientWidth || 720, h = 280;
  const m = { top: 20, right: 60, bottom: 40, left: 50 };
  const svg = el.append('svg').attr('width', w).attr('height', h);

  const decades = trends.map(t => String(t.decade));
  const x = d3.scalePoint().domain(decades).range([m.left, w - m.right]).padding(0.5);
  const yLeft = d3.scaleLinear().domain([0, d3.max(trends, t => t.cross) * 1.1 || 1]).range([h - m.bottom, m.top]);
  const yRight = d3.scaleLinear().domain([0, d3.max(trends, t => t.rate) * 1.4 || 0.05]).range([h - m.bottom, m.top]);

  // Bars: cross volume
  svg.append('g').selectAll('rect').data(trends).join('rect')
    .attr('x', t => x(String(t.decade)) - 16)
    .attr('y', t => yLeft(t.cross))
    .attr('width', 32)
    .attr('height', t => yLeft(0) - yLeft(t.cross))
    .attr('fill', '#d4cec5');

  // Line: reciprocity rate
  const line = d3.line().x(t => x(String(t.decade))).y(t => yRight(t.rate)).curve(d3.curveMonotoneX);
  svg.append('path').datum(trends).attr('fill', 'none').attr('stroke', '#5a3e28').attr('stroke-width', 2.5).attr('d', line);
  svg.append('g').selectAll('circle').data(trends).join('circle')
    .attr('cx', t => x(String(t.decade)))
    .attr('cy', t => yRight(t.rate))
    .attr('r', 4).attr('fill', '#5a3e28');

  svg.append('g').attr('transform', `translate(0,${h - m.bottom})`).call(d3.axisBottom(x))
    .selectAll('text').style('font-size', '10px');
  svg.append('g').attr('transform', `translate(${m.left},0)`).call(d3.axisLeft(yLeft).ticks(5))
    .selectAll('text').style('font-size', '10px');
  svg.append('g').attr('transform', `translate(${w - m.right},0)`).call(d3.axisRight(yRight).ticks(5).tickFormat(d3.format('.0%')))
    .selectAll('text').style('font-size', '10px');

  svg.append('text').attr('x', m.left).attr('y', 14).attr('font-size', 11).attr('fill', '#7a7268')
    .text('Cross-field edges (bars) and reciprocity rate (line) per decade');
}

// Three views over the most_reciprocated rows: per-article (default,
// the original table), per-author (each author summed across the
// articles they appear on), per-journal. Switching is client-side only —
// the underlying data is the same article-level array.
let _dsTwView = 'article';

function renderTable(data) {
  const el = document.getElementById('ds-tw-table');
  const rows = data.most_reciprocated || [];
  if (!rows.length) { el.innerHTML = '<p class="explore-hint">No reciprocated articles.</p>'; return; }

  // Tab strip
  const tabs = ['article', 'author', 'journal'];
  const tabHtml = '<div style="margin-bottom:0.6rem;font-size:0.82rem;">' +
    tabs.map(t => '<button type="button" data-tw-view="' + t + '" ' +
      'style="margin-right:0.3rem;padding:0.25rem 0.7rem;cursor:pointer;' +
      'background:' + (_dsTwView === t ? '#5a3e28' : '#fdfbf7') + ';' +
      'color:' + (_dsTwView === t ? '#fdfbf7' : '#5a3e28') + ';' +
      'border:1px solid #c8c4bc;border-radius:11px;font-size:0.78rem;">' +
      'by ' + t + '</button>').join('') +
    '</div>';

  let bodyHtml = '';
  if (_dsTwView === 'article') {
    bodyHtml = _renderArticleTable(rows);
  } else if (_dsTwView === 'author') {
    bodyHtml = _renderAuthorTable(rows);
  } else {
    bodyHtml = _renderJournalTable(rows);
  }

  el.innerHTML = tabHtml + bodyHtml;
  el.querySelectorAll('button[data-tw-view]').forEach(btn => {
    btn.addEventListener('click', () => {
      _dsTwView = btn.getAttribute('data-tw-view');
      renderTable(data);
    });
  });
}

function _renderArticleTable(rows) {
  let html = '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
  html += '<thead><tr>';
  ['Article', 'Year', 'Mutual partners', 'Cites RC', 'Cited by RC'].forEach(h =>
    html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>');
  html += '</tr></thead><tbody>';
  rows.forEach(r => {
    html += '<tr>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">'
      + '<a href="/article/' + r.id + '" style="color:#5a3e28;">' + escapeHtml(r.title || ('#' + r.id)) + '</a>'
      + '<div style="font-size:0.78rem;color:#9c9890;">' + escapeHtml(r.journal || '') + '</div></td>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + (r.year || '—') + '</td>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;font-weight:600;">' + r.n_mutual_partners + '</td>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + r.n_cites_rc + '</td>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + r.n_cited_by_rc + '</td>';
    html += '</tr>';
  });
  html += '</tbody></table>';
  return html;
}

function _renderAuthorTable(rows) {
  // Sum per-author across rows. Authors are ';' delimited per the corpus
  // convention. Skip empty / unknown.
  const byAuthor = {};
  rows.forEach(r => {
    (r.authors || '').split(';').map(a => a.trim()).filter(Boolean).forEach(a => {
      if (!byAuthor[a]) byAuthor[a] = { author: a, n_articles: 0, n_mutual_partners: 0, n_cites_rc: 0, n_cited_by_rc: 0 };
      byAuthor[a].n_articles += 1;
      byAuthor[a].n_mutual_partners += r.n_mutual_partners || 0;
      byAuthor[a].n_cites_rc += r.n_cites_rc || 0;
      byAuthor[a].n_cited_by_rc += r.n_cited_by_rc || 0;
    });
  });
  const arr = Object.values(byAuthor).sort((a, b) => b.n_mutual_partners - a.n_mutual_partners);
  if (!arr.length) return '<p class="explore-hint">No author data on these rows.</p>';
  let html = '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
  html += '<thead><tr>';
  ['Author', 'Reciprocated articles', 'Mutual partners', 'Cites RC', 'Cited by RC'].forEach(h =>
    html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>');
  html += '</tr></thead><tbody>';
  arr.slice(0, 50).forEach(a => {
    html += '<tr>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + escapeHtml(a.author) + '</td>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + a.n_articles + '</td>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;font-weight:600;">' + a.n_mutual_partners + '</td>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + a.n_cites_rc + '</td>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + a.n_cited_by_rc + '</td>';
    html += '</tr>';
  });
  html += '</tbody></table>';
  if (arr.length > 50) html += '<p class="explore-hint" style="margin-top:0.4rem;">Showing 50 of ' + arr.length + ' authors.</p>';
  return html;
}

function _renderJournalTable(rows) {
  const byJournal = {};
  rows.forEach(r => {
    const j = r.journal || '—';
    if (!byJournal[j]) byJournal[j] = { journal: j, n_articles: 0, n_mutual_partners: 0, n_cites_rc: 0, n_cited_by_rc: 0 };
    byJournal[j].n_articles += 1;
    byJournal[j].n_mutual_partners += r.n_mutual_partners || 0;
    byJournal[j].n_cites_rc += r.n_cites_rc || 0;
    byJournal[j].n_cited_by_rc += r.n_cited_by_rc || 0;
  });
  const arr = Object.values(byJournal).sort((a, b) => b.n_mutual_partners - a.n_mutual_partners);
  let html = '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
  html += '<thead><tr>';
  ['Journal', 'Reciprocated articles', 'Mutual partners', 'Cites RC', 'Cited by RC'].forEach(h =>
    html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>');
  html += '</tr></thead><tbody>';
  arr.forEach(j => {
    html += '<tr>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + escapeHtml(j.journal) + '</td>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + j.n_articles + '</td>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;font-weight:600;">' + j.n_mutual_partners + '</td>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + j.n_cites_rc + '</td>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + j.n_cited_by_rc + '</td>';
    html += '</tr>';
  });
  html += '</tbody></table>';
  return html;
}

window.loadDsTwoWayStreet = loadDsTwoWayStreet;
