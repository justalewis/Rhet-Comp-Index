// static/js/utils/colors.js
//
// Journal colour for the network visualisations (citation network,
// cocitation, bibcoupling, centrality, communities, journal flow,
// institutions, author network).
//
// This used to hold a literal array of 25 warm hues, cycled with
// `PALETTE[i % PALETTE.length]`. Both halves of that were wrong once the
// site gained a second figure and a validated palette:
//
//   * a literal cannot invert, so black-figure would have drawn every
//     chart in red-figure ink -- colour now comes from CSS custom
//     properties through utils/theme.js, and follows the page;
//
//   * cycling says two journals are the same journal. Twenty-five hues
//     were never distinguishable anyway. The eight here are a fixed
//     order, validated for colour-vision separation in both figures, and
//     the tail folds into one neutral instead of reusing a hue.
//
// NOTE FOR REVIEW -- this changes what the journal-coloured charts show.
// The index carries 54 journals; the eight most present keep an identity
// and the rest are drawn as "Other". That is a real loss of detail, and
// it is deliberate: 54 categories is not an encoding a reader can hold,
// and 25 cycled hues only pretended otherwise. If the old behaviour is
// wanted back, widen CATEGORICAL_LIMIT below -- but the palette has only
// been validated to eight, so anything past that is unchecked.

import { assign, categorical, chrome, onFigureChange } from "./theme.js";

/** How many journals keep an identity before the tail folds to "Other". */
export const CATEGORICAL_LIMIT = 8;

/**
 * The categorical order, live. ES module bindings are live, so importers
 * see the rebuilt array after a figure change without re-importing.
 */
export let PALETTE = categorical();

let _assignment = null;
export let _journalColorMap = {};

function journalNames() {
  // Injected by templates/explore.html before the loader runs. Ranked by
  // the server, so slicing the head takes the most-present journals.
  return (window.ALL_JOURNALS || []).map((j) => j.name);
}

function rebuild() {
  PALETTE = categorical();
  _assignment = assign(journalNames().slice(0, CATEGORICAL_LIMIT * 4));
  _journalColorMap = {};
  for (const n of _assignment.shown) _journalColorMap[n] = _assignment.colorOf(n);
}

rebuild();

/**
 * Colour for the i-th series in a fixed order. Past the eighth, the
 * neutral -- this does not wrap.
 */
export function journalColor(i) {
  return i < PALETTE.length ? PALETTE[i] : chrome().other;
}

/**
 * Colour for a journal by name. Identity, not rank: a journal keeps its
 * hue when a filter removes the series around it.
 */
export function citnetJournalColor(name) {
  if (!_assignment) rebuild();
  return _assignment.colorOf(name);
}

/** True when this journal is drawn as "Other" rather than with a hue. */
export function isFolded(name) {
  if (!_assignment) rebuild();
  return _assignment.isFolded(name);
}

/** The journals that kept an identity, in palette order. */
export function namedJournals() {
  if (!_assignment) rebuild();
  return _assignment.shown;
}

// Rebuild on a figure change so the next render picks up the other set.
// Redrawing is the caller's job: a chart cannot be recoloured in place
// without re-reading every mark, and the modules already know how to draw
// themselves from scratch.
onFigureChange(rebuild);

export { chrome, onFigureChange };
