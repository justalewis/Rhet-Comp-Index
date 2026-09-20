# `static/` — the Alexandrian Suite

Every page loads three stylesheets, in this order, via
[`templates/_alexandrian_head.html`](../templates/_alexandrian_head.html):

| File | Lines | Role |
|---|---:|---|
| `tokens.css` | 229 | The palette, type scale and spacing, plus the element baseline. |
| `fonts.css` | 226 | 23 `@font-face` rules over `fonts/`, subset by `unicode-range`. |
| `pinakes.css` | 2493 | Every component on the site. |
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

Nothing loads from a third party. That is what lets the CSP in
[`web_helpers.py`](../web_helpers.py) read `style-src 'self'; font-src 'self'`.

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
| `_icons_core.html` | 12 | Every page, by `base-core.html`. |
| `_icons_tools.html` | 19 | `/tools` and `/explore`. |
| `_icons_datastories.html` | 26 | The Datastories pages only. |

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

The order is fixed and never cycled. Past eight series the tail folds into
`--cat-other` rather than reusing a hue — see `CATEGORICAL_LIMIT` in
`js/utils/colors.js`. Repeating a hue does not say "ninth series", it says "the
same series".

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

A page that builds charts outside those loaders has to do both itself;
`templates/author.html` is the one that does, and says so.

## JavaScript layout

```
js/
├── explore-loader.js      entry point for /explore
├── datastories-loader.js  entry point for the Datastories tools
├── utils/
│   ├── theme.js           the token bridge; categorical(), chrome(), onFigureChange()
│   ├── chartjs-theme.js   Chart.js defaults and recolouring
│   ├── colors.js          journal colour, by identity, folding past eight
│   ├── tooltips.js, highlight.js
├── viz/                   one file per visualisation (18 + 26 Datastories)
├── wac/                   the /wac dashboard
└── shared/                common, filters, export
```

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
