// static/js/viz/topics.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


async function loadHeatmap() {
  const resp = await fetch('/api/stats/tag-cooccurrence');
  const data = await resp.json();

  const container = document.getElementById('heatmap-container');
  container.innerHTML = '';

  const tags   = data.tags;
  const matrix = data.matrix;
  const n      = tags.length;

  if (n === 0) {
    container.innerHTML = '<p class="explore-hint">No tag data available.</p>';
    return;
  }

  // Only show top 30 tags (matrix can be huge otherwise)
  const maxTags = Math.min(n, 30);
  const shownTags   = tags.slice(0, maxTags);
  const shownMatrix = matrix.slice(0, maxTags).map(row => row.slice(0, maxTags));

  const margin = { top: 10, right: 10, bottom: 140, left: 140 };
  const cellSize = 18;
  const width  = cellSize * maxTags;
  const height = cellSize * maxTags;

  const maxVal = Math.max(...shownMatrix.flat().filter(v => v > 0));
  const colorScale = d3.scaleSequential()
    .domain([0, Math.log1p(maxVal)])
    .interpolator(d3.interpolate('#f2f0eb', '#5a3e28'));

  const svg = d3.select('#heatmap-container')
    .append('svg')
    .attr('width',  width  + margin.left + margin.right)
    .attr('height', height + margin.top  + margin.bottom)
    .append('g')
    .attr('transform', `translate(${margin.left},${margin.top})`);

  // X axis labels (rotated)
  svg.append('g')
    .selectAll('text')
    .data(shownTags)
    .enter().append('text')
      .attr('x', (d, i) => i * cellSize + cellSize / 2)
      .attr('y', height + 8)
      .attr('text-anchor', 'start')
      .attr('transform', (d, i) => `rotate(45,${i * cellSize + cellSize / 2},${height + 8})`)
      .style('font-family', 'system-ui, sans-serif')
      .style('font-size', '10px')
      .style('fill', '#6b6760')
      .text(d => d.length > 18 ? d.slice(0, 16) + '…' : d);

  // Y axis labels
  svg.append('g')
    .selectAll('text')
    .data(shownTags)
    .enter().append('text')
      .attr('x', -6)
      .attr('y', (d, i) => i * cellSize + cellSize / 2 + 4)
      .attr('text-anchor', 'end')
      .style('font-family', 'system-ui, sans-serif')
      .style('font-size', '10px')
      .style('fill', '#6b6760')
      .text(d => d.length > 20 ? d.slice(0, 18) + '…' : d);

  // Tooltip
  const tooltip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip')
    .style('display', 'none');

  // Cells
  shownMatrix.forEach((row, i) => {
    row.forEach((val, j) => {
      svg.append('rect')
        .attr('x', j * cellSize)
        .attr('y', i * cellSize)
        .attr('width',  cellSize - 1)
        .attr('height', cellSize - 1)
        .attr('rx', 1)
        .style('fill', val > 0 ? colorScale(Math.log1p(val)) : '#f2f0eb')
        .style('cursor', val > 0 ? 'pointer' : 'default')
        .on('mouseover', function(event) {
          if (val > 0) {
            tooltip
              .style('display', 'block')
              .html(`<strong>${shownTags[i]}</strong> &amp; <strong>${shownTags[j]}</strong><br>${val} article${val !== 1 ? 's' : ''}`);
          }
        })
        .on('mousemove', function(event) {
          positionTooltip(tooltip, event, { pad: 12, vPad: -28 });
        })
        .on('mouseout', function() {
          tooltip.style('display', 'none');
        });
    });
  });
}

// ── Inline-handler globals ────────────────────────────────────
window.loadHeatmap = loadHeatmap;
