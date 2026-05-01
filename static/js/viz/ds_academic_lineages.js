// static/js/viz/ds_academic_lineages.js — Ch 8, Tool 22: Academic Lineages
import { setLoading, setError, fetchJson, escapeHtml, enableZoomPan, enableDrag } from "../shared/common.js";
import { renderFilterBar, filterParams } from "../shared/filters.js";
import { renderExportToolbar } from "../shared/export.js";

let _filtersWired_loadDsAcademicLineages = false;

let _exportWired_loadDsAcademicLineages = false;

async function loadDsAcademicLineages() {
  if (!_exportWired_loadDsAcademicLineages) {
    renderExportToolbar('tab-ds-academic-lineages', { svgSelector: '#ds-al-network svg', dataProvider: () => (window.__dsAlData && window.__dsAlData.mentorships || []) });
    _exportWired_loadDsAcademicLineages = true;
  }
  if (!_filtersWired_loadDsAcademicLineages) {
    renderFilterBar('tab-ds-academic-lineages', {  onApply: () => loadDsAcademicLineages() });
    _filtersWired_loadDsAcademicLineages = true;
  }
  setLoading('ds-al-network', 'Inferring mentor → mentee pairs from co-authorship…');
  try {
    const gap = document.getElementById('ds-al-gap').value || 10;
    const params = filterParams('tab-ds-academic-lineages');
    params.set('min_gap', gap);
    const data = await fetchJson('/api/datastories/ch8-academic-lineages?' + params.toString());
    window.__dsAlData = data;
    renderNetwork(data);
    renderTable(data);
  } catch (e) {
    setError('ds-al-network', 'Failed to load: ' + e.message);
  }
}

function dsAcademicLineagesReload() { loadDsAcademicLineages(); }

function renderNetwork(data) {
  const container = d3.select('#ds-al-network');
  container.selectAll('*').remove();
  const w = container.node().clientWidth || 720, h = 560;
  const svg = container.append('svg').attr('width', w).attr('height', h)
    .style('background', '#fdfbf7').style('border', '1px solid #e8e4de');

  const graph = data.graph || { nodes: [], links: [] };
  const nodes = graph.nodes.map(n => ({...n}));
  const links = graph.links.map(l => ({...l}));
  if (!nodes.length) {
    svg.append('text').attr('x', 20).attr('y', 30).attr('fill','#9c9890').text('No mentor pairs at this gap threshold.');
    return;
  }

  const root = enableZoomPan(svg);

  const nodeOutDeg = {};  // mentor -> # mentees
  links.forEach(l => { nodeOutDeg[l.source] = (nodeOutDeg[l.source] || 0) + 1; });

  const sim = d3.forceSimulation(nodes)
    .force('link',  d3.forceLink(links).id(d => d.id).distance(60))
    .force('charge', d3.forceManyBody().strength(-60))
    .force('center', d3.forceCenter(w/2, h/2))
    .force('collide', d3.forceCollide(d => 4 + Math.sqrt(nodeOutDeg[d.id] || 0)));

  const link = root.append('g').selectAll('line').data(links).join('line')
    .attr('stroke', '#a04525').attr('stroke-opacity', 0.4)
    .attr('stroke-width', d => 0.6 + Math.log(d.value || 1));

  const node = root.append('g').selectAll('circle').data(nodes).join('circle')
    .attr('r', d => 3 + Math.sqrt(nodeOutDeg[d.id] || 0))
    .attr('fill', d => (nodeOutDeg[d.id] || 0) > 0 ? '#5a3e28' : '#9c9890')
    .attr('stroke', '#3a3026').attr('stroke-width', 0.4)
    .style('cursor', 'pointer')
    .call(enableDrag(sim))
    .on('click', (e, d) => { if (e.defaultPrevented) return; window.location.href = '/author/' + encodeURIComponent(d.id); });
  node.append('title').text(d => d.id + (d.first_pub ? ' (first pub ' + d.first_pub + ')' : '') +
    (nodeOutDeg[d.id] ? ' — ' + nodeOutDeg[d.id] + ' mentees' : ''));

  // Labels for prolific mentors
  const labelNodes = nodes.filter(n => (nodeOutDeg[n.id] || 0) >= 3);
  const label = root.append('g').selectAll('text').data(labelNodes).join('text')
    .attr('font-size', 10).attr('fill', '#3a3026').attr('text-anchor', 'middle')
    .attr('pointer-events', 'none')
    .attr('paint-order', 'stroke').attr('stroke', '#fdfbf7').attr('stroke-width', 3)
    .text(d => d.id);

  sim.on('tick', () => {
    link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    node.attr('cx', d => d.x).attr('cy', d => d.y);
    label.attr('x', d => d.x).attr('y', d => d.y - 8);
  });

  // Stats
  const s = data.summary || {};
  svg.append('text').attr('x', 12).attr('y', 18).attr('font-size', 11).attr('fill','#7a7268')
    .text((s.n_pairs || 0) + ' mentor pairs · ' + (s.n_mentors || 0) + ' distinct mentors · gap ≥ ' + (s.min_gap || 10) + 'y');
}

function renderTable(data) {
  const el = document.getElementById('ds-al-table');
  const rows = data.prolific_mentors || [];
  if (!rows.length) { el.innerHTML = '<p class="explore-hint">No prolific mentors at this threshold.</p>'; return; }
  let html = '<table class="ds-table" style="width:100%;border-collapse:collapse;font-size:0.84rem;">';
  html += '<thead><tr>';
  ['Mentor','Mentees'].forEach(h => html += '<th style="border-bottom:2px solid #e8e4de;padding:0.4rem 0.5rem;text-align:left;">' + escapeHtml(h) + '</th>');
  html += '</tr></thead><tbody>';
  rows.forEach(r => {
    html += '<tr>';
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;"><a href="/author/${encodeURIComponent(r.mentor)}" style="color:#5a3e28;">${escapeHtml(r.mentor)}</a></td>`;
    html += `<td style="padding:0.3rem 0.5rem;border-bottom:1px solid #f1ede6;">${r.n_mentees}</td>`;
    html += '</tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

window.loadDsAcademicLineages = loadDsAcademicLineages;
window.dsAcademicLineagesReload = dsAcademicLineagesReload;
