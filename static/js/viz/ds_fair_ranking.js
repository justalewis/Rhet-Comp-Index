// static/js/viz/ds_fair_ranking.js — Ch 5, Tool 10: The Fair Ranking
import { setLoading, setError, fetchJson, escapeHtml } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";

const CAT_COLOR = {
  stable_canon:  "#3a5a28",
  age_advantage: "#a04525",
  rising_fast:   "#5a3e28",
  off:           "#d4cec5",
};

let _filtersWired_loadDsFairRanking = false;

let _exportWired_loadDsFairRanking = false;

async function loadDsFairRanking() {
  if (!_exportWired_loadDsFairRanking) {
    renderExportToolbar('tab-ds-fair-ranking', { svgSelector: '#ds-fr-scatter svg', dataProvider: () => (window.__dsFrData && window.__dsFrData.comparison || []) });
    _exportWired_loadDsFairRanking = true;
  }
  if (!_filtersWired_loadDsFairRanking) {
    renderFilterBar('tab-ds-fair-ranking', {  onApply: () => loadDsFairRanking() });
    _filtersWired_loadDsFairRanking = true;
  }
  setLoading('ds-fr-scatter', 'Computing time-normalized rankings…');
  try {
    const ex = document.getElementById('ds-fr-exclude').value || 2;
    const params = filterParams('tab-ds-fair-ranking');
    params.set('exclude_recent', ex);
    const data = await fetchJson('/api/datastories/ch5-fair-ranking?' + params.toString());
    window.__dsFrData = data;
    renderScatter(data);
    renderTable(data);
  } catch (e) {
    setError('ds-fr-scatter', 'Failed to load: ' + e.message);
  }
}

function dsFairRankingReload() { loadDsFairRanking(); }

function renderScatter(data) {
  const el = d3.select('#ds-fr-scatter');
  el.selectAll('*').remove();
  const arts = data.comparison || [];
  if (!arts.length) { el.append('p').attr('class','explore-hint').text('No articles.'); return; }

  const w = el.node().clientWidth || 720, h = 480;
  const m = { top: 30, right: 30, bottom: 50, left: 60 };
  const svg = el.append('svg').attr('width', w).attr('height', h);

  const maxRank = data.top_n || 50;
  const x = d3.scaleLinear().domain([1, maxRank + 5]).range([m.left, w - m.right]);
  const y = d3.scaleLinear().domain([1, maxRank + 5]).range([m.top, h - m.bottom]);

  // Diagonal line (raw rank == norm rank)
  svg.append('line').attr('x1', x(1)).attr('y1', y(1)).attr('x2', x(maxRank)).attr('y2', y(maxRank))
    .attr('stroke', '#c8c4bc').attr('stroke-dasharray','4 3');

  svg.append('g').attr('transform', `translate(0,${h - m.bottom})`).call(d3.axisBottom(x).ticks(8))
    .selectAll('text').style('font-size','10px');
  svg.append('g').attr('transform', `translate(${m.left},0)`).call(d3.axisLeft(y).ticks(8))
    .selectAll('text').style('font-size','10px');
  svg.append('text').attr('x', w/2).attr('y', h - 10).attr('text-anchor','middle').attr('font-size',11).attr('fill','#7a7268')
    .text('Raw rank (lower = more cited)');
  svg.append('text').attr('x', 14).attr('y', h/2).attr('transform',`rotate(-90, 14, ${h/2})`).attr('text-anchor','middle').attr('font-size',11).attr('fill','#7a7268')
    .text('Normalized rank (cit/yr)');

  svg.append('g').selectAll('circle').data(arts.filter(a => a.raw_rank <= maxRank + 5 && a.norm_rank <= maxRank + 5)).join('circle')
    .attr('cx', d => x(d.raw_rank))
    .attr('cy', d => y(d.norm_rank))
    .attr('r',  4.5)
    .attr('fill', d => CAT_COLOR[d.category] || '#9c9890')
    .attr('stroke', '#3a3026').attr('stroke-width', 0.5)
    .attr('opacity', 0.85)
    .style('cursor','pointer')
    .on('click', (e, d) => { window.location.href = '/article/' + d.id; })
    .append('title').text(d =>
      (d.title || '#' + d.id) + '\n' + (d.journal || '') + ' (' + (d.year || '—') + ')\n' +
      'cited ' + d.cited_by + ' times, ' + d.cit_per_year + '/yr\n' +
      d.category.replace(/_/g, ' '));

  // Legend with counts
  const cats = data.categories || {};
  const legend = svg.append('g').attr('transform', `translate(${w - m.right - 200}, ${m.top})`);
  Object.entries(cats).forEach(([k, n], i) => {
    legend.append('circle').attr('cx', 8).attr('cy', i * 18 + 8).attr('r', 5).attr('fill', CAT_COLOR[k] || '#9c9890');
    legend.append('text').attr('x', 22).attr('y', i * 18 + 12).attr('font-size', 11).text(k.replace(/_/g, ' ') + ': ' + n);
  });
}

function renderTable(data) {
  const el = document.getElementById('ds-fr-table');
  const rising = (data.comparison || []).filter(r => r.category === 'rising_fast').slice(0, 15);
  const aging  = (data.comparison || []).filter(r => r.category === 'age_advantage').slice(0, 15);

  function row(r) {
    return `<tr>
      <td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;"><a href="/article/${r.id}" style="color:#5a3e28;">${escapeHtml(r.title || '#'+r.id)}</a><div style="font-size:0.78rem;color:#9c9890;">${escapeHtml(r.journal || '')}</div></td>
      <td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${r.year || '—'}</td>
      <td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${r.cited_by}</td>
      <td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${r.cit_per_year}</td>
      <td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">#${r.raw_rank} → #${r.norm_rank}</td>
    </tr>`;
  }
  function table(label, rows) {
    if (!rows.length) return '';
    let html = '<h5 style="margin-top:1rem;color:#3a3026;">' + escapeHtml(label) + '</h5>';
    html += '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
    html += '<thead><tr>';
    ['Article','Year','Cited by','Cit/yr','Rank shift'].forEach(h =>
      html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>');
    html += '</tr></thead><tbody>';
    rows.forEach(r => html += row(r));
    html += '</tbody></table>';
    return html;
  }
  el.innerHTML = table('Rising fast — high cit/yr, low total', rising) + table('Age advantage — high total, low cit/yr', aging);
}

window.loadDsFairRanking = loadDsFairRanking;
window.dsFairRankingReload = dsFairRankingReload;
