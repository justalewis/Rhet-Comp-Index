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

/* ============================================================
   Colour arithmetic, in OKLCH.

   A network cannot aggregate. A stacked bar can sum its tail into one
   "Other" band and say something true; a force layout has one mark per
   entity, so folding 47 journals into one neutral just draws 47 grey
   blobs and a legend that says they are identical.

   Fifty-four distinguishable colours do not exist -- that part of the
   rule stands. So identity is carried by two channels instead of one:

     hue        the sub-field the journal belongs to (seven groups)
     lightness  its rank by indexed output within that sub-field

   Both are decodable. "This cluster is technical communication" is a
   real reading, and arguably the more useful one for a field that keeps
   asking whether it is one field or two. "Darker means more work in the
   index" is a real reading too. What you cannot do is name the
   fourteenth journal from its tint alone, and nothing here pretends
   otherwise -- exact identity comes from the hover.

   Lightness moves in OKLCH so chroma and hue stay put; a naive RGB
   lighten would drift the hue and flatten the saturation at the ends.
   The band is bounded to the range the palette was validated in, so no
   step falls below 3:1 against its ground.
   ============================================================ */

// A light ground wants dark marks and a dark ground wants light ones, so
// the ladder runs in opposite directions in the two figures.
//
// No single band can guarantee contrast on its own: OKLab lightness is not
// WCAG relative luminance, and two hues at the same L can sit either side
// of the 3:1 floor -- at L 0.70 the olive step measured 2.21 against
// papyrus while others were comfortable. So the band sets the shape of the
// ramp and `ensureContrast` guarantees the floor, which also means the
// ramp survives someone re-picking the eight hues.
const L_BAND = { light: [0.40, 0.62], dark: [0.56, 0.74] };
const CONTRAST_FLOOR = 3;

function relLuminance(hex) {
  const v = hex.replace('#', '').match(/../g).map((x) => {
    const n = parseInt(x, 16) / 255;
    return n <= 0.03928 ? n / 12.92 : Math.pow((n + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
}

function contrast(a, b) {
  const x = relLuminance(a);
  const y = relLuminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

/** Walk L away from the ground until the mark clears the floor. */
function ensureContrast(hex, surface) {
  const dark = isDark();
  let { L, C, h } = hexToOklch(hex);
  let out = hex;
  for (let i = 0; i < 30; i++) {
    out = oklchToHex(L, C, h);
    if (contrast(out, surface) >= CONTRAST_FLOOR) return out;
    L += dark ? 0.015 : -0.015;
    if (L > 0.97 || L < 0.06) break;
  }
  return out;
}

function srgbToLinear(c) {
  c /= 255;
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function linearToSrgb(c) {
  const v = c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
  return Math.round(Math.min(1, Math.max(0, v)) * 255);
}

function hexToOklch(hex) {
  const h = hex.trim().replace('#', '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const r = srgbToLinear(parseInt(full.slice(0, 2), 16));
  const g = srgbToLinear(parseInt(full.slice(2, 4), 16));
  const b = srgbToLinear(parseInt(full.slice(4, 6), 16));

  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);

  const L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s;
  const A = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s;

  return { L, C: Math.hypot(A, B), h: Math.atan2(B, A) };
}

function oklchToHex(L, C, h) {
  const A = Math.cos(h) * C;
  const B = Math.sin(h) * C;

  const l = Math.pow(L + 0.3963377774 * A + 0.2158037573 * B, 3);
  const m = Math.pow(L - 0.1055613458 * A - 0.0638541728 * B, 3);
  const s = Math.pow(L - 0.0894841775 * A - 1.2914855480 * B, 3);

  const r = linearToSrgb(+4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s);
  const g = linearToSrgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s);
  const b = linearToSrgb(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s);

  return '#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('').toUpperCase();
}

/** Is the page currently in black-figure? */
function isDark() {
  const set = ROOT.getAttribute('data-theme');
  if (set === 'dark') return true;
  if (set === 'light') return false;
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

/**
 * `hex` re-lit at position `i` of `n`, across the band this figure was
 * validated in. i = 0 is the darkest step, which goes to the top-ranked
 * member so the heaviest presence reads heaviest.
 */
export function step(hex, i, n) {
  const { C, h } = hexToOklch(hex);
  const [lo, hi] = isDark() ? L_BAND.dark : L_BAND.light;
  // i = 0 is the end furthest from the ground, so the top-ranked member of
  // a sub-field reads heaviest in either figure.
  const t = n <= 1 ? 0.5 : i / (n - 1);
  const L = isDark() ? hi - (hi - lo) * t : lo + (hi - lo) * t;
  return ensureContrast(oklchToHex(L, C, h), chrome().surface);
}

/**
 * Colour every journal by sub-field and rank.
 *
 * `journals` is the ALL_JOURNALS payload: {name, count, group}. Groups
 * take the categorical steps in the order build_sidebar emits them, which
 * is the order journals.py declares -- fixed, so a journal keeps its hue
 * when a filter removes the series around it. "Other" means genuinely
 * ungrouped and takes the neutral.
 */
export function assignByGroup(journals) {
  const cats = categorical();
  const c = chrome();
  const byGroup = new Map();

  for (const j of journals || []) {
    const g = j.group || 'Other';
    if (!byGroup.has(g)) byGroup.set(g, []);
    byGroup.get(g).push(j);
  }

  // Hue follows the order journals.py declares, injected by the template.
  // Insertion order here would follow whichever journal sorted first, so a
  // single new journal could swap two sub-fields' colours.
  const declared = window.JOURNAL_GROUP_ORDER || [];
  const ordered = [
    ...declared.filter((g) => byGroup.has(g)),
    ...[...byGroup.keys()].filter((g) => !declared.includes(g)),
  ];

  const map = new Map();
  const groupHue = new Map();
  let hueIndex = 0;

  for (const g of ordered) {
    const members = byGroup.get(g);
    const base = g === 'Other' ? c.other : cats[hueIndex++ % cats.length];
    groupHue.set(g, base);
    // Darkest step to the most-published, so weight reads as weight.
    members.sort((a, b) => (b.count || 0) - (a.count || 0));
    members.forEach((j, i) => map.set(j.name, step(base, i, members.length)));
  }

  return {
    colorOf: (name) => map.get(name) || c.other,
    groupOf: (name) => (journals || []).find((j) => j.name === name)?.group || 'Other',
    groups: ordered,
    hueOf: (group) => groupHue.get(group) || c.other,
    size: map.size,
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
