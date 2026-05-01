// static/js/viz/ds_origins_frontiers.js — Ch 3, Tool 3: Origins and Frontiers (placeholder)
import { setLoading, setError, fetchJson, escapeHtml } from "./_ds_common.js";
import { renderFilterBar, filterParams } from "./_ds_filters.js";
import { renderExportToolbar } from "./_ds_export.js";

let _filtersWired_loadDsOriginsFrontiers = false;

let _exportWired_loadDsOriginsFrontiers = false;

async function loadDsOriginsFrontiers() {
  if (!_exportWired_loadDsOriginsFrontiers) {
    renderExportToolbar('tab-ds-origins-frontiers', { svgSelector: '#ds-of-journal-chart svg', dataProvider: () => (window.__dsOriginsData && window.__dsOriginsData.journal_rates || []) });
    _exportWired_loadDsOriginsFrontiers = true;
  }
  if (!_filtersWired_loadDsOriginsFrontiers) {
    renderFilterBar('tab-ds-origins-frontiers', {  onApply: () => loadDsOriginsFrontiers() });
    _filtersWired_loadDsOriginsFrontiers = true;
  }
  setLoading('ds-of-summary', 'Computing source/sink counts…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-origins-frontiers').toString(); return fetchJson('/api/datastories/ch3-origins-frontiers' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsOriginsData = data;
    renderSummary(data);
    renderYears(data);
    renderJournals(data);
    renderNotable(data);
  } catch (e) {
    setError('ds-of-summary', 'Failed to load: ' + e.message);
  }
}

function renderSummary(data) {
  const s = data.summary || {};
  const el = document.getElementById('ds-of-summary');
  el.innerHTML = '';
  const grid = document.createElement('div');
  grid.style.cssText = 'display:grid;grid-template-columns:repeat(auto-fit, minmax(160px, 1fr));gap:0.8rem;';
  const cards = [
    ['Total articles',  s.n_articles],
    ['Sources',         s.n_sources, s.pct_sources],
    ['Sinks (frontier)',s.n_frontier, s.pct_frontier],
    ['Sinks (data gap)',s.n_data_gap, s.pct_data_gap],
  ];
  cards.forEach(([label, count, pct]) => {
    if (count == null) return;
    const card = document.createElement('div');
    card.style.cssText = 'padding:0.6rem 0.9rem;background:#fdfbf7;border-left:3px solid #b38a6a;font-size:0.84rem;';
    card.innerHTML = '<div style="font-size:0.78rem;color:#9c9890;text-transform:uppercase;letter-spacing:0.04em;">'
      + escapeHtml(label) + '</div><div style="font-size:1.3rem;font-weight:700;color:#3a3026;">'
      + (count || 0).toLocaleString() + (pct != null ? '<span style="font-size:0.86rem;color:#9c9890;font-weight:400;"> · '
      + pct.toFixed(1) + '%</span>' : '') + '</div>';
    grid.appendChild(card);
  });
  el.appendChild(grid);
}

function renderYears(data) {
  const el = d3.select('#ds-of-year-chart');
  el.selectAll('*').remove();
  const dist = data.year_distributions || {};
  const years = Object.keys(dist).sort();
  if (!years.length) { el.append('p').attr('class', 'explore-hint').text('No year data.'); return; }

  const w = el.node().clientWidth || 720, h = 260, m = { top: 20, right: 20, bottom: 40, left: 50 };
  const svg = el.append('svg').attr('width', w).attr('height', h);
  const x = d3.scaleBand().domain(years).range([m.left, w - m.right]).padding(0.1);
  const stacks = years.map(y => ({
    year: y,
    sources:  (dist[y].sources  || 0),
    sinks:    (dist[y].sinks    || 0),
    other:    (dist[y].other    || 0),
  }));
  const ymax = d3.max(stacks, d => d.sources + d.sinks + d.other) || 1;
  const y = d3.scaleLinear().domain([0, ymax]).range([h - m.bottom, m.top]);

  const colors = { sources: '#5a3e28', sinks: '#3a5a28', other: '#d4cec5' };
  ['other','sinks','sources'].forEach((k, idx, arr) => {
    svg.append('g').selectAll('rect').data(stacks).join('rect')
      .attr('x', d => x(d.year))
      .attr('y', d => {
        let bot = 0; for (let j = 0; j <= idx; j++) bot += d[arr[j]];
        return y(bot);
      })
      .attr('width', x.bandwidth())
      .attr('height', d => y(0) - y(d[k]))
      .attr('fill', colors[k]);
  });

  svg.append('g').attr('transform', `translate(0,${h - m.bottom})`)
    .call(d3.axisBottom(x).tickValues(years.filter((_, i) => i % Math.ceil(years.length / 12) === 0)))
    .selectAll('text').style('font-size', '10px');
  svg.append('g').attr('transform', `translate(${m.left},0)`).call(d3.axisLeft(y).ticks(6))
    .selectAll('text').style('font-size', '10px');
  svg.append('text').attr('x', m.left).attr('y', 14).attr('font-size', 11).attr('fill', '#7a7268')
    .text('Articles per year, stacked: sources / sinks / other');
}

function renderJournals(data) {
  const el = d3.select('#ds-of-journal-chart');
  el.selectAll('*').remove();
  const journals = (data.journal_rates || []).slice(0, 30);
  if (!journals.length) { el.append('p').attr('class', 'explore-hint').text('No journal data.'); return; }

  const w = el.node().clientWidth || 720;
  const rowH = 22;
  const h = journals.length * rowH + 40;
  const m = { top: 20, right: 80, bottom: 20, left: 280 };
  const svg = el.append('svg').attr('width', w).attr('height', h);

  const x = d3.scaleLinear().domain([0, 1]).range([m.left, w - m.right]);
  journals.forEach((j, i) => {
    const yp = m.top + i * rowH;
    svg.append('text').attr('x', m.left - 8).attr('y', yp + 14).attr('text-anchor', 'end')
      .attr('font-size', 10).attr('fill', '#3a3026').text(j.journal);
    svg.append('rect').attr('x', m.left).attr('y', yp + 4).attr('height', rowH - 8)
      .attr('width', x(j.source_rate || 0) - m.left).attr('fill', '#5a3e28').attr('opacity', 0.85);
    svg.append('rect').attr('x', x(1 - (j.sink_rate || 0))).attr('y', yp + 4).attr('height', rowH - 8)
      .attr('width', (w - m.right) - x(1 - (j.sink_rate || 0))).attr('fill', '#3a5a28').attr('opacity', 0.85);
    svg.append('text').attr('x', w - m.right + 6).attr('y', yp + 14).attr('font-size', 10).attr('fill', '#7a7268')
      .text((j.n_articles || 0).toLocaleString());
  });
  svg.append('text').attr('x', m.left).attr('y', 14).attr('font-size', 11).attr('fill', '#7a7268')
    .text('← source rate     ·     sink rate →');
}

function renderNotable(data) {
  const el = document.getElementById('ds-of-notable');
  if (!el) return;
  const notable = data.notable || {};
  let html = '';
  ['top_sources', 'top_frontier', 'top_data_gap'].forEach(k => {
    const arts = notable[k] || [];
    if (!arts.length) return;
    const label = { top_sources: 'Top sources', top_frontier: 'Top frontier sinks', top_data_gap: 'Top data-gap sinks' }[k];
    html += '<h5 style="margin-top:1rem;color:#3a3026;">' + escapeHtml(label) + '</h5>';
    html += '<ul style="font-size:0.84rem;list-style:none;padding-left:0;">';
    arts.slice(0, 8).forEach(a => {
      const yr = (a.pub_date || '').slice(0, 4);
      html += '<li style="padding:0.25rem 0;border-bottom:1px solid #f1ede6;">'
        + '<a href="/article/' + a.id + '" style="color:#5a3e28;">' + escapeHtml(a.title || '') + '</a>'
        + ' <span style="color:#9c9890;">' + escapeHtml(a.journal || '') + (yr ? ' · ' + yr : '') + '</span>'
        + '</li>';
    });
    html += '</ul>';
  });
  el.innerHTML = html;
}

window.loadDsOriginsFrontiers = loadDsOriginsFrontiers;
