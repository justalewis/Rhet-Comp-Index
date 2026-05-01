// static/js/viz/ds_long_tail.js — Ch 5, Tool 9: The Long Tail
import { setLoading, setError, fetchJson, escapeHtml } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";

const BREADTH_COLOR = {
  broadly_canonical:  "#3a5a28",
  narrowly_canonical: "#a04525",
  mixed:              "#b38a6a",
};

let _filtersWired_loadDsLongTail = false;

let _exportWired_loadDsLongTail = false;

async function loadDsLongTail() {
  if (!_exportWired_loadDsLongTail) {
    renderExportToolbar('tab-ds-long-tail', { svgSelector: '#ds-lt-scatter svg', dataProvider: () => (window.__dsLtData && window.__dsLtData.articles || []) });
    _exportWired_loadDsLongTail = true;
  }
  if (!_filtersWired_loadDsLongTail) {
    renderFilterBar('tab-ds-long-tail', {  onApply: () => loadDsLongTail() });
    _filtersWired_loadDsLongTail = true;
  }
  setLoading('ds-lt-scatter', 'Computing concentration metrics…');
  try {
    const top = document.getElementById('ds-lt-topn').value || 50;
    const params = filterParams('tab-ds-long-tail');
    params.set('top_n', top);
    const data = await fetchJson('/api/datastories/ch5-long-tail?' + params.toString());
    window.__dsLtData = data;
    renderScatter(data);
    renderTable(data);
  } catch (e) {
    setError('ds-lt-scatter', 'Failed to load: ' + e.message);
  }
}

function dsLongTailReload() { loadDsLongTail(); }

function renderScatter(data) {
  const el = d3.select('#ds-lt-scatter');
  el.selectAll('*').remove();
  const arts = data.articles || [];
  if (!arts.length) { el.append('p').attr('class','explore-hint').text('No articles.'); return; }

  const w = el.node().clientWidth || 720, h = 420;
  const m = { top: 30, right: 30, bottom: 50, left: 60 };
  const svg = el.append('svg').attr('width', w).attr('height', h);

  const x = d3.scaleLinear().domain([0, 1]).range([m.left, w - m.right]);
  const y = d3.scaleLinear().domain([0, d3.max(arts, a => a.unique_citing_authors) * 1.05 || 1]).range([h - m.bottom, m.top]);
  const r = d3.scaleSqrt().domain([0, d3.max(arts, a => a.total_citations) || 1]).range([3, 14]);

  svg.append('g').attr('transform', `translate(0,${h - m.bottom})`).call(d3.axisBottom(x).tickFormat(d3.format('.0%')))
    .selectAll('text').style('font-size','10px');
  svg.append('g').attr('transform', `translate(${m.left},0)`).call(d3.axisLeft(y))
    .selectAll('text').style('font-size','10px');
  svg.append('text').attr('x', w/2).attr('y', h - 8).attr('text-anchor','middle').attr('font-size',11).attr('fill','#7a7268')
    .text('Self-journal rate (% of citations from same journal)');
  svg.append('text').attr('x', 14).attr('y', h/2).attr('transform', `rotate(-90, 14, ${h/2})`).attr('text-anchor','middle').attr('font-size',11).attr('fill','#7a7268')
    .text('Unique citing authors');

  const dots = svg.append('g').selectAll('circle').data(arts).join('circle')
    .attr('cx', d => x(d.self_journal_rate))
    .attr('cy', d => y(d.unique_citing_authors))
    .attr('r',  d => r(d.total_citations))
    .attr('fill', d => BREADTH_COLOR[d.breadth] || '#9c9890')
    .attr('opacity', 0.6)
    .attr('stroke', '#3a3026').attr('stroke-width', 0.5)
    .style('cursor','pointer')
    .on('click', (e, d) => { window.location.href = '/article/' + d.id; })
    .on('mouseover', function(_, d) { dots.attr('opacity', 0.08); d3.select(this).attr('opacity', 1).attr('r', r(d.total_citations) * 1.6).raise(); })
    .on('mouseout',  function(_, d) { dots.attr('opacity', 0.6); d3.select(this).attr('r', r(d.total_citations)); });
  dots.append('title').text(d =>
      (d.title || '#' + d.id) + '\n' + (d.journal || '') + ' (' + (d.year || '—') + ')\n' +
      d.total_citations + ' citations, ' + d.unique_citing_authors + ' authors\n' +
      'breadth: ' + d.breadth.replace(/_/g,' ')
    );

  // Legend
  const legendG = svg.append('g').attr('transform', `translate(${w - m.right - 180}, ${m.top})`);
  Object.entries(BREADTH_COLOR).forEach(([k, c], i) => {
    legendG.append('circle').attr('cx', 8).attr('cy', i * 18 + 8).attr('r', 6).attr('fill', c).attr('opacity', 0.65);
    legendG.append('text').attr('x', 22).attr('y', i * 18 + 12).attr('font-size', 11).text(k.replace(/_/g, ' '));
  });
}

function renderTable(data) {
  const el = document.getElementById('ds-lt-table');
  const rows = (data.articles || []).slice(0, 30);
  if (!rows.length) { el.innerHTML = '<p class="explore-hint">No rows.</p>'; return; }
  let html = '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
  html += '<thead><tr>';
  ['Article','Year','Citations','Self-journal %','Unique authors','Top citer share','Breadth'].forEach(h =>
    html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>');
  html += '</tr></thead><tbody>';
  rows.forEach(r => {
    html += '<tr>';
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;"><a href="/article/${r.id}" style="color:#5a3e28;">${escapeHtml(r.title || '#'+r.id)}</a><div style="font-size:0.78rem;color:#9c9890;">${escapeHtml(r.journal || '')}</div></td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${r.year || '—'}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${r.total_citations}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${(r.self_journal_rate*100).toFixed(0)}%</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${r.unique_citing_authors}</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${(r.top_citer_share*100).toFixed(0)}%</td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;color:${BREADTH_COLOR[r.breadth] || '#3a3026'};">${escapeHtml(r.breadth.replace(/_/g,' '))}</td>`;
    html += '</tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

window.loadDsLongTail = loadDsLongTail;
window.dsLongTailReload = dsLongTailReload;
