# 05 — CSS audit (Prompt C1, Item 5)

## Theme system

The Rhet-Comp Index uses a localStorage-based theme switcher. Three stylesheets are always loaded (`style.css`, `style-scandi.css`, `style-terminal.css`), but theme-specific styling is applied via CSS selectors scoped to an `<html>` class. On page load, JavaScript in `base-core.html` reads `localStorage.getItem('rc-theme')` and adds either `class="terminal"` or `class="scandi"` to the `<html>` element if a theme is saved (defaults to "normal" with no class). The Scandi stylesheet uses `html.scandi .selector` patterns, and Terminal uses `html.terminal .selector` patterns, ensuring that only the chosen theme's styles apply.

## Stylesheet inventory

| File | Lines | Purpose |
|------|-------|---------|
| `style.css` | 3,531 | Default theme (normal) — includes layout, components, page-specific styles, and all core styling |
| `style-scandi.css` | 1,687 | Scandi theme — minimal Scandinavian aesthetic with refined color palette and reduced visual clutter |
| `style-terminal.css` | 720 | Terminal theme — monospace-based design mimicking a retro terminal interface |

**Total CSS lines: 5,938**

## Suspected dead selectors

### style.css

Twelve class selectors that are defined in CSS but never appear in any template file, JavaScript manipulation, or dynamically set class attribute:

- `.author-letter-section` (line 910) — not found in templates or JS
- `.tool-card-section` (line 1276) — not found in templates or JS
- `.ego-tip` (line 1921) — not found in templates or JS
- `.citnet-tip` (line 2234) — not found in templates or JS
- `.most-cited-controls` (line 2480) — not found in templates or JS
- `.most-cited-control-group` (line 2490) — not found in templates or JS
- `.show-more-wrap` (line 2616) — not found in templates or JS
- `.about-section` (line 2920) — not found in templates or JS
- `.about-body` (line 2936) — not found in templates or JS
- `.citation-block` (line 2949) — not found in templates or JS (note: `.book-citation-block` and `.cocitation-block` are used, but bare `.citation-block` is not)
- `.about-colophon` (line 2961) — not found in templates or JS
- `.sidebar-about-link` (line 3006) — not found in templates or JS

### style-scandi.css

**No suspected dead selectors.** All class selectors in the Scandi stylesheet target classes that are defined and used in the base templates (e.g., `.layout`, `.sidebar`, `.article`, etc.). The Scandi theme only provides style overrides for existing components.

### style-terminal.css

**No suspected dead selectors.** All class selectors in the Terminal stylesheet target classes that are defined and used in the base templates. Like Scandi, Terminal is purely a theme override layer.

## False-positive caveats

- **Template logic interpolation**: Several selectors like `.active-feature-link`, `.alpha-link`, `.detail-page`, and `.ego-page` are used in templates via conditional Jinja2 logic (e.g., `{% if condition %} classname{% endif %}`). These are correctly included in the used set.
- **Dynamic className assignments**: `theme-toggle-btn` and `scandi-toggle-btn` are set dynamically via `termBtn.className = 'theme-toggle-btn'` in `base-core.html` and are therefore in use.
- **ID selectors**: No ID selectors (prefixed with `#`) appear to be dead. All ID selectors in CSS correspond to elements with matching `id` attributes in templates or are targeted by JavaScript.
- **Pseudo-class and tag selectors**: This audit skipped pseudo-classes (`:hover`, `:focus`, `:first-child`, etc.) and tag selectors (`body`, `h1`, etc.) as instructed. Only class and ID selectors were audited.
- **CSS variables**: Root-level CSS variable definitions (`:root`, `--variable-name`) propagate globally and are not audited as "dead" even if no explicit selector references them.

## Recommendation

Total CSS lines: 5,938. Suspected-dead rule count: 12 (all in `style.css`). The dead selectors appear to be remnants of features or design iterations that were removed from templates or refactored but whose CSS was not cleaned up. Recommend a follow-up review pass to either:

1. Remove the 12 dead CSS rules if those features are truly abandoned, or
2. Verify whether any of these selectors are injected dynamically at runtime by D3.js or other visualization libraries that are not statically analyzable.

**Do NOT delete in this prompt.** This audit is informational to support a future cleanup pass.
