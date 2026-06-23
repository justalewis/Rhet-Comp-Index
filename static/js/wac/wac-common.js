// wac-common.js — shared helpers for the WAC Clearinghouse dashboard (/wac).
// Plain ES module; D3 and Chart.js are loaded as globals by the template.

// Work-type palette (kept in sync with style-wac.css).
export const TYPE_COLORS = {
  "journal-article": "#5a3e28",
  "book-chapter":    "#b38a6a",
  "edited-book":     "#3a5a28",
  "monograph":       "#28445a",
};
export const TYPE_LABEL = {
  "journal-article": "Journal articles",
  "book-chapter":    "Book chapters",
  "edited-book":     "Edited collections",
  "monograph":       "Monographs",
};
// A warm categorical palette for journals / institutions / generic series.
export const WARM = [
  "#5a3e28","#8b6045","#b38a6a","#3a5a28","#456b40","#6b8f60",
  "#28445a","#405c78","#608aac","#7a4560","#ac6882","#5a5828","#aaaa60","#9c7a3a",
];
export const palette = (i) => WARM[i % WARM.length];

export function fmt(n) {
  if (n == null) return "";
  return Number(n).toLocaleString("en-US");
}

export function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

export const $ = (id) => document.getElementById(id);

// Write a status message (loading / error). A <canvas> can't hold HTML children
// and must survive for Chart.js to draw into it, so for canvases we float an
// absolutely-positioned overlay inside the .wac-chart-wrap parent instead of
// replacing content. Non-canvas containers get their innerHTML replaced (the
// viz render then overwrites it). Pass html=null to clear a canvas overlay.
function status(id, html) {
  const e = $(id);
  if (!e) return;
  if (e.tagName === "CANVAS") {
    const wrap = e.parentElement;
    if (!wrap) return;
    let ov = wrap.querySelector(".wac-status-ov");
    if (html == null) { if (ov) ov.remove(); return; }
    if (!ov) { ov = document.createElement("div"); ov.className = "wac-status-ov"; wrap.appendChild(ov); }
    ov.innerHTML = html;
  } else {
    e.innerHTML = html == null ? "" : html;
  }
}
export function loading(id, msg) { status(id, '<div class="wac-loading">' + escapeHtml(msg || "Loading…") + "</div>"); }
export function showError(id, msg) { status(id, '<div class="wac-err">' + escapeHtml(msg) + "</div>"); }
export function clearStatus(id) { status(id, null); }

export async function getJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error("HTTP " + r.status + " for " + url);
  return r.json();
}

// ── shared tooltip ──────────────────────────────────────────
let _tip = null;
function tipEl() {
  if (!_tip) {
    _tip = document.createElement("div");
    _tip.className = "wac-tip";
    document.body.appendChild(_tip);
  }
  return _tip;
}
export function showTip(html, event) {
  const t = tipEl();
  t.innerHTML = html;
  t.style.display = "block";
  moveTip(event);
}
export function moveTip(event) {
  if (!_tip) return;
  const pad = 14;
  let x = event.clientX + pad, y = event.clientY + pad;
  const w = _tip.offsetWidth, h = _tip.offsetHeight;
  if (x + w > window.innerWidth - 8) x = event.clientX - w - pad;
  if (y + h > window.innerHeight - 8) y = event.clientY - h - pad;
  _tip.style.left = x + "px";
  _tip.style.top = y + "px";
}
export function hideTip() { if (_tip) _tip.style.display = "none"; }

// ── viz registry (filled by the section modules, consumed by the loader) ──
export const registry = {};
export function register(name, fn) { registry[name] = fn; }

// ── a small CSS-bar list builder (used by several table-ish views) ──
// rows: [{label, value, href?, segments?:[{w, color, title}], valueText?}]
export function renderBars(containerId, rows, opts) {
  opts = opts || {};
  const el = $(containerId);
  if (!el) return;
  if (!rows || !rows.length) { el.innerHTML = '<div class="wac-empty">No data.</div>'; return; }
  const max = opts.max || Math.max(...rows.map(r => r.value || 0), 1);
  let html = '<div class="wac-bars">';
  rows.forEach(r => {
    const lbl = r.href
      ? '<a href="' + r.href + '" target="_blank" rel="noopener">' + escapeHtml(r.label) + "</a>"
      : escapeHtml(r.label);
    let fill;
    if (r.segments) {
      const totalW = r.segments.reduce((a, s) => a + s.w, 0) || 1;
      const pct = (r.value || totalW) / max * 100;
      fill = '<div class="wac-bar-fill" style="width:' + pct + '%">' +
        r.segments.map(s =>
          '<span class="wac-bar-seg" style="width:' + (s.w / totalW * 100) + '%;background:' + s.color + '"' +
          (s.title ? ' title="' + escapeHtml(s.title) + '"' : "") + "></span>").join("") +
        "</div>";
    } else {
      const pct = (r.value || 0) / max * 100;
      fill = '<div class="wac-bar-fill" style="width:' + pct + '%"><span class="wac-bar-seg" style="width:100%;background:' +
        (r.color || "#8b6045") + '"></span></div>';
    }
    html += '<div class="wac-bar-row"><span class="lbl">' + lbl + '</span>' +
      '<span class="wac-bar-track">' + fill + "</span>" +
      '<span class="val">' + escapeHtml(r.valueText != null ? r.valueText : fmt(r.value)) + "</span></div>";
  });
  html += "</div>";
  el.innerHTML = html;
}

// Legend row for the work-type colors.
export function typeLegend(types) {
  types = types || ["journal-article", "book-chapter", "edited-book", "monograph"];
  return '<div class="wac-legend">' + types.map(t =>
    '<span><i style="background:' + TYPE_COLORS[t] + '"></i>' + escapeHtml(TYPE_LABEL[t]) + "</span>").join("") + "</div>";
}

// Destroy + recreate a Chart.js instance keyed by canvas id.
const _charts = {};
export function chart(canvasId, config) {
  const c = $(canvasId);
  if (!c) return null;
  if (_charts[canvasId]) _charts[canvasId].destroy();
  _charts[canvasId] = new Chart(c.getContext("2d"), config);
  clearStatus(canvasId);  // remove any loading/error overlay now that we've drawn
  return _charts[canvasId];
}

export const CHART_FONT = { family: "system-ui, sans-serif", size: 11 };
