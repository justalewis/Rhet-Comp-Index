// static/js/viz/ds_braided_path.js — Ch 3, Tool 1: The Braided Path
import { setLoading, setError, fetchJson, escapeHtml } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";

let _data = null;
let _filtersWired = false;

let _exportWired_loadDsBraidedPath = false;

async function loadDsBraidedPath() {
  if (!_exportWired_loadDsBraidedPath) {
    renderExportToolbar('tab-ds-braided-path', { svgSelector: '#ds-bp-container svg', dataProvider: () => (_data.summary || []) });
    _exportWired_loadDsBraidedPath = true;
  }
  if (!_filtersWired) {
    renderFilterBar('tab-ds-braided-path', { onApply: () => loadDsBraidedPath() });
    _filtersWired = true;
  }
  setLoading('ds-bp-container', 'Loading citation flows by decade…');
  try {
    const qs = filterParams('tab-ds-braided-path').toString();
    const url = '/api/datastories/ch3-braided-path' + (qs ? ('?' + qs) : '');
    _data = await fetchJson(url);
    window.__dsBpData = _data;
    populateDecadeSelector();
    renderSummaryTable();
    // Default to the most recent decade with data
    const sel = document.getElementById('ds-bp-decade');
    if (sel && sel.options.length) sel.selectedIndex = sel.options.length - 1;
    renderSelectedDecade();
  } catch (e) {
    setError('ds-bp-container', 'Failed to load: ' + e.message);
  }
}

function populateDecadeSelector() {
  const sel = document.getElementById('ds-bp-decade');
  if (!sel || !_data || !_data.decades) return;
  sel.innerHTML = '';
  _data.decades.forEach(d => {
    const opt = document.createElement('option');
    opt.value = d.decade;
    opt.textContent = d.label || (d.decade + 's');
    sel.appendChild(opt);
  });
  const optAll = document.createElement('option');
  optAll.value = '__all__';
  optAll.textContent = 'All decades (aggregate)';
  sel.appendChild(optAll);
}

function renderSelectedDecade() {
  const sel = document.getElementById('ds-bp-decade');
  if (!sel || !_data) return;
  const val = sel.value;
  let pane = null;
  if (val === '__all__') {
    pane = _data.aggregate;
  } else {
    pane = (_data.decades || []).find(d => String(d.decade) === String(val));
  }
  if (!pane) {
    setError('ds-bp-container', 'No data for that decade.');
    return;
  }
  drawSankey(pane);
  document.getElementById('ds-bp-decade-summary').textContent =
    (pane.total_edges || 0).toLocaleString() + ' citation edges';
}

function drawSankey(pane) {
  const container = d3.select('#ds-bp-container');
  container.selectAll('*').remove();

  const width = container.node().clientWidth || 720;
  const height = 380;

  const svg = container.append('svg')
    .attr('width', width).attr('height', height)
    .style('font', '11px system-ui, sans-serif');

  if (!pane.nodes || !pane.nodes.length || !pane.links || !pane.links.length) {
    svg.append('text').attr('x', 20).attr('y', 30).attr('fill', '#9c9890')
      .text('No flows recorded for this period.');
    return;
  }

  // d3-sankey is loaded from CDN as global
  const sankey = d3.sankey()
    .nodeWidth(18)
    .nodePadding(14)
    .extent([[10, 10], [width - 120, height - 10]]);

  // Deep-copy so d3-sankey can mutate freely
  const graph = sankey({
    nodes: pane.nodes.map(n => ({ ...n })),
    links: pane.links.map(l => ({ ...l })),
  });

  const colorOf = d => {
    if (d.group === 'TPC')       return '#5a3e28';
    if (d.group === 'RHET_COMP') return '#3a5a28';
    return '#9c9890';
  };

  svg.append('g')
    .selectAll('path')
    .data(graph.links)
    .join('path')
    .attr('d', d3.sankeyLinkHorizontal())
    .attr('stroke', d => colorOf(d.source))
    .attr('stroke-width', d => Math.max(1, d.width))
    .attr('fill', 'none')
    .attr('stroke-opacity', 0.45)
    .append('title')
    .text(d => d.source.name + ' → ' + d.target.name + '\n' + d.value.toLocaleString() + ' citations');

  const nodeG = svg.append('g')
    .selectAll('g')
    .data(graph.nodes)
    .join('g');

  nodeG.append('rect')
    .attr('x', d => d.x0).attr('y', d => d.y0)
    .attr('width',  d => d.x1 - d.x0)
    .attr('height', d => Math.max(1, d.y1 - d.y0))
    .attr('fill', colorOf)
    .append('title').text(d => d.name + ' — ' + (d.value || 0).toLocaleString());

  nodeG.append('text')
    .attr('x', d => d.x0 < width / 2 ? d.x1 + 6 : d.x0 - 6)
    .attr('y', d => (d.y0 + d.y1) / 2)
    .attr('dy', '0.35em')
    .attr('text-anchor', d => d.x0 < width / 2 ? 'start' : 'end')
    .attr('fill', '#3a3026')
    .text(d => d.name + ' (' + (d.value || 0).toLocaleString() + ')');
}

function renderSummaryTable() {
  const el = document.getElementById('ds-bp-summary-table');
  if (!el || !_data || !_data.summary) return;
  let html = '<h4 class="methodology-heading">By decade</h4>';
  html += '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
  html += '<thead><tr>';
  ['Decade', 'TPC → TPC', 'TPC → RC', 'RC → TPC', 'RC → RC', 'Total'].forEach(h => {
    html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>';
  });
  html += '</tr></thead><tbody>';
  _data.summary.forEach(row => {
    html += '<tr>';
    html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + escapeHtml(row.label || (row.decade + 's')) + '</td>';
    ['tpc_tpc', 'tpc_rc', 'rc_tpc', 'rc_rc', 'total'].forEach(k => {
      html += '<td style="padding:0.35rem 0.5rem;border-bottom:1px solid #f1ede6;">' + (row[k] || 0).toLocaleString() + '</td>';
    });
    html += '</tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

function dsBraidedPathSelectDecade() { renderSelectedDecade(); }

window.loadDsBraidedPath = loadDsBraidedPath;
window.dsBraidedPathSelectDecade = dsBraidedPathSelectDecade;
