// static/js/viz/ds_reach_of_citation.js — Ch 5, Tool 12: The Reach of a Citation
import { setLoading, setError, fetchJson, escapeHtml } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";

const PATTERN_COLOR = {
  steady_classic: "#3a5a28",
  late_bloomer:   "#5a3e28",
  one_wave:       "#a04525",
  front_loaded:   "#8b6045",
  too_recent:     "#b38a6a",
  too_few:        "#d4cec5",
};

let _filtersWired_loadDsReachOfCitation = false;

let _exportWired_loadDsReachOfCitation = false;

async function loadDsReachOfCitation() {
  if (!_exportWired_loadDsReachOfCitation) {
    renderExportToolbar('tab-ds-reach-of-citation', { svgSelector: '#ds-roc-grid svg', dataProvider: () => (window.__dsRocData && window.__dsRocData.articles || []).map(a => ({id: a.id, title: a.title, journal: a.journal, year: a.year, total_citations: a.total_citations, pattern: a.pattern, peak_year: a.peak_year, peak_count: a.peak_count})) });
    _exportWired_loadDsReachOfCitation = true;
  }
  if (!_filtersWired_loadDsReachOfCitation) {
    renderFilterBar('tab-ds-reach-of-citation', {  onApply: () => loadDsReachOfCitation() });
    _filtersWired_loadDsReachOfCitation = true;
  }
  setLoading('ds-roc-grid', 'Computing accumulation curves…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-reach-of-citation').toString(); return fetchJson('/api/datastories/ch5-reach-of-citation' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsRocData = data;
    renderGrid(data);
    renderBars(data);
  } catch (e) {
    setError('ds-roc-grid', 'Failed to load: ' + e.message);
  }
}

// Pattern ordering used when sorting cards — groups visually-similar
// curves together so eye-comparison across cards is easier.
const PATTERN_ORDER = [
  'steady_classic', 'late_bloomer', 'one_wave', 'front_loaded',
  'too_recent', 'too_few',
];

function renderGrid(data) {
  const el = d3.select('#ds-roc-grid');
  el.selectAll('*').remove();
  // Sort by pattern first (so same-shape sparklines are adjacent), then by
  // total citations within each pattern group so the heaviest hitters lead.
  const articles = (data.articles || []).slice();
  articles.sort((a, b) => {
    const ia = PATTERN_ORDER.indexOf(a.pattern); const ib = PATTERN_ORDER.indexOf(b.pattern);
    const pa = ia === -1 ? 99 : ia; const pb = ib === -1 ? 99 : ib;
    if (pa !== pb) return pa - pb;
    return (b.total_citations || 0) - (a.total_citations || 0);
  });
  const arts = articles.slice(0, 60);
  if (!arts.length) { el.append('p').attr('class','explore-hint').text('No data.'); return; }

  // Opacity ramp keyed off total citations across the visible set, so the
  // most-cited article in the cohort gets a fully saturated area-fill and
  // the long tail fades. Pattern still drives hue.
  const maxTotal = d3.max(arts, a => a.total_citations) || 1;
  const opacity = d3.scaleSqrt().domain([0, maxTotal]).range([0.18, 0.85]);

  const w = el.node().clientWidth || 720;
  const cols = 4;
  const cellW = (w - 30) / cols;
  const cellH = 80;
  const rows = Math.ceil(arts.length / cols);
  const h = rows * cellH + 30;
  const svg = el.append('svg').attr('width', w).attr('height', h);

  arts.forEach((art, i) => {
    const cx = (i % cols) * cellW + 8;
    const cy = Math.floor(i / cols) * cellH + 20;

    // Background card
    svg.append('rect').attr('x', cx).attr('y', cy).attr('width', cellW - 16).attr('height', cellH - 12)
      .attr('fill', '#fdfbf7').attr('stroke', '#e8e4de').attr('stroke-width', 0.5);

    // Sparkline (area + line). Area opacity encodes total citations within
    // the cohort, so heavy hitters read at a glance even before label text.
    const series = art.series || [];
    if (series.length) {
      const x = d3.scaleLinear().domain([0, series.length - 1]).range([cx + 6, cx + cellW - 22]);
      const ymax = d3.max(series, p => p.cumulative) || 1;
      const yRange = [cy + cellH - 24, cy + 28];
      const y = d3.scaleLinear().domain([0, ymax]).range(yRange);
      const color = PATTERN_COLOR[art.pattern] || '#9c9890';
      const area = d3.area().x((p, k) => x(k)).y0(yRange[0]).y1(p => y(p.cumulative));
      const line = d3.line().x((p, k) => x(k)).y(p => y(p.cumulative));
      svg.append('path').datum(series).attr('d', area)
        .attr('fill', color).attr('fill-opacity', opacity(art.total_citations || 0)).attr('stroke', 'none');
      svg.append('path').datum(series).attr('d', line).attr('fill', 'none')
        .attr('stroke', color).attr('stroke-width', 1.5);
    }

    // Label (clickable)
    const a = svg.append('a').attr('href', '/article/' + art.id);
    const t = ((art.title || '#'+art.id) + '').slice(0, 38);
    a.append('text').attr('x', cx + 6).attr('y', cy + 14).attr('font-size', 9).attr('fill', '#3a3026').text(t);
    svg.append('text').attr('x', cx + 6).attr('y', cy + cellH - 14).attr('font-size', 8).attr('fill', '#7a7268')
      .text(art.year + ' · ' + art.total_citations + ' cites · ' + (art.pattern || '').replace(/_/g,' '));
  });
}

function renderBars(data) {
  const el = d3.select('#ds-roc-bars');
  el.selectAll('*').remove();
  const patterns = data.patterns || {};
  if (!Object.keys(patterns).length) { el.append('p').attr('class','explore-hint').text('No data.'); return; }

  const w = el.node().clientWidth || 720, h = 220;
  const m = { top: 20, right: 20, bottom: 30, left: 140 };
  const svg = el.append('svg').attr('width', w).attr('height', h);

  const entries = Object.entries(patterns).sort((a, b) => b[1] - a[1]);
  const x = d3.scaleLinear().domain([0, d3.max(entries, e => e[1])]).range([m.left, w - m.right]);
  const y = d3.scaleBand().domain(entries.map(e => e[0])).range([m.top, h - m.bottom]).padding(0.2);

  svg.append('g').selectAll('rect').data(entries).join('rect')
    .attr('x', m.left).attr('y', d => y(d[0]))
    .attr('width', d => x(d[1]) - m.left).attr('height', y.bandwidth())
    .attr('fill', d => PATTERN_COLOR[d[0]] || '#9c9890');
  svg.append('g').selectAll('text.lbl').data(entries).join('text')
    .attr('x', m.left - 6).attr('y', d => y(d[0]) + y.bandwidth() / 2 + 3)
    .attr('text-anchor','end').attr('font-size', 11).attr('fill','#3a3026').text(d => d[0].replace(/_/g, ' '));
  svg.append('g').selectAll('text.cnt').data(entries).join('text')
    .attr('x', d => x(d[1]) + 6).attr('y', d => y(d[0]) + y.bandwidth() / 2 + 3)
    .attr('font-size', 10).attr('fill','#7a7268').text(d => d[1]);
}

window.loadDsReachOfCitation = loadDsReachOfCitation;
