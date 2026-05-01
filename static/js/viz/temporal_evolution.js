// static/js/viz/temporal_evolution.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { renderExportToolbar } from "../shared/export.js";
import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


let teChart = null;
let teSimulation = null;
let teData = null;
let teSnapDebounce = null;

function toggleAllTeJournals(master) {
  document.querySelectorAll('.te-journal-check').forEach(c => c.checked = master.checked);
  updateTeJournalCount();
}

function updateTeJournalCount() {
  const checked = document.querySelectorAll('.te-journal-check:checked').length;
  const total   = document.querySelectorAll('.te-journal-check').length;
  document.getElementById('te-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}/${total})`;
  document.getElementById('te-check-all').checked = (checked === total);
}

let _exportWired_loadTemporalEvolution = false;

async function loadTemporalEvolution() {
  if (!_exportWired_loadTemporalEvolution) {
    renderExportToolbar('tab-temporal', { svgSelector: '#temporal-container svg', dataProvider: () => (window.__expTemporal && window.__expTemporal.snapshots || []) });
    _exportWired_loadTemporalEvolution = true;
  }
  // Clean up
  if (teChart) { teChart.destroy(); teChart = null; }
  if (teSimulation) { teSimulation.stop(); teSimulation = null; }
  d3.selectAll('.te-tip').remove();
  document.getElementById('te-stats').textContent = '';
  document.getElementById('te-chart-container').style.display = 'none';
  document.getElementById('te-snapshot-container').style.display = 'none';
  document.getElementById('te-metric-toggles').style.display = 'none';
  document.getElementById('te-placeholder').style.display = 'none';

  const viewMode = document.getElementById('te-view-mode').value;
  const minCit   = document.getElementById('te-min-slider').value;
  const winSize  = document.getElementById('te-win-slider').value;
  let yearFrom   = document.getElementById('te-year-from').value;
  let yearTo     = document.getElementById('te-year-to').value;
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo))
    [yearFrom, yearTo] = [yearTo, yearFrom];
  const checked = [...document.querySelectorAll('.te-journal-check:checked')]
                    .map(c => c.value);
  const total   = document.querySelectorAll('.te-journal-check').length;

  const params = new URLSearchParams({
    min_citations: minCit, window_size: winSize
  });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  const statsEl = document.getElementById('te-stats');
  statsEl.textContent = 'Computing temporal metrics\u2026';

  let data;
  try {
    const resp = await fetch('/api/citations/temporal-evolution?' + params.toString());
    data = await resp.json();
    window.__expTemporal = data;
  } catch (e) {
    statsEl.textContent = 'Failed to load temporal data.';
    return;
  }

  if (!data.windows || data.windows.length === 0) {
    statsEl.textContent = 'No data matches these filters \u2014 try lowering the minimum citation count.';
    return;
  }

  teData = data;

  statsEl.textContent =
    `${data.stats.total_windows} windows \u00b7 ` +
    `${data.stats.total_articles.toLocaleString()} articles \u00b7 ` +
    `${data.stats.total_citations.toLocaleString()} citations \u00b7 ` +
    `${data.stats.year_range[0]}\u2013${data.stats.year_range[1]}`;

  if (viewMode === 'timeseries') {
    renderTeTimeSeries(data);
  } else {
    renderTeSnapshotView(data);
  }
}

const TE_METRICS = [
  { key: 'node_count',           label: 'Articles (window)',      color: '#608aac', yAxis: 'y2', checked: true,  fmt: 'int'   },
  { key: 'edge_count',           label: 'Citations (window)',     color: '#ac6882', yAxis: 'y2', checked: true,  fmt: 'int'   },
  { key: 'density',              label: 'Density',                color: '#5a3e28', yAxis: 'y',  checked: true,  fmt: 'float' },
  { key: 'transitivity',         label: 'Clustering',             color: '#3a5a28', yAxis: 'y',  checked: true,  fmt: 'float' },
  { key: 'giant_component_frac', label: 'Giant Comp. Fraction',   color: '#28445a', yAxis: 'y',  checked: true,  fmt: 'float' },
  { key: 'modularity',           label: 'Modularity',             color: '#5a2840', yAxis: 'y',  checked: false, fmt: 'float' },
  { key: 'avg_degree',           label: 'Avg. Degree',            color: '#8b6045', yAxis: 'y2', checked: false, fmt: 'float' },
  { key: 'avg_path_length',      label: 'Avg. Path Length',       color: '#456b40', yAxis: 'y2', checked: false, fmt: 'float' },
  { key: 'new_nodes',            label: 'New Articles',           color: '#aaaa60', yAxis: 'y2', checked: false, fmt: 'int'   },
  { key: 'cum_node_count',       label: 'Cumulative Articles',    color: '#405c78', yAxis: 'y2', checked: false, fmt: 'int'   },
  { key: 'cum_edge_count',       label: 'Cumulative Citations',   color: '#7a4560', yAxis: 'y2', checked: false, fmt: 'int'   },
  { key: 'cum_density',          label: 'Cumulative Density',     color: '#6a5a8a', yAxis: 'y',  checked: false, fmt: 'float' },
  { key: 'cum_giant_frac',       label: 'Cum. Giant Comp. Frac.', color: '#5a7a4a', yAxis: 'y',  checked: false, fmt: 'float' },
];


function renderTeTimeSeries(data) {
  const chartContainer = document.getElementById('te-chart-container');
  const togglesEl      = document.getElementById('te-metric-toggles');
  chartContainer.style.display = 'block';
  togglesEl.style.display = 'flex';

  window._teWindows = data.windows;
  window._teLabels  = data.windows.map(w => w.window_label);

  // Render toggle checkboxes
  togglesEl.innerHTML = TE_METRICS.map(m =>
    `<label style="cursor:pointer; display:inline-flex; align-items:center; gap:0.25rem;">` +
      `<input type="checkbox" class="te-metric-check" data-key="${m.key}" ` +
             `${m.checked ? 'checked' : ''} onchange="updateTeChart()">` +
      `<span style="display:inline-block;width:10px;height:10px;border-radius:50%;` +
             `background:${m.color};"></span>` +
      `<span>${m.label}</span>` +
    `</label>`
  ).join('');

  updateTeChart();
}

function updateTeChart() {
  const windows = window._teWindows;
  const labels  = window._teLabels;
  if (!windows || !labels) return;

  const ctx = document.getElementById('te-chart').getContext('2d');
  if (teChart) teChart.destroy();

  const datasets = [];
  TE_METRICS.forEach(m => {
    const cb = document.querySelector(`.te-metric-check[data-key="${m.key}"]`);
    if (!cb || !cb.checked) return;
    datasets.push({
      label: m.label,
      data: windows.map(w => w[m.key]),
      borderColor: m.color,
      backgroundColor: 'transparent',
      borderWidth: 2,
      pointRadius: 1.5,
      pointHoverRadius: 5,
      tension: 0.3,
      yAxisID: m.yAxis,
      spanGaps: true,
    });
  });

  const hasY2 = datasets.some(ds => ds.yAxisID === 'y2');

  teChart = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'bottom',
          labels: { font: { family: 'system-ui, sans-serif', size: 10 }, boxWidth: 12, padding: 8 },
        },
        tooltip: {
          callbacks: {
            label: ctx2 => {
              const v = ctx2.parsed.y;
              if (v === null || v === undefined) return ` ${ctx2.dataset.label}: \u2014`;
              const m = TE_METRICS.find(mm => mm.label === ctx2.dataset.label);
              if (m && m.fmt === 'int') return ` ${ctx2.dataset.label}: ${v.toLocaleString()}`;
              return ` ${ctx2.dataset.label}: ${v.toFixed(4)}`;
            },
          },
        },
      },
      scales: {
        x: { ticks: { font: { family: 'system-ui, sans-serif', size: 11 }, maxRotation: 45 } },
        y: {
          type: 'linear', position: 'left', beginAtZero: true,
          title: { display: true, text: 'Fraction / coefficient',
                   font: { family: 'system-ui, sans-serif', size: 11 }, color: '#5a3e28' },
          ticks: { font: { family: 'system-ui, sans-serif', size: 11 } },
        },
        y2: {
          type: 'linear', position: 'right', beginAtZero: true,
          display: hasY2,
          grid: { drawOnChartArea: false },
          title: { display: true, text: 'Count / degree',
                   font: { family: 'system-ui, sans-serif', size: 11 }, color: '#9c9890' },
          ticks: { font: { family: 'system-ui, sans-serif', size: 11 }, color: '#9c9890' },
        },
      },
    },
  });
}

function renderTeSnapshotView(data) {
  const snapContainer = document.getElementById('te-snapshot-container');
  snapContainer.style.display = 'block';

  const slider = document.getElementById('te-snap-year-slider');
  slider.min   = data.stats.year_range[0];
  slider.max   = data.stats.year_range[1];
  slider.value = data.stats.year_range[1];
  document.getElementById('te-snap-year-val').textContent = slider.value;

  // Load the initial snapshot at the most recent year
  loadTeSnapshot(parseInt(slider.value));
}

function onTeSnapYearChange(val) {
  document.getElementById('te-snap-year-val').textContent = val;
  clearTimeout(teSnapDebounce);
  teSnapDebounce = setTimeout(() => loadTeSnapshot(parseInt(val)), 400);
}

async function loadTeSnapshot(year) {
  const container = document.getElementById('te-graph-container');
  container.innerHTML = '<div class="loading-msg">Loading network snapshot\u2026</div>';
  if (teSimulation) { teSimulation.stop(); teSimulation = null; }
  d3.selectAll('.te-tip').remove();

  const minCit  = document.getElementById('te-min-slider').value;
  const checked = [...document.querySelectorAll('.te-journal-check:checked')]
                    .map(c => c.value);
  const total   = document.querySelectorAll('.te-journal-check').length;
  let yearFrom  = document.getElementById('te-year-from').value;

  const params = new URLSearchParams({
    min_citations: minCit,
    window_size: 1,
    snapshot_year: year,
    max_nodes: 500,
  });
  if (yearFrom) params.set('year_from', yearFrom);
  params.set('year_to', year);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/citations/temporal-evolution?' + params.toString());
    data = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load snapshot.</p>';
    return;
  }

  if (!data.snapshot || data.snapshot.nodes.length === 0) {
    container.innerHTML = '<p class="explore-hint">No network data at year ' + year + '.</p>';
    document.getElementById('te-snap-stats').textContent = '';
    return;
  }

  const ss = data.snapshot;
  document.getElementById('te-snap-stats').textContent =
    `${ss.nodes.length} articles \u00b7 ${ss.links.length} citations`;

  renderTeForceGraph(container, ss);
}

function renderTeForceGraph(container, snapshot) {
  container.innerHTML = '';

  const W = container.clientWidth || 820;
  const H = 500;
  const nodes = snapshot.nodes.map(d => ({...d}));
  const links = snapshot.links.map(d => ({...d}));

  const maxCit = d3.max(nodes, d => d.internal_cited_by_count) || 1;
  const rScale = d3.scaleSqrt().domain([0, maxCit]).range([3, 18]);

  const svg = d3.select(container).append('svg')
    .attr('width', W).attr('height', H)
    .style('background', '#faf8f5');
  const g = svg.append('g');

  svg.call(d3.zoom().scaleExtent([0.15, 6])
    .on('zoom', e => g.attr('transform', e.transform)));

  const linkSel = g.append('g').selectAll('line')
    .data(links).enter().append('line')
    .style('stroke', '#ccc7bb').style('stroke-opacity', 0.3)
    .style('stroke-width', 0.7);

  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip te-tip')
    .style('opacity', 0).style('pointer-events', 'none');

  const nodeGroup = g.append('g').selectAll('g')
    .data(nodes).enter().append('g')
    .style('cursor', 'pointer')
    .on('click', (e, d) => window.open('/article/' + d.id, '_blank'))
    .on('mouseover', (event, d) => {
      const yr = (d.pub_date || '').substring(0, 4);
      tip.html(
        `<strong>${d.title || 'Untitled'}</strong><br>` +
        `${(d.authors || '').split(',')[0].split(';')[0]}${(d.authors || '').includes(',') || (d.authors || '').includes(';') ? ' et al.' : ''} (${yr})<br>` +
        `<em>${d.journal || ''}</em><br>` +
        `Cited ${d.internal_cited_by_count}\u00d7 \u00b7 degree ${d.degree}`
      ).style('opacity', 1);
      positionTooltip(tip, event);
    })
    .on('mousemove', event => {
      positionTooltip(tip, event);
    })
    .on('mouseout', () => tip.style('opacity', 0))
    .call(d3.drag()
      .on('start', (e, d) => {
        if (!e.active) teSimulation.alphaTarget(0.2).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end', (e, d) => {
        if (!e.active) teSimulation.alphaTarget(0);
        d.fx = null; d.fy = null;
      })
    );

  nodeGroup.append('circle')
    .attr('r', d => rScale(d.internal_cited_by_count))
    .style('fill', d => citnetJournalColor(d.journal))
    .style('stroke', '#fff').style('stroke-width', 0.8)
    .style('opacity', 0.85);

  // Labels for highly cited nodes
  const labelThreshold = Math.max(5, d3.quantile(nodes.map(d => d.internal_cited_by_count).sort(d3.ascending), 0.9));
  nodeGroup.filter(d => d.internal_cited_by_count >= labelThreshold)
    .append('text')
    .text(d => {
      const t = d.title || '';
      return t.length > 30 ? t.substring(0, 28) + '\u2026' : t;
    })
    .attr('dx', d => rScale(d.internal_cited_by_count) + 3)
    .attr('dy', '0.35em')
    .style('font-size', '8px')
    .style('fill', '#5a3e28')
    .style('pointer-events', 'none');

  teSimulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(40).strength(0.25))
    .force('charge', d3.forceManyBody().strength(-40))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => rScale(d.internal_cited_by_count) + 2))
    .on('tick', () => {
      linkSel
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeGroup.attr('transform', d => `translate(${d.x},${d.y})`);
    });
}

// ── Inline-handler globals ────────────────────────────────────
window.toggleAllTeJournals = toggleAllTeJournals;
window.updateTeJournalCount = updateTeJournalCount;
window.loadTemporalEvolution = loadTemporalEvolution;
window.updateTeChart = updateTeChart;
window.onTeSnapYearChange = onTeSnapYearChange;
