// static/js/viz/sleeping_beauties.js
//
// Extracted from the monolithic static/explore.js during prompt F2.
// Imports shared utilities from ../utils/. Inline-handler globals are
// re-attached to `window` at the bottom so onclick=/onchange=/oninput=
// attributes in explore.html and inside HTML-string fragments resolve.

import { renderExportToolbar } from "../shared/export.js";
import { escapeHtml, positionTooltip, showNetInfobar, clearNetInfobar } from "../utils/tooltips.js";
import { journalColor, citnetJournalColor } from "../utils/colors.js";
import { applyHighlight, clearHighlight } from "../utils/highlight.js";


let sbDetailChart = null;
let sbData        = null;


/**
 * Plain-language gloss for a Beauty Coefficient (B) value.
 *
 * The Ke et al. (2015) coefficient has no fixed scale — it grows with how
 * much an article's actual citation curve diverges from a linear trajectory
 * from publication to peak. The bands below are calibrated against the
 * Pinakes corpus's distribution: most articles sit below B = 5, and B above
 * roughly 50 indicates a substantively long sleep with a sharp awakening.
 * Returns a short hint that helps a reader who doesn't yet have a feel for
 * what the number means.
 */
function beautyCoefficientGloss(b) {
  const v = Math.abs(b);
  if (v < 2)   return "barely a sleeper";
  if (v < 8)   return "shallow sleep, gentle wake";
  if (v < 25)  return "moderate sleep, modest wake";
  if (v < 60)  return "long sleep, clear wake";
  if (v < 120) return "long sleep, sharp wake";
  return "extreme sleeper, dramatic wake";
}

let _exportWired_loadSleepingBeauties = false;

async function loadSleepingBeauties() {
  if (!_exportWired_loadSleepingBeauties) {
    renderExportToolbar('tab-sleepers', { svgSelector: '#sb-detail-chart', dataProvider: () => (window.__expSleepingBeauties && window.__expSleepingBeauties.articles || []) });
    _exportWired_loadSleepingBeauties = true;
  }
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
  window.__expSleepingBeauties = sbData;
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

    // Build a sparkline from citation timeline. Bars are dim during the
    // sleep period and warm after the awakening year so the awakening is
    // legible as a colour shift, not just a height jump. Each bar carries
    // a <title> tooltip with year + citation count for hover-inspection.
    const tl = art.citation_timeline || [];
    const maxCount = Math.max(1, ...tl.map(t => t.count));
    const sparkH = 24;
    const barW = Math.max(1, Math.min(4, Math.floor(200 / Math.max(1, tl.length))));
    const sparkW = Math.max(barW, barW * tl.length);
    let sparkBars = '';
    tl.forEach(t => {
      const h = Math.max(0.5, (t.count / maxCount) * sparkH);
      const isAwake = t.year >= art.awakening_year;
      const fill = isAwake ? '#b38a6a' : '#d4cec5';
      sparkBars += `<rect x="${(t.year - tl[0].year) * barW}" y="${sparkH - h}" width="${Math.max(1, barW - 0.5)}" height="${h}" fill="${fill}"><title>${t.year}: ${t.count} citation${t.count !== 1 ? 's' : ''}</title></rect>`;
    });
    const sparkSvg = tl.length > 0
      ? `<svg width="${sparkW}" height="${sparkH}" style="vertical-align:middle;margin-left:0.5rem;" role="img" aria-label="Citation timeline (click row for detail)">${sparkBars}</svg>`
      : '<span style="color:#9c9890;font-size:0.78rem;">no timeline</span>';

    const bGloss = beautyCoefficientGloss(art.beauty_coefficient);

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
          <div style="display:flex;flex-direction:column;align-items:flex-end;min-width:7.2rem;">
            <span style="font-weight:700;font-size:0.92rem;color:#5a3e28;font-variant-numeric:tabular-nums;" title="Beauty Coefficient (Ke et al. 2015) \u2014 quantifies how dormant and how sharp the awakening was">B\u2009=\u2009${art.beauty_coefficient.toFixed(0)}</span>
            <span style="font-size:0.72rem;color:#9c9890;font-style:italic;line-height:1.2;margin-top:0.1rem;">${bGloss}</span>
          </div>
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
  // Cross-link into the Datastories Prince Network tool with this SB
  // pre-selected via URL param. Hidden when Datastories isn't enabled
  // (the link 404s in production, so don't render it there).
  const princeLink = (window.DATASTORIES_ENABLED !== false)
    ? ` \u2002\u00b7\u2002 <a href="/datastories?seed=${art.id}#ch9-prince-network" ` +
      `style="color:#b38a6a;" title="Open the Prince Network tool with this Sleeping Beauty pre-selected">` +
      `Show princes \u2192</a>`
    : '';
  document.getElementById('sb-detail-meta').innerHTML =
    `${escapeHtml(byline)}${year ? ' (' + year + ')' : ''} \u2014 ` +
    `<em>${escapeHtml(art.journal)}</em> \u2002\u00b7\u2002 ` +
    `B\u2009=\u2009${art.beauty_coefficient.toFixed(1)} \u2002\u00b7\u2002 ` +
    `Slept ${art.sleep_years} years \u2002\u00b7\u2002 ` +
    `Awakened ${art.awakening_year} \u2002\u00b7\u2002 ` +
    `<a href="/article/${art.id}" style="color:#b38a6a;">View article \u2192</a>` +
    princeLink;

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

// ── Inline-handler globals ────────────────────────────────────
window.loadSleepingBeauties = loadSleepingBeauties;
