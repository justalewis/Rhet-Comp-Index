// static/js/viz/ds_border_crossers.js — Ch 4, Tool 6: Border Crossers
import { setLoading, setError, fetchJson, escapeHtml, GROUP_COLORS, enableZoomPan, enableDrag } from "./_ds_common.js";
import { renderFilterBar, filterParams } from "./_ds_filters.js";
import { renderExportToolbar } from "./_ds_export.js";

let _filtersWired_loadDsBorderCrossers = false;

let _exportWired_loadDsBorderCrossers = false;

async function loadDsBorderCrossers() {
  if (!_exportWired_loadDsBorderCrossers) {
    renderExportToolbar('tab-ds-border-crossers', { svgSelector: '#ds-bc-network svg', dataProvider: () => (window.__dsBcData && window.__dsBcData.top_bridges || []) });
    _exportWired_loadDsBorderCrossers = true;
  }
  if (!_filtersWired_loadDsBorderCrossers) {
    renderFilterBar('tab-ds-border-crossers', {  onApply: () => loadDsBorderCrossers() });
    _filtersWired_loadDsBorderCrossers = true;
  }
  const loading = document.getElementById('ds-bc-loading');
  if (loading) loading.style.display = 'block';
  setLoading('ds-bc-bars', 'Computing exact betweenness centrality (first run can take a minute)…');
  try {
    const data = await (() => { const _qs = filterParams('tab-ds-border-crossers').toString(); return fetchJson('/api/datastories/ch4-border-crossers' + (_qs ? ('?' + _qs) : '')); })();
    window.__dsBcData = data;
    if (loading) loading.style.display = 'none';
    renderBars(data);
    renderNetwork(data);
  } catch (e) {
    if (loading) loading.style.display = 'none';
    setError('ds-bc-bars', 'Failed to load: ' + e.message);
  }
}

function renderBars(data) {
  const el = d3.select('#ds-bc-bars');
  el.selectAll('*').remove();
  const top = data.top_bridges || [];
  if (!top.length) { el.append('p').attr('class', 'explore-hint').text('No bridges found.'); return; }

  const w = el.node().clientWidth || 720;
  const rowH = 24;
  const h = top.length * rowH + 30;
  const m = { top: 16, right: 60, bottom: 14, left: 320 };
  const svg = el.append('svg').attr('width', w).attr('height', h);

  const xMax = d3.max(top, d => d.betweenness) || 1;
  const x = d3.scaleLinear().domain([0, xMax]).range([m.left, w - m.right]);

  top.forEach((d, i) => {
    const y = m.top + i * rowH;
    // Title (clickable)
    const a = svg.append('a').attr('href', '/article/' + d.id);
    a.append('text').attr('x', m.left - 10).attr('y', y + 14).attr('text-anchor', 'end')
      .attr('font-size', 11).attr('fill', '#3a3026')
      .text(((d.title || '#' + d.id)).slice(0, 60) + ((d.title || '').length > 60 ? '…' : ''));
    // Bar
    svg.append('rect').attr('x', m.left).attr('y', y + 4)
      .attr('width', x(d.betweenness) - m.left).attr('height', rowH - 8)
      .attr('fill', GROUP_COLORS[d.group] || '#9c9890');
    // Score
    svg.append('text').attr('x', x(d.betweenness) + 6).attr('y', y + 14)
      .attr('font-size', 10).attr('fill', '#7a7268')
      .text(d.betweenness.toFixed(4) + '  · ' + d.boundary + ' cross-edges');
  });
  svg.append('text').attr('x', m.left).attr('y', 12).attr('font-size', 11).attr('fill', '#7a7268')
    .text('Betweenness centrality, top 25');
}

function renderNetwork(data) {
  const container = d3.select('#ds-bc-network');
  container.selectAll('*').remove();
  const w = container.node().clientWidth || 720;
  const h = 540;
  const svg = container.append('svg').attr('width', w).attr('height', h)
    .style('background', '#fdfbf7').style('border', '1px solid #e8e4de');

  const nodes = (data.neighborhood && data.neighborhood.nodes || []).map(n => ({ ...n }));
  const links = (data.neighborhood && data.neighborhood.links || []).map(l => ({ ...l }));
  if (!nodes.length) { svg.append('text').attr('x', 20).attr('y', 30).attr('fill', '#9c9890').text('No neighbourhood to display.'); return; }

  // Zoomable inner group; reset-view chip is drawn by enableZoomPan in the corner.
  const root = enableZoomPan(svg);

  const sim = d3.forceSimulation(nodes)
    .force('link',  d3.forceLink(links).id(d => d.id).distance(40))
    .force('charge', d3.forceManyBody().strength(-50))
    .force('center', d3.forceCenter(w/2, h/2))
    .force('collide', d3.forceCollide(d => (d.is_seed ? 8 : 4) + 1));

  const link = root.append('g').selectAll('line').data(links).join('line')
    .attr('stroke', '#c8c4bc').attr('stroke-opacity', 0.5).attr('stroke-width', 0.5);

  const node = root.append('g').selectAll('circle').data(nodes).join('circle')
    .attr('r', d => d.is_seed ? 6 + Math.sqrt((d.cited_by || 0)) : 3)
    .attr('fill', d => GROUP_COLORS[d.group] || '#9c9890')
    .attr('stroke', d => d.is_seed ? '#3a3026' : 'none')
    .attr('stroke-width', d => d.is_seed ? 1.5 : 0)
    .style('cursor', 'pointer')
    .call(enableDrag(sim))
    .on('mouseenter', (e, d) => {
      tip.style('display', 'block').html(
        '<strong>' + escapeHtml(d.title || '#' + d.id) + '</strong><br>'
        + escapeHtml(d.journal || '') + (d.year ? ' · ' + d.year : '')
        + '<br>betweenness ' + (d.betweenness || 0).toFixed(4)
      );
    })
    .on('mousemove', e => tip.style('left', (e.clientX + 12) + 'px').style('top', (e.clientY + 8) + 'px'))
    .on('mouseleave', () => tip.style('display', 'none'))
    .on('click', (e, d) => { if (e.defaultPrevented) return; window.location.href = '/article/' + d.id; });

  const tip = d3.select('body').append('div')
    .style('position', 'fixed').style('display', 'none')
    .style('background', '#fffefb').style('border', '1px solid #c8c4bc')
    .style('padding', '0.4rem 0.6rem').style('font-size', '0.78rem')
    .style('max-width', '320px').style('z-index', '1000')
    .style('pointer-events', 'none');

  sim.on('tick', () => {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    node.attr('cx', d => d.x).attr('cy', d => d.y);
  });
}

window.loadDsBorderCrossers = loadDsBorderCrossers;
