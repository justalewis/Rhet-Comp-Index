# 17 — The Alexandrian Suite rebuild

September 2026. Pinakes moved off the Lewis Design System onto the Alexandrian
Suite, the design system already shared by [Eunomia's
Labors](https://eunomiaslabors.xyz) and the `pinakes.xyz` redirect page.

Net: **131 files, +5,656 / −7,731**.

## Why, and what was actually broken

The starting point was not a working design system that needed a repaint.

`base-core.html` linked its tokens from
`justalewis.github.io/lewis-design-system/v1/tokens.css`. The CSP in
`web_helpers.py` set `style-src 'self' 'unsafe-inline' fonts.googleapis.com` —
which never listed that host. The stylesheet had been blocked on every request
since the move to the Clearinghouse:

```
Loading the stylesheet 'https://justalewis.github.io/lewis-design-system/v1/tokens.css'
violates the following Content Security Policy directive: "style-src 'self' 'unsafe-inline' fonts.googleapis.com"
```

Every value in effect came from a six-line block in `base-core.html` labelled
*"Last-resort token values for when the design-system CDN is unreachable."* The
green, the system font — that was the emergency fallback, running as the design.
`preconnect` tags pointed at Google Fonts with no stylesheet behind them, so no
webfont ever loaded. `connect-src 'self'` was blocking the GoatCounter beacon.

Also true at the start: no dark mode anywhere (zero `prefers-color-scheme` rules
across all three sheets), no `prefers-reduced-motion`, 147 hardcoded hex values
in the CSS, and 33 custom properties in a 3,768-line stylesheet.

## The system

Named for the Library of Alexandria, whose catalogue Callimachus called the
*Pinakes*. Eunomia's Labors already called its data-table component the pinax.
The system was arguably designed for this site and reached the other two first.

The palette is sampled from Attic red-figure pottery: `--papyrus` ground,
`--gloss` fired slip for text, `--dilute` for secondary, `--terracotta` as the
only saturated hue with a documented budget, `--faience` which marks absence and
nothing else. Dark mode inverts gloss and clay, which is black-figure.

The grammar is the Alexandrian critical apparatus: the **paragraphos** (a stroke
hanging in the left gutter, not a rule across the measure), the **diple** (the
margin mark for "note this" — the current journal, the FAQ chevron, the abstract
fold), the **coronis** (the end mark closing a page), the **obelos** (superseded
text, kept and marked).

`tokens.css` and `fonts.css` are verbatim copies. Edit upstream, then copy. See
`static/README.md`.

## Order of work

| Phase | What |
|---|---|
| 0 | CSP, self-hosted fonts, the woff2 MIME type, the migration mechanism |
| 1 | 57 icons in three sprites, and `/design/icons` |
| 2 | `/tools` as the pilot — the shell, the capsa, tool cards |
| 3–4 | Every remaining template, in traffic order |
| 5 | The chart bridge and the validated categorical palette |
| 6 | Retirement |

The pilot came before the fan-out deliberately: the shell was reviewed on one
real page before thirty-eight others depended on it.

**The migration mechanism** was `base-alexandrian.html` sitting *alongside*
`base.html` rather than replacing it, so a page crossed by changing one
`extends` line. When the last one crossed, `base.html`, `_feature_nav.html` and
`_journal_list.html` were deleted.

## Decisions worth keeping

**The sidebar is the capsa** — the box a library stood its rolls in, each with
its sillybos tag turned outward. The current journal takes a diple in the
margin rather than a colour wash, so the mark survives a printout with
background graphics off and does not rest on colour alone. Unavailable journals
are faience.

**Eight categorical hues, never cycled.** The old palette was 25 hues cycled
with `i % length` over 54 journals. Cycling says two journals are the same
journal, and 25 hues were never distinguishable. The eight most-present journals
keep an identity; the tail is drawn as "Other". This is a real loss of detail and
it is deliberate — widen `CATEGORICAL_LIMIT` in `js/utils/colors.js` to revert,
but nothing past eight has been validated.

Both figures were checked with the `dataviz` validator, not by eye. An earlier
ordering put madder beside verdigris and failed at deutan ΔE 1.7 — invisible to
a deutan reader. **The order is what was validated; do not reshuffle it.**

**Colour is read at draw time.** About 280 literals across 42 modules moved onto
the token layer, split by how each is consumed: a value painted to canvas or set
as an SVG presentation attribute became a `chrome()` accessor, because neither
resolves `var()`; a colour inside a CSS string became `var(--chart-*)`.

**Chart.js ships its own greys** — `#666` text, `rgba(0,0,0,0.1)` grid — and none
of the seven modules using it ever overrode them. `utils/chartjs-theme.js` sets
them from tokens.

**The error page keeps `base-core`.** The capsa's journal tags touch the DB for
article counts, and the most likely reason that page is showing is that the DB
is unreachable. Rendering a sidebar there cascades one failure into a second.
`test_404_inherits_from_base_core` now asserts this directly.

**`/wac` keeps its own masthead.** It is a standalone portrait of a press, not
part of the index's navigation.

## Things that bit, recorded so they do not again

- **`font-src` was missing `'self'` entirely.** Self-hosted faces could not have
  loaded no matter what else was right.
- **Windows seeds `mimetypes` from the registry**, where `.woff2` is frequently
  absent, so Flask would have served the faces as `application/octet-stream`.
  Registered explicitly in `app.py`.
- **`.woff2` was not in `.gitattributes`.** With `* text=auto eol=lf` and this
  repo authored from Windows and Linux both, a line-ending pass over a font file
  is silent corruption. Now marked binary.
- **`style-terminal.css:9` `@import`ed Share Tech Mono from Google Fonts**, and
  that sheet was served to every page. Tightening the CSP early would have put a
  console error on all 39 legacy pages, so the two origins stayed until the
  Terminal theme went.
- **Jinja's `Undefined` raises when iterated.** `/about`, `/glossary` and the
  admin routes pass no sidebar context, so the capsa would have 500'd on exactly
  the pages that touch the database least. `_capsa_tags` gates on
  `journal_groups`.
- **Document CSS does not reach into a `<use>` shadow tree**, but inherited
  custom properties do. That is why icon accents are properties, not classes.
- **`update()` is not recolouring.** Chart.js keeps dataset colours as the
  literal strings they were built from.
- **Nine templates carried a hand-written masthead each**, because `base-core`
  had no navigation. Removing them was −231 lines on its own.
- **A legend that said nothing, 47 times.** Handing Chart.js all 55 journals
  under a folding palette produced 47 legend entries in one grey. The timeline
  ranks and aggregates the tail instead.

## Retirement

Deleted: `style.css` (3,768), `style-scandi.css` (1,689), `style-terminal.css`
(720), `base.html`, `_feature_nav.html`, `_journal_list.html`.

The Scandi and Terminal themes are gone. Red-figure and black-figure replace
them, and a reader's stored `rc-theme` value is simply ignored.

A **legacy-token shim** carried the migration: six pages had their own `<style>`
blocks — about 375 lines — written against `--border`, `--accent`,
`--text-muted`. Mapping those names onto Alexandrian tokens was far cheaper than
rewriting them mid-migration, and it meant page-local CSS inverted for
black-figure with no edit. Those declarations were rewritten against the real
token names before the shim was removed, so nothing depends on it now.

The CSP ends at `style-src 'self'; font-src 'self'` — no third party for styles
or fonts at all. `script-src` still names `cdn.jsdelivr.net` for D3 and Chart.js
and `gc.zgo.at` for analytics.

## What this bought, besides the look

Dark mode, which the site never had. `prefers-reduced-motion`. A token layer that
actually loads. Webfonts at all, including Greek and polytonic in titles and
author names. And no third-party CSS or font requests, which for a
Clearinghouse-hosted scholarly index is a reasonable thing to be able to state
plainly.

## Open

- **`about.html` still compares Pinakes to CompPile** at lines 21 and 55. The
  September 2026 decision was to keep that framing out of public copy, since
  CompPile is also a Clearinghouse tool. The credit passages (lines 30, 51) read
  as attribution and are a different thing. Not changed here: it is authored
  prose, not markup.
- **Series identity colours in a few viz modules are still literal** —
  `#5a3e28`, `#3a5a28`, `#b38a6a`. Those are data, not chrome, and folding them
  into the categorical order changes what the charts show.
- **`docs/refactor-notes/05-css-audit.md` and `06-repo-hygiene.md`** describe
  stylesheets that no longer exist.
