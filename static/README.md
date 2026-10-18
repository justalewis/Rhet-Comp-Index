# `static/` — the Alexandrian Suite

Every page built on `base-core.html` loads three stylesheets, in this order, via
[`templates/_alexandrian_head.html`](../templates/_alexandrian_head.html). The
one template that does not is `usage_report.html`, the printable usage report,
which carries its own inline styles.

| File | Lines | Role |
|---|---:|---|
| `tokens.css` | 229 | The palette, type scale and spacing, plus the element baseline. |
| `fonts.css` | 226 | 23 `@font-face` rules over `fonts/`, subset by `unicode-range`. |
| `pinakes.css` | 2546 | Every component on the site. |
| `style-wac.css` | 193 | Loaded only by `/wac`, on top of the three above. |

Order matters: `tokens.css` declares the palette, `fonts.css` maps the faces it
names, `pinakes.css` builds components on top.

## `tokens.css` and `fonts.css` are copies, not sources

Three sites share them — Pinakes, [Eunomia's Labors](https://eunomiaslabors.xyz),
and the `pinakes.xyz` redirect page. **Edit them upstream on Eunomia's Labors**,
which generates the faces with its `tools/fetch_fonts.py`, then copy both the CSS
and the matching files in `fonts/` here. Editing them in place will be silently
reverted by the next sync.

Pinakes carries all 23 faces where the redirect page carries 12: the index holds
Greek and polytonic characters in titles and author names, and a 50,000-row
catalogue needs Literata 600 for table headers and result emphasis. All four
families are SIL OFL 1.1 — see `fonts/README.txt`.

No style or font loads from a third party. That is what lets the CSP in
[`web_helpers.py`](../web_helpers.py) keep `style-src 'self' 'unsafe-inline'`
and `font-src 'self'`. Scripts are another matter: `script-src` still allows
`cdn.jsdelivr.net` (D3 and Chart.js) and `gc.zgo.at` (the GoatCounter tag), and
`connect-src` allows `pinakes.goatcounter.com` for its beacon.

## Red-figure and black-figure

The palette is sampled from Attic red-figure pottery, and dark mode is not a
theme hack: a red-figure vase is already a dark-ground design, so inverting
gloss and clay gives you black-figure. Both states live in `tokens.css`.

A page follows the reader's system setting unless they pick otherwise with the
toggle at the foot of the capsa. The choice is stored as `pinakes-figure` in
`localStorage` and applied before first paint by the inline script in
`_alexandrian_head.html`, so a reader who chose the other figure never watches
the page repaint into it.

`.figure-switching` suppresses component transitions for the frame the switch
happens in — without it, the hover transitions turn a theme change into a fade
through the wrong palette.

## Icons

57 SVG symbols across three sprite partials in `templates/`:

| Partial | Symbols | Loaded |
|---|---:|---|
| `_icons_core.html` | 12 | Every page built on `base-core.html`. |
| `_icons_tools.html` | 19 | `/tools` (`tools.html`) and `/design/icons`. |
| `_icons_datastories.html` | 26 | `/design/icons` only. No Datastories template includes it. |

Drawing rules are at the top of each partial and in the icon section of
`pinakes.css`. The short version: a 24×24 grid, `currentColor` at 1.6, and
exactly one accented element per icon — the one carrying the meaning.

Accents travel as **inherited custom properties**, not classes. A `<use>` clone
lives in a shadow tree that document CSS selectors do not reach, but inherited
properties cross that boundary, so `--ic-accent` set on `.ic` lands on the paths
inside the symbol.

`/design/icons` renders the whole set with the type specimen. Unlinked,
`noindex`, and `Disallow: /design/` in robots.txt.

## Charts

`utils/theme.js` is the bridge. Chart colour is read from CSS custom properties
**at draw time**, so charts follow the figure; a literal cannot invert, and
black-figure would otherwise have drawn every chart in red-figure ink.

The eight categorical steps live in `pinakes.css` (`--cat-1` … `--cat-8`), not in
`tokens.css`, because that file is a verbatim copy. They are pigments from the
same world as the Suite — terracotta, verdigris, ochre, indigo, olive,
manganese, Egyptian blue, madder — pushed to the chroma a categorical encoding
needs.

Both figures were checked with the `dataviz` validator rather than by eye:

- red-figure on `#F5EFE1` — all five checks pass
- black-figure on `#171512` — pass, with `--cat-7` against `--cat-6` at deutan
  ΔE 6.8

That last pair sits in the 6–8 band, which is legal **only** with a secondary
encoding. So on any chart that can show both at once, a legend is not optional.
**Do not reorder these**: adjacency is what was validated, and madder next to
verdigris in particular is invisible to a deutan reader.

The order is fixed and never cycled. Repeating a hue does not say "ninth
series", it says "the same series". How a chart gets past eight depends on what
it draws (all of it in `js/utils/colors.js`):

- **Charts that can sum a tail**, like the stacked timeline, keep
  `CATEGORICAL_LIMIT` (8) series and fold the rest into one `--cat-other` band.
- **Journal-coloured networks** have one mark per journal and nothing to sum,
  so they colour by sub-field: each sub-field group in `journals.py` takes one
  step as its hue, and lightness carries a journal's rank within the group, the
  darkest going to the journal with the most work in the index. Only ungrouped
  journals take the neutral. `js/utils/legend.js` groups the legend the same
  way. There are eight sub-fields, one per step; `assignByGroup` in
  `utils/theme.js` would wrap round to reuse a hue if a ninth were added.
- **Other ranked series** (institutions, work types) take the steps in order
  and the neutral past the eighth; `journalColor(i)` does not wrap.

### Redrawing when the figure changes

Two different mechanisms, because the two chart libraries differ:

- **Chart.js** charts are recoloured in place by `utils/chartjs-theme.js`, which
  also supplies the library's defaults. Chart.js keeps dataset colours as the
  literal strings they were built from, so `update()` alone re-renders the same
  hues.
- **D3** scenes cannot be recoloured from outside, because each module owns its
  own mapping from datum to mark. `explore-loader.js` and
  `datastories-loader.js` ask the *visible* tool to draw itself again. Only the
  visible one — the rest are drawn fresh when next shown.

A page that builds charts outside those loaders has to handle this itself.
`templates/author.html` is the one such page: it sets the Chart.js defaults in
its own script and imports `utils/chartjs-theme.js` for the recolouring, but its
D3 co-citation graph reads the palette once at load and keeps those colours
until the page is reloaded.

## JavaScript layout

```
js/
├── explore-loader.js      entry point for /explore
├── datastories-loader.js  entry point for the Datastories tools
├── tag-feedback.js        tag votes and topic suggestions on the article page
├── utils/
│   ├── theme.js           the token bridge; categorical(), chrome(), assignByGroup(), onFigureChange()
│   ├── chartjs-theme.js   Chart.js defaults and recolouring
│   ├── colors.js          journal colour by sub-field and rank; CATEGORICAL_LIMIT for foldable charts
│   ├── legend.js          the sub-field journal legend for the networks
│   ├── tooltips.js, highlight.js
├── viz/                   one file per visualisation (18 + 26 Datastories)
├── wac/                   the /wac dashboard
└── shared/                common, filters, export, load (JSON fetch with error text), year_range
```

Beside `js/`, the `static/` root holds three plain (non-module) scripts and an
XSL stylesheet: `feed-copy.js` (the "Copy address" buttons on `/feeds` and each
feed's page), `feed-select.js` (the feed builder on `/feeds`), `usage.js` (the
browser half of usage statistics, loaded only when recording is on), and
`feed.xsl`, which browsers apply to the Atom feeds so a clicked feed link shows
a readable page.

`explore-loader.js` eagerly imports every viz module at page load — see
[`../docs/refactor-notes/11-explore-js-split-inventory.md`](../docs/refactor-notes/11-explore-js-split-inventory.md)
for why (race conditions with inline `onclick=` handlers).

## Adding a component

Style it in `pinakes.css`, against tokens. Never a literal colour: a literal
does not invert, and black-figure is not a variant you can skip.

Some class names in `pinakes.css` are in an older vocabulary — `.article-*`
beside `.entry-*`, `.citnet-*`, `.net-*`. Those markup names stayed when their
pages moved, because JavaScript addresses a good deal of that markup by class
and renaming it would have meant editing scripts in order to change a colour.
The values behind them are tokens like everything else.

## History

The rebuild is written up in
[`../docs/refactor-notes/17-alexandrian-rebuild.md`](../docs/refactor-notes/17-alexandrian-rebuild.md),
including what the site looked like before and why the token layer was not
loading at all.
