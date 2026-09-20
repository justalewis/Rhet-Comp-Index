// static/js/utils/colors.js
//
// Journal colour for the network visualisations (citation network,
// cocitation, bibcoupling, centrality, communities, journal flow,
// institutions, author network).
//
// Three versions of this have now existed, and the middle one was wrong in
// an instructive way:
//
//   1. A literal array of 25 warm hues, cycled with `i % length`. A literal
//      cannot invert, and cycling says two journals are the same journal.
//
//   2. Eight validated hues with the tail folded into one neutral. Correct
//      for a stacked bar, which can sum its tail into a single band that
//      says something true. Wrong for a network, which has one mark per
//      entity and therefore cannot aggregate anything: folding produced 47
//      grey blobs and a legend asserting they were identical.
//
//   3. What is here. Hue carries the sub-field, lightness carries rank
//      within it. Every journal gets a real colour; nothing is grey unless
//      it is genuinely ungrouped.
//
// Fifty-four distinguishable colours still do not exist. The difference is
// that this stops pretending either way -- it encodes what can be read
// (which sub-field, roughly how much of it) and leaves exact identity to
// the hover, rather than either faking 54 hues or giving up on 46 of them.

import {
  assignByGroup, categorical, chrome, onFigureChange, step,
} from "./theme.js";

/**
 * How many series a *foldable* chart keeps before aggregating the tail.
 *
 * This is for charts that can genuinely aggregate -- the stacked timeline
 * sums its tail into one "Other (47 journals)" band, which says something
 * true. Networks must not use it: they have one mark per entity and
 * nothing to sum, which is why they colour by sub-field instead.
 */
export const CATEGORICAL_LIMIT = 8;

/**
 * The categorical order, live. ES module bindings are live, so importers
 * see the rebuilt array after a figure change without re-importing.
 */
export let PALETTE = categorical();

let _assignment = null;
export let _journalColorMap = {};

function journals() {
  // Injected by the templates before the loader runs: {name, count, group}.
  return window.ALL_JOURNALS || [];
}

function rebuild() {
  PALETTE = categorical();
  _assignment = assignByGroup(journals());
  _journalColorMap = {};
  for (const j of journals()) _journalColorMap[j.name] = _assignment.colorOf(j.name);
}

rebuild();

/**
 * Colour for the i-th series in a fixed order, for series that are not
 * journals -- institutions, work types, a ranked head. Past the eighth,
 * the neutral; this does not wrap.
 */
export function journalColor(i) {
  return i < PALETTE.length ? PALETTE[i] : chrome().other;
}

/**
 * Colour for a journal by name: its sub-field's hue, re-lit for its rank
 * within that sub-field. Identity, not position -- a journal keeps its
 * colour when a filter removes the series around it.
 */
export function citnetJournalColor(name) {
  if (!_assignment) rebuild();
  return _assignment.colorOf(name);
}

/** The sub-field a journal belongs to. */
export function journalGroup(name) {
  if (!_assignment) rebuild();
  return _assignment.groupOf(name);
}

/** The sub-fields present, in palette order, with the hue each one owns. */
export function journalGroups() {
  if (!_assignment) rebuild();
  return _assignment.groups.map((g) => ({ label: g, color: _assignment.hueOf(g) }));
}

/**
 * Legend rows grouped by sub-field, for the journal-coloured networks.
 * `present` is the set of journal names actually drawn, so the legend
 * describes the picture rather than the whole index.
 */
export function legendByGroup(present) {
  if (!_assignment) rebuild();
  const want = new Set(present || []);
  const out = new Map();
  // Seed in the declared group order, so the legend reads in the same
  // order the hues were handed out rather than by whichever journal
  // happened to sort first.
  for (const g of _assignment.groups) {
    out.set(g, { label: g, color: _assignment.hueOf(g), journals: [] });
  }
  for (const j of journals()) {
    if (want.size && !want.has(j.name)) continue;
    const g = _assignment.groupOf(j.name);
    if (!out.has(g)) out.set(g, { label: g, color: _assignment.hueOf(g), journals: [] });
    out.get(g).journals.push({ name: j.name, color: _assignment.colorOf(j.name) });
  }
  // Within a sub-field, heaviest first -- the same order the ladder runs.
  for (const row of out.values()) {
    const rank = new Map(journals().map((j) => [j.name, j.count || 0]));
    row.journals.sort((a, b) => (rank.get(b.name) || 0) - (rank.get(a.name) || 0));
  }
  return [...out.values()].filter((row) => row.journals.length);
}

/** Nothing folds any more; kept so older call sites keep working. */
export function isFolded() {
  return false;
}

/** Every journal now keeps an identity. */
export function namedJournals() {
  return journals().map((j) => j.name);
}

// Rebuild on a figure change so the next render picks up the other set.
// Redrawing is the caller's job: the loaders ask the visible tool to draw
// itself again, because a chart cannot be recoloured in place without
// re-reading every mark.
onFigureChange(rebuild);

export { chrome, onFigureChange, step };
