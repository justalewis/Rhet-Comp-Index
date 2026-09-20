// static/js/utils/theme.js
//
// The bridge between the Alexandrian Suite and the visualisations.
//
// Every chart on the site used to carry its colours as literals -- around
// 280 hex values spread over nineteen modules, in D3 attributes, Chart.js
// config objects and inline tooltip HTML. That was survivable while the
// site had one appearance. It stopped being survivable when it gained two:
// a literal cannot invert, so black-figure would have drawn every chart in
// red-figure ink on a black ground.
//
// So colour is read from CSS custom properties at draw time instead. The
// figure is whatever the stylesheet currently says it is, which means the
// charts follow the page for free -- including a reader who flips the
// toggle with a chart already on screen.
//
// Read these through the accessors, never cache the returned strings past
// a render. `onFigureChange` exists for the redraw.

const ROOT = document.documentElement;

function tok(name, fallback) {
  const v = getComputedStyle(ROOT).getPropertyValue(name).trim();
  return v || fallback;
}

/** The fixed categorical order. Never cycled -- see `assign`. */
export function categorical() {
  return [
    tok('--cat-1', '#A8462C'),
    tok('--cat-2', '#008B78'),
    tok('--cat-3', '#B0821A'),
    tok('--cat-4', '#5A57A8'),
    tok('--cat-5', '#3E7D2A'),
    tok('--cat-6', '#8A3A73'),
    tok('--cat-7', '#1466A0'),
    tok('--cat-8', '#A83A5C'),
  ];
}

/** Chart chrome. Recessive by construction. */
export function chrome() {
  return {
    ink:     tok('--chart-ink', '#33302A'),
    muted:   tok('--chart-muted', '#6A5F4E'),
    grid:    tok('--chart-grid', '#DDD3BF'),
    surface: tok('--chart-surface', '#F5EFE1'),
    halo:    tok('--chart-halo', '#FBF7EE'),
    accent:  tok('--terracotta', '#A8462C'),
    absent:  tok('--faience', '#4A6670'),
    other:   tok('--cat-other', '#8C8271'),
  };
}

/**
 * Map a list of entity names onto the categorical order, by identity and
 * not by rank: the same journal keeps the same hue whatever else is on
 * screen, so a filter that drops series never repaints the survivors.
 *
 * Past the eighth name the tail folds into one neutral rather than
 * reusing a hue. Repeating a hue does not say "ninth series", it says
 * "the same series", and no reader holds more than eight identities at
 * once anyway. Callers that want the long tail visible should rank first
 * and pass the head.
 *
 * Returns { colorOf(name), shown: [names], folded: [names], other }.
 */
export function assign(names) {
  const cats = categorical();
  const c = chrome();
  const shown = names.slice(0, cats.length);
  const folded = names.slice(cats.length);
  const map = new Map(shown.map((n, i) => [n, cats[i]]));
  return {
    colorOf: (name) => map.get(name) || c.other,
    shown,
    folded,
    other: c.other,
    isFolded: (name) => !map.has(name),
  };
}

/**
 * Run `cb` whenever the figure changes -- the toggle in the capsa, or the
 * reader's system setting moving under a page with no explicit override.
 * Charts should re-read their colours and redraw; they must not try to
 * recolour existing nodes from a cached palette.
 */
export function onFigureChange(cb) {
  const mo = new MutationObserver((records) => {
    for (const r of records) {
      if (r.attributeName === 'data-theme') { cb(); return; }
    }
  });
  mo.observe(ROOT, { attributes: true, attributeFilter: ['data-theme'] });

  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  mq.addEventListener('change', () => {
    // Only relevant while no explicit override is set; with one, the
    // MutationObserver above is what fires.
    if (!ROOT.hasAttribute('data-theme')) cb();
  });

  return () => mo.disconnect();
}
