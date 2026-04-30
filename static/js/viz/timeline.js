// static/js/viz/timeline.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


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

// ── Inline-handler globals ────────────────────────────────────
window.loadTimeline = loadTimeline;
window.toggleJournals = toggleJournals;
