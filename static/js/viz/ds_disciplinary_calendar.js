// static/js/viz/ds_disciplinary_calendar.js — Ch 9, Tool 25: The Disciplinary Calendar
import { setLoading, setError, fetchJson, escapeHtml } from "./_ds_common.js";
import { renderFilterBar, filterParams } from "./_ds_filters.js";
import { renderExportToolbar } from "./_ds_export.js";

const TYPE_COLOR = {
  journal_founded:  "#3a5a28",
  landmark_article: "#5a3e28",
  external_crisis:  "#a04525",
  special_issue:    "#608aac",
};

let _filtersWired_loadDsDisciplinaryCalendar = false;

let _exportWired_loadDsDisciplinaryCalendar = false;

async function loadDsDisciplinaryCalendar() {
  if (!_exportWired_loadDsDisciplinaryCalendar) {
    renderExportToolbar('tab-ds-disciplinary-calendar', { svgSelector: '#ds-dc-timeline svg', dataProvider: () => (window.__dsDcData && window.__dsDcData.events || []) });
    _exportWired_loadDsDisciplinaryCalendar = true;
  }
  if (!_filtersWired_loadDsDisciplinaryCalendar) {
    renderFilterBar('tab-ds-disciplinary-calendar', {  onApply: () => loadDsDisciplinaryCalendar() });
    _filtersWired_loadDsDisciplinaryCalendar = true;
  }
  setLoading('ds-dc-timeline', 'Loading disciplinary events…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-disciplinary-calendar').toString(); return fetchJson('/api/datastories/ch9-disciplinary-calendar' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsDcData = data;
    renderTimeline(data);
    renderCorrelation(data);
  } catch (e) {
    setError('ds-dc-timeline', 'Failed to load: ' + e.message);
  }
}

function renderTimeline(data) {
  const el = d3.select('#ds-dc-timeline');
  el.selectAll('*').remove();
  const events = data.events || [];
  const awak = data.awakenings || [];
  if (!events.length && !awak.length) { el.append('p').attr('class','explore-hint').text('No data.'); return; }

  const w = el.node().clientWidth || 720, h = 460;
  const m = { top: 30, right: 30, bottom: 50, left: 60 };
  const svg = el.append('svg').attr('width', w).attr('height', h);

  const allYears = events.map(e => e.year).concat(awak.map(a => a.year));
  const xMin = d3.min(allYears), xMax = d3.max(allYears);
  const x = d3.scaleLinear().domain([xMin - 1, xMax + 1]).range([m.left, w - m.right]);
  const y = d3.scaleLinear().domain([0, d3.max(awak, a => a.n) || 1]).range([h - m.bottom - 60, m.top]);

  // Awakening bars
  svg.append('g').selectAll('rect').data(awak).join('rect')
    .attr('x', d => x(d.year) - 1.5).attr('y', d => y(d.n))
    .attr('width', 3).attr('height', d => y(0) - y(d.n))
    .attr('fill', '#b38a6a').attr('opacity', 0.8);

  // Event ticks (bottom strip)
  const stripY = h - m.bottom - 30;
  svg.append('line').attr('x1', m.left).attr('x2', w - m.right).attr('y1', stripY).attr('y2', stripY)
    .attr('stroke', '#c8c4bc');
  events.forEach((ev, i) => {
    const lx = x(ev.year);
    svg.append('circle').attr('cx', lx).attr('cy', stripY).attr('r', 5)
      .attr('fill', TYPE_COLOR[ev.type] || '#9c9890').attr('stroke', '#3a3026').attr('stroke-width', 0.5)
      .append('title').text(ev.year + ' · ' + ev.title + ' (' + ev.type + ')');
    // Stagger labels above
    const ly = stripY - 12 - (i % 4) * 12;
    svg.append('line').attr('x1', lx).attr('x2', lx).attr('y1', stripY - 4).attr('y2', ly + 2)
      .attr('stroke', '#c8c4bc').attr('stroke-width', 0.5);
    svg.append('text').attr('x', lx).attr('y', ly).attr('text-anchor','middle')
      .attr('font-size', 9).attr('fill', TYPE_COLOR[ev.type] || '#3a3026').text(ev.year);
  });

  svg.append('g').attr('transform', `translate(0,${y(0)})`).call(d3.axisBottom(x).tickFormat(d3.format('d')))
    .selectAll('text').style('font-size','10px');
  svg.append('g').attr('transform', `translate(${m.left},0)`).call(d3.axisLeft(y).ticks(5))
    .selectAll('text').style('font-size','10px');
  svg.append('text').attr('x', m.left).attr('y', 14).attr('font-size', 11).attr('fill','#7a7268')
    .text('Awakenings per year (bars) — disciplinary events (dots, hover for detail)');
}

function renderCorrelation(data) {
  const el = d3.select('#ds-dc-correlation');
  el.selectAll('*').remove();
  const tm = data.type_means || {};
  if (!Object.keys(tm).length) return;
  const w = el.node().clientWidth || 720, h = 240;
  const m = { top: 20, right: 20, bottom: 40, left: 160 };
  const svg = el.append('svg').attr('width', w).attr('height', h);
  const entries = Object.entries(tm);
  const x = d3.scaleLinear().domain([0, d3.max(entries, e => e[1])]).range([m.left, w - m.right]);
  const y = d3.scaleBand().domain(entries.map(e => e[0])).range([m.top, h - m.bottom]).padding(0.2);

  svg.append('g').selectAll('rect').data(entries).join('rect')
    .attr('x', m.left).attr('y', d => y(d[0]))
    .attr('width', d => x(d[1]) - m.left).attr('height', y.bandwidth())
    .attr('fill', d => TYPE_COLOR[d[0]] || '#9c9890');
  svg.append('g').selectAll('text.lbl').data(entries).join('text')
    .attr('x', m.left - 8).attr('y', d => y(d[0]) + y.bandwidth() / 2 + 3).attr('text-anchor','end')
    .attr('font-size', 10).attr('fill','#3a3026').text(d => d[0].replace(/_/g, ' '));
  svg.append('g').selectAll('text.cnt').data(entries).join('text')
    .attr('x', d => x(d[1]) + 6).attr('y', d => y(d[0]) + y.bandwidth() / 2 + 3)
    .attr('font-size', 10).attr('fill','#7a7268').text(d => 'avg ' + d[1] + ' awakenings/yr post-event');
}

window.loadDsDisciplinaryCalendar = loadDsDisciplinaryCalendar;
