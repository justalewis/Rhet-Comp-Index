// static/js/utils/chartjs-theme.js
//
// Chart.js ships its own greys -- #666 for text, rgba(0,0,0,0.1) for grid
// lines -- and seven modules here never override them. Those defaults were
// invisible while the site had one appearance. In black-figure they are a
// dark grey axis on a near-black ground, which is the chart quietly losing
// its scale.
//
// Nothing in the per-chart configs needs to change: this sets the defaults
// the charts already inherit, from the same tokens everything else reads.
//
// Import this before any module that constructs a Chart.

import { chrome, onFigureChange } from "./theme.js";

function uiFont() {
  const v = getComputedStyle(document.documentElement)
    .getPropertyValue('--f-ui').trim();
  return v || "system-ui, sans-serif";
}

function apply() {
  const C = window.Chart;
  if (!C || !C.defaults) return false;

  const c = chrome();
  C.defaults.color = c.muted;
  C.defaults.borderColor = c.grid;
  if (C.defaults.font) {
    C.defaults.font.family = uiFont();
    C.defaults.font.size = 12;
  }
  // The built-in tooltip is canvas-drawn, so it cannot take the CSS one.
  // Give it the same object: paper ground, gloss text, a hairline.
  if (C.defaults.plugins && C.defaults.plugins.tooltip) {
    Object.assign(C.defaults.plugins.tooltip, {
      backgroundColor: c.halo,
      titleColor: c.ink,
      bodyColor: c.ink,
      borderColor: c.grid,
      borderWidth: 1,
      cornerRadius: 2,
      displayColors: true,
      padding: 10,
    });
  }
  return true;
}

// The two figures' categorical steps, in palette order, with the neutral
// last. This duplicates what pinakes.css declares, and only for one job:
// recognising a colour a chart was built with so it can be swapped for its
// counterpart. Chart.js keeps dataset colours as the literal strings they
// were constructed from, so `update()` alone re-renders the same hues --
// on a black-figure page that is a red-figure chart. Keep in step with the
// --cat-* tokens if those ever move.
const STEPS = {
  light: ["#A8462C", "#008B78", "#B0821A", "#5A57A8", "#3E7D2A",
          "#8A3A73", "#1466A0", "#A83A5C", "#8C8271"],
  dark:  ["#C96B50", "#2FA890", "#A8842A", "#7F7BC8", "#6BA34B",
          "#B2689C", "#4F93C7", "#CB6E88", "#877C69"],
};

function stepIndex(color) {
  if (typeof color !== "string") return -1;
  const c = color.trim().toUpperCase();
  const i = STEPS.light.findIndex((h) => h.toUpperCase() === c);
  if (i !== -1) return i;
  return STEPS.dark.findIndex((h) => h.toUpperCase() === c);
}

/** Re-point one dataset colour at the current figure's step. */
function restep(value, current) {
  if (Array.isArray(value)) return value.map((v) => restep(v, current));
  const i = stepIndex(value);
  return i === -1 ? value : current[i];
}

/** Recolour and redraw every live chart after the figure changes. */
function refresh() {
  if (!apply()) return;
  const C = window.Chart;
  const dark = getComputedStyle(document.documentElement)
    .getPropertyValue("--cat-1").trim().toUpperCase() === STEPS.dark[0];
  const current = dark ? STEPS.dark : STEPS.light;

  const live = C.instances
    ? Object.values(C.instances)
    : (C.registry && C.registry.instances) || [];
  for (const inst of live) {
    try {
      for (const ds of inst.data.datasets) {
        if (ds.backgroundColor) ds.backgroundColor = restep(ds.backgroundColor, current);
        if (ds.borderColor) ds.borderColor = restep(ds.borderColor, current);
      }
      inst.update("none");
    } catch (e) { /* a chart mid-teardown */ }
  }
}

// Chart.js arrives from a plain <script> tag, which may or may not have run
// by the time this module is evaluated. Try now, and fall back to the next
// task if it has not.
if (!apply()) {
  const retry = setInterval(() => { if (apply()) clearInterval(retry); }, 50);
  setTimeout(() => clearInterval(retry), 5000);
}

onFigureChange(refresh);

export { apply as applyChartTheme, refresh as refreshCharts };
