# `static/` — assets and the theme system

Three CSS files plus one JS module live here. Every page loads all three CSS files via [`templates/base-core.html`](../templates/base-core.html); themes are switched by adding a class to `<html>` rather than swapping stylesheets, so all three are effective at once on every request.

## Files

| File | Lines | Role |
|---|---:|---|
| `style.css` | 3531 | Default theme. All component styles live here. |
| `style-scandi.css` | 1687 | Scandi theme. Pure override layer (`html.scandi .x`). |
| `style-terminal.css` | 720 | Terminal theme. Pure override layer (`html.terminal .x`). |
| `explore.js` | — | D3-based visualisations on `/explore`. |

## Theme switching

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

A one-time audit in [`docs/refactor-notes/05-css-audit.md`](../docs/refactor-notes/05-css-audit.md) lists 12 selectors in `style.css` that no longer match any template or JS class. They are not deleted — left as a follow-up so a maintainer can confirm none are dynamically injected (e.g., by D3 in `explore.js`) before removing them.
