// ── Accordion navigation ──────────────────────────────────────────────────────
function toggleAccordion(headerBtn) {
  const section = headerBtn.closest('.accordion-section');
  const wasOpen = section.classList.contains('open');
  // Close all sections
  document.querySelectorAll('.accordion-section.open').forEach(s => {
    s.classList.remove('open');
    s.querySelector('.accordion-body').style.maxHeight = '0';
  });
  // Open this one if it wasn't already open
  if (!wasOpen) {
    section.classList.add('open');
    const body = section.querySelector('.accordion-body');
    body.style.maxHeight = body.scrollHeight + 'px';
  }
}

function openAccordionForHash(hash) {
  // Find the accordion card with matching data-hash
  const card = document.querySelector('.accordion-card[data-hash="' + hash + '"]');
  if (!card) return;
  const section = card.closest('.accordion-section');
  if (!section) return;
  // Close all, open this section
  document.querySelectorAll('.accordion-section.open').forEach(s => {
    s.classList.remove('open');
    s.querySelector('.accordion-body').style.maxHeight = '0';
  });
  section.classList.add('open');
  const body = section.querySelector('.accordion-body');
  body.style.maxHeight = body.scrollHeight + 'px';
}

function highlightAccordionCard(hash) {
  // Remove active from all cards
  document.querySelectorAll('.accordion-card.active').forEach(c => c.classList.remove('active'));
  // Activate matching card
  if (hash) {
    const card = document.querySelector('.accordion-card[data-hash="' + hash + '"]');
    if (card) card.classList.add('active');
  }
}

// Wire up accordion card clicks
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.accordion-card[data-tab]').forEach(function(card) {
    card.addEventListener('click', function(e) {
      e.preventDefault();
      const tabName = card.getAttribute('data-tab');
      const hash = card.getAttribute('data-hash');
      // Find the hidden explore-tab button and click it
      const btn = document.querySelector('.explore-tab[data-hash="' + hash + '"]');
      if (btn) btn.click();
    });
  });
});

// ── Tab switching ─────────────────────────────────────────────────────────────
let topicsLoaded       = false;
let timelineLoaded     = false;
let networkLoaded      = false;
let authorcocitLoaded  = false;
let citationsLoaded    = false;
let citTrendsLoaded    = false;
let citnetLoaded       = false;
let centralityLoaded   = false;
let communitiesLoaded  = false;
let cocitationLoaded   = false;
let bibcouplingLoaded  = false;
let sleepersLoaded     = false;
let journalflowLoaded  = false;
let halflifeLoaded     = false;
let mainpathLoaded     = false;
let temporalLoaded     = false;
let readingpathLoaded  = false;
let institutionsLoaded = false;

function showTab(name, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.style.display = 'none');
  document.querySelectorAll('.explore-tab').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).style.display = 'block';
  btn.classList.add('active');
  // Update URL hash for deep-linking
  const hash = btn.getAttribute('data-hash') || name;
  history.replaceState(null, '', '#' + hash);

  if (name === 'timeline' && !timelineLoaded) {
    timelineLoaded = true;
    loadTimeline();
  }
  if (name === 'topics' && !topicsLoaded) {
    topicsLoaded = true;
    loadHeatmap();
  }
  if (name === 'network' && !networkLoaded) {
    networkLoaded = true;
    loadNetwork();
    // Wire controls (safe to call multiple times — buttons only added once)
    document.getElementById('net-reload-btn').addEventListener('click', () => {
      networkLoaded = false; // allow reload
      document.getElementById('net-search').value = '';
      loadNetwork();
      networkLoaded = true;
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
  if (name === 'authorcocit' && !authorcocitLoaded) {
    authorcocitLoaded = true;
    initAuthorCocitation();
  }
  if (name === 'citations' && !citationsLoaded) {
    citationsLoaded = true;
    loadCitations();
  }
  if (name === 'cittrends' && !citTrendsLoaded) {
    citTrendsLoaded = true;
    loadCitTrends();
  }
  if (name === 'citnet' && !citnetLoaded) {
    citnetLoaded = true;
    loadCitationNetwork();
  }
  if (name === 'centrality' && !centralityLoaded) {
    // Don't auto-load — user clicks "Compute" to start
    centralityLoaded = true;
  }
  if (name === 'communities' && !communitiesLoaded) {
    communitiesLoaded = true;
  }
  if (name === 'cocitation' && !cocitationLoaded) {
    // Don't auto-load — user clicks "Compute" to start
    cocitationLoaded = true;
  }
  if (name === 'bibcoupling' && !bibcouplingLoaded) {
    // Don't auto-load — user clicks "Compute" to start
    bibcouplingLoaded = true;
  }
  if (name === 'sleepers' && !sleepersLoaded) {
    // Don't auto-load — user clicks "Compute" to start
    sleepersLoaded = true;
  }
  if (name === 'journalflow' && !journalflowLoaded) {
    // Don't auto-load — user clicks "Compute" to start
    journalflowLoaded = true;
  }
  if (name === 'halflife' && !halflifeLoaded) {
    // Don't auto-load — user clicks "Compute" to start
    halflifeLoaded = true;
  }
  if (name === 'mainpath' && !mainpathLoaded) {
    mainpathLoaded = true;
  }
  if (name === 'temporal' && !temporalLoaded) {
    temporalLoaded = true;
  }
  if (name === 'readingpath' && !readingpathLoaded) {
    readingpathLoaded = true;
    initReadingPath();
  }
  if (name === 'institutions' && !institutionsLoaded) {
    institutionsLoaded = true;
    loadInstitutions();
  }

  // Sync accordion state
  const activeHash = btn.getAttribute('data-hash') || name;
  highlightAccordionCard(activeHash);
  openAccordionForHash(activeHash);
}

// ── Warm palette for journals ─────────────────────────────────────────────────
const PALETTE = [
  '#5a3e28','#8b6045','#b38a6a','#c9a882','#d4bc99',
  '#3a5a28','#456b40','#6b8f60','#8aac82','#b2ccaa',
  '#28445a','#405c78','#608aac','#82aacc','#aaccdd',
  '#5a2840','#7a4560','#ac6882','#cc8aaa','#ddaac4',
  '#5a5828','#787845','#aaaa60','#c8c880','#e0e0a8',
];

function journalColor(i) {
  return PALETTE[i % PALETTE.length];
}

// ── Timeline ──────────────────────────────────────────────────────────────────
let timelineChart = null;
let allSeries = [];
let allYears  = [];
let showingTop8 = false;

async function loadTimeline() {
  const resp = await fetch('/api/stats/timeline');
  const data = await resp.json();
  allYears  = data.years;
  allSeries = data.series;
  renderTimeline(allSeries);
}

function renderTimeline(series) {
  const ctx = document.getElementById('timeline-chart').getContext('2d');

  const datasets = series.map((s, i) => ({
    label: s.journal,
    data:  s.counts,
    backgroundColor: journalColor(i),
    borderWidth: 0,
  }));

  if (timelineChart) timelineChart.destroy();

  timelineChart = new Chart(ctx, {
    type: 'bar',
    data: { labels: allYears, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            font: { family: 'system-ui, sans-serif', size: 11 },
            boxWidth: 12,
            padding: 8,
          }
        },
        tooltip: {
          mode: 'index',
          intersect: false,
        }
      },
      scales: {
        x: {
          stacked: true,
          ticks: { font: { family: 'system-ui, sans-serif', size: 11 } },
        },
        y: {
          stacked: true,
          ticks: { font: { family: 'system-ui, sans-serif', size: 11 } },
        }
      }
    }
  });
}

function toggleJournals() {
  showingTop8 = !showingTop8;
  const btn = document.getElementById('toggle-journals-btn');
  if (showingTop8) {
    // Calculate top 8 by total count
    const totals = allSeries.map(s => ({ s, total: s.counts.reduce((a,b) => a+b, 0) }));
    totals.sort((a, b) => b.total - a.total);
    renderTimeline(totals.slice(0, 8).map(t => t.s));
    btn.textContent = 'Show all journals';
  } else {
    renderTimeline(allSeries);
    btn.textContent = 'Show top 8 journals only';
  }
}

// Timeline loads on demand when its tab is activated


// ── Tag co-occurrence heatmap ─────────────────────────────────────────────────
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


// ── Author co-authorship network ──────────────────────────────────────────────

let netZoomBehavior = null;   // stored so Reset view can call it
let netSvgEl       = null;

async function loadNetwork(minPapers, topN) {
  minPapers = minPapers || parseInt(document.getElementById('net-min-papers').value) || 3;
  topN      = topN      || parseInt(document.getElementById('net-top-n').value)      || 150;

  const container = document.getElementById('network-container');
  container.innerHTML = '<div class="loading-msg">Loading author data…</div>';
  clearNetInfobar();

  const resp = await fetch(`/api/stats/author-network?min_papers=${minPapers}&top_n=${topN}`);
  const data = await resp.json();

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

  netZoomBehavior = d3.zoom().scaleExtent([0.2, 6]).on('zoom', (event) => {
    g.attr('transform', event.transform);
  });

  const svgSel = d3.select('#network-container')
    .append('svg')
    .attr('width', W)
    .attr('height', H)
    .call(netZoomBehavior);

  netSvgEl = svgSel.node();
  const g = svgSel.append('g');

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
      .text(d => { const p = d.id.split(' '); return p[p.length - 1]; })
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

function applyHighlight(id, neighbors, nodeGroup, linkSel) {
  const nb = neighbors[id] || new Set();
  nodeGroup.style('opacity', d => (d.id === id || nb.has(d.id)) ? 1 : 0.12);
  linkSel.style('opacity', l => {
    const s = typeof l.source === 'object' ? l.source.id : l.source;
    const t = typeof l.target === 'object' ? l.target.id : l.target;
    return (s === id || t === id) ? 0.8 : 0.05;
  }).style('stroke', l => {
    const s = typeof l.source === 'object' ? l.source.id : l.source;
    const t = typeof l.target === 'object' ? l.target.id : l.target;
    return (s === id || t === id) ? '#8b6340' : '#ccc7bb';
  });
}

function clearHighlight(nodeGroup, linkSel) {
  nodeGroup.style('opacity', 1);
  linkSel.style('opacity', 0.55).style('stroke', '#ccc7bb');
}

function showNetInfobar(d, neighbors) {
  const nb = neighbors[d.id] ? neighbors[d.id].size : 0;
  const bar = document.getElementById('net-infobar');
  document.getElementById('net-infobar-name').textContent = d.id;
  document.getElementById('net-infobar-detail').textContent =
    `${d.count} article${d.count !== 1 ? 's' : ''} · ${nb} co-author${nb !== 1 ? 's' : ''} in index`;
  document.getElementById('net-infobar-link').href = '/author/' + encodeURIComponent(d.id);
  bar.style.display = 'flex';
}

function clearNetInfobar() {
  document.getElementById('net-infobar').style.display = 'none';
}


// ── Most-Cited rankings ────────────────────────────────────────────────────────
async function loadCitations() {
  const container = document.getElementById('citations-list-container');
  container.innerHTML = '<div class="loading-msg">Loading…</div>';

  const yearFrom = document.getElementById('cite-year-from').value;
  const yearTo   = document.getElementById('cite-year-to').value;
  const journal  = document.getElementById('cite-journal').value;
  const tag      = document.getElementById('cite-tag').value;

  const params = new URLSearchParams({ limit: 50 });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (journal)  params.set('journal',   journal);
  if (tag)      params.set('tag',       tag);

  let data;
  try {
    const resp = await fetch('/api/stats/most-cited?' + params.toString());
    data = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load citation data.</p>';
    return;
  }

  if (!data || data.length === 0) {
    container.innerHTML = '<p class="explore-hint">No citation data yet — run <code>python cite_fetcher.py</code> to populate.</p>';
    return;
  }

  let html = '<ol class="most-cited-list">';
  data.forEach((item, i) => {
    const year = item.pub_date ? item.pub_date.slice(0, 4) : '';
    const authors = item.authors || '';
    const firstAuthor = authors.split(';')[0].trim();
    const lastName = firstAuthor.split(' ').filter(Boolean).pop() || '';
    const multiAuthor = authors.includes(';');
    const byline = lastName
      ? (lastName + (multiAuthor ? ' et al.' : '') + (year ? ` (${year})` : ''))
      : (year || '');
    const count = item.internal_cited_by_count;
    const citesLabel = count === 1 ? '1 citation' : `${count} citations`;

    html += `
      <li class="most-cited-item">
        <span class="most-cited-rank">${i + 1}</span>
        <div class="most-cited-body">
          <div class="most-cited-title">
            <a href="/article/${item.id}">${escapeHtml(item.title)}</a>
          </div>
          <div class="most-cited-meta">
            ${byline ? `<span class="most-cited-byline">${escapeHtml(byline)}</span>` : ''}
            <span class="most-cited-journal">${escapeHtml(item.journal)}</span>
            <span class="most-cited-count">${citesLabel} in this index</span>
          </div>
        </div>
      </li>`;
  });
  html += '</ol>';

  container.innerHTML = html;
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/**
 * Position a tooltip near the cursor, clamped to the viewport so it never
 * overflows off-screen.  Uses clientX/clientY (viewport-relative) because
 * all tooltips use `position: fixed`.
 */
function positionTooltip(tipSel, event, opts) {
  const pad  = opts && opts.pad  != null ? opts.pad  : 14;   // gap between cursor and tooltip
  const vPad = opts && opts.vPad != null ? opts.vPad : -10;  // vertical offset from cursor
  const margin = 8;                          // min distance from viewport edge

  // Make sure the tooltip is visible before measuring
  const el = tipSel.node();
  if (!el) return;
  const rect = el.getBoundingClientRect();
  const tw = rect.width  || 200;   // fallback if still hidden
  const th = rect.height || 60;

  let x = event.clientX + pad;
  let y = event.clientY + vPad;

  // Right edge: flip to left of cursor
  if (x + tw + margin > window.innerWidth) {
    x = event.clientX - tw - pad;
  }
  // Bottom edge: push up
  if (y + th + margin > window.innerHeight) {
    y = window.innerHeight - th - margin;
  }
  // Top edge: push down
  if (y < margin) {
    y = margin;
  }
  // Left edge: push right
  if (x < margin) {
    x = margin;
  }

  tipSel.style('left', x + 'px').style('top', y + 'px');
}


// ── Citation Trends ────────────────────────────────────────────────────────────
let citTrendsChart = null;

async function loadCitTrends() {
  const noteEl = document.getElementById('cittrends-note');
  noteEl.textContent = 'Loading…';

  const journal = document.getElementById('cittrends-journal').value;
  const params  = new URLSearchParams();
  if (journal) params.set('journal', journal);

  let rows;
  try {
    const resp = await fetch('/api/stats/citation-trends?' + params.toString());
    rows = await resp.json();
  } catch (e) {
    noteEl.textContent = 'Failed to load data.';
    return;
  }

  if (!rows || rows.length === 0) {
    noteEl.textContent =
      'No citation data yet — run python cite_fetcher.py to populate.';
    if (citTrendsChart) { citTrendsChart.destroy(); citTrendsChart = null; }
    return;
  }

  const totalArts = rows.reduce((s, r) => s + r.article_count, 0);
  noteEl.textContent = `Based on ${totalArts.toLocaleString()} articles with citation data`;

  const years     = rows.map(r => r.year);
  const avgCites  = rows.map(r => r.avg_cites);
  const artCounts = rows.map(r => r.article_count);
  renderCitTrends(years, avgCites, artCounts);
}

function renderCitTrends(years, avgCites, artCounts) {
  const ctx = document.getElementById('cittrends-chart').getContext('2d');
  if (citTrendsChart) citTrendsChart.destroy();

  citTrendsChart = new Chart(ctx, {
    data: {
      labels: years,
      datasets: [
        {
          type: 'line',
          label: 'Avg. internal citations per article',
          data: avgCites,
          borderColor: '#5a3e28',
          backgroundColor: 'rgba(90,62,40,0.07)',
          fill: true,
          borderWidth: 2,
          pointRadius: 3,
          pointHoverRadius: 5,
          tension: 0.35,
          yAxisID: 'y',
          order: 1,
        },
        {
          type: 'bar',
          label: 'Articles with citation data',
          data: artCounts,
          backgroundColor: 'rgba(90,62,40,0.13)',
          borderWidth: 0,
          yAxisID: 'y2',
          order: 2,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            font: { family: 'system-ui, sans-serif', size: 11 },
            boxWidth: 12,
            padding: 8,
          },
        },
        tooltip: {
          callbacks: {
            label: (ctx) => ctx.datasetIndex === 0
              ? ` ${ctx.parsed.y.toFixed(2)} avg. internal citations`
              : ` ${ctx.parsed.y} articles`,
          },
        },
      },
      scales: {
        x: {
          ticks: { font: { family: 'system-ui, sans-serif', size: 11 } },
        },
        y: {
          type: 'linear',
          position: 'left',
          beginAtZero: true,
          title: {
            display: true,
            text: 'Avg. internal citations / article',
            font: { family: 'system-ui, sans-serif', size: 11 },
            color: '#5a3e28',
          },
          ticks: { font: { family: 'system-ui, sans-serif', size: 11 } },
        },
        y2: {
          type: 'linear',
          position: 'right',
          beginAtZero: true,
          grid: { drawOnChartArea: false },
          title: {
            display: true,
            text: 'Articles with citation data',
            font: { family: 'system-ui, sans-serif', size: 11 },
            color: '#9c9890',
          },
          ticks: {
            font: { family: 'system-ui, sans-serif', size: 11 },
            color: '#9c9890',
          },
        },
      },
    },
  });
}


// ── Citation Network ───────────────────────────────────────────────────────────

// Stable journal→colour lookup keyed by name (alphabetical order = all_journals order)
// ALL_JOURNALS is injected via <script> tag in explore.html before this file loads
const _journalColorMap = {};
(window.ALL_JOURNALS || []).forEach((j, i) => { _journalColorMap[j.name] = PALETTE[i % PALETTE.length]; });
function citnetJournalColor(name) { return _journalColorMap[name] || '#9c9890'; }

let citnetSimulation   = null;
let citnetZoomBehavior = null;
let citnetSvgEl        = null;
let citnetNodeSel      = null;   // d3 selection of <g> node groups
let citnetLinkSel      = null;   // d3 selection of <line> link elements
let citnetNodes        = [];
let citnetLinks        = [];
let citnetDebounce     = null;

// Slider debounce: update label immediately, reload after 700 ms idle
function onCitnetSliderChange(val) {
  document.getElementById('citnet-min-val').textContent = val;
  clearTimeout(citnetDebounce);
  citnetDebounce = setTimeout(loadCitationNetwork, 700);
}

function toggleAllCitnetJournals(cb) {
  document.querySelectorAll('.citnet-journal-check').forEach(c => c.checked = cb.checked);
  updateCitnetJournalCount();
}

function updateCitnetJournalCount() {
  const total   = document.querySelectorAll('.citnet-journal-check').length;
  const checked = document.querySelectorAll('.citnet-journal-check:checked').length;
  document.getElementById('citnet-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}\u202f/\u202f${total})`;
  const allCb = document.getElementById('citnet-check-all');
  if (allCb) allCb.checked = (checked === total);
  if (allCb) allCb.indeterminate = (checked > 0 && checked < total);
}

async function loadCitationNetwork() {
  clearTimeout(citnetDebounce);

  const container = document.getElementById('citnet-container');
  container.innerHTML = '<div class="loading-msg">Loading…</div>';
  document.getElementById('citnet-stats').textContent = '';
  document.getElementById('citnet-legend').innerHTML  = '';
  // Clear previous search highlights
  document.getElementById('citnet-search').value = '';
  document.getElementById('citnet-search-count').textContent = '';

  if (citnetSimulation) { citnetSimulation.stop(); citnetSimulation = null; }
  d3.selectAll('.citnet-tip').remove();

  const minCit   = document.getElementById('citnet-min-slider').value;
  const yearFrom = document.getElementById('citnet-year-from').value;
  const yearTo   = document.getElementById('citnet-year-to').value;
  const checked  = [...document.querySelectorAll('.citnet-journal-check:checked')].map(c => c.value);
  const total    = document.querySelectorAll('.citnet-journal-check').length;

  const params = new URLSearchParams({ min_citations: minCit });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/citations/network?' + params.toString());
    data = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load network data.</p>';
    return;
  }

  if (!data.nodes || data.nodes.length === 0) {
    container.innerHTML =
      '<p class="explore-hint">No articles match these filters — try a lower minimum citation count, ' +
      'or run <code>python cite_fetcher.py</code> to populate citation data.</p>';
    return;
  }

  document.getElementById('citnet-stats').textContent =
    `${data.node_count} articles\u2002·\u2002${data.link_count} citation links`;

  // Legend: unique journals present in this result set
  const seenJournals = [...new Set(data.nodes.map(n => n.journal))].sort();
  document.getElementById('citnet-legend').innerHTML = seenJournals.map(j =>
    `<span class="citnet-legend-item">` +
    `<span class="citnet-legend-dot" style="background:${citnetJournalColor(j)}"></span>` +
    `${escapeHtml(j)}</span>`
  ).join('');

  renderCitationNetwork(container, data);
}

function renderCitationNetwork(container, data) {
  container.innerHTML = '';
  citnetNodes = data.nodes.map(d => ({ ...d }));
  citnetLinks = data.links.map(d => ({ ...d }));

  const W = container.clientWidth  || 820;
  const H = 580;

  const maxCit = d3.max(citnetNodes, d => d.internal_cited_by_count) || 1;
  const rScale = d3.scaleSqrt().domain([0, maxCit]).range([3, 18]);

  // SVG canvas + zoom
  citnetSvgEl = d3.select('#citnet-container')
    .append('svg')
    .attr('width',  W)
    .attr('height', H);

  citnetZoomBehavior = d3.zoom()
    .scaleExtent([0.1, 8])
    .on('zoom', (event) => gRoot.attr('transform', event.transform));

  citnetSvgEl.call(citnetZoomBehavior);
  const gRoot = citnetSvgEl.append('g');

  // Links
  citnetLinkSel = gRoot.append('g').attr('class', 'citnet-links')
    .selectAll('line')
    .data(citnetLinks)
    .enter().append('line')
    .style('stroke',         '#ccc7bb')
    .style('stroke-opacity', 0.35)
    .style('stroke-width',   0.7);

  // Tooltip (one per render; removed at top of loadCitationNetwork on re-render)
  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip citnet-tip')
    .style('display',    'none')
    .style('max-width',  '260px')
    .style('line-height','1.45');

  // Node groups
  const nodeGroup = gRoot.append('g').attr('class', 'citnet-nodes')
    .selectAll('g')
    .data(citnetNodes)
    .enter().append('g')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => {
        if (!event.active) citnetSimulation.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end',   (event, d) => {
        if (!event.active) citnetSimulation.alphaTarget(0);
        d.fx = null; d.fy = null;
      })
    )
    .on('click', (event, d) => {
      event.stopPropagation();
      window.location = '/article/' + d.id;
    })
    .on('mouseover', (event, d) => {
      const year  = d.pub_date ? d.pub_date.slice(0, 4) : '';
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      const last  = first.split(' ').filter(Boolean).pop() || '';
      const byline = last
        + (auth.includes(';') ? ' et al.' : '')
        + (year ? ` (${year})` : '');
      tip.style('display', 'block').html(
        `<strong>${escapeHtml(d.title)}</strong><br>` +
        (byline ? `${escapeHtml(byline)}<br>` : '') +
        `<em>${escapeHtml(d.journal)}</em><br>` +
        `Cited <strong>${d.internal_cited_by_count}</strong>\u00d7 in this index`
      );
    })
    .on('mousemove', (event) => {
      positionTooltip(tip, event, { pad: 14, vPad: -38 });
    })
    .on('mouseout', () => tip.style('display', 'none'));

  citnetNodeSel = nodeGroup;

  // Circles
  nodeGroup.append('circle')
    .attr('r', d => rScale(d.internal_cited_by_count))
    .style('fill',         d => citnetJournalColor(d.journal))
    .style('fill-opacity', 0.85)
    .style('stroke',       '#fff')
    .style('stroke-width', 1.5);

  // Labels for top-cited nodes (top 25% of range or threshold of 3)
  const labelThreshold = Math.max(3, Math.ceil(maxCit * 0.25));
  nodeGroup.filter(d => d.internal_cited_by_count >= labelThreshold)
    .append('text')
    .text(d => {
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      return first.split(' ').filter(Boolean).pop() || '';
    })
    .attr('dy',           d => rScale(d.internal_cited_by_count) + 10)
    .attr('text-anchor',  'middle')
    .style('font-family', 'system-ui, sans-serif')
    .style('font-size',   '9px')
    .style('fill',        '#5a3e28')
    .style('pointer-events', 'none');

  // Dismiss tooltip on background click
  citnetSvgEl.on('click', () => tip.style('display', 'none'));

  // Force simulation
  citnetSimulation = d3.forceSimulation(citnetNodes)
    .force('link', d3.forceLink(citnetLinks)
      .id(d => d.id)
      .distance(50)
      .strength(0.35))
    .force('charge',    d3.forceManyBody().strength(-80))
    .force('center',    d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => rScale(d.internal_cited_by_count) + 2))
    .alphaDecay(0.025)
    .on('tick', () => {
      citnetLinkSel
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeGroup.attr('transform', d => `translate(${d.x},${d.y})`);
    });
}

function searchCitationNetwork(query) {
  if (!citnetNodeSel || citnetNodes.length === 0) return;

  const countEl = document.getElementById('citnet-search-count');
  const q = query.trim().toLowerCase();

  if (!q) {
    citnetNodeSel.select('circle')
      .style('opacity',      1)
      .style('stroke',       '#fff')
      .style('stroke-width', 1.5);
    if (citnetLinkSel) citnetLinkSel.style('stroke-opacity', 0.35);
    countEl.textContent = '';
    return;
  }

  const matchIds = new Set(
    citnetNodes
      .filter(d =>
        (d.title   && d.title.toLowerCase().includes(q)) ||
        (d.authors && d.authors.toLowerCase().includes(q))
      )
      .map(d => d.id)
  );

  citnetNodeSel.select('circle')
    .style('opacity',      d => matchIds.has(d.id) ? 1 : 0.07)
    .style('stroke',       d => matchIds.has(d.id) ? '#e08020' : '#fff')
    .style('stroke-width', d => matchIds.has(d.id) ? 2.5 : 1.5);

  if (citnetLinkSel) {
    citnetLinkSel.style('stroke-opacity', d => {
      const s = typeof d.source === 'object' ? d.source.id : d.source;
      const t = typeof d.target === 'object' ? d.target.id : d.target;
      return (matchIds.has(s) || matchIds.has(t)) ? 0.55 : 0.04;
    });
  }

  const count = matchIds.size;
  countEl.textContent = count ? `${count} match${count !== 1 ? 'es' : ''}` : 'No matches';

  // Pan + zoom to first matching node
  if (count >= 1) {
    const first = citnetNodes.find(d => matchIds.has(d.id));
    if (first && first.x != null && citnetSvgEl && citnetZoomBehavior) {
      const W     = document.getElementById('citnet-container').clientWidth || 820;
      const H     = 580;
      const scale = 2;
      citnetSvgEl.transition().duration(500).call(
        citnetZoomBehavior.transform,
        d3.zoomIdentity
          .translate(W / 2 - first.x * scale, H / 2 - first.y * scale)
          .scale(scale)
      );
    }
  }
}

// ── Centrality ────────────────────────────────────────────────────────────────

let centSimulation   = null;
let centZoomBehavior = null;
let centSvgEl        = null;
let centNodeSel      = null;
let centLinkSel      = null;
let centNodes        = [];
let centLinks        = [];
let centDebounce     = null;
let centData         = null;   // cached API response for re-render on toggle change

// Heat colour scale: warm cream to dark brown
const centHeatScale = d3.scaleSequential(d3.interpolateYlOrBr).domain([0, 1]);

function onCentSliderChange(val) {
  document.getElementById('cent-min-val').textContent = val;
  clearTimeout(centDebounce);
  centDebounce = setTimeout(loadCentrality, 700);
}

function toggleAllCentJournals(cb) {
  document.querySelectorAll('.cent-journal-check').forEach(c => c.checked = cb.checked);
  updateCentJournalCount();
}

function updateCentJournalCount() {
  const total   = document.querySelectorAll('.cent-journal-check').length;
  const checked = document.querySelectorAll('.cent-journal-check:checked').length;
  document.getElementById('cent-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}\u202f/\u202f${total})`;
  const allCb = document.getElementById('cent-check-all');
  if (allCb) allCb.checked = (checked === total);
  if (allCb) allCb.indeterminate = (checked > 0 && checked < total);
}

async function loadCentrality() {
  clearTimeout(centDebounce);

  const container = document.getElementById('cent-container');
  container.innerHTML = '<div class="loading-msg">Computing centrality scores — this may take a few seconds\u2026</div>';
  document.getElementById('cent-stats').textContent = '';
  document.getElementById('cent-legend').innerHTML  = '';
  document.getElementById('cent-tables').style.display = 'none';

  if (centSimulation) { centSimulation.stop(); centSimulation = null; }
  d3.selectAll('.cent-tip').remove();

  const minCit   = document.getElementById('cent-min-slider').value;
  let yearFrom = document.getElementById('cent-year-from').value;
  let yearTo   = document.getElementById('cent-year-to').value;
  // Auto-swap reversed year range
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo)) {
    [yearFrom, yearTo] = [yearTo, yearFrom];
  }
  const checked  = [...document.querySelectorAll('.cent-journal-check:checked')].map(c => c.value);
  const total    = document.querySelectorAll('.cent-journal-check').length;

  const params = new URLSearchParams({ min_citations: minCit });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  try {
    const resp = await fetch('/api/citations/centrality?' + params.toString());
    centData = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load centrality data.</p>';
    return;
  }

  if (!centData.nodes || centData.nodes.length === 0) {
    container.innerHTML =
      '<p class="explore-hint">No articles match these filters \u2014 try a lower minimum citation count, ' +
      'or run <code>python cite_fetcher.py</code> to populate citation data.</p>';
    return;
  }

  renderCentrality(container, centData);
  renderCentralityTables(centData);
}

function renderCentrality(container, data) {
  container.innerHTML = '';
  centNodes = data.nodes.map(d => ({ ...d }));
  centLinks = data.links.map(d => ({ ...d }));

  const sizeMode  = document.getElementById('cent-size-mode').value;
  const colorMode = document.getElementById('cent-color-mode').value;

  // Stats bar
  const sizeLabel = sizeMode === 'eigenvector' ? 'eigenvector'
                  : sizeMode === 'betweenness' ? 'betweenness' : 'citations';
  document.getElementById('cent-stats').textContent =
    `${data.node_count} articles\u2002\u00b7\u2002${data.link_count} citation links\u2002\u00b7\u2002` +
    `sized by ${sizeLabel}`;

  // Legend
  const legendEl = document.getElementById('cent-legend');
  if (colorMode === 'journal') {
    const seenJournals = [...new Set(centNodes.map(n => n.journal))].sort();
    legendEl.innerHTML = seenJournals.map(j =>
      `<span class="citnet-legend-item">` +
      `<span class="citnet-legend-dot" style="background:${citnetJournalColor(j)}"></span>` +
      `${escapeHtml(j)}</span>`
    ).join('');
  } else {
    // Heat scale legend
    const label = colorMode === 'eigenvector' ? 'Eigenvector centrality' : 'Betweenness centrality';
    legendEl.innerHTML =
      `<span class="citnet-legend-item" style="display:flex;align-items:center;gap:0.3rem;">` +
      `<span style="font-size:0.78rem;color:#9c9890;">Low</span>` +
      `<span style="display:inline-block;width:120px;height:12px;border-radius:3px;` +
      `background:linear-gradient(to right,${centHeatScale(0)},${centHeatScale(0.25)},${centHeatScale(0.5)},${centHeatScale(0.75)},${centHeatScale(1)});"></span>` +
      `<span style="font-size:0.78rem;color:#9c9890;">High</span>` +
      `<span style="font-size:0.78rem;color:#9c9890;margin-left:0.5rem;">${label}</span>` +
      `</span>`;
  }

  const W = container.clientWidth  || 820;
  const H = 600;

  // Radius scale
  const maxCit = d3.max(centNodes, n => n.internal_cited_by_count) || 1;
  const citRScale = d3.scaleSqrt().domain([0, maxCit]).range([3, 18]);
  function nodeR(d) {
    if (sizeMode === 'eigenvector') return Math.sqrt(d.eigenvector_centrality) * 22 + 3;
    if (sizeMode === 'betweenness') return Math.sqrt(d.betweenness_centrality) * 22 + 3;
    return citRScale(d.internal_cited_by_count);
  }

  // Colour function
  function nodeColor(d) {
    if (colorMode === 'eigenvector') return centHeatScale(d.eigenvector_centrality);
    if (colorMode === 'betweenness') return centHeatScale(d.betweenness_centrality);
    return citnetJournalColor(d.journal);
  }

  // SVG + zoom
  centSvgEl = d3.select('#cent-container')
    .append('svg')
    .attr('width', W)
    .attr('height', H);

  centZoomBehavior = d3.zoom()
    .scaleExtent([0.1, 8])
    .on('zoom', (event) => gRoot.attr('transform', event.transform));

  centSvgEl.call(centZoomBehavior);
  const gRoot = centSvgEl.append('g');

  // Links
  centLinkSel = gRoot.append('g').attr('class', 'cent-links')
    .selectAll('line')
    .data(centLinks)
    .enter().append('line')
    .style('stroke',         '#ccc7bb')
    .style('stroke-opacity', 0.25)
    .style('stroke-width',   0.6);

  // Tooltip
  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip cent-tip')
    .style('display',    'none')
    .style('max-width',  '300px')
    .style('line-height','1.45');

  // Node groups
  const nodeGroup = gRoot.append('g').attr('class', 'cent-nodes')
    .selectAll('g')
    .data(centNodes)
    .enter().append('g')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => {
        if (!event.active) centSimulation.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end',   (event, d) => {
        if (!event.active) centSimulation.alphaTarget(0);
        d.fx = null; d.fy = null;
      })
    )
    .on('click', (event, d) => {
      event.stopPropagation();
      window.location = '/article/' + d.id;
    })
    .on('mouseover', (event, d) => {
      const year  = d.pub_date ? d.pub_date.slice(0, 4) : '';
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      const last  = first.split(' ').filter(Boolean).pop() || '';
      const byline = last
        + (auth.includes(';') ? ' et al.' : '')
        + (year ? ` (${year})` : '');
      tip.style('display', 'block').html(
        `<strong>${escapeHtml(d.title)}</strong><br>` +
        (byline ? `${escapeHtml(byline)}<br>` : '') +
        `<em>${escapeHtml(d.journal)}</em><br>` +
        `Cited <strong>${d.internal_cited_by_count}</strong>\u00d7 in index<br>` +
        `<span style="color:#b38a6a;">Eigenvector: ${(d.eigenvector_centrality * 100).toFixed(1)}%</span><br>` +
        `<span style="color:#6b8f60;">Betweenness: ${(d.betweenness_centrality * 100).toFixed(1)}%</span>`
      );
    })
    .on('mousemove', (event) => {
      positionTooltip(tip, event, { pad: 14, vPad: -38 });
    })
    .on('mouseout', () => tip.style('display', 'none'));

  centNodeSel = nodeGroup;

  // Circles
  nodeGroup.append('circle')
    .attr('r', d => nodeR(d))
    .style('fill',         d => nodeColor(d))
    .style('fill-opacity', 0.88)
    .style('stroke',       '#fff')
    .style('stroke-width', 1.5);

  // Labels for top 20 nodes by chosen metric
  const sortedBySize = [...centNodes].sort((a, b) => {
    if (sizeMode === 'eigenvector') return b.eigenvector_centrality - a.eigenvector_centrality;
    if (sizeMode === 'betweenness') return b.betweenness_centrality - a.betweenness_centrality;
    return b.internal_cited_by_count - a.internal_cited_by_count;
  });
  const labelIds = new Set(sortedBySize.slice(0, 20).map(n => n.id));

  nodeGroup.filter(d => labelIds.has(d.id))
    .append('text')
    .text(d => {
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      return first.split(' ').filter(Boolean).pop() || '';
    })
    .attr('dy',           d => nodeR(d) + 10)
    .attr('text-anchor',  'middle')
    .style('font-family', 'system-ui, sans-serif')
    .style('font-size',   '9px')
    .style('fill',        '#5a3e28')
    .style('pointer-events', 'none');

  // Dismiss tooltip on background click
  centSvgEl.on('click', () => tip.style('display', 'none'));

  // Force simulation
  centSimulation = d3.forceSimulation(centNodes)
    .force('link', d3.forceLink(centLinks)
      .id(d => d.id)
      .distance(50)
      .strength(0.35))
    .force('charge',    d3.forceManyBody().strength(-90))
    .force('center',    d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => nodeR(d) + 2))
    .alphaDecay(0.025)
    .on('tick', () => {
      centLinkSel
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeGroup.attr('transform', d => `translate(${d.x},${d.y})`);
    });
}

function updateCentralityViz() {
  // Re-render the graph with the same data but different size/color modes
  if (!centData || !centData.nodes || centData.nodes.length === 0) return;
  if (centSimulation) { centSimulation.stop(); centSimulation = null; }
  d3.selectAll('.cent-tip').remove();
  const container = document.getElementById('cent-container');
  renderCentrality(container, centData);
}

function renderCentralityTables(data) {
  const tablesEl = document.getElementById('cent-tables');
  tablesEl.style.display = 'block';

  function renderTable(containerId, items) {
    const ol = document.getElementById(containerId);
    ol.innerHTML = items.map((d, i) => {
      const year  = d.pub_date ? d.pub_date.slice(0, 4) : '';
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      const last  = first.split(' ').filter(Boolean).pop() || '';
      const byline = last + (auth.includes(';') ? ' et al.' : '');
      const metric = containerId.includes('eigen')
        ? (d.eigenvector_centrality * 100).toFixed(1) + '%'
        : (d.betweenness_centrality * 100).toFixed(1) + '%';

      return `<li class="article" style="padding:0.4rem 0;border-bottom:1px solid var(--border-color,#e8e4de);">
        <div style="display:flex;justify-content:space-between;align-items:baseline;gap:0.5rem;">
          <div style="min-width:0;">
            <a href="/article/${d.id}" class="article-title" style="font-size:0.84rem;">${escapeHtml(d.title)}</a>
            <div style="font-size:0.76rem;color:#9c9890;margin-top:0.1rem;">
              ${escapeHtml(byline)}${year ? ' (' + year + ')' : ''} \u2014 <em>${escapeHtml(d.journal)}</em>
              \u2002\u00b7\u2002Cited ${d.internal_cited_by_count}\u00d7
            </div>
          </div>
          <span style="flex-shrink:0;font-weight:600;font-size:0.84rem;color:#5a3e28;font-variant-numeric:tabular-nums;">${metric}</span>
        </div>
      </li>`;
    }).join('');
  }

  renderTable('cent-table-eigen', data.top_eigenvector);
  renderTable('cent-table-between', data.top_betweenness);
}


// ── Co-Citation ──────────────────────────────────────────────────────────────

let cocitSimulation   = null;
let cocitZoomBehavior = null;
let cocitSvgEl        = null;
let cocitNodeSel      = null;
let cocitLinkSel      = null;
let cocitNodes        = [];
let cocitLinks        = [];
let cocitDebounce     = null;

function onCocitSliderChange(val) {
  document.getElementById('cocit-min-val').textContent = val;
  clearTimeout(cocitDebounce);
  cocitDebounce = setTimeout(loadCocitation, 700);
}

function toggleAllCocitJournals(cb) {
  document.querySelectorAll('.cocit-journal-check').forEach(c => c.checked = cb.checked);
  updateCocitJournalCount();
}

function updateCocitJournalCount() {
  const total   = document.querySelectorAll('.cocit-journal-check').length;
  const checked = document.querySelectorAll('.cocit-journal-check:checked').length;
  document.getElementById('cocit-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}\u202f/\u202f${total})`;
  const allCb = document.getElementById('cocit-check-all');
  if (allCb) allCb.checked = (checked === total);
  if (allCb) allCb.indeterminate = (checked > 0 && checked < total);
}

async function loadCocitation() {
  clearTimeout(cocitDebounce);

  const container = document.getElementById('cocit-container');
  container.innerHTML = '<div class="loading-msg">Computing co-citation network\u2026</div>';
  document.getElementById('cocit-stats').textContent = '';
  document.getElementById('cocit-legend').innerHTML  = '';

  if (cocitSimulation) { cocitSimulation.stop(); cocitSimulation = null; }
  d3.selectAll('.cocit-tip').remove();

  const minCocit = document.getElementById('cocit-min-slider').value;
  let yearFrom = document.getElementById('cocit-year-from').value;
  let yearTo   = document.getElementById('cocit-year-to').value;
  // Swap if user entered years in reverse order
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo)) {
    [yearFrom, yearTo] = [yearTo, yearFrom];
    document.getElementById('cocit-year-from').value = yearFrom;
    document.getElementById('cocit-year-to').value   = yearTo;
  }
  const checked  = [...document.querySelectorAll('.cocit-journal-check:checked')].map(c => c.value);
  const total    = document.querySelectorAll('.cocit-journal-check').length;

  const params = new URLSearchParams({ min_cocitations: minCocit });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/citations/cocitation?' + params.toString());
    data = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load co-citation data.</p>';
    return;
  }

  if (!data.nodes || data.nodes.length === 0) {
    const minVal = parseInt(document.getElementById('cocit-min-slider').value);
    let hint = 'No co-citation pairs meet the current threshold.';
    if (minVal > 1) {
      hint += ` Try lowering the minimum co-citations \u2014 recent articles and smaller journals often haven\u2019t accumulated enough co-citations to appear at ${minVal}+.`;
    }
    const yearFrom = document.getElementById('cocit-year-from').value;
    const yearTo   = document.getElementById('cocit-year-to').value;
    if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo)) {
      hint += ' <strong>Note:</strong> your \u201cFrom\u201d year is later than your \u201cTo\u201d year \u2014 the server swapped them automatically, but double-check the range.';
    }
    container.innerHTML = '<p class="explore-hint">' + hint + '</p>';
    return;
  }

  renderCocitation(container, data);
}

function renderCocitation(container, data) {
  container.innerHTML = '';
  cocitNodes = data.nodes.map(d => ({ ...d }));
  cocitLinks = data.links.map(d => ({ ...d }));

  // Stats bar
  const maxWeight = d3.max(cocitLinks, l => l.weight) || 1;
  document.getElementById('cocit-stats').textContent =
    `${data.node_count} articles\u2002\u00b7\u2002${data.link_count} co-citation pairs` +
    `\u2002\u00b7\u2002strongest pair co-cited ${maxWeight}\u00d7`;

  // Legend — colour by journal
  const seenJournals = [...new Set(cocitNodes.map(n => n.journal))].sort();
  const legendEl = document.getElementById('cocit-legend');
  legendEl.innerHTML = seenJournals.map(j =>
    `<span class="citnet-legend-item">` +
    `<span class="citnet-legend-dot" style="background:${citnetJournalColor(j)}"></span>` +
    `${escapeHtml(j)}</span>`
  ).join('');

  const W = container.clientWidth || 820;
  const H = 600;

  // Node radius: by co-citation strength (sum of edge weights)
  const maxStrength = d3.max(cocitNodes, n => n.cocitation_strength) || 1;
  const rScale = d3.scaleSqrt().domain([0, maxStrength]).range([3, 20]);

  // Edge thickness: by co-citation weight
  const edgeScale = d3.scaleLinear().domain([1, Math.max(maxWeight, 2)]).range([0.8, 5]);

  // SVG + zoom
  cocitSvgEl = d3.select('#cocit-container')
    .append('svg')
    .attr('width', W)
    .attr('height', H);

  cocitZoomBehavior = d3.zoom()
    .scaleExtent([0.1, 8])
    .on('zoom', (event) => gRoot.attr('transform', event.transform));

  cocitSvgEl.call(cocitZoomBehavior);
  const gRoot = cocitSvgEl.append('g');

  // Links (undirected, weighted)
  cocitLinkSel = gRoot.append('g').attr('class', 'cocit-links')
    .selectAll('line')
    .data(cocitLinks)
    .enter().append('line')
    .style('stroke',         '#ccc7bb')
    .style('stroke-opacity', d => 0.15 + 0.55 * (d.weight / maxWeight))
    .style('stroke-width',   d => edgeScale(d.weight));

  // Tooltip
  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip cocit-tip')
    .style('display',    'none')
    .style('max-width',  '320px')
    .style('line-height','1.45');

  // Node groups
  const nodeGroup = gRoot.append('g').attr('class', 'cocit-nodes')
    .selectAll('g')
    .data(cocitNodes)
    .enter().append('g')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => {
        if (!event.active) cocitSimulation.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end',   (event, d) => {
        if (!event.active) cocitSimulation.alphaTarget(0);
        d.fx = null; d.fy = null;
      })
    )
    .on('click', (event, d) => {
      event.stopPropagation();
      window.location = '/article/' + d.id;
    })
    .on('mouseover', (event, d) => {
      const year  = d.pub_date ? d.pub_date.slice(0, 4) : '';
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      const last  = first.split(' ').filter(Boolean).pop() || '';
      const byline = last
        + (auth.includes(';') ? ' et al.' : '')
        + (year ? ` (${year})` : '');

      // Find top co-citation partners for this node
      const partners = cocitLinks
        .filter(l => {
          const sid = typeof l.source === 'object' ? l.source.id : l.source;
          const tid = typeof l.target === 'object' ? l.target.id : l.target;
          return sid === d.id || tid === d.id;
        })
        .map(l => {
          const sid = typeof l.source === 'object' ? l.source.id : l.source;
          const tid = typeof l.target === 'object' ? l.target.id : l.target;
          const partnerId = sid === d.id ? tid : sid;
          const partner = cocitNodes.find(n => n.id === partnerId);
          return { name: partner ? partner.title : '?', weight: l.weight };
        })
        .sort((a, b) => b.weight - a.weight)
        .slice(0, 5);

      const partnerHtml = partners.length > 0
        ? '<br><span style="font-size:0.78rem;color:#9c9890;">Top co-cited with:</span>' +
          partners.map(p =>
            `<br><span style="font-size:0.78rem;">\u2022 ${escapeHtml(p.name.length > 60 ? p.name.slice(0, 57) + '\u2026' : p.name)} (${p.weight}\u00d7)</span>`
          ).join('')
        : '';

      tip.style('display', 'block').html(
        `<strong>${escapeHtml(d.title)}</strong><br>` +
        (byline ? `${escapeHtml(byline)}<br>` : '') +
        `<em>${escapeHtml(d.journal)}</em><br>` +
        `Co-citation strength: <strong>${d.cocitation_strength}</strong>` +
        (d.internal_cited_by_count ? ` \u2002\u00b7\u2002 Cited ${d.internal_cited_by_count}\u00d7` : '') +
        partnerHtml
      );

      // Highlight connected edges
      cocitLinkSel.style('stroke-opacity', l => {
        const sid = typeof l.source === 'object' ? l.source.id : l.source;
        const tid = typeof l.target === 'object' ? l.target.id : l.target;
        return (sid === d.id || tid === d.id)
          ? 0.8
          : 0.06;
      });
      nodeGroup.select('circle').style('fill-opacity', n => {
        if (n.id === d.id) return 1;
        const connected = cocitLinks.some(l => {
          const sid = typeof l.source === 'object' ? l.source.id : l.source;
          const tid = typeof l.target === 'object' ? l.target.id : l.target;
          return (sid === d.id && tid === n.id) || (tid === d.id && sid === n.id);
        });
        return connected ? 0.9 : 0.15;
      });
    })
    .on('mousemove', (event) => {
      positionTooltip(tip, event, { pad: 14, vPad: -38 });
    })
    .on('mouseout', () => {
      tip.style('display', 'none');
      cocitLinkSel.style('stroke-opacity', l => 0.15 + 0.55 * (l.weight / maxWeight));
      nodeGroup.select('circle').style('fill-opacity', 0.88);
    });

  cocitNodeSel = nodeGroup;

  // Circles
  nodeGroup.append('circle')
    .attr('r', d => rScale(d.cocitation_strength))
    .style('fill',         d => citnetJournalColor(d.journal))
    .style('fill-opacity', 0.88)
    .style('stroke',       '#fff')
    .style('stroke-width', 1.5);

  // Labels for top 20 nodes by co-citation strength
  const sortedByStrength = [...cocitNodes].sort((a, b) => b.cocitation_strength - a.cocitation_strength);
  const labelIds = new Set(sortedByStrength.slice(0, 20).map(n => n.id));

  nodeGroup.filter(d => labelIds.has(d.id))
    .append('text')
    .text(d => {
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      return first.split(' ').filter(Boolean).pop() || '';
    })
    .attr('dy',           d => rScale(d.cocitation_strength) + 10)
    .attr('text-anchor',  'middle')
    .style('font-family', 'system-ui, sans-serif')
    .style('font-size',   '9px')
    .style('fill',        '#5a3e28')
    .style('pointer-events', 'none');

  // Dismiss tooltip on background click
  cocitSvgEl.on('click', () => {
    tip.style('display', 'none');
    cocitLinkSel.style('stroke-opacity', l => 0.15 + 0.55 * (l.weight / maxWeight));
    nodeGroup.select('circle').style('fill-opacity', 0.88);
  });

  // Force simulation — undirected, weighted
  // Stronger co-citation pulls nodes closer; higher weight = shorter distance
  cocitSimulation = d3.forceSimulation(cocitNodes)
    .force('link', d3.forceLink(cocitLinks)
      .id(d => d.id)
      .distance(d => Math.max(20, 80 - d.weight * 3))
      .strength(d => 0.2 + 0.6 * (d.weight / maxWeight)))
    .force('charge',    d3.forceManyBody().strength(-80))
    .force('center',    d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => rScale(d.cocitation_strength) + 2))
    .alphaDecay(0.025)
    .on('tick', () => {
      cocitLinkSel
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeGroup.attr('transform', d => `translate(${d.x},${d.y})`);
    });
}


// ── Bibliographic Coupling ────────────────────────────────────────────────────

let bibcoupSimulation   = null;
let bibcoupZoomBehavior = null;
let bibcoupSvgEl        = null;
let bibcoupNodeSel      = null;
let bibcoupLinkSel      = null;
let bibcoupNodes        = [];
let bibcoupLinks        = [];
let bibcoupDebounce     = null;

function onBibcoupSliderChange(val) {
  document.getElementById('bibcoup-min-val').textContent = val;
  clearTimeout(bibcoupDebounce);
  bibcoupDebounce = setTimeout(loadBibcoupling, 700);
}

function toggleAllBibcoupJournals(cb) {
  document.querySelectorAll('.bibcoup-journal-check').forEach(c => c.checked = cb.checked);
  updateBibcoupJournalCount();
}

function updateBibcoupJournalCount() {
  const total   = document.querySelectorAll('.bibcoup-journal-check').length;
  const checked = document.querySelectorAll('.bibcoup-journal-check:checked').length;
  document.getElementById('bibcoup-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}\u202f/\u202f${total})`;
  const allCb = document.getElementById('bibcoup-check-all');
  if (allCb) allCb.checked = (checked === total);
  if (allCb) allCb.indeterminate = (checked > 0 && checked < total);
}

async function loadBibcoupling() {
  clearTimeout(bibcoupDebounce);

  const container = document.getElementById('bibcoup-container');
  container.innerHTML = '<div class="loading-msg">Computing bibliographic coupling\u2026</div>';
  document.getElementById('bibcoup-stats').textContent = '';
  document.getElementById('bibcoup-legend').innerHTML  = '';

  if (bibcoupSimulation) { bibcoupSimulation.stop(); bibcoupSimulation = null; }
  d3.selectAll('.bibcoup-tip').remove();

  const minCoup  = document.getElementById('bibcoup-min-slider').value;
  let yearFrom = document.getElementById('bibcoup-year-from').value;
  let yearTo   = document.getElementById('bibcoup-year-to').value;
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo)) {
    [yearFrom, yearTo] = [yearTo, yearFrom];
    document.getElementById('bibcoup-year-from').value = yearFrom;
    document.getElementById('bibcoup-year-to').value   = yearTo;
  }
  const checked  = [...document.querySelectorAll('.bibcoup-journal-check:checked')].map(c => c.value);
  const total    = document.querySelectorAll('.bibcoup-journal-check').length;

  const params = new URLSearchParams({ min_coupling: minCoup });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/citations/bibcoupling?' + params.toString());
    data = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load bibliographic coupling data.</p>';
    return;
  }

  if (!data.nodes || data.nodes.length === 0) {
    const minVal = parseInt(document.getElementById('bibcoup-min-slider').value);
    let hint = 'No bibliographic coupling pairs meet the current threshold.';
    if (minVal > 1) {
      hint += ` Try lowering the minimum shared references \u2014 articles need reference lists deposited with CrossRef to appear here.`;
    }
    container.innerHTML = '<p class="explore-hint">' + hint + '</p>';
    return;
  }

  renderBibcoupling(container, data);
}

function renderBibcoupling(container, data) {
  container.innerHTML = '';
  bibcoupNodes = data.nodes.map(d => ({ ...d }));
  bibcoupLinks = data.links.map(d => ({ ...d }));

  const maxWeight = d3.max(bibcoupLinks, l => l.weight) || 1;
  document.getElementById('bibcoup-stats').textContent =
    `${data.node_count} articles\u2002\u00b7\u2002${data.link_count} coupling pairs` +
    `\u2002\u00b7\u2002strongest pair shares ${maxWeight} references`;

  // Legend — colour by journal
  const seenJournals = [...new Set(bibcoupNodes.map(n => n.journal))].sort();
  const legendEl = document.getElementById('bibcoup-legend');
  legendEl.innerHTML = seenJournals.map(j =>
    `<span class="citnet-legend-item">` +
    `<span class="citnet-legend-dot" style="background:${citnetJournalColor(j)}"></span>` +
    `${escapeHtml(j)}</span>`
  ).join('');

  const W = container.clientWidth || 820;
  const H = 600;

  const maxStrength = d3.max(bibcoupNodes, n => n.coupling_strength) || 1;
  const rScale = d3.scaleSqrt().domain([0, maxStrength]).range([3, 20]);

  const edgeScale = d3.scaleLinear().domain([1, Math.max(maxWeight, 2)]).range([0.8, 5]);

  bibcoupSvgEl = d3.select('#bibcoup-container')
    .append('svg')
    .attr('width', W)
    .attr('height', H);

  bibcoupZoomBehavior = d3.zoom()
    .scaleExtent([0.1, 8])
    .on('zoom', (event) => gRoot.attr('transform', event.transform));

  bibcoupSvgEl.call(bibcoupZoomBehavior);
  const gRoot = bibcoupSvgEl.append('g');

  // Links
  bibcoupLinkSel = gRoot.append('g').attr('class', 'bibcoup-links')
    .selectAll('line')
    .data(bibcoupLinks)
    .enter().append('line')
    .style('stroke',         '#ccc7bb')
    .style('stroke-opacity', d => 0.15 + 0.55 * (d.weight / maxWeight))
    .style('stroke-width',   d => edgeScale(d.weight));

  // Tooltip
  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip bibcoup-tip')
    .style('display',    'none')
    .style('max-width',  '320px')
    .style('line-height','1.45');

  // Node groups
  const nodeGroup = gRoot.append('g').attr('class', 'bibcoup-nodes')
    .selectAll('g')
    .data(bibcoupNodes)
    .enter().append('g')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => {
        if (!event.active) bibcoupSimulation.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end',   (event, d) => {
        if (!event.active) bibcoupSimulation.alphaTarget(0);
        d.fx = null; d.fy = null;
      })
    )
    .on('click', (event, d) => {
      event.stopPropagation();
      window.location = '/article/' + d.id;
    })
    .on('mouseover', (event, d) => {
      const year  = d.pub_date ? d.pub_date.slice(0, 4) : '';
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      const last  = first.split(' ').filter(Boolean).pop() || '';
      const byline = last
        + (auth.includes(';') ? ' et al.' : '')
        + (year ? ` (${year})` : '');

      // Top coupling partners
      const partners = bibcoupLinks
        .filter(l => {
          const sid = typeof l.source === 'object' ? l.source.id : l.source;
          const tid = typeof l.target === 'object' ? l.target.id : l.target;
          return sid === d.id || tid === d.id;
        })
        .map(l => {
          const sid = typeof l.source === 'object' ? l.source.id : l.source;
          const tid = typeof l.target === 'object' ? l.target.id : l.target;
          const partnerId = sid === d.id ? tid : sid;
          const partner = bibcoupNodes.find(n => n.id === partnerId);
          return { name: partner ? partner.title : '?', weight: l.weight };
        })
        .sort((a, b) => b.weight - a.weight)
        .slice(0, 5);

      const partnerHtml = partners.length > 0
        ? '<br><span style="font-size:0.78rem;color:#9c9890;">Most coupled with:</span>' +
          partners.map(p =>
            `<br><span style="font-size:0.78rem;">\u2022 ${escapeHtml(p.name.length > 60 ? p.name.slice(0, 57) + '\u2026' : p.name)} (${p.weight} shared)</span>`
          ).join('')
        : '';

      tip.style('display', 'block').html(
        `<strong>${escapeHtml(d.title)}</strong><br>` +
        (byline ? `${escapeHtml(byline)}<br>` : '') +
        `<em>${escapeHtml(d.journal)}</em><br>` +
        `Coupling strength: <strong>${d.coupling_strength}</strong>` +
        (d.internal_cites_count ? ` \u2002\u00b7\u2002 ${d.internal_cites_count} refs in index` : '') +
        partnerHtml
      );

      // Highlight connected edges
      bibcoupLinkSel.style('stroke-opacity', l => {
        const sid = typeof l.source === 'object' ? l.source.id : l.source;
        const tid = typeof l.target === 'object' ? l.target.id : l.target;
        return (sid === d.id || tid === d.id) ? 0.8 : 0.06;
      });
      nodeGroup.select('circle').style('fill-opacity', n => {
        if (n.id === d.id) return 1;
        const connected = bibcoupLinks.some(l => {
          const sid = typeof l.source === 'object' ? l.source.id : l.source;
          const tid = typeof l.target === 'object' ? l.target.id : l.target;
          return (sid === d.id && tid === n.id) || (tid === d.id && sid === n.id);
        });
        return connected ? 0.9 : 0.15;
      });
    })
    .on('mousemove', (event) => {
      positionTooltip(tip, event, { pad: 14, vPad: -38 });
    })
    .on('mouseout', () => {
      tip.style('display', 'none');
      bibcoupLinkSel.style('stroke-opacity', l => 0.15 + 0.55 * (l.weight / maxWeight));
      nodeGroup.select('circle').style('fill-opacity', 0.88);
    });

  bibcoupNodeSel = nodeGroup;

  // Circles
  nodeGroup.append('circle')
    .attr('r', d => rScale(d.coupling_strength))
    .style('fill',         d => citnetJournalColor(d.journal))
    .style('fill-opacity', 0.88)
    .style('stroke',       '#fff')
    .style('stroke-width', 1.5);

  // Labels for top 20 nodes by coupling strength
  const sortedByStrength = [...bibcoupNodes].sort((a, b) => b.coupling_strength - a.coupling_strength);
  const labelIds = new Set(sortedByStrength.slice(0, 20).map(n => n.id));

  nodeGroup.filter(d => labelIds.has(d.id))
    .append('text')
    .text(d => {
      const auth  = d.authors || '';
      const first = auth.split(';')[0].trim();
      return first.split(' ').filter(Boolean).pop() || '';
    })
    .attr('dy',           d => rScale(d.coupling_strength) + 10)
    .attr('text-anchor',  'middle')
    .style('font-family', 'system-ui, sans-serif')
    .style('font-size',   '9px')
    .style('fill',        '#5a3e28')
    .style('pointer-events', 'none');

  // Dismiss tooltip on background click
  bibcoupSvgEl.on('click', () => {
    tip.style('display', 'none');
    bibcoupLinkSel.style('stroke-opacity', l => 0.15 + 0.55 * (l.weight / maxWeight));
    nodeGroup.select('circle').style('fill-opacity', 0.88);
  });

  // Force simulation
  bibcoupSimulation = d3.forceSimulation(bibcoupNodes)
    .force('link', d3.forceLink(bibcoupLinks)
      .id(d => d.id)
      .distance(d => Math.max(20, 80 - d.weight * 3))
      .strength(d => 0.2 + 0.6 * (d.weight / maxWeight)))
    .force('charge',    d3.forceManyBody().strength(-80))
    .force('center',    d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => rScale(d.coupling_strength) + 2))
    .alphaDecay(0.025)
    .on('tick', () => {
      bibcoupLinkSel
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeGroup.attr('transform', d => `translate(${d.x},${d.y})`);
    });
}


// ── Journal Citation Flow (chord diagram) ────────────────────────────────────

// Journal picker helpers
function toggleAllJflowJournals(master) {
  document.querySelectorAll('.jflow-journal-check').forEach(c => c.checked = master.checked);
  updateJflowJournalCount();
}
function updateJflowJournalCount() {
  const checked = document.querySelectorAll('.jflow-journal-check:checked').length;
  const total   = document.querySelectorAll('.jflow-journal-check').length;
  document.getElementById('jflow-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}/${total})`;
  document.getElementById('jflow-check-all').checked = (checked === total);
}

// Short-name map for long journal titles
const JFLOW_ABBREV = {
  'College Composition and Communication': 'CCC',
  'College English': 'CE',
  'Written Communication': 'WC',
  'Rhetoric Society Quarterly': 'RSQ',
  'Rhetoric Review': 'RR',
  'Technical Communication Quarterly': 'TCQ',
  'Research in the Teaching of English': 'RTE',
  'Journal of Business and Technical Communication': 'JBTC',
  'Journal of Technical Writing and Communication': 'JTWC',
  'Philosophy & Rhetoric': 'P&R',
  'Rhetoric & Public Affairs': 'R&PA',
  'Teaching English in the Two-Year College': 'TETYC',
  'Pedagogy': 'Pedagogy',
  'Community Literacy Journal': 'CLJ',
  'Assessing Writing': 'AW',
  'Business and Professional Communication Quarterly': 'BPCQ',
  'Computers and Composition': 'C&C',
  'Composition Studies': 'CS',
  'Composition Forum': 'CF',
  'Literacy in Composition Studies': 'LiCS',
  'Advances in the History of Rhetoric': 'AHR',
  'Journal of Writing Analytics': 'JWA',
  'Communication Design Quarterly': 'CDQ',
  'Communication Design Quarterly Review': 'CDQR',
  'Rhetoric of Health and Medicine': 'RHM',
  'Double Helix': 'DH',
  'Poroi': 'Poroi',
  'Peitho': 'Peitho',
  'Enculturation': 'Enc',
  'The WAC Journal': 'WAC J',
  'Across the Disciplines': 'AtD',
  'Writing Center Journal': 'WCJ',
  'The Peer Review': 'TPR',
  'Kairos: A Journal of Rhetoric, Technology, and Pedagogy': 'Kairos',
  'KB Journal: The Journal of the Kenneth Burke Society': 'KB J',
  'Present Tense: A Journal of Rhetoric in Society': 'PT',
  'Reflections: A Journal of Community-Engaged Writing and Rhetoric': 'Reflections',
  'Journal of Multimodal Rhetorics': 'JMR',
  'Basic Writing e-Journal': 'BWe-J',
  'Writing on the Edge': 'WoE',
  'Praxis: A Writing Center Journal': 'Praxis',
  'Pre/Text': 'Pre/Text',
  'Prompt: A Journal of Academic Writing Assignments': 'Prompt',
  'Writing Lab Newsletter': 'WLN',
};
function jflowAbbrev(name) { return JFLOW_ABBREV[name] || name; }

async function loadJournalFlow() {
  const container = document.getElementById('jflow-container');
  container.innerHTML = '<div class="loading-msg">Computing journal citation flows\u2026</div>';
  document.getElementById('jflow-stats').textContent = '';

  // Remove old tooltips
  d3.selectAll('.jflow-tip').remove();

  const minCit   = document.getElementById('jflow-min-slider').value;
  let yearFrom = document.getElementById('jflow-year-from').value;
  let yearTo   = document.getElementById('jflow-year-to').value;
  // Auto-swap reversed year range
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo)) {
    [yearFrom, yearTo] = [yearTo, yearFrom];
  }
  const checked  = [...document.querySelectorAll('.jflow-journal-check:checked')].map(c => c.value);
  const total    = document.querySelectorAll('.jflow-journal-check').length;

  const params = new URLSearchParams({ min_citations: minCit });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/citations/journal-flow?' + params.toString());
    data = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load journal citation flow data.</p>';
    return;
  }

  if (!data.journals || data.journals.length === 0) {
    container.innerHTML =
      '<p class="explore-hint">No citation flows match these filters \u2014 try a lower minimum, ' +
      'or run <code>python cite_fetcher.py</code> to populate citation data.</p>';
    return;
  }

  document.getElementById('jflow-stats').textContent =
    `${data.journals.length} journals \u00b7 ${data.total_citations.toLocaleString()} total citations \u00b7 ` +
    `${data.self_citations.toLocaleString()} self-citations (${Math.round(100*data.self_citations/data.total_citations)}%)`;

  renderChordDiagram(container, data);
}

function renderChordDiagram(container, data) {
  container.innerHTML = '';

  const W = container.clientWidth || 820;
  const H = Math.min(W, 750);
  const outerRadius = Math.min(W, H) / 2 - 60;
  const innerRadius = outerRadius - 24;

  const svg = d3.select(container).append('svg')
    .attr('width', W).attr('height', H);

  const g = svg.append('g')
    .attr('transform', `translate(${W/2},${H/2})`);

  // Chord layout
  const chord = d3.chord()
    .padAngle(0.04)
    .sortSubgroups(d3.descending)
    .sortChords(d3.descending);

  const chords = chord(data.matrix);

  const arc    = d3.arc().innerRadius(innerRadius).outerRadius(outerRadius);
  const ribbon = d3.ribbon().radius(innerRadius);

  // Colour: reuse the existing journal colour map
  const color = name => citnetJournalColor(name);

  // Tooltip
  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip jflow-tip')
    .style('opacity', 0).style('pointer-events', 'none');

  // Draw ribbons (chords) first — behind arcs
  const ribbons = g.append('g')
    .attr('class', 'jflow-ribbons')
    .selectAll('path')
    .data(chords)
    .enter().append('path')
    .attr('d', ribbon)
    .style('fill', d => color(data.journals[d.source.index]))
    .style('fill-opacity', 0.55)
    .style('stroke', '#fff')
    .style('stroke-width', 0.3)
    .on('mouseover', function(event, d) {
      // Highlight this chord
      ribbons.style('fill-opacity', r => r === d ? 0.85 : 0.08);
      const src = data.journals[d.source.index];
      const tgt = data.journals[d.target.index];
      const fwd = data.matrix[d.source.index][d.target.index];
      const rev = data.matrix[d.target.index][d.source.index];
      let html;
      if (d.source.index === d.target.index) {
        html = `<strong>${jflowAbbrev(src)}</strong> self-citations: ${fwd.toLocaleString()}`;
      } else {
        const net = fwd - rev;
        const arrow = net > 0 ? '\u2192' : net < 0 ? '\u2190' : '\u2194';
        html = `<strong>${jflowAbbrev(src)}</strong> \u2192 <strong>${jflowAbbrev(tgt)}</strong>: ${fwd.toLocaleString()}<br>` +
               `<strong>${jflowAbbrev(tgt)}</strong> \u2192 <strong>${jflowAbbrev(src)}</strong>: ${rev.toLocaleString()}<br>` +
               `<span style="color:#888;">Net flow ${arrow} ${Math.abs(net).toLocaleString()}</span>`;
      }
      tip.html(html).style('opacity', 1);
      positionTooltip(tip, event);
    })
    .on('mousemove', function(event) {
      positionTooltip(tip, event);
    })
    .on('mouseout', function() {
      ribbons.style('fill-opacity', 0.55);
      tip.style('opacity', 0);
    });

  // Draw outer arcs (groups)
  const groups = g.append('g')
    .attr('class', 'jflow-groups')
    .selectAll('g')
    .data(chords.groups)
    .enter().append('g');

  groups.append('path')
    .attr('d', arc)
    .style('fill', d => color(data.journals[d.index]))
    .style('stroke', '#fff')
    .style('stroke-width', 1)
    .style('cursor', 'pointer')
    .on('mouseover', function(event, d) {
      // Highlight only chords connected to this journal
      ribbons.style('fill-opacity', r =>
        r.source.index === d.index || r.target.index === d.index ? 0.85 : 0.06);
      groups.selectAll('path').style('opacity', g =>
        g.index === d.index ? 1 : 0.3);
      const name = data.journals[d.index];
      const sent = data.matrix[d.index].reduce((a, b) => a + b, 0);
      const received = data.matrix.reduce((a, row) => a + row[d.index], 0);
      const selfCit = data.matrix[d.index][d.index];
      tip.html(
        `<strong>${name}</strong><br>` +
        `Citations sent: ${sent.toLocaleString()}<br>` +
        `Citations received: ${received.toLocaleString()}<br>` +
        `Self-citations: ${selfCit.toLocaleString()}`
      ).style('opacity', 1);
      positionTooltip(tip, event);
    })
    .on('mousemove', function(event) {
      positionTooltip(tip, event);
    })
    .on('mouseout', function() {
      ribbons.style('fill-opacity', 0.55);
      groups.selectAll('path').style('opacity', 1);
      tip.style('opacity', 0);
    });

  // Labels along arcs
  groups.append('text')
    .each(d => { d.angle = (d.startAngle + d.endAngle) / 2; })
    .attr('dy', '0.35em')
    .attr('transform', d =>
      `rotate(${(d.angle * 180 / Math.PI - 90)})` +
      `translate(${outerRadius + 8})` +
      (d.angle > Math.PI ? 'rotate(180)' : '')
    )
    .attr('text-anchor', d => d.angle > Math.PI ? 'end' : 'start')
    .style('font-size', '0.72rem')
    .style('font-family', 'var(--font-ui, sans-serif)')
    .style('fill', 'var(--text, #3a2e1f)')
    .text(d => {
      // Hide label if arc is too small
      const arcLen = d.endAngle - d.startAngle;
      if (arcLen < 0.08) return '';
      return jflowAbbrev(data.journals[d.index]);
    });
}


// ── Citation Half-Life ────────────────────────────────────────────────────────

let hlChart = null;

// Journal picker helpers
function toggleAllHlJournals(master) {
  document.querySelectorAll('.hl-journal-check').forEach(c => c.checked = master.checked);
  updateHlJournalCount();
}
function updateHlJournalCount() {
  const checked = document.querySelectorAll('.hl-journal-check:checked').length;
  const total   = document.querySelectorAll('.hl-journal-check').length;
  document.getElementById('hl-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}/${total})`;
  document.getElementById('hl-check-all').checked = (checked === total);
}

async function loadHalfLife() {
  const container = document.getElementById('hl-container');
  container.innerHTML = '<div class="loading-msg">Computing citation half-life\u2026</div>';
  document.getElementById('hl-stats').textContent = '';
  if (hlChart) { hlChart.destroy(); hlChart = null; }

  let yearFrom = document.getElementById('hl-year-from').value;
  let yearTo   = document.getElementById('hl-year-to').value;
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo)) {
    [yearFrom, yearTo] = [yearTo, yearFrom];
  }
  const checked = [...document.querySelectorAll('.hl-journal-check:checked')].map(c => c.value);
  const total   = document.querySelectorAll('.hl-journal-check').length;
  const viewMode = document.getElementById('hl-view-mode').value;

  const params = new URLSearchParams();
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));
  if (viewMode === 'timeseries')   params.set('timeseries',   '1');
  if (viewMode === 'distribution') params.set('distribution', '1');

  let data;
  try {
    const resp = await fetch('/api/citations/half-life?' + params.toString());
    data = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load half-life data.</p>';
    return;
  }

  if (!data.journals || data.journals.length === 0) {
    container.innerHTML =
      '<p class="explore-hint">No citation data available for these filters \u2014 ' +
      'try widening the year range or run <code>python cite_fetcher.py</code>.</p>';
    return;
  }

  // Stats summary
  const withCiting = data.journals.filter(j => j.citing_half_life !== null);
  const withCited  = data.journals.filter(j => j.cited_half_life  !== null);
  document.getElementById('hl-stats').textContent =
    `${data.journals.length} journals \u00b7 ${data.total_citations.toLocaleString()} citations analysed \u00b7 ` +
    `${withCiting.length} with citing data \u00b7 ${withCited.length} with cited data`;

  if (viewMode === 'comparison') {
    renderHalfLifeComparison(container, data);
  } else if (viewMode === 'timeseries') {
    renderHalfLifeTimeseries(container, data);
  } else if (viewMode === 'distribution') {
    renderHalfLifeDistribution(container, data);
  }
}

function renderHalfLifeComparison(container, data) {
  // Filter to journals that have at least one metric, sort by citing half-life desc
  const journals = data.journals
    .filter(j => j.citing_half_life !== null || j.cited_half_life !== null)
    .sort((a, b) => (b.citing_half_life || 0) - (a.citing_half_life || 0));

  if (journals.length === 0) {
    container.innerHTML = '<p class="explore-hint">No half-life data available.</p>';
    return;
  }

  const labels = journals.map(j => jflowAbbrev(j.name));
  const fullNames = journals.map(j => j.name);

  // IQR bars as floating bars [q25, q75], median as a point overlay
  const citingIQR = journals.map(j =>
    j.citing_half_life !== null ? [j.citing_q25 || 0, j.citing_q75 || 0] : [0, 0]);
  const citedIQR = journals.map(j =>
    j.cited_half_life !== null ? [j.cited_q25 || 0, j.cited_q75 || 0] : [0, 0]);
  const citingMedian = journals.map(j => j.citing_half_life);
  const citedMedian  = journals.map(j => j.cited_half_life);

  const h = Math.max(400, journals.length * 42 + 80);
  container.innerHTML = `<canvas id="hl-canvas" style="width:100%;height:${h}px;"></canvas>`;
  const ctx = document.getElementById('hl-canvas').getContext('2d');

  hlChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Citing IQR',
          data: citingIQR,
          backgroundColor: 'rgba(90, 62, 40, 0.18)',
          borderColor: 'rgba(90, 62, 40, 0.3)',
          borderWidth: 1,
          borderSkipped: false,
          barPercentage: 0.7,
          categoryPercentage: 0.8,
          order: 2,
        },
        {
          label: 'Citing half-life (median)',
          data: citingMedian,
          type: 'scatter',
          backgroundColor: '#5a3e28',
          borderColor: '#fff',
          borderWidth: 1.5,
          pointRadius: 6,
          pointStyle: 'circle',
          order: 1,
        },
        {
          label: 'Cited IQR',
          data: citedIQR,
          backgroundColor: 'rgba(58, 90, 40, 0.18)',
          borderColor: 'rgba(58, 90, 40, 0.3)',
          borderWidth: 1,
          borderSkipped: false,
          barPercentage: 0.7,
          categoryPercentage: 0.8,
          order: 2,
        },
        {
          label: 'Cited half-life (median)',
          data: citedMedian,
          type: 'scatter',
          backgroundColor: '#3a5a28',
          borderColor: '#fff',
          borderWidth: 1.5,
          pointRadius: 6,
          pointStyle: 'triangle',
          order: 1,
        },
      ],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      layout: { padding: { right: 20 } },
      scales: {
        x: {
          title: { display: true, text: 'Years', font: { size: 13 } },
          beginAtZero: true,
          grid: { color: 'rgba(0,0,0,0.06)' },
        },
        y: {
          grid: { display: false },
          ticks: { font: { size: 12 } },
        },
      },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: {
            filter: item => item.text.includes('median'),
            usePointStyle: true,
            pointStyle: (ctx) => ctx.dataset?.pointStyle || 'rect',
            font: { size: 12 },
          },
        },
        tooltip: {
          callbacks: {
            title: (items) => {
              const idx = items[0].dataIndex;
              return fullNames[idx];
            },
            label: (item) => {
              const idx = item.dataIndex;
              const j = journals[idx];
              if (item.dataset.label.startsWith('Citing')) {
                if (j.citing_half_life === null) return 'Citing: no data';
                return `Citing: ${j.citing_half_life} yr (IQR ${j.citing_q25}\u2013${j.citing_q75}, n=${j.citing_count.toLocaleString()})`;
              } else {
                if (j.cited_half_life === null) return 'Cited: no data';
                return `Cited: ${j.cited_half_life} yr (IQR ${j.cited_q25}\u2013${j.cited_q75}, n=${j.cited_count.toLocaleString()})`;
              }
            },
          },
        },
      },
    },
  });
}

function renderHalfLifeTimeseries(container, data) {
  if (!data.timeseries || Object.keys(data.timeseries).length === 0) {
    container.innerHTML = '<p class="explore-hint">Not enough data for a time series \u2014 ' +
      'each journal needs at least 10 citations per year to plot a reliable median.</p>';
    return;
  }

  // Collect all years across all journals
  const allYears = new Set();
  const journalNames = Object.keys(data.timeseries).sort();
  journalNames.forEach(j => {
    (data.timeseries[j].citing || []).forEach(d => allYears.add(d.year));
  });
  const years = [...allYears].sort();

  const datasets = journalNames.map(jname => {
    const ts = data.timeseries[jname].citing || [];
    const yearMap = {};
    ts.forEach(d => yearMap[d.year] = d.half_life);
    return {
      label: jflowAbbrev(jname),
      data: years.map(y => yearMap[y] !== undefined ? yearMap[y] : null),
      borderColor: citnetJournalColor(jname),
      backgroundColor: citnetJournalColor(jname),
      fill: false,
      tension: 0.3,
      pointRadius: 3,
      spanGaps: true,
      borderWidth: 2,
    };
  });

  container.innerHTML = '<canvas id="hl-canvas" style="width:100%;height:500px;"></canvas>';
  const ctx = document.getElementById('hl-canvas').getContext('2d');

  hlChart = new Chart(ctx, {
    type: 'line',
    data: { labels: years, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: false },
      scales: {
        x: {
          title: { display: true, text: 'Year of citing article', font: { size: 13 } },
          grid: { color: 'rgba(0,0,0,0.06)' },
        },
        y: {
          title: { display: true, text: 'Citing half-life (years)', font: { size: 13 } },
          beginAtZero: true,
          grid: { color: 'rgba(0,0,0,0.06)' },
        },
      },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: { usePointStyle: true, font: { size: 11 } },
        },
        tooltip: {
          callbacks: {
            title: items => `Year: ${items[0].label}`,
            label: item => {
              const jname = journalNames[item.datasetIndex];
              const ts = data.timeseries[jname].citing || [];
              const point = ts.find(d => d.year == item.label);
              const n = point ? point.count : '?';
              return `${jflowAbbrev(jname)}: ${item.parsed.y} yr (n=${n})`;
            },
          },
        },
      },
    },
  });
}

function renderHalfLifeDistribution(container, data) {
  if (!data.distributions || Object.keys(data.distributions).length === 0) {
    container.innerHTML = '<p class="explore-hint">No distribution data available.</p>';
    return;
  }

  // Pick journals that have citing distribution data, limit to top 8 by citation count
  const eligible = data.journals
    .filter(j => j.citing_count > 0)
    .sort((a, b) => b.citing_count - a.citing_count)
    .slice(0, 8);

  if (eligible.length === 0) {
    container.innerHTML = '<p class="explore-hint">No citing distribution data available.</p>';
    return;
  }

  // Find max age across all distributions (cap at 50)
  let maxAge = 0;
  eligible.forEach(j => {
    const dist = data.distributions[j.name]?.citing || [];
    dist.forEach(d => { if (d.age <= 50 && d.age > maxAge) maxAge = d.age; });
  });
  const ages = Array.from({length: maxAge + 1}, (_, i) => i);

  const datasets = eligible.map(j => {
    const dist = data.distributions[j.name]?.citing || [];
    const ageMap = {};
    dist.forEach(d => { if (d.age <= 50) ageMap[d.age] = d.count; });
    // Normalize to percentage of total for comparability
    const total = dist.reduce((s, d) => s + (d.age <= 50 ? d.count : 0), 0);
    return {
      label: jflowAbbrev(j.name),
      data: ages.map(a => ageMap[a] !== undefined ? Math.round(ageMap[a] / total * 1000) / 10 : 0),
      borderColor: citnetJournalColor(j.name),
      backgroundColor: citnetJournalColor(j.name) + '33',
      fill: true,
      tension: 0.4,
      pointRadius: 0,
      borderWidth: 2,
    };
  });

  container.innerHTML = '<canvas id="hl-canvas" style="width:100%;height:500px;"></canvas>';
  const ctx = document.getElementById('hl-canvas').getContext('2d');

  hlChart = new Chart(ctx, {
    type: 'line',
    data: { labels: ages, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'nearest', intersect: false },
      scales: {
        x: {
          title: { display: true, text: 'Citation age (years)', font: { size: 13 } },
          grid: { color: 'rgba(0,0,0,0.06)' },
        },
        y: {
          title: { display: true, text: '% of citations at this age', font: { size: 13 } },
          beginAtZero: true,
          grid: { color: 'rgba(0,0,0,0.06)' },
        },
      },
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: { usePointStyle: true, font: { size: 11 } },
        },
        tooltip: {
          callbacks: {
            title: items => `Age: ${items[0].label} years`,
            label: item => `${item.dataset.label}: ${item.parsed.y}%`,
          },
        },
      },
    },
  });
}


// ── Community Detection ──────────────────────────────────────────────────────

const COMM_PALETTE = [
  '#8b6045','#3a5a28','#4a6a8a','#8a4a5a','#6a5a8a',
  '#5a7a4a','#8a7a3a','#3a6a6a','#7a4a3a','#5a5a6a',
  '#7a6a2a','#3a4a7a',
];

let commSimulation = null;

function toggleAllCommJournals(master) {
  document.querySelectorAll('.comm-journal-check').forEach(c => c.checked = master.checked);
  updateCommJournalCount();
}
function updateCommJournalCount() {
  const checked = document.querySelectorAll('.comm-journal-check:checked').length;
  const total   = document.querySelectorAll('.comm-journal-check').length;
  document.getElementById('comm-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}/${total})`;
  document.getElementById('comm-check-all').checked = (checked === total);
}

async function loadCommunities() {
  const container = document.getElementById('comm-container');
  container.innerHTML = '<div class="loading-msg">Running community detection\u2026</div>';
  document.getElementById('comm-stats').textContent = '';
  document.getElementById('comm-legend').innerHTML = '';
  document.getElementById('comm-tables').style.display = 'none';
  if (commSimulation) { commSimulation.stop(); commSimulation = null; }
  d3.selectAll('.comm-tip').remove();

  const minCit = document.getElementById('comm-min-slider').value;
  const resolution = document.getElementById('comm-res-slider').value;
  let yearFrom = document.getElementById('comm-year-from').value;
  let yearTo   = document.getElementById('comm-year-to').value;
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo))
    [yearFrom, yearTo] = [yearTo, yearFrom];
  const checked = [...document.querySelectorAll('.comm-journal-check:checked')].map(c => c.value);
  const total   = document.querySelectorAll('.comm-journal-check').length;

  const params = new URLSearchParams({ min_citations: minCit, resolution });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/citations/communities?' + params.toString());
    data = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load community data.</p>';
    return;
  }

  if (!data.nodes || data.nodes.length === 0) {
    container.innerHTML =
      '<p class="explore-hint">No articles match these filters \u2014 try a lower minimum.</p>';
    return;
  }

  document.getElementById('comm-stats').textContent =
    `${data.node_count} articles \u00b7 ${data.link_count} citation links \u00b7 ` +
    `${data.community_count} communities \u00b7 modularity Q\u2009=\u2009${data.modularity}`;

  renderCommunities(container, data);
  renderCommunityTables(data);
}

function renderCommunities(container, data) {
  container.innerHTML = '';
  const commColor = i => COMM_PALETTE[i % COMM_PALETTE.length];

  // Legend
  const legendEl = document.getElementById('comm-legend');
  legendEl.innerHTML = data.communities.map(c => {
    const label = c.topics.length ? c.topics.slice(0,2).join(', ') : `Community ${c.id}`;
    return `<span class="citnet-legend-item" data-comm="${c.id}" style="cursor:pointer;">` +
      `<span class="citnet-legend-dot" style="background:${commColor(c.id)}"></span>` +
      `${label} (${c.size})</span>`;
  }).join('');

  const W = container.clientWidth || 820;
  const H = 600;
  const nodes = data.nodes.map(d => ({ ...d }));
  const links = data.links.map(d => ({ ...d }));

  const citExtent = d3.extent(nodes, d => d.internal_cited_by_count);
  const rScale = d3.scaleSqrt().domain([0, citExtent[1] || 1]).range([3, 18]);

  const svg = d3.select(container).append('svg').attr('width', W).attr('height', H);
  const g = svg.append('g');

  const zoom = d3.zoom().scaleExtent([0.2, 5])
    .on('zoom', e => g.attr('transform', e.transform));
  svg.call(zoom);

  const linkSel = g.append('g').selectAll('line')
    .data(links).enter().append('line')
    .style('stroke', '#ccc7bb').style('stroke-opacity', 0.4)
    .style('stroke-width', d => Math.max(0.5, Math.min(d.weight * 1.5, 4)));

  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip comm-tip')
    .style('opacity', 0).style('pointer-events', 'none');

  const nodeGroup = g.append('g').selectAll('g')
    .data(nodes).enter().append('g')
    .style('cursor', 'pointer')
    .on('click', (e, d) => window.open('/article/' + d.id, '_blank'))
    .on('mouseover', (event, d) => {
      const yr = (d.pub_date || '').substring(0, 4);
      const comm = data.communities.find(c => c.id === d.community);
      const commLabel = comm && comm.topics.length ? comm.topics.slice(0,3).join(', ') : `Community ${d.community}`;
      tip.html(
        `<strong>${d.title || 'Untitled'}</strong><br>` +
        `${d.authors || ''} (${yr})<br>` +
        `<em>${d.journal || ''}</em><br>` +
        `Cited ${d.internal_cited_by_count}\u00d7 internally<br>` +
        `<span style="color:${commColor(d.community)};">\u25CF</span> ${commLabel}`
      ).style('opacity', 1);
      positionTooltip(tip, event);
    })
    .on('mousemove', event => {
      positionTooltip(tip, event);
    })
    .on('mouseout', () => tip.style('opacity', 0))
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) commSimulation.alphaTarget(0.2).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag',  (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end',   (e, d) => { if (!e.active) commSimulation.alphaTarget(0); d.fx = null; d.fy = null; })
    );

  nodeGroup.append('circle')
    .attr('r', d => rScale(d.internal_cited_by_count))
    .style('fill', d => commColor(d.community))
    .style('stroke', '#fff').style('stroke-width', 0.8);

  // Build link-strength function: stronger within community
  const nodeIdx = {};
  nodes.forEach((n, i) => nodeIdx[n.id] = i);

  commSimulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links)
      .id(d => d.id)
      .distance(60)
      .strength(d => {
        const sComm = nodes.find(n => n.id === (d.source.id || d.source))?.community;
        const tComm = nodes.find(n => n.id === (d.target.id || d.target))?.community;
        return sComm === tComm ? 0.4 : 0.05;
      }))
    .force('charge', d3.forceManyBody().strength(-60))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => rScale(d.internal_cited_by_count) + 2))
    .on('tick', () => {
      linkSel.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
             .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeGroup.attr('transform', d => `translate(${d.x},${d.y})`);
    });

  // Legend click to highlight a community
  legendEl.querySelectorAll('.citnet-legend-item').forEach(el => {
    el.addEventListener('click', () => {
      const cid = parseInt(el.dataset.comm);
      const active = el.classList.toggle('comm-highlight');
      if (active) {
        nodeGroup.style('opacity', d => d.community === cid ? 1 : 0.08);
        linkSel.style('stroke-opacity', d => {
          const s = typeof d.source === 'object' ? d.source : nodes.find(n => n.id === d.source);
          const t = typeof d.target === 'object' ? d.target : nodes.find(n => n.id === d.target);
          return (s?.community === cid || t?.community === cid) ? 0.6 : 0.02;
        });
        legendEl.querySelectorAll('.citnet-legend-item').forEach(e => {
          if (e !== el) e.classList.remove('comm-highlight');
        });
      } else {
        nodeGroup.style('opacity', 1);
        linkSel.style('stroke-opacity', 0.4);
      }
    });
  });
}

function renderCommunityTables(data) {
  const tablesEl = document.getElementById('comm-tables');
  if (!data.communities.length) { tablesEl.style.display = 'none'; return; }
  const commColor = i => COMM_PALETTE[i % COMM_PALETTE.length];

  let html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;">';
  for (const c of data.communities) {
    const topics = c.topics.map(t =>
      `<span style="display:inline-block;padding:0.1rem 0.45rem;font-size:0.72rem;background:#f5f0e8;border-radius:3px;color:#5a3e28;margin:0.1rem 0.15rem;">${t}</span>`
    ).join('');
    const journals = c.top_journals.map(j => `${j.name} (${j.count})`).join(', ');
    const articles = c.top_articles.map(a => {
      const yr = (a.pub_date || '').substring(0, 4);
      return `<li style="margin-bottom:0.3rem;"><a href="/article/${a.id}" style="color:#5a3e28;">${a.title || 'Untitled'}</a> <span style="color:#999;">${yr} &middot; cited ${a.internal_cited_by_count}&times;</span></li>`;
    }).join('');

    html += `<div style="border:1px solid #e5ddd0;border-radius:6px;padding:0.8rem;background:#faf8f4;">
      <h4 style="margin:0 0 0.4rem;font-size:0.9rem;">
        <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${commColor(c.id)};margin-right:0.3rem;"></span>
        Community ${c.id + 1} <span style="font-weight:normal;color:#999;">(${c.size} articles)</span>
      </h4>
      <div style="margin-bottom:0.4rem;">${topics}</div>
      <div style="font-size:0.78rem;color:#888;margin-bottom:0.4rem;">${journals}</div>
      <ol style="font-size:0.82rem;padding-left:1.2rem;margin:0;">${articles}</ol>
    </div>`;
  }
  html += '</div>';
  tablesEl.innerHTML = html;
  tablesEl.style.display = 'block';
}


// ── Main Path Analysis ───────────────────────────────────────────────────────

function toggleAllMpJournals(master) {
  document.querySelectorAll('.mp-journal-check').forEach(c => c.checked = master.checked);
  updateMpJournalCount();
}
function updateMpJournalCount() {
  const checked = document.querySelectorAll('.mp-journal-check:checked').length;
  const total   = document.querySelectorAll('.mp-journal-check').length;
  document.getElementById('mp-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}/${total})`;
  document.getElementById('mp-check-all').checked = (checked === total);
}

async function loadMainPath() {
  const container = document.getElementById('mp-container');
  container.innerHTML = '<div class="loading-msg">Building citation DAG and computing main path\u2026</div>';
  document.getElementById('mp-stats').textContent = '';

  const minCit = document.getElementById('mp-min-slider').value;
  let yearFrom = document.getElementById('mp-year-from').value;
  let yearTo   = document.getElementById('mp-year-to').value;
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo))
    [yearFrom, yearTo] = [yearTo, yearFrom];
  const checked = [...document.querySelectorAll('.mp-journal-check:checked')].map(c => c.value);
  const total   = document.querySelectorAll('.mp-journal-check').length;

  const params = new URLSearchParams({ min_citations: minCit });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/citations/main-path?' + params.toString());
    data = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load main path data.</p>';
    return;
  }

  if (!data.path || data.path.length < 2) {
    container.innerHTML =
      '<p class="explore-hint">No main path found \u2014 the citation DAG may be too sparse. ' +
      'Try lowering the minimum citation threshold or widening the year range.</p>';
    return;
  }

  const s = data.stats;
  document.getElementById('mp-stats').textContent =
    `DAG: ${s.dag_nodes} articles, ${s.dag_edges} citation links \u00b7 ` +
    `Main path: ${s.path_length} articles \u00b7 ` +
    `${s.source_count} frontier \u00b7 ${s.sink_count} foundational` +
    (s.cycles_removed ? ` \u00b7 ${s.cycles_removed} cycles removed` : '');

  renderMainPath(container, data);
}

function renderMainPath(container, data) {
  container.innerHTML = '';

  const path = data.path;
  const edges = data.edges;

  // Render as a vertical timeline of article cards
  const el = document.createElement('div');
  el.style.cssText = 'max-width:44rem;margin:0 auto;padding:1rem 0;';

  for (let i = 0; i < path.length; i++) {
    const node = path[i];
    const yr = (node.pub_date || '').substring(0, 4);
    const authors = node.authors || '';
    const firstAuthor = authors.split(',')[0].split(';')[0].trim();
    const title = node.title || 'Untitled';
    const jColor = citnetJournalColor(node.journal);

    // Edge weight bar (between nodes)
    if (i > 0 && edges[i - 1]) {
      const e = edges[i - 1];
      const barW = Math.max(8, Math.round(e.spc_normalized * 100));
      const edgeDiv = document.createElement('div');
      edgeDiv.style.cssText = 'display:flex;align-items:center;justify-content:center;padding:0.3rem 0;';
      edgeDiv.innerHTML =
        `<div style="width:2px;height:20px;background:#b38a6a;"></div>` +
        `<span style="font-size:0.68rem;color:#999;margin-left:0.5rem;">SPC ${e.spc_weight.toLocaleString()}</span>`;
      el.appendChild(edgeDiv);
    }

    // Article card
    const card = document.createElement('a');
    card.href = '/article/' + node.id;
    card.target = '_blank';
    card.style.cssText =
      'display:block;border:1px solid #e5ddd0;border-left:4px solid ' + jColor +
      ';border-radius:6px;padding:0.7rem 1rem;background:#faf8f4;' +
      'text-decoration:none;color:#3a2e1f;transition:box-shadow 0.15s;';
    card.onmouseover = () => card.style.boxShadow = '0 2px 8px rgba(0,0,0,0.1)';
    card.onmouseout  = () => card.style.boxShadow = 'none';

    const posLabel = i === 0 ? '<span style="color:#3a5a28;font-weight:600;font-size:0.72rem;">FRONTIER</span> ' :
                     i === path.length - 1 ? '<span style="color:#8b6045;font-weight:600;font-size:0.72rem;">FOUNDATION</span> ' : '';

    card.innerHTML =
      `<div style="display:flex;justify-content:space-between;align-items:baseline;">` +
        `<div style="font-weight:600;font-size:0.92rem;line-height:1.35;flex:1;">${posLabel}${title}</div>` +
        `<span style="font-size:0.78rem;color:#999;white-space:nowrap;margin-left:0.8rem;">cited ${node.internal_cited_by_count}\u00d7</span>` +
      `</div>` +
      `<div style="font-size:0.8rem;color:#777;margin-top:0.25rem;">` +
        `${firstAuthor}${authors.includes(',') || authors.includes(';') ? ' et al.' : ''} \u00b7 ${yr} \u00b7 ` +
        `<span style="color:${jColor};">${jflowAbbrev(node.journal)}</span>` +
      `</div>`;

    el.appendChild(card);
  }

  container.appendChild(el);
}


// ── Temporal Network Evolution ────────────────────────────────────────────────

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

async function loadTemporalEvolution() {
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

/* ── Time-series multi-line chart ── */

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

/* ── Network snapshot with year slider ── */

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


// ── Sleeping Beauties ─────────────────────────────────────────────────────────

let sbDetailChart = null;
let sbData        = null;

async function loadSleepingBeauties() {
  const container = document.getElementById('sb-list-container');
  container.innerHTML = '<div class="loading-msg">Computing Beauty Coefficients\u2026</div>';
  document.getElementById('sb-detail').style.display = 'none';
  if (sbDetailChart) { sbDetailChart.destroy(); sbDetailChart = null; }

  const minCit   = document.getElementById('sb-min-slider').value;
  let yearFrom = document.getElementById('sb-year-from').value;
  let yearTo   = document.getElementById('sb-year-to').value;
  const journal  = document.getElementById('sb-journal').value;

  // Swap reversed years
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo)) {
    [yearFrom, yearTo] = [yearTo, yearFrom];
    document.getElementById('sb-year-from').value = yearFrom;
    document.getElementById('sb-year-to').value   = yearTo;
  }

  const params = new URLSearchParams({ min_citations: minCit, max_results: 50 });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (journal)  params.append('journal', journal);

  try {
    const resp = await fetch('/api/citations/sleeping-beauties?' + params.toString());
    sbData = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load Sleeping Beauties data.</p>';
    return;
  }

  if (!sbData.articles || sbData.articles.length === 0) {
    container.innerHTML =
      '<p class="explore-hint">No Sleeping Beauties found with these filters. ' +
      'Try lowering the minimum citation threshold or widening the year range.</p>';
    return;
  }

  renderSleepingBeauties(container, sbData);
}

function renderSleepingBeauties(container, data) {
  container.innerHTML = '';

  const list = document.createElement('ol');
  list.className = 'article-list';
  list.style.cssText = 'font-size:0.85rem;';

  data.articles.forEach((art, idx) => {
    const year = art.pub_date ? art.pub_date.slice(0, 4) : '';
    const auth = art.authors || '';
    const first = auth.split(';')[0].trim();
    const last = first.split(' ').filter(Boolean).pop() || '';
    const byline = last + (auth.includes(';') ? ' et al.' : '');

    // Build a sparkline from citation timeline
    const tl = art.citation_timeline || [];
    const maxCount = Math.max(1, ...tl.map(t => t.count));
    const sparkH = 24;
    const barW = Math.max(1, Math.min(4, Math.floor(200 / tl.length)));
    const sparkW = barW * tl.length;
    let sparkBars = '';
    tl.forEach(t => {
      const h = Math.max(0.5, (t.count / maxCount) * sparkH);
      const isAwake = t.year >= art.awakening_year;
      const fill = isAwake ? '#b38a6a' : '#d4cec5';
      sparkBars += `<rect x="${(t.year - tl[0].year) * barW}" y="${sparkH - h}" width="${Math.max(1, barW - 0.5)}" height="${h}" fill="${fill}"/>`;
    });
    const sparkSvg = `<svg width="${sparkW}" height="${sparkH}" style="vertical-align:middle;margin-left:0.5rem;" title="Citation timeline — click for detail">${sparkBars}</svg>`;

    const li = document.createElement('li');
    li.className = 'article';
    li.style.cssText = 'padding:0.6rem 0;border-bottom:1px solid var(--border-color,#e8e4de);cursor:pointer;';
    li.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:0.5rem;">
        <div style="min-width:0;flex:1;">
          <a href="/article/${art.id}" class="article-title" style="font-size:0.84rem;">${escapeHtml(art.title)}</a>
          <div style="font-size:0.76rem;color:#9c9890;margin-top:0.15rem;">
            ${escapeHtml(byline)}${year ? ' (' + year + ')' : ''} \u2014 <em>${escapeHtml(art.journal)}</em>
            \u2002\u00b7\u2002Cited ${art.internal_cited_by_count}\u00d7 in index${art.crossref_cited_by_count ? ` (${art.crossref_cited_by_count}\u00d7 globally)` : ''}
          </div>
          <div style="font-size:0.76rem;color:#9c9890;margin-top:0.1rem;">
            Slept <strong>${art.sleep_years}</strong> years
            \u2002\u00b7\u2002Awakened <strong>${art.awakening_year}</strong>
            \u2002\u00b7\u2002Peak <strong>${art.peak_citations}</strong>\u00d7 in ${art.peak_year}
          </div>
        </div>
        <div style="display:flex;align-items:center;gap:0.8rem;flex-shrink:0;">
          ${sparkSvg}
          <span style="font-weight:700;font-size:0.92rem;color:#5a3e28;font-variant-numeric:tabular-nums;min-width:3rem;text-align:right;" title="Beauty Coefficient">B\u2009=\u2009${art.beauty_coefficient.toFixed(0)}</span>
        </div>
      </div>
    `;

    // Click row to show detail chart (but not if clicking the title link)
    li.addEventListener('click', (e) => {
      if (e.target.tagName === 'A') return;
      e.preventDefault();
      showSleepingBeautyDetail(art);
    });

    list.appendChild(li);
  });

  container.appendChild(list);
}

function showSleepingBeautyDetail(art) {
  const detailEl = document.getElementById('sb-detail');
  detailEl.style.display = 'block';

  const year = art.pub_date ? art.pub_date.slice(0, 4) : '';
  const auth = art.authors || '';
  const first = auth.split(';')[0].trim();
  const last = first.split(' ').filter(Boolean).pop() || '';
  const byline = last + (auth.includes(';') ? ' et al.' : '');

  document.getElementById('sb-detail-title').textContent = art.title;
  document.getElementById('sb-detail-meta').innerHTML =
    `${escapeHtml(byline)}${year ? ' (' + year + ')' : ''} \u2014 ` +
    `<em>${escapeHtml(art.journal)}</em> \u2002\u00b7\u2002 ` +
    `B\u2009=\u2009${art.beauty_coefficient.toFixed(1)} \u2002\u00b7\u2002 ` +
    `Slept ${art.sleep_years} years \u2002\u00b7\u2002 ` +
    `Awakened ${art.awakening_year} \u2002\u00b7\u2002 ` +
    `<a href="/article/${art.id}" style="color:#b38a6a;">View article \u2192</a>`;

  const tl = art.citation_timeline || [];
  const years  = tl.map(t => t.year);
  const counts = tl.map(t => t.count);

  // Colour bars: muted during sleep, warm brown after awakening
  const barColors = years.map(y => y >= art.awakening_year ? '#b38a6a' : '#d4cec5');

  // Compute the expected linear trajectory for overlay
  const t0 = parseInt(year);
  const tm = art.peak_year;
  const ctm = art.peak_citations;
  const linearData = years.map(y => {
    if (y < t0 || y > tm || tm === t0) return null;
    return ctm * (y - t0) / (tm - t0);
  });

  const ctx = document.getElementById('sb-detail-chart').getContext('2d');
  if (sbDetailChart) sbDetailChart.destroy();

  sbDetailChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: years,
      datasets: [
        {
          label: 'Citations in this index',
          data: counts,
          backgroundColor: barColors,
          borderRadius: 2,
          order: 2,
        },
        {
          label: 'Expected linear trajectory',
          data: linearData,
          type: 'line',
          borderColor: '#9c9890',
          borderDash: [5, 3],
          borderWidth: 1.5,
          pointRadius: 0,
          fill: false,
          order: 1,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          labels: { font: { family: 'system-ui, sans-serif', size: 10 }, boxWidth: 12 },
        },
        tooltip: {
          callbacks: {
            title: (items) => items[0].label,
            label: (item) => {
              if (item.datasetIndex === 0)
                return `${item.raw} citation${item.raw !== 1 ? 's' : ''}`;
              return `Expected: ${item.raw.toFixed(1)}`;
            },
          },
        },
      },
      scales: {
        x: {
          title: { display: true, text: 'Year', font: { family: 'system-ui, sans-serif', size: 11 } },
          ticks: {
            font: { family: 'system-ui, sans-serif', size: 9 },
            maxRotation: 45,
            autoSkip: true,
            autoSkipPadding: 8,
          },
        },
        y: {
          title: { display: true, text: 'Citations', font: { family: 'system-ui, sans-serif', size: 11 } },
          beginAtZero: true,
          ticks: {
            font: { family: 'system-ui, sans-serif', size: 10 },
            stepSize: 1,
          },
        },
      },
    },
  });

  // Scroll the detail chart into view
  detailEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}


// ── Institutions ───────────────────────────────────────────────────────────────
let instBarChart  = null;
let instLineChart = null;

async function loadInstitutions() {
  const barContainer = document.getElementById('inst-bar-container');
  barContainer.innerHTML = '<div class="loading-msg">Loading institution data…</div>';

  let data;
  try {
    const resp = await fetch('/api/stats/institutions');
    data = await resp.json();
  } catch (e) {
    barContainer.innerHTML = '<p class="explore-hint">Failed to load institution data.</p>';
    return;
  }

  const institutions = data.institutions || [];
  const timeline     = data.top10_timeline || { years: [], series: [] };

  // ── Bar chart: top 25 institutions ──────────────────────────────────────────
  if (institutions.length === 0) {
    barContainer.innerHTML =
      '<p class="explore-hint">No institution data yet — run <code>python enrich_openalex.py</code> to populate.</p>';
  } else {
    barContainer.innerHTML = '<canvas id="inst-bar-chart" style="cursor:pointer"></canvas>';
    const barCtx = document.getElementById('inst-bar-chart').getContext('2d');
    if (instBarChart) instBarChart.destroy();

    const labels = institutions.map(d => d.name);
    const counts = institutions.map(d => d.count);

    instBarChart = new Chart(barCtx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          label: 'Articles',
          data: counts,
          backgroundColor: PALETTE[0],
          borderWidth: 0,
        }],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => ` ${ctx.parsed.x} article${ctx.parsed.x !== 1 ? 's' : ''}`,
            },
          },
        },
        onClick: (event, elements) => {
          if (elements.length > 0) {
            const idx = elements[0].index;
            const inst = institutions[idx];
            if (inst && inst.id) {
              window.location = '/institution/' + inst.id;
            }
          }
        },
        scales: {
          x: {
            beginAtZero: true,
            ticks: { font: { family: 'system-ui, sans-serif', size: 11 } },
            title: {
              display: true,
              text: 'Article count',
              font: { family: 'system-ui, sans-serif', size: 11 },
            },
          },
          y: {
            ticks: {
              font: { family: 'system-ui, sans-serif', size: 10 },
              callback: (val, idx) => {
                const name = labels[idx];
                return name.length > 40 ? name.slice(0, 38) + '…' : name;
              },
            },
          },
        },
      },
    });

    // Adjust container height based on number of bars
    barContainer.style.height = Math.max(360, institutions.length * 26) + 'px';
  }

  // ── Line chart: top 10 institutions over time ────────────────────────────────
  const lineContainer = document.getElementById('inst-line-container');
  if (!timeline.years || timeline.years.length === 0 || !timeline.series || timeline.series.length === 0) {
    lineContainer.innerHTML = '<p class="explore-hint">No timeline data available yet.</p>';
  } else {
    const lineCtx = document.getElementById('inst-line-chart').getContext('2d');
    if (instLineChart) instLineChart.destroy();

    const datasets = timeline.series.map((s, i) => ({
      label: s.institution,
      data: s.counts,
      borderColor: PALETTE[i % PALETTE.length],
      backgroundColor: 'transparent',
      borderWidth: 2,
      pointRadius: 2,
      pointHoverRadius: 4,
      tension: 0.3,
    }));

    instLineChart = new Chart(lineCtx, {
      type: 'line',
      data: { labels: timeline.years, datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              font: { family: 'system-ui, sans-serif', size: 10 },
              boxWidth: 12,
              padding: 8,
            },
          },
          tooltip: {
            callbacks: {
              label: (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y}`,
            },
          },
        },
        scales: {
          x: {
            ticks: { font: { family: 'system-ui, sans-serif', size: 11 } },
          },
          y: {
            beginAtZero: true,
            ticks: { font: { family: 'system-ui, sans-serif', size: 11 } },
            title: {
              display: true,
              text: 'Articles',
              font: { family: 'system-ui, sans-serif', size: 11 },
            },
          },
        },
      },
    });
  }
}


// ── Author Co-Citation ───────────────────────────────────────────────────────

let acocitSimulation = null;
let acocitSvgEl = null;
let acocitZoomBehavior = null;
let acocitNodes = [];
let acocitLinks = [];
let acocitLinkSel = null;
let acocitNodeSel = null;
let acocitDebounce = null;
let acocitPairsData = [];

function initAuthorCocitation() {
  document.getElementById('acocit-infobar-close').addEventListener('click', clearAcocitInfobar);

  // Search filter
  document.getElementById('acocit-search').addEventListener('input', function() {
    const q = this.value.trim().toLowerCase();
    if (!acocitNodeSel) return;
    if (!q) {
      acocitNodeSel.style('opacity', 1);
      if (acocitLinkSel) acocitLinkSel.style('stroke-opacity', l => 0.15 + 0.4 * (l.weight / (d3.max(acocitLinks, ll => ll.weight) || 1)));
      return;
    }
    acocitNodeSel.style('opacity', d => d.name.toLowerCase().includes(q) ? 1 : 0.08);
    if (acocitLinkSel) acocitLinkSel.style('stroke-opacity', 0.04);
  });
}

function onAcocitSliderChange(val) {
  document.getElementById('acocit-min-val').textContent = val;
}

function toggleAllAcocitJournals(cb) {
  document.querySelectorAll('.acocit-journal-check').forEach(c => c.checked = cb.checked);
  updateAcocitJournalCount();
}

function updateAcocitJournalCount() {
  const total   = document.querySelectorAll('.acocit-journal-check').length;
  const checked = document.querySelectorAll('.acocit-journal-check:checked').length;
  document.getElementById('acocit-journal-count').textContent =
    checked === total ? '(all)' : `(${checked}\u202f/\u202f${total})`;
  const allCb = document.getElementById('acocit-check-all');
  if (allCb) allCb.checked = (checked === total);
  if (allCb) allCb.indeterminate = (checked > 0 && checked < total);
}

async function loadAuthorCocitation() {
  const container = document.getElementById('acocit-container');
  container.innerHTML = '<div class="loading-msg">Computing author co-citation network\u2026 this may take a few seconds.</div>';
  document.getElementById('acocit-stats').textContent = '';
  document.getElementById('acocit-legend').innerHTML  = '';
  document.getElementById('acocit-pairs-section').style.display = 'none';
  clearAcocitInfobar();

  if (acocitSimulation) { acocitSimulation.stop(); acocitSimulation = null; }

  const minCocit    = document.getElementById('acocit-min-slider').value;
  const maxAuthors  = document.getElementById('acocit-max-authors').value;
  let yearFrom      = document.getElementById('acocit-year-from').value;
  let yearTo        = document.getElementById('acocit-year-to').value;
  if (yearFrom && yearTo && parseInt(yearFrom) > parseInt(yearTo)) {
    [yearFrom, yearTo] = [yearTo, yearFrom];
    document.getElementById('acocit-year-from').value = yearFrom;
    document.getElementById('acocit-year-to').value   = yearTo;
  }
  const checked = [...document.querySelectorAll('.acocit-journal-check:checked')].map(c => c.value);
  const total   = document.querySelectorAll('.acocit-journal-check').length;

  const params = new URLSearchParams({ min_cocitations: minCocit, max_authors: maxAuthors });
  if (yearFrom) params.set('year_from', yearFrom);
  if (yearTo)   params.set('year_to',   yearTo);
  if (checked.length < total) checked.forEach(j => params.append('journal', j));

  let data;
  try {
    const resp = await fetch('/api/author-cocitation?' + params.toString());
    data = await resp.json();
  } catch (e) {
    container.innerHTML = '<p class="explore-hint">Failed to load author co-citation data.</p>';
    return;
  }

  if (!data.nodes || data.nodes.length === 0) {
    const minVal = parseInt(minCocit);
    let hint = 'No author co-citation pairs meet the current threshold.';
    if (minVal > 1) hint += ` Try lowering the minimum co-citations.`;
    container.innerHTML = '<p class="explore-hint">' + hint + '</p>';
    return;
  }

  acocitPairsData = data.pairs || [];
  renderAuthorCocitation(container, data);
  renderAcocitPairs(data.pairs || []);
}

function renderAuthorCocitation(container, data) {
  container.innerHTML = '';
  acocitNodes = data.nodes.map(d => ({ ...d }));
  acocitLinks = data.edges.map(d => ({ ...d }));

  // Stats bar
  const maxWeight = d3.max(acocitLinks, l => l.weight) || 1;
  document.getElementById('acocit-stats').textContent =
    `${data.stats.total_authors} authors\u2002\u00b7\u2002${data.stats.total_edges} co-citation pairs` +
    `\u2002\u00b7\u2002strongest pair co-cited ${data.stats.max_cocitation}\u00d7`;

  // Legend — colour by top journal
  const seenJournals = [...new Set(acocitNodes.map(n => n.top_journal).filter(Boolean))].sort();
  const legendEl = document.getElementById('acocit-legend');
  legendEl.innerHTML = seenJournals.map(j =>
    `<span class="citnet-legend-item">` +
    `<span class="citnet-legend-dot" style="background:${citnetJournalColor(j)}"></span>` +
    `${escapeHtml(j)}</span>`
  ).join('');

  const W = container.clientWidth || 820;
  const H = 600;

  // Node radius: by co-citation strength
  const maxStrength = d3.max(acocitNodes, n => n.total_cocitation_strength) || 1;
  const rScale = d3.scaleSqrt().domain([0, maxStrength]).range([4, 22]);

  // Edge thickness: by co-citation weight
  const edgeScale = d3.scaleLinear().domain([1, Math.max(maxWeight, 2)]).range([0.6, 5]);

  // SVG + zoom
  acocitSvgEl = d3.select('#acocit-container')
    .append('svg')
    .attr('width', W)
    .attr('height', H);

  acocitZoomBehavior = d3.zoom()
    .scaleExtent([0.1, 8])
    .on('zoom', (event) => gRoot.attr('transform', event.transform));

  acocitSvgEl.call(acocitZoomBehavior);
  const gRoot = acocitSvgEl.append('g');

  // Links
  acocitLinkSel = gRoot.append('g').attr('class', 'acocit-links')
    .selectAll('line')
    .data(acocitLinks)
    .enter().append('line')
    .style('stroke',         '#ccc7bb')
    .style('stroke-opacity', d => 0.15 + 0.4 * (d.weight / maxWeight))
    .style('stroke-width',   d => edgeScale(d.weight));

  // Tooltip
  const tip = d3.select('body').append('div')
    .attr('class', 'heatmap-tooltip acocit-tip')
    .style('display',    'none')
    .style('max-width',  '340px')
    .style('line-height','1.45');

  // Node groups
  const nodeGroup = gRoot.append('g').attr('class', 'acocit-nodes')
    .selectAll('g')
    .data(acocitNodes)
    .enter().append('g')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => {
        if (!event.active) acocitSimulation.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag',  (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end',   (event, d) => {
        if (!event.active) acocitSimulation.alphaTarget(0);
        d.fx = null; d.fy = null;
      })
    );

  // Circles
  nodeGroup.append('circle')
    .attr('r', d => rScale(d.total_cocitation_strength))
    .attr('fill', d => citnetJournalColor(d.top_journal || ''))
    .attr('stroke', '#fff')
    .attr('stroke-width', 1.5);

  // Labels for large nodes
  nodeGroup.filter(d => rScale(d.total_cocitation_strength) >= 9)
    .append('text')
    .attr('font-size', '8px')
    .attr('text-anchor', 'middle')
    .attr('dy', d => -(rScale(d.total_cocitation_strength) + 3))
    .attr('fill', '#3a2a18')
    .text(d => {
      const parts = d.name.split(/\s+/);
      return parts[parts.length - 1]; // last name only
    });

  acocitNodeSel = nodeGroup;

  // Hover
  nodeGroup.on('mouseover', (event, d) => {
    // Top partners
    const partners = acocitLinks
      .filter(l => {
        const sid = typeof l.source === 'object' ? l.source.id : l.source;
        const tid = typeof l.target === 'object' ? l.target.id : l.target;
        return sid === d.id || tid === d.id;
      })
      .map(l => {
        const sid = typeof l.source === 'object' ? l.source.id : l.source;
        const tid = typeof l.target === 'object' ? l.target.id : l.target;
        const partnerId = sid === d.id ? tid : sid;
        const partner = acocitNodes.find(n => n.id === partnerId);
        return { name: partner ? partner.name : '?', weight: l.weight };
      })
      .sort((a, b) => b.weight - a.weight)
      .slice(0, 3);

    const partnerHtml = partners.length > 0
      ? '<br><span style="font-size:0.78rem;color:#9c9890;">Top co-cited with:</span>' +
        partners.map(p =>
          `<br><span style="font-size:0.78rem;">\u2022 ${escapeHtml(p.name)} (${p.weight}\u00d7)</span>`
        ).join('')
      : '';

    tip.style('display', 'block').html(
      `<strong>${escapeHtml(d.name)}</strong><br>` +
      `<span style="font-size:0.82rem;">${d.article_count} articles in index</span><br>` +
      `<span style="font-size:0.82rem;">Co-citation strength: <strong>${d.total_cocitation_strength}</strong></span>` +
      (d.top_journal ? `<br><em style="font-size:0.78rem;">${escapeHtml(d.top_journal)}</em>` : '') +
      partnerHtml
    );

    // Highlight edges
    acocitLinkSel.style('stroke-opacity', l => {
      const sid = typeof l.source === 'object' ? l.source.id : l.source;
      const tid = typeof l.target === 'object' ? l.target.id : l.target;
      return (sid === d.id || tid === d.id) ? 0.85 : 0.04;
    }).style('stroke', l => {
      const sid = typeof l.source === 'object' ? l.source.id : l.source;
      const tid = typeof l.target === 'object' ? l.target.id : l.target;
      return (sid === d.id || tid === d.id) ? '#8b6045' : '#ccc7bb';
    });
  })
  .on('mousemove', (event) => {
    positionTooltip(tip, event);
  })
  .on('mouseout', () => {
    tip.style('display', 'none');
    acocitLinkSel.style('stroke-opacity', l => 0.15 + 0.4 * (l.weight / maxWeight))
                  .style('stroke', '#ccc7bb');
  })
  .on('click', (event, d) => {
    event.stopPropagation();
    showAcocitInfobar(d);
  });

  // Click background to clear
  acocitSvgEl.on('click', () => clearAcocitInfobar());

  // Force simulation
  acocitSimulation = d3.forceSimulation(acocitNodes)
    .force('link', d3.forceLink(acocitLinks).id(d => d.id).distance(100))
    .force('charge', d3.forceManyBody().strength(-180))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collision', d3.forceCollide().radius(d => rScale(d.total_cocitation_strength) + 2))
    .on('tick', () => {
      acocitLinkSel
        .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
        .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      nodeGroup
        .attr('transform', d => `translate(${d.x},${d.y})`);
    });
}

function showAcocitInfobar(d) {
  const infobar = document.getElementById('acocit-infobar');
  document.getElementById('acocit-infobar-name').textContent = d.name;

  // Find top partners
  const partners = acocitLinks
    .filter(l => {
      const sid = typeof l.source === 'object' ? l.source.id : l.source;
      const tid = typeof l.target === 'object' ? l.target.id : l.target;
      return sid === d.id || tid === d.id;
    })
    .map(l => {
      const sid = typeof l.source === 'object' ? l.source.id : l.source;
      const tid = typeof l.target === 'object' ? l.target.id : l.target;
      const partnerId = sid === d.id ? tid : sid;
      const partner = acocitNodes.find(n => n.id === partnerId);
      return { name: partner ? partner.name : '?', weight: l.weight };
    })
    .sort((a, b) => b.weight - a.weight);

  const topStr = partners.slice(0, 5).map(p => `${p.name} (${p.weight}\u00d7)`).join(', ');

  document.getElementById('acocit-infobar-detail').innerHTML =
    `${d.article_count} articles · strength ${d.total_cocitation_strength}` +
    (topStr ? `<br><small>Top co-cited: ${escapeHtml(topStr)}</small>` : '');

  document.getElementById('acocit-infobar-link').href = '/author/' + encodeURIComponent(d.name);
  infobar.style.display = 'flex';
}

function clearAcocitInfobar() {
  document.getElementById('acocit-infobar').style.display = 'none';
}

function renderAcocitPairs(pairs) {
  if (!pairs || pairs.length === 0) return;

  document.getElementById('acocit-pairs-section').style.display = 'block';
  const container = document.getElementById('acocit-pairs-container');

  let html = '<table style="width:100%;border-collapse:collapse;font-size:0.88rem;">';
  html += '<thead><tr style="border-bottom:2px solid #d4bc99;text-align:left;">' +
    '<th style="padding:0.4rem 0.6rem;width:3rem;">#</th>' +
    '<th style="padding:0.4rem 0.6rem;">Author A</th>' +
    '<th style="padding:0.4rem 0.6rem;">Author B</th>' +
    '<th style="padding:0.4rem 0.6rem;width:6rem;text-align:right;">Co-cited</th>' +
    '</tr></thead><tbody>';

  pairs.slice(0, 50).forEach((p, i) => {
    html += `<tr style="border-bottom:1px solid #ede8e0;">` +
      `<td style="padding:0.4rem 0.6rem;color:#8b6045;font-weight:600;">${i + 1}</td>` +
      `<td style="padding:0.4rem 0.6rem;"><a href="/author/${encodeURIComponent(p.author1)}" style="color:#3a2a18;">${escapeHtml(p.author1)}</a></td>` +
      `<td style="padding:0.4rem 0.6rem;"><a href="/author/${encodeURIComponent(p.author2)}" style="color:#3a2a18;">${escapeHtml(p.author2)}</a></td>` +
      `<td style="padding:0.4rem 0.6rem;text-align:right;font-weight:600;">${p.cocitation_count}\u00d7</td>` +
      `</tr>`;
  });

  html += '</tbody></table>';

  if (pairs.length > 50) {
    html += `<p style="font-size:0.82rem;color:#8b6045;margin-top:0.5rem;">${pairs.length - 50} more pairs not shown.</p>`;
  }

  container.innerHTML = html;
}


// ── Reading Path ─────────────────────────────────────────────────────────────

let rpData = null;       // full response from /api/citations/reading-path
let rpSeedId = null;     // selected seed article id
let rpSvg = null;        // D3 svg selection
let rpSimulation = null; // D3 force simulation
let rpDebounce = null;   // search debounce timer

const RP_COLORS = {
  seed:     '#e74c3c',
  cites:    '#3498db',
  cited_by: '#2ecc71',
  cocited:  '#e67e22',
  coupled:  '#9b59b6',
};

function initReadingPath() {
  const searchInput = document.getElementById('rp-search');
  const acBox = document.getElementById('rp-autocomplete');
  const clearBtn = document.getElementById('rp-clear-btn');
  const buildBtn = document.getElementById('rp-build-btn');

  // Debounced autocomplete search
  searchInput.addEventListener('input', function () {
    clearTimeout(rpDebounce);
    const q = this.value.trim();
    if (q.length < 2) { acBox.style.display = 'none'; return; }
    rpDebounce = setTimeout(() => rpSearch(q), 250);
  });

  // Close autocomplete on outside click
  document.addEventListener('click', function (e) {
    if (!e.target.closest('#rp-search') && !e.target.closest('#rp-autocomplete')) {
      acBox.style.display = 'none';
    }
  });

  // Close autocomplete on Escape
  searchInput.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') acBox.style.display = 'none';
  });

  clearBtn.addEventListener('click', rpClearSeed);
  buildBtn.addEventListener('click', rpBuild);

  // Export buttons
  document.getElementById('rp-export-bibtex').addEventListener('click', rpExportBibtex);
  document.getElementById('rp-export-text').addEventListener('click', rpExportText);

  // Check if we have a seed from URL parameter
  const urlParams = new URLSearchParams(location.search);
  const seedParam = urlParams.get('seed');
  if (seedParam) {
    rpSeedId = parseInt(seedParam);
    // Fetch seed info and auto-build
    rpBuildFromId(rpSeedId);
  }
}

async function rpSearch(q) {
  const acBox = document.getElementById('rp-autocomplete');
  try {
    const resp = await fetch('/api/articles/search?q=' + encodeURIComponent(q) + '&limit=8');
    const results = await resp.json();
    if (!results.length) {
      acBox.innerHTML = '<div style="padding:0.6rem 0.8rem;color:#8b6045;font-size:0.85rem;">No results found</div>';
      acBox.style.display = 'block';
      return;
    }
    acBox.innerHTML = results.map(a => `
      <div class="rp-ac-item" data-id="${a.id}" style="padding:0.5rem 0.8rem;cursor:pointer;border-bottom:1px solid #f0ebe3;transition:background 0.15s;">
        <div style="font-weight:600;font-size:0.88rem;color:#3a2a18;">${escHtml(a.title || '(no title)')}</div>
        <div style="font-size:0.78rem;color:#8b6045;">${escHtml(a.authors || '')}${a.pub_date ? ' · ' + a.pub_date.slice(0, 4) : ''} · ${escHtml(a.journal || '')}</div>
      </div>
    `).join('');
    acBox.style.display = 'block';

    // Wire click handlers
    acBox.querySelectorAll('.rp-ac-item').forEach(item => {
      item.addEventListener('mouseenter', () => item.style.background = '#f5f0e8');
      item.addEventListener('mouseleave', () => item.style.background = '');
      item.addEventListener('click', () => {
        const id = parseInt(item.getAttribute('data-id'));
        const art = results.find(r => r.id === id);
        if (art) rpSelectSeed(art);
        acBox.style.display = 'none';
      });
    });
  } catch (e) {
    console.error('Reading path search error:', e);
  }
}

function rpSelectSeed(article) {
  rpSeedId = article.id;
  document.getElementById('rp-search').value = '';
  document.getElementById('rp-seed-title').textContent = article.title || '(no title)';
  document.getElementById('rp-seed-authors').textContent = article.authors || '';
  const meta = [article.journal, article.pub_date ? article.pub_date.slice(0, 4) : ''].filter(Boolean).join(' · ');
  document.getElementById('rp-seed-meta').textContent = meta;
  document.getElementById('rp-seed-card').style.display = 'block';
  document.getElementById('rp-results').style.display = 'none';
}

function rpClearSeed() {
  rpSeedId = null;
  rpData = null;
  document.getElementById('rp-seed-card').style.display = 'none';
  document.getElementById('rp-results').style.display = 'none';
  document.getElementById('rp-search').value = '';
  document.getElementById('rp-search').focus();
  if (rpSimulation) { rpSimulation.stop(); rpSimulation = null; }
}

async function rpBuildFromId(articleId) {
  // Auto-build when seed comes from URL param or cross-link
  rpSeedId = articleId;
  document.getElementById('rp-loading').style.display = 'block';
  document.getElementById('rp-results').style.display = 'none';
  document.getElementById('rp-seed-card').style.display = 'none';

  try {
    const resp = await fetch('/api/citations/reading-path?article=' + articleId);
    rpData = await resp.json();
    if (rpData.error) {
      document.getElementById('rp-loading').style.display = 'none';
      alert('Error: ' + rpData.error);
      return;
    }
    // Show seed card
    rpSelectSeed(rpData.seed);
    document.getElementById('rp-loading').style.display = 'none';
    rpRenderResults();
  } catch (e) {
    document.getElementById('rp-loading').style.display = 'none';
    console.error('Reading path build error:', e);
  }
}

async function rpBuild() {
  if (!rpSeedId) return;
  document.getElementById('rp-loading').style.display = 'block';
  document.getElementById('rp-results').style.display = 'none';
  document.getElementById('rp-build-btn').disabled = true;

  try {
    const resp = await fetch('/api/citations/reading-path?article=' + rpSeedId);
    rpData = await resp.json();
    if (rpData.error) {
      document.getElementById('rp-loading').style.display = 'none';
      document.getElementById('rp-build-btn').disabled = false;
      alert('Error: ' + rpData.error);
      return;
    }
    document.getElementById('rp-loading').style.display = 'none';
    document.getElementById('rp-build-btn').disabled = false;
    rpRenderResults();
  } catch (e) {
    document.getElementById('rp-loading').style.display = 'none';
    document.getElementById('rp-build-btn').disabled = false;
    console.error('Reading path build error:', e);
  }
}

function rpRenderResults() {
  if (!rpData) return;
  const stats = rpData.stats;

  // Stats bar
  const statsEl = document.getElementById('rp-stats');
  statsEl.innerHTML = [
    `<span><strong>${stats.unique_articles}</strong> related articles</span>`,
    `<span><strong>${stats.cites_count}</strong> backward citations</span>`,
    `<span><strong>${stats.cited_by_count}</strong> forward citations</span>`,
    `<span><strong>${stats.cocited_count}</strong> co-cited</span>`,
    `<span><strong>${stats.coupled_count}</strong> bib. coupled</span>`,
  ].join('');

  document.getElementById('rp-results').style.display = 'block';

  // Show graph by default
  rpShowView('graph', document.querySelector('#rp-results .explore-tab.active'));
  rpRenderGraph();
  rpRenderList(rpData.reading_list);
}

function rpShowView(view, btn) {
  // Toggle view between graph and list
  const parent = document.getElementById('rp-results');
  parent.querySelectorAll('.explore-tab').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  document.getElementById('rp-graph-view').style.display = view === 'graph' ? 'block' : 'none';
  document.getElementById('rp-list-view').style.display  = view === 'list'  ? 'block' : 'none';
}

function rpRenderGraph() {
  if (!rpData || !rpData.graph) return;
  const container = document.getElementById('rp-graph-container');
  container.innerHTML = '';

  const width = container.clientWidth;
  const height = container.clientHeight || 560;
  const graph = rpData.graph;

  if (!graph.nodes || graph.nodes.length === 0) {
    container.innerHTML = '<p class="explore-hint" style="padding:2rem;">No citation relationships found for this article.</p>';
    return;
  }

  const svg = d3.select(container).append('svg')
    .attr('width', width).attr('height', height)
    .style('font-family', 'system-ui, sans-serif');
  rpSvg = svg;

  const g = svg.append('g');

  // Zoom
  const zoom = d3.zoom()
    .scaleExtent([0.2, 5])
    .on('zoom', (event) => g.attr('transform', event.transform));
  svg.call(zoom);

  // Determine node color based on primary relationship type
  function nodeColor(d) {
    if (d.is_seed) return RP_COLORS.seed;
    const types = d.rel_types || [];
    if (types.length === 1) return RP_COLORS[types[0]] || '#999';
    // Multiple types — use the "strongest" one
    const priority = ['cites', 'cited_by', 'cocited', 'coupled'];
    for (const t of priority) {
      if (types.includes(t)) return RP_COLORS[t];
    }
    return '#999';
  }

  // Node radius based on score
  function nodeRadius(d) {
    if (d.is_seed) return 14;
    const s = d.score || 1;
    return Math.max(5, Math.min(12, 3 + s * 1.2));
  }

  // Edge color based on type
  function edgeColor(d) {
    return RP_COLORS[d.type] || '#ccc';
  }

  // Build simulation
  if (rpSimulation) rpSimulation.stop();

  // Create node map for quick lookup
  const nodeMap = {};
  graph.nodes.forEach(n => { nodeMap[n.id] = n; });

  // Ensure links reference actual node objects
  const links = graph.links.filter(l => {
    const src = typeof l.source === 'object' ? l.source.id : l.source;
    const tgt = typeof l.target === 'object' ? l.target.id : l.target;
    return nodeMap[src] && nodeMap[tgt];
  });

  rpSimulation = d3.forceSimulation(graph.nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(80))
    .force('charge', d3.forceManyBody().strength(-200))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide().radius(d => nodeRadius(d) + 2));

  // Draw edges
  const link = g.append('g')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('stroke', edgeColor)
    .attr('stroke-opacity', 0.35)
    .attr('stroke-width', d => Math.max(1, Math.min(3, d.weight || 1)));

  // Draw nodes
  const node = g.append('g')
    .selectAll('circle')
    .data(graph.nodes)
    .join('circle')
    .attr('r', nodeRadius)
    .attr('fill', nodeColor)
    .attr('stroke', d => d.is_seed ? '#c0392b' : '#fff')
    .attr('stroke-width', d => d.is_seed ? 3 : 1.5)
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => {
        if (!event.active) rpSimulation.alphaTarget(0.3).restart();
        d.fx = d.x; d.fy = d.y;
      })
      .on('drag', (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on('end', (event, d) => {
        if (!event.active) rpSimulation.alphaTarget(0);
        d.fx = null; d.fy = null;
      })
    );

  // Labels for seed and high-score nodes
  const labels = g.append('g')
    .selectAll('text')
    .data(graph.nodes.filter(d => d.is_seed || (d.score || 0) >= 6))
    .join('text')
    .attr('font-size', d => d.is_seed ? '11px' : '9px')
    .attr('font-weight', d => d.is_seed ? '700' : '400')
    .attr('fill', '#3a2a18')
    .attr('text-anchor', 'middle')
    .attr('dy', d => -(nodeRadius(d) + 4))
    .text(d => {
      const t = d.title || '';
      return t.length > 40 ? t.slice(0, 38) + '…' : t;
    });

  // Tooltip
  const tooltip = document.getElementById('rp-graph-tooltip');

  node.on('mouseover', (event, d) => {
    tooltip.innerHTML = `
      <div style="font-weight:600;margin-bottom:0.2rem;">${escHtml(d.title || '')}</div>
      <div style="font-size:0.78rem;color:#8b6045;">${escHtml(d.authors || '')}</div>
      <div style="font-size:0.78rem;color:#8b6045;">${escHtml(d.journal || '')}${d.pub_date ? ' · ' + d.pub_date.slice(0, 4) : ''}</div>
      ${d.is_seed ? '<div style="margin-top:0.2rem;font-weight:600;color:#e74c3c;">Seed article</div>' :
        `<div style="margin-top:0.2rem;font-size:0.78rem;">Score: ${d.score || 0} · ${(d.rel_types || []).join(', ')}</div>`
      }
    `;
    tooltip.style.display = 'block';
  })
  .on('mousemove', (event) => {
    const rect = container.getBoundingClientRect();
    tooltip.style.left = (event.clientX - rect.left + 12) + 'px';
    tooltip.style.top  = (event.clientY - rect.top - 10) + 'px';
  })
  .on('mouseout', () => { tooltip.style.display = 'none'; })
  .on('click', (event, d) => {
    window.open('/article/' + d.id, '_blank');
  });

  // Tick
  rpSimulation.on('tick', () => {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    node
      .attr('cx', d => d.x).attr('cy', d => d.y);
    labels
      .attr('x', d => d.x).attr('y', d => d.y);
  });

  // Fit view after simulation settles
  rpSimulation.on('end', () => {
    const bounds = g.node().getBBox();
    if (bounds.width > 0 && bounds.height > 0) {
      const pad = 40;
      const scale = Math.min(
        (width - pad * 2) / bounds.width,
        (height - pad * 2) / bounds.height,
        1.5
      );
      const tx = width / 2 - (bounds.x + bounds.width / 2) * scale;
      const ty = height / 2 - (bounds.y + bounds.height / 2) * scale;
      svg.transition().duration(600)
        .call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
    }
  });
}

function rpRenderList(list, filter) {
  const container = document.getElementById('rp-list-container');
  const filtered = filter && filter !== 'all'
    ? list.filter(a => a.rel_types && a.rel_types.includes(filter))
    : list;

  if (!filtered.length) {
    container.innerHTML = '<p class="explore-hint">No articles match this filter.</p>';
    return;
  }

  container.innerHTML = filtered.map((a, i) => {
    const relBadges = (a.rel_types || []).map(t =>
      `<span style="display:inline-block;padding:0.1rem 0.4rem;border-radius:3px;font-size:0.72rem;color:#fff;background:${RP_COLORS[t] || '#999'};margin-right:0.3rem;">${t.replace('_', ' ')}</span>`
    ).join('');

    return `
    <div class="rp-list-item" style="padding:0.7rem 0;border-bottom:1px solid #ede8e0;${i === 0 ? 'border-top:1px solid #ede8e0;' : ''}">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;">
        <div style="flex:1;min-width:0;">
          <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.2rem;">
            <span style="font-weight:700;color:#8b6045;font-size:0.82rem;">#${i + 1}</span>
            <span style="font-size:0.78rem;color:#5a3e28;">score ${a.score}</span>
            ${relBadges}
          </div>
          <a href="/article/${a.id}" style="font-weight:600;color:#3a2a18;text-decoration:none;font-size:0.95rem;" target="_blank">${escHtml(a.title || '(no title)')}</a>
          <div style="font-size:0.82rem;color:#5a3e28;margin-top:0.15rem;">${escHtml(a.authors || '')}</div>
          <div style="font-size:0.78rem;color:#8b6045;margin-top:0.1rem;">
            ${escHtml(a.journal || '')}${a.pub_date ? ' · ' + a.pub_date.slice(0, 4) : ''}
            ${a.internal_cited_by_count ? ' · cited ' + a.internal_cited_by_count + '×' : ''}
          </div>
          <div style="font-size:0.78rem;color:#8b6045;margin-top:0.15rem;font-style:italic;">${escHtml(a.reason || '')}</div>
        </div>
      </div>
    </div>`;
  }).join('');
}

function rpFilterList(filter, btn) {
  document.querySelectorAll('.rp-filter-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  if (rpData && rpData.reading_list) {
    rpRenderList(rpData.reading_list, filter);
  }
}

function rpExportBibtex() {
  if (!rpData || !rpData.reading_list) return;
  const lines = [];
  rpData.reading_list.forEach(a => {
    const lastWord = (a.authors || 'unknown').split(';')[0].trim().split(/\s+/).pop().replace(/[^a-z0-9]/gi, '').toLowerCase() || 'unknown';
    const year = (a.pub_date || '').slice(0, 4);
    const firstTitleWord = (a.title || 'untitled').split(/\s+/)[0].replace(/[^a-z0-9]/gi, '').toLowerCase() || 'untitled';
    const key = lastWord + year + firstTitleWord;
    const authors = (a.authors || '').split(';').map(s => s.trim()).filter(Boolean).join(' and ');
    const title = (a.title || '').replace(/[{}]/g, '');
    lines.push(`@article{${key},`);
    if (authors) lines.push(`  author  = {${authors}},`);
    lines.push(`  title   = {${title}},`);
    if (a.journal) lines.push(`  journal = {${a.journal}},`);
    if (year) lines.push(`  year    = {${year}},`);
    if (a.doi) lines.push(`  doi     = {${a.doi}},`);
    if (a.url) lines.push(`  url     = {${a.url}},`);
    lines.push('}');
    lines.push('');
  });
  rpDownloadFile('reading-path.bib', lines.join('\n'), 'application/x-bibtex');
}

function rpExportText() {
  if (!rpData || !rpData.reading_list) return;
  const lines = [`Reading Path — ${rpData.seed.title}`, `Seed: ${rpData.seed.authors || ''} (${(rpData.seed.pub_date || '').slice(0, 4)})`, ''];
  rpData.reading_list.forEach((a, i) => {
    lines.push(`${i + 1}. ${a.title || '(no title)'}`);
    lines.push(`   ${a.authors || ''}`);
    lines.push(`   ${a.journal || ''}${a.pub_date ? ', ' + a.pub_date.slice(0, 4) : ''}`);
    lines.push(`   Relevance: ${a.score} — ${a.reason || ''}`);
    if (a.doi) lines.push(`   DOI: ${a.doi}`);
    lines.push('');
  });
  rpDownloadFile('reading-path.txt', lines.join('\n'), 'text/plain');
}

function rpDownloadFile(filename, content, mime) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function escHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}


// ── Auto-activate tab from URL hash or ?tab= param ────────────────────────
// Hash takes priority; ?tab= kept for backward compat with article pages.
window.addEventListener('DOMContentLoaded', function () {
  // Build a lookup: hash → tab name (from data-hash attributes)
  const hashMap = {};
  document.querySelectorAll('.explore-tab[data-hash]').forEach(b => {
    hashMap[b.getAttribute('data-hash')] = b;
  });

  // Check hash first
  const hash = location.hash.replace('#', '');
  if (hash) {
    // Special case: #citation-trends → cittrends tab
    const btn = hashMap[hash];
    if (btn) { btn.click(); return; }
  }

  // Fall back to ?tab= param
  const tp = new URLSearchParams(location.search).get('tab');
  if (tp) {
    const btn = Array.from(document.querySelectorAll('.explore-tab'))
                     .find(b => (b.getAttribute('onclick') || '').includes("'" + tp + "'"));
    if (btn) { btn.click(); return; }
  }

  // No hash and no ?tab= — activate the default tab (first / Author Network)
  const defaultBtn = document.querySelector('.explore-tab.active');
  if (defaultBtn) defaultBtn.click();
});
