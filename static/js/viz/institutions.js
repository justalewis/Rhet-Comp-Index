// static/js/viz/institutions.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { renderExportToolbar } from "../shared/export.js";
import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { PALETTE, journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


let instBarChart  = null;
let instLineChart = null;

let _exportWired_loadInstitutions = false;

async function loadInstitutions() {
  if (!_exportWired_loadInstitutions) {
    renderExportToolbar('tab-institutions', { svgSelector: '#institutions-container svg', dataProvider: () => (window.__expInstitutions && window.__expInstitutions.institutions || []) });
    _exportWired_loadInstitutions = true;
  }
  const barContainer = document.getElementById('inst-bar-container');
  barContainer.innerHTML = '<div class="loading-msg">Loading institution data…</div>';

  let data;
  try {
    const resp = await fetch('/api/stats/institutions', { cache: 'no-cache' });
    data = await resp.json();
    window.__expInstitutions = data;
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

// ── Inline-handler globals ────────────────────────────────────
window.loadInstitutions = loadInstitutions;
