// static/js/viz/half_life.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { renderExportToolbar } from "./_ds_export.js";
import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


let hlChart = null;


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

let _exportWired_loadHalfLife = false;

async function loadHalfLife() {
  if (!_exportWired_loadHalfLife) {
    renderExportToolbar('tab-halflife', { svgSelector: '#halflife-container svg', dataProvider: () => (window.__expHalfLife && window.__expHalfLife.journals || []) });
    _exportWired_loadHalfLife = true;
  }
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
    window.__expHalfLife = data;
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

// ── Inline-handler globals ────────────────────────────────────
window.toggleAllHlJournals = toggleAllHlJournals;
window.updateHlJournalCount = updateHlJournalCount;
window.loadHalfLife = loadHalfLife;
