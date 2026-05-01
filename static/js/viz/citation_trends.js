// static/js/viz/citation_trends.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { renderExportToolbar } from "../shared/export.js";
import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


let citTrendsChart = null;

let _exportWired_loadCitTrends = false;

async function loadCitTrends() {
  if (!_exportWired_loadCitTrends) {
    renderExportToolbar('tab-cittrends', { svgSelector: '#cittrends-chart', dataProvider: () => (window.__expCitTrends || []) });
    _exportWired_loadCitTrends = true;
  }
  const noteEl = document.getElementById('cittrends-note');
  noteEl.textContent = 'Loading…';

  const journal = document.getElementById('cittrends-journal').value;
  const params  = new URLSearchParams();
  if (journal) params.set('journal', journal);

  let rows;
  try {
    const resp = await fetch('/api/stats/citation-trends?' + params.toString());
    rows = await resp.json();
    window.__expCitTrends = rows;
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

// ── Inline-handler globals ────────────────────────────────────
window.loadCitTrends = loadCitTrends;
