// static/js/viz/author_network.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { renderExportToolbar } from "../shared/export.js";
import { enableZoomPan } from "../shared/common.js";
import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { PALETTE, journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


let netZoomBehavior = null;   // stored so Reset view can call it
let netSvgEl       = null;
let netButtonsWired = false;  // ensure reload/reset/close buttons are wired exactly once

let _exportWired_loadNetwork = false;

async function loadNetwork(minPapers, topN) {
  if (!_exportWired_loadNetwork) {
    renderExportToolbar('tab-network', { svgSelector: '#network-container svg', dataProvider: () => (window.__expAuthorNetwork && window.__expAuthorNetwork.nodes || []) });
    _exportWired_loadNetwork = true;
  }
  // Wire the network-tab control buttons on the first call. They were
  // originally wired from explore.js's showTab(); moved here during the
  // F2 split because they need access to module-private state
  // (netSvgEl, netZoomBehavior).
  if (!netButtonsWired) {
    netButtonsWired = true;
    document.getElementById('net-reload-btn').addEventListener('click', () => {
      document.getElementById('net-search').value = '';
      loadNetwork();
    });
    document.getElementById('net-reset-btn').addEventListener('click', () => {
      if (netSvgEl && netZoomBehavior) {
        d3.select(netSvgEl).transition().duration(400)
          .call(netZoomBehavior.transform, d3.zoomIdentity);
      }
    });
    document.getElementById('net-infobar-close').addEventListener('click', () => {
      clearNetInfobar();
    });
  }

  minPapers = minPapers || parseInt(document.getElementById('net-min-papers').value) || 3;
  topN      = topN      || parseInt(document.getElementById('net-top-n').value)      || 150;

  const container = document.getElementById('network-container');
  container.innerHTML = '<div class="loading-msg">Loading author data…</div>';
  clearNetInfobar();

  const resp = await fetch(`/api/stats/author-network?min_papers=${minPapers}&top_n=${topN}`);
  const data = await resp.json();
  window.__expAuthorNetwork = data;

  container.innerHTML = '';

  const nodes = data.nodes;
  const links = data.links;

  // Stats bar
  document.getElementById('net-stats').textContent =
    `${nodes.length} authors · ${links.length} co-authorship links`;

  if (!nodes || nodes.length === 0) {
    container.innerHTML = '<p class="explore-hint">No authors found with those settings. Try lowering the minimum publications threshold.</p>';
    return;
  }

  const W = container.clientWidth || 720;
  const H = 560;

  const countExtent = d3.extent(nodes, d => d.count);
  const rScale = d3.scaleSqrt().domain(countExtent).range([4, 18]);

  const linkExtent = links.length > 0 ? d3.extent(links, d => d.value) : [1, 1];
  const lwScale = d3.scaleLinear().domain(linkExtent).range([0.5, 4]);

  // Build adjacency for highlight
  const neighbors = {};
  nodes.forEach(n => { neighbors[n.id] = new Set(); });
  links.forEach(l => {
    const s = typeof l.source === 'object' ? l.source.id : l.source;
    const t = typeof l.target === 'object' ? l.target.id : l.target;
    neighbors[s] && neighbors[s].add(t);
    neighbors[t] && neighbors[t].add(s);
  });

  const svgSel = d3.select('#network-container')
    .append('svg')
    .attr('width', W)
    .attr('height', H);

  netSvgEl = svgSel.node();
  const g = enableZoomPan(svgSel, { scaleExtent: [0.2, 6] });
  netZoomBehavior = svgSel.node().__dsZoomBehavior;

  const simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links)
      .id(d => d.id)
      .distance(d => 55 / (d.value || 1) + 38)
    )
    .force('charge', d3.forceManyBody().strength(-130))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => rScale(d.count) + 4));

  const linkSel = g.append('g')
    .selectAll('line')
    .data(links)
    .enter().append('line')
      .style('stroke', '#ccc7bb')
      .style('stroke-opacity', 0.55)
      .style('stroke-width', d => lwScale(d.value));

  let selectedId = null;

  const nodeGroup = g.append('g')
    .selectAll('g')
    .data(nodes)
    .enter().append('g')
      .style('cursor', 'pointer')
      .call(d3.drag()
        .on('start', (event, d) => {
          if (!event.active) simulation.alphaTarget(0.3).restart();
          d.fx = d.x; d.fy = d.y;
        })
        .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
        .on('end',  (event, d) => {
          if (!event.active) simulation.alphaTarget(0);
          d.fx = null; d.fy = null;
        })
      )
      .on('click', (event, d) => {
        event.stopPropagation();
        if (selectedId === d.id) {
          // Second click → navigate
          window.location = '/author/' + encodeURIComponent(d.id);
          return;
        }
        selectedId = d.id;
        applyHighlight(d.id, neighbors, nodeGroup, linkSel);
        showNetInfobar(d, neighbors);
      });

  // Click background to clear selection
  svgSel.on('click', () => {
    selectedId = null;
    clearHighlight(nodeGroup, linkSel);
    clearNetInfobar();
  });

  const circleSel = nodeGroup.append('circle')
    .attr('r', d => rScale(d.count))
    .style('fill', d => {
      const idx = (d.id.charCodeAt(d.id.lastIndexOf(' ') + 1) || 0) % PALETTE.length;
      return PALETTE[idx];
    })
    .style('fill-opacity', 0.85)
    .style('stroke', '#fff')
    .style('stroke-width', 1.5);

  // Labels for nodes with count >= 5
  nodeGroup.filter(d => d.count >= 5)
    .append('text')
      .text(d => {
        // Keep the last two words so joint surnames (e.g., "Lopez Garcia",
        // "de la Cruz") aren't truncated to a single token; cap at 22 chars.
        const parts = d.id.split(' ').filter(Boolean);
        const tail = parts.slice(-2).join(' ');
        return tail.length > 22 ? tail.slice(0, 21) + '…' : tail;
      })
      .attr('dy', d => rScale(d.count) + 10)
      .attr('text-anchor', 'middle')
      .style('font-family', 'system-ui, sans-serif')
      .style('font-size', '9px')
      .style('fill', '#6b6760')
      .style('pointer-events', 'none');

  // Tooltip
  const tooltip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip')
    .style('display', 'none');

  nodeGroup
    .on('mouseover', function(event, d) {
      const nb = neighbors[d.id] ? neighbors[d.id].size : 0;
      tooltip.style('display', 'block')
        .html(`<strong>${d.id}</strong><br>${d.count} article${d.count !== 1 ? 's' : ''} · ${nb} co-author${nb !== 1 ? 's' : ''}<br><em style="font-size:0.8em;opacity:0.7">Click to highlight · Click again to open profile</em>`);
    })
    .on('mousemove', function(event) {
      positionTooltip(tooltip, event, { pad: 12, vPad: -28 });
    })
    .on('mouseout', function() { tooltip.style('display', 'none'); });

  simulation.on('tick', () => {
    linkSel
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    nodeGroup.attr('transform', d => `translate(${d.x},${d.y})`);
  });

  // Search highlighting
  document.getElementById('net-search').addEventListener('input', function() {
    const q = this.value.trim().toLowerCase();
    if (!q) { clearHighlight(nodeGroup, linkSel); clearNetInfobar(); return; }
    const matched = nodes.filter(n => n.id.toLowerCase().includes(q));
    if (matched.length === 1) {
      selectedId = matched[0].id;
      applyHighlight(matched[0].id, neighbors, nodeGroup, linkSel);
      showNetInfobar(matched[0], neighbors);
    } else {
      // Dim non-matching nodes
      nodeGroup.style('opacity', d => d.id.toLowerCase().includes(q) ? 1 : 0.15);
      linkSel.style('opacity', 0.1);
    }
  });
}

// ── Inline-handler globals ────────────────────────────────────
window.loadNetwork = loadNetwork;
