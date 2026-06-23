// wac-charts.js — Chart.js-based panels for the WAC dashboard.
import {
  register, getJSON, loading, showError, chart, $,
  TYPE_COLORS, TYPE_LABEL, palette, CHART_FONT, fmt,
} from "./wac-common.js";

const LEGEND = { position: "bottom", labels: { font: CHART_FONT, boxWidth: 12, padding: 8 } };
const AX = { ticks: { font: CHART_FONT }, grid: { color: "#f0ece5" } };

// ── 1. Output over time, by type (stacked bar + normalize toggle) ──
register("timeline", async (card) => {
  loading("wac-timeline", "Loading output history…");
  let data;
  try { data = await getJSON("/api/wac/timeline"); }
  catch (e) { return showError("wac-timeline", e.message); }
  let normalized = false;
  const draw = () => {
    const totals = data.years.map((_, i) =>
      data.series.reduce((a, s) => a + s.counts[i], 0));
    const datasets = data.series.map(s => ({
      label: TYPE_LABEL[s.type] || s.type,
      data: s.counts.map((c, i) => normalized ? (totals[i] ? c / totals[i] * 100 : 0) : c),
      backgroundColor: TYPE_COLORS[s.type],
      borderWidth: 0,
      _raw: s.counts,
    }));
    chart("wac-timeline", {
      type: "bar",
      data: { labels: data.years, datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: LEGEND,
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const raw = ctx.dataset._raw[ctx.dataIndex];
                return ctx.dataset.label + ": " + raw + (normalized ? " (" + Math.round(ctx.raw) + "%)" : "");
              },
            },
          },
        },
        scales: {
          x: { stacked: true, ...AX, ticks: { font: CHART_FONT, maxTicksLimit: 18 } },
          y: { stacked: true, ...AX, max: normalized ? 100 : undefined,
               title: { display: true, text: normalized ? "share of year (%)" : "works", font: CHART_FONT } },
        },
      },
    });
  };
  draw();
  const btn = card.querySelector('[data-toggle="normalize"]');
  if (btn && !btn._wired) {
    btn._wired = true;
    btn.addEventListener("click", () => {
      normalized = !normalized;
      btn.classList.toggle("is-on", normalized);
      btn.textContent = normalized ? "Show counts" : "Show as share (100%)";
      draw();
    });
  }
});

// ── 4b. Affiliation coverage (line by decade per type) ──
register("affiliation-coverage", async () => {
  loading("wac-coverage", "Loading coverage…");
  let d;
  try { d = await getJSON("/api/wac/affiliation-coverage"); }
  catch (e) { return showError("wac-coverage", e.message); }
  chart("wac-coverage", {
    type: "line",
    data: {
      labels: d.decades.map(x => x + "s"),
      datasets: d.series.map(s => ({
        label: s.label, data: s.pct, borderColor: TYPE_COLORS[s.type],
        backgroundColor: TYPE_COLORS[s.type], tension: 0.25, pointRadius: 2, borderWidth: 2,
      })),
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: LEGEND, tooltip: { callbacks: { label: c => c.dataset.label + ": " + c.raw + "%" } } },
      scales: { x: AX, y: { ...AX, min: 0, max: 100, title: { display: true, text: "% with affiliation", font: CHART_FONT } } },
    },
  });
});

// ── 5b. Citation Lorenz curve + Gini ──
register("citation-lorenz", async () => {
  loading("wac-lorenz", "Loading…");
  let d;
  try { d = await getJSON("/api/wac/citation-lorenz"); }
  catch (e) { return showError("wac-lorenz", e.message); }
  chart("wac-lorenz", {
    type: "line",
    data: {
      datasets: [
        { label: "WAC works (actual)", data: d.points, borderColor: "#5a3e28", backgroundColor: "rgba(90,62,40,0.08)",
          fill: true, pointRadius: 0, borderWidth: 2, tension: 0 },
        { label: "if citations were shared equally", data: [{ x: 0, y: 0 }, { x: 1, y: 1 }], borderColor: "#b9b2a6",
          borderDash: [5, 4], pointRadius: 0, borderWidth: 1, fill: false },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, parsing: false,
      plugins: {
        legend: LEGEND,
        tooltip: { callbacks: { title: () => "", label: c =>
          "Least-cited " + Math.round(c.parsed.x * 100) + "% of works hold " + Math.round(c.parsed.y * 100) + "% of citations" } },
      },
      scales: {
        x: { type: "linear", min: 0, max: 1, ...AX, title: { display: true, text: "works, least-cited → most-cited", font: CHART_FONT },
             ticks: { font: CHART_FONT, callback: v => Math.round(v * 100) + "%" } },
        y: { min: 0, max: 1, ...AX, title: { display: true, text: "share of all citations", font: CHART_FONT },
             ticks: { font: CHART_FONT, callback: v => Math.round(v * 100) + "%" } },
      },
    },
  });
  const note = $("wac-gini-note");
  if (note) note.innerHTML =
    "Attention is highly concentrated: the <strong>most-cited 1%</strong> of works hold <strong>" + d.top1_share +
    "%</strong> of all citations, the top 10% hold <strong>" + d.top10_share +
    "%</strong>, and the bottom half hold just <strong>" + d.bottom50_share + "%</strong>. " +
    "Read any point as “the least-cited X% of works account for Y% of citations” — the more the solid line sags below the dashed equality line, the more uneven the spread (Gini " +
    d.gini + "; " + d.n_cited.toLocaleString() + " of " + d.n_works.toLocaleString() + " works cited at all).";
});

// ── 5c. Citations vs age (scatter + median line) ──
register("citations-vs-age", async (card) => {
  loading("wac-age", "Loading…");
  let d;
  try { d = await getJSON("/api/wac/citations-vs-age"); }
  catch (e) { return showError("wac-age", e.message); }
  const btn = card.querySelector("#wac-ctl-age-cited");
  const draw = () => {
    const onlyCited = btn && btn.dataset.on === "1";
    const byType = {};
    d.points.forEach(p => {
      if (onlyCited && !p.cited_by) return;
      (byType[p.type] = byType[p.type] || []).push({ x: p.year, y: p.cited_by, t: p.title });
    });
    const scatter = Object.keys(byType).map(t => ({
      type: "scatter", label: TYPE_LABEL[t] || t, data: byType[t],
      backgroundColor: TYPE_COLORS[t] + "99", pointRadius: 2.2, pointHoverRadius: 4,
    }));
    scatter.push({
      type: "line", label: "yearly median", data: d.medians.map(m => ({ x: m.year, y: m.median })),
      borderColor: "#a04545", borderWidth: 1.6, pointRadius: 0, tension: 0.2, fill: false,
    });
    chart("wac-age", {
      data: { datasets: scatter },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: LEGEND,
          tooltip: { callbacks: { label: c => c.raw.t ? (c.raw.t.slice(0, 60) + " — " + c.raw.y + " cites") : (c.dataset.label + ": " + c.raw.y) } },
        },
        scales: {
          x: { type: "linear", ...AX, title: { display: true, text: "year", font: CHART_FONT }, ticks: { font: CHART_FONT, callback: v => v } },
          y: { ...AX, title: { display: true, text: "inbound citations", font: CHART_FONT } },
        },
      },
    });
  };
  if (btn && !btn._wired) {
    btn._wired = true;
    btn.addEventListener("click", () => {
      btn.dataset.on = btn.dataset.on === "1" ? "0" : "1";
      const on = btn.dataset.on === "1";
      btn.classList.toggle("is-on", on);
      btn.textContent = on ? "show all" : "hide uncited";
      draw();
    });
  }
  draw();
});

// ── 6a. Collection anatomy (histogram) ──
register("collection-anatomy", async () => {
  loading("wac-anatomy", "Loading…");
  let d;
  try { d = await getJSON("/api/wac/collection-anatomy"); }
  catch (e) { return showError("wac-anatomy", e.message); }
  chart("wac-anatomy", {
    type: "bar",
    data: {
      labels: d.histogram.map(h => h.bucket),
      datasets: [{ label: "edited collections", data: d.histogram.map(h => h.count), backgroundColor: "#3a5a28", borderWidth: 0 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: { title: c => c[0].label + " chapters", label: c => c.raw + " collections" } } },
      scales: { x: { ...AX, title: { display: true, text: "chapters per collection", font: CHART_FONT } }, y: { ...AX, title: { display: true, text: "collections", font: CHART_FONT } } },
    },
  });
  const note = $("wac-anatomy-note");
  if (note) note.innerHTML = "Median <strong>" + d.median_chapters + "</strong> chapters per collection · " +
    d.edited_collections + " edited collections vs " + d.monographs + " single-author monographs · largest has " + d.max_chapters + ".";
});

// ── 7a. Co-authorship rate over time ──
register("coauthorship-trend", async () => {
  loading("wac-collab", "Loading…");
  let d;
  try { d = await getJSON("/api/wac/coauthorship-trend"); }
  catch (e) { return showError("wac-collab", e.message); }
  chart("wac-collab", {
    data: {
      labels: d.years,
      datasets: [
        { type: "line", label: "% multi-author", data: d.pct_multi, borderColor: "#5a3e28", backgroundColor: "rgba(90,62,40,0.07)",
          fill: true, yAxisID: "y", pointRadius: 0, borderWidth: 2, tension: 0.25 },
        { type: "line", label: "mean authors/work", data: d.mean_authors, borderColor: "#3a5a28", yAxisID: "y2",
          pointRadius: 0, borderWidth: 2, tension: 0.25 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: LEGEND },
      scales: {
        x: { ...AX, ticks: { font: CHART_FONT, maxTicksLimit: 16 } },
        y: { ...AX, min: 0, max: 100, position: "left", title: { display: true, text: "% multi-author", font: CHART_FONT } },
        y2: { min: 1, position: "right", grid: { drawOnChartArea: false }, ticks: { font: CHART_FONT }, title: { display: true, text: "mean authors", font: CHART_FONT } },
      },
    },
  });
});

// ── 7b. Team size over decades (100% stacked) ──
register("team-size", async () => {
  loading("wac-team", "Loading…");
  let d;
  try { d = await getJSON("/api/wac/team-size"); }
  catch (e) { return showError("wac-team", e.message); }
  const cols = ["#b38a6a", "#8b6045", "#5a3e28", "#3a322a"];
  const datasets = d.series.map((s, i) => ({
    label: s.bucket + (s.bucket === "1" ? " author" : " authors"),
    data: s.counts.map((c, j) => d.totals[j] ? c / d.totals[j] * 100 : 0),
    backgroundColor: cols[i], borderWidth: 0, _raw: s.counts,
  }));
  chart("wac-team", {
    type: "bar",
    data: { labels: d.decades.map(x => x + "s"), datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: LEGEND, tooltip: { callbacks: { label: c => c.dataset.label + ": " + c.dataset._raw[c.dataIndex] + " (" + Math.round(c.raw) + "%)" } } },
      scales: { x: { stacked: true, ...AX }, y: { stacked: true, ...AX, max: 100, title: { display: true, text: "share of works (%)", font: CHART_FONT } } },
    },
  });
});

// ── 7c. Title-term trend explorer ──
register("title-terms", async (card) => {
  const draw = async (terms) => {
    loading("wac-terms", "Loading title vocabulary…");
    let d;
    const url = "/api/wac/title-term-series" + (terms ? "?terms=" + encodeURIComponent(terms) : "");
    try { d = await getJSON(url); }
    catch (e) { return showError("wac-terms", e.message); }
    // smooth with 3-year trailing mean for readability
    const smooth = (arr) => arr.map((_, i) => {
      const w = arr.slice(Math.max(0, i - 2), i + 1);
      return w.reduce((a, b) => a + b, 0) / w.length;
    });
    chart("wac-terms", {
      type: "line",
      data: {
        labels: d.years,
        datasets: d.series.map((s, i) => ({
          label: s.term + " (" + s.total + ")", data: smooth(s.share),
          borderColor: palette(i), backgroundColor: palette(i), pointRadius: 0, borderWidth: 2, tension: 0.3,
        })),
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: LEGEND, tooltip: { callbacks: { label: c => c.dataset.label.split(" (")[0] + ": " + c.raw.toFixed(1) + "% of titles" } } },
        scales: { x: { ...AX, ticks: { font: CHART_FONT, maxTicksLimit: 16 } }, y: { ...AX, min: 0, title: { display: true, text: "% of year's titles", font: CHART_FONT } } },
      },
    });
  };
  await draw(null);
  const input = card.querySelector("#wac-term-input");
  const go = card.querySelector("#wac-term-go");
  if (go && !go._wired) {
    go._wired = true;
    const run = () => draw((input.value || "").trim());
    go.addEventListener("click", run);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") run(); });
  }
});
