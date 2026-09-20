// static/js/utils/legend.js
//
// The journal legend for the networks.
//
// It used to be a flat run of every journal in the picture, each with a
// dot. Once the palette folded past eight that became its own argument
// against itself: seventeen entries in the same grey, asserting seventeen
// times that they were indistinguishable.
//
// Colour now carries the sub-field and lightness carries rank within it,
// so the legend says that instead. The sub-field is the thing a reader can
// actually decode from the picture, so it leads; the journals sit under it
// in their own tints, which is where the ordering becomes legible -- the
// darkest chip in a group is the one with the most work in the index.
//
// Five modules rendered the same markup by hand. They all call this now.

import { legendByGroup } from "./colors.js";
import { escapeHtml } from "./tooltips.js";

/**
 * Render the legend for a network into `el`.
 *
 * `present` is the journal names actually drawn, so the legend describes
 * the picture rather than the whole index. Pass nothing for the lot.
 *
 * `opts.interactive` carries the click-to-filter affordance the citation
 * network puts on its legend: each journal keeps its data-journal hook and
 * its cursor, so the handler bound in that module still finds them.
 */
export function renderJournalLegend(el, present, opts) {
  const interactive = !!(opts && opts.interactive);
  const target = typeof el === "string" ? document.getElementById(el) : el;
  if (!target) return;

  const groups = legendByGroup(present);
  if (!groups.length) {
    target.innerHTML = "";
    return;
  }

  target.innerHTML = groups.map((g) => (
    `<div class="jlegend-group">` +
      `<span class="jlegend-head">` +
        `<span class="jlegend-swatch" style="background:${g.color}"></span>` +
        `${escapeHtml(g.label)}` +
      `</span>` +
      `<span class="jlegend-items">` +
        g.journals.map((j) => (
          `<span class="jlegend-item"` +
            (interactive
              ? ` data-journal="${escapeHtml(j.name)}" style="cursor:pointer;"` +
                ` title="Click to toggle this journal in the filter"`
              : ``) +
          `>` +
            `<span class="jlegend-dot" style="background:${j.color}"></span>` +
            `${escapeHtml(j.name)}` +
          `</span>`
        )).join("") +
      `</span>` +
    `</div>`
  )).join("");
}

/**
 * The key that explains the encoding. Worth showing once per panel, since
 * "darker means more" is not guessable from the picture alone.
 */
export function legendKeyHtml() {
  return `<p class="jlegend-key">Colour is the journal&rsquo;s sub-field; ` +
         `within a sub-field, the darker the mark the more of that ` +
         `journal the index holds. Hover a node for its journal by name.</p>`;
}
