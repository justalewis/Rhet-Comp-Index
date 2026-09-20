# `static/` — assets, and a stylesheet migration in progress

Two systems live here at once. The **Alexandrian Suite** is the one being built
toward; the three legacy sheets are what most pages still run on. A template
picks between them by overriding `base-core.html`'s `stylesheets` block, so the
rebuild lands one page at a time rather than as a single cutover.

## The Alexandrian Suite

| File | Role |
|---|---|
| `tokens.css` | The palette, type scale and spacing. Copied verbatim from `eunomiaslabors.xyz/static/tokens.css`. |
| `fonts.css` | 23 `@font-face` rules over `fonts/`, subset by `unicode-range`. Also copied verbatim from upstream. |
| `fonts/*.woff2` | GFS Didot, Literata, EB Garamond, Atkinson Hyperlegible — all SIL OFL 1.1. See `fonts/README.txt`. |
| `pinakes.css` | Components on top of the tokens. Currently the icon system only; the rest arrives with the `/tools` pilot. |

**`tokens.css` and `fonts.css` are copies, not sources.** Three sites share
them — Pinakes, Eunomia's Labors, and the `pinakes.xyz` redirect page. Edit them
upstream on Eunomia's Labors, which generates the faces with its
`tools/fetch_fonts.py`, then copy both the CSS and the `fonts/` files here.
Editing them in place will be silently reverted by the next sync.

Pinakes carries all 23 faces where the redirect page carries 12: the index holds
Greek and polytonic characters in titles and author names, and a 50,000-row
catalogue needs Literata 600 for table headers and result emphasis.

Nothing loads from a third party, which is the point — it lets the CSP in
[`web_helpers.py`](../web_helpers.py) reach `style-src 'self'; font-src 'self'`
once the legacy sheets are gone. Two Google Fonts origins are still allowed
there purely because `style-terminal.css:9` `@import`s Share Tech Mono.

### The shell

`templates/base-alexandrian.html` is the rebuilt page shell — the capsa (the
box a library stood its rolls in, which is what the left column does) plus the
content column. It sits *alongside* `base.html` rather than replacing it, so
the templates still on the legacy sheets are untouched while pages move across
one at a time. Its partials are `_capsa_nav.html` and `_capsa_tags.html`,
which mirror `_feature_nav.html` and `_journal_list.html` link for link.

A page joins the new system by extending `base-alexandrian.html`; a page that
needs the old sidebar keeps extending `base.html`. When the last one has moved,
`base.html`, `_feature_nav.html` and `_journal_list.html` go, and
`base-alexandrian.html` takes the name. `templates/tools.html` is the first
one across and is the reference for the rest.

### Icons

57 SVG symbols across three sprite partials in `templates/`:
`_icons_core.html` (12 — the wordmark mark and the sidebar destinations, carried
on every page), `_icons_tools.html` (19 analytical tools), and
`_icons_datastories.html` (26 chapter panels, loaded only where they are used).
Drawing rules and the accent mechanism are documented at the top of each file
and in the icon section of `pinakes.css`. `/design/icons` renders all of them
with the type specimen; it is unlinked and `noindex`.

## The legacy sheets

Everything not yet rebuilt loads all three of these, and switches between them by
adding a class to `<html>` rather than swapping stylesheets, so all three are
effective at once on every request. All three are slated for deletion once the
rebuild reaches the last template.

| File | Lines | Role |
|---|---:|---|
| `style.css` | 3768 | Default theme. All legacy component styles live here. |
| `style-scandi.css` | 1689 | Scandi theme. Pure override layer (`html.scandi .x`). |
| `style-terminal.css` | 720 | Terminal theme. Pure override layer (`html.terminal .x`). |
| `style-wac.css` | 182 | Loaded only by `templates/wac.html`. |
| `explore.js` | — | D3-based visualisations on `/explore`. |

Until 2026-09-19 these sheets read their tokens from a CDN
(`justalewis.github.io/lewis-design-system`) that the CSP never allowed, so the
whole site ran on the six-line literal fallback in `base-core.html`. That
`<link>` has been removed; the fallback block is now the honest, declared source
of those values until each page moves to the Suite.

## Theme switching (legacy)

A small inline `<script>` in `base-core.html` does the work:

```js
var t = localStorage.getItem('rc-theme');
if (t === 'terminal') document.documentElement.classList.add('terminal');
else if (t === 'scandi') document.documentElement.classList.add('scandi');
```

Two toggle buttons (created by another inline script in `base-core.html`) flip the value in `localStorage` and add/remove the corresponding class on `<html>`. There is no server-side theme state; reload preserves the choice via `localStorage`.

Theme override stylesheets target `html.terminal .selector` / `html.scandi .selector`, so the default theme is whatever `style.css` declares for `.selector` without a theme prefix. To add a new component:

1. Style it in `style.css` first.
2. If the Terminal or Scandi themes need a different presentation, add `html.terminal .new-class { ... }` to `style-terminal.css` (or the Scandi equivalent). Otherwise the default cascades through.

## Dead rule audit

A one-time audit in [`docs/refactor-notes/05-css-audit.md`](../docs/refactor-notes/05-css-audit.md) lists 12 selectors in `style.css` that no longer match any template or JS class. They are not deleted — left as a follow-up so a maintainer can confirm none are dynamically injected (e.g., by D3 in the viz modules) before removing them.

## JavaScript module layout (after F2)

The `/explore` page is the only page with substantial client-side JS. As of prompt F2 it loads from a small ES module loader rather than the monolithic `explore.js`:

```
js/
├── explore-loader.js     entry point loaded by templates/explore.html
├── utils/                shared helpers (colors, tooltips, highlight)
└── viz/                  one file per visualization (18 modules)
```

`explore-loader.js` eagerly imports every viz module at page load — see [`../docs/refactor-notes/11-explore-js-split-inventory.md`](../docs/refactor-notes/11-explore-js-split-inventory.md) for the rationale (race-condition avoidance with inline `onclick=` handlers).

The original `static/explore.js` is kept in place as a one-line revert path: if anything regresses, swap the `<script>` tag in `templates/explore.html` back and the old monolithic file takes over.
