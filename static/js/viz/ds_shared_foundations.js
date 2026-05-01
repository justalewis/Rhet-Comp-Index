// static/js/viz/ds_shared_foundations.js — Ch 7, Tool 17: Shared Foundations
import { setLoading, setError, fetchJson, escapeHtml, GROUP_COLORS, enableZoomPan, enableDrag } from "./_ds_common.js";
import { renderFilterBar, filterParams } from "./_ds_filters.js";
import { renderExportToolbar } from "./_ds_export.js";

let _filtersWired_loadDsSharedFoundations = false;

let _exportWired_loadDsSharedFoundations = false;

async function loadDsSharedFoundations() {
  if (!_exportWired_loadDsSharedFoundations) {
    renderExportToolbar('tab-ds-shared-foundations', { svgSelector: '#ds-sf-network svg', dataProvider: () => (window.__dsSfData && window.__dsSfData.nodes || []) });
    _exportWired_loadDsSharedFoundations = true;
  }
  if (!_filtersWired_loadDsSharedFoundations) {
    renderFilterBar('tab-ds-shared-foundations', {  onApply: () => loadDsSharedFoundations() });
    _filtersWired_loadDsSharedFoundations = true;
  }
  setLoading('ds-sf-network', 'Computing bibliographic coupling network…');
  try {
    const min_c = document.getElementById('ds-sf-min').value || 3;
    const params = filterParams('tab-ds-shared-foundations');
    params.set('min_coupling', min_c);
    const data = await fetchJson('/api/datastories/ch7-shared-foundations?' + params.toString());
    window.__dsSfData = data;
    renderNetwork(data);
  } catch (e) {
    setError('ds-sf-network', 'Failed to load: ' + e.message);
  }
}

function dsSharedFoundationsReload() { loadDsSharedFoundations(); }

function renderNetwork(data) {
  const container = d3.select('#ds-sf-network');
  container.selectAll('*').remove();
  const w = container.node().clientWidth || 720, h = 560;
  const svg = container.append('svg').attr('width', w).attr('height', h)
    .style('background', '#fdfbf7').style('border', '1px solid #e8e4de');

  const nodes = (data.nodes || []).map(n => ({...n}));
  const links = (data.links || []).map(l => ({...l}));
  if (!nodes.length) { svg.append('text').attr('x', 20).attr('y', 30).attr('fill','#9c9890').text('No coupling pairs at this threshold.'); return; }

  const root = enableZoomPan(svg);

  const sim = d3.forceSimulation(nodes)
    .force('link',  d3.forceLink(links).id(d => d.id).distance(d => 70 / Math.max(1, d.value || 1)))
    .force('charge', d3.forceManyBody().strength(-30))
    .force('center', d3.forceCenter(w/2, h/2))
    .force('collide', d3.forceCollide(5));

  const link = root.append('g').selectAll('line').data(links).join('line')
    .attr('stroke', '#c8c4bc').attr('stroke-opacity', 0.5)
    .attr('stroke-width', d => Math.min(2.5, 0.4 + Math.log(d.value || 1)));

  const nodeG = root.append('g').selectAll('circle').data(nodes).join('circle')
    .attr('r', d => 3 + Math.sqrt(d.degree || 0) * 0.6)
    .attr('fill', d => GROUP_COLORS[d.group] || '#9c9890')
    .attr('stroke', '#3a3026').attr('stroke-width', 0.4)
    .style('cursor','pointer')
    .call(enableDrag(sim))
    .on('click', (e, d) => { if (e.defaultPrevented) return; window.location.href = '/article/' + d.id; });
  nodeG.append('title').text(d =>
    (d.title || '#'+d.id) + '\n' + (d.journal || '') + (d.year ? ' (' + d.year + ')' : '') +
    '\nlinked to ' + d.degree + ' coupled refs');

  // Labels for the top 20 by degree only — full labels would obscure the network.
  const labelNodes = nodes.slice().sort((a, b) => (b.degree || 0) - (a.degree || 0)).slice(0, 20);
  const label = root.append('g').selectAll('text').data(labelNodes).join('text')
    .attr('font-size', 10).attr('fill', '#3a3026').attr('text-anchor', 'middle')
    .attr('pointer-events', 'none')
    .attr('paint-order', 'stroke').attr('stroke', '#fdfbf7').attr('stroke-width', 3)
    .text(d => {
      const auth = d.authors ? d.authors.split(';')[0].trim().split(' ').slice(-1)[0] : '';
      return (auth || (d.title || '').slice(0, 18)) + (d.year ? ' ' + d.year : '');
    });

  sim.on('tick', () => {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    nodeG.attr('cx', d => d.x).attr('cy', d => d.y);
    label.attr('x', d => d.x).attr('y', d => d.y - 9);
  });

  // Stats footer
  const stats = data.stats || {};
  svg.append('text').attr('x', 12).attr('y', 18).attr('font-size', 11).attr('fill', '#7a7268')
    .text(stats.n_pairs + ' coupling pairs, showing top ' + (stats.n_nodes || 0) + ' articles');
}

window.loadDsSharedFoundations = loadDsSharedFoundations;
window.dsSharedFoundationsReload = dsSharedFoundationsReload;
